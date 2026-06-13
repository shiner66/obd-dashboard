/* global React, Icon, Sparkline, LineChart, RadialGauge, TripMap,
          DpfPill, AlertChip, StatCard, TripCard, InsightCard,
          VEHICLE, TRIPS, ALERTS, TREND_INSIGHTS, PID_CATALOG, PID_GROUPS,
          TweaksPanel, useTweaks, TweakSection, TweakRadio, TweakColor,
          TweakToggle, TweakSlider, TweakSelect */
const { useState, useMemo, useEffect, useRef } = React;

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "theme": "midnight",
  "accent": "cyan",
  "density": "regular",
  "anim": "playful",
  "sidebar": "auto",
  "showSpeakerSparks": true,
  "mapTiles": "dark"
}/*EDITMODE-END*/;

const ACCENT_HUES = { cyan: 200, amber: 75, violet: 290, mint: 152, magenta: 0, sky: 240 };
const ACCENT_COLORS = {
  cyan:    "oklch(0.78 0.14 200)",
  amber:   "oklch(0.78 0.16 75)",
  violet:  "oklch(0.72 0.14 290)",
  mint:    "oklch(0.78 0.14 152)",
  magenta: "oklch(0.70 0.18 0)",
  sky:     "oklch(0.78 0.14 240)",
};

/* ============== Top bar ============== */
const TopBar = ({ view, onSearch, onMenu }) => (
  <div className="topbar">
    <button className="menu-btn" onClick={onMenu} aria-label="Apri menu">
      <Icon name="list" size={18} />
    </button>
    <div>
      <div className="crumb">{VEHICLE.name} · {VEHICLE.ecu}</div>
      <h1>{view}</h1>
    </div>
    <div className="topbar-spacer" />
    <div className="search">
      <Icon name="search" size={14} />
      <input placeholder="Cerca viaggi, PID, alert…" onChange={e => onSearch?.(e.target.value)} />
      <span className="kbd">⌘K</span>
    </div>
    <button className="icon-btn"><Icon name="download" size={14} /><span>Esporta</span></button>
    <button className="icon-btn"><Icon name="bell" size={14} /></button>
  </div>
);

/* ============== Sidebar ============== */
const Sidebar = ({ active, setActive }) => {
  const usefulPids = PID_CATALOG.filter(p => p.useful !== false).length;
  const items = [
    { id: "dashboard", icon: "gauge",    label: "Dashboard" },
    { id: "trips",     icon: "list",     label: "Viaggi",       badge: TRIPS.length },
    { id: "map",       icon: "map",      label: "Mappa" },
    { id: "pids",      icon: "pid",      label: "PID Explorer", badge: usefulPids },
    { id: "dpf",       icon: "chart",    label: "DPF / FAP" },
    { id: "myopel",    icon: "fuel",     label: "MyOpel" },
    { id: "trends",    icon: "trend",    label: "Trend & AI" },
    { id: "admin",     icon: "gauge",    label: "Admin" },
  ];

  // Trip counts
  const obdCount = TRIPS.filter(t => t.sources.includes("obd")).length;
  const myopCount = TRIPS.filter(t => t.sources.includes("myopel")).length;

  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-mark"></div>
        <div>
          <div className="brand-name">OBD Cockpit</div>
          <div className="brand-sub"><span className="live-dot"></span>v0.5 · live</div>
        </div>
      </div>

      <div className="nav-section">Workspace</div>
      {items.map(it => (
        <div key={it.id}
             className={`nav-item ${active === it.id ? "active" : ""}`}
             onClick={() => setActive(it.id)}>
          <Icon name={it.icon} size={15} className="nav-icon" />
          <span>{it.label}</span>
          {it.badge != null && <span className="nav-badge">{it.badge}</span>}
        </div>
      ))}

      <div className="nav-section">Sorgenti</div>
      <div className="nav-item" style={{ cursor: "default" }}>
        <span className="src-tag obd">OBD</span>
        <span style={{ fontSize: 12, color: "var(--fg-2)" }}>CarScanner CSV/BRC</span>
        <span className="nav-badge">{obdCount}</span>
      </div>
      <div className="nav-item" style={{ cursor: "default" }}>
        <span className="src-tag myopel">MYOPEL</span>
        <span style={{ fontSize: 12, color: "var(--fg-2)" }}>Stellantis .myop</span>
        <span className="nav-badge">{myopCount}</span>
      </div>

      <div className="veh-card">
        {VEHICLE.vin && (
          <img
            src={`https://visual3d-secure.opel-vauxhall.com/V3DImage.ashx?client=MyMarque&vin=${encodeURIComponent(VEHICLE.vin)}&format=png&width=320&view=001`}
            alt={VEHICLE.name}
            className="veh-img"
            onError={e => {
              e.target.src = `https://cdn.imagin.studio/getImage?customer=img&make=opel&modelFamily=corsa&modelYear=2022&zoomType=fullscreen&angle=29`;
              e.target.onerror = () => { e.target.style.display = "none"; };
            }}
          />
        )}
        <div className="veh-name">{VEHICLE.name}</div>
        <div className="muted mono" style={{ fontSize: 11, marginBottom: 8 }}>{VEHICLE.ecu}</div>
        <div className="veh-row"><span>Odometro</span><span className="v">{VEHICLE.odometer?.toLocaleString("it-IT") ?? "—"} km</span></div>
        <div className="veh-row"><span>Carburante</span><span className="v">{VEHICLE.fuelLevel ?? "—"}% · {VEHICLE.fuelAutonomy ?? "—"} km</span></div>
        <div className="veh-row"><span>AdBlue</span><span className="v">{VEHICLE.adblueRange?.toLocaleString("it-IT") ?? "—"} km</span></div>
        <div className="veh-row"><span>Batteria</span><span className="v">{VEHICLE.battery?.toFixed(2) ?? "—"} V</span></div>
        <div className="veh-row"><span>Service</span><span className="v">{VEHICLE.nextService?.days ?? "—"} g · {VEHICLE.nextService?.km?.toLocaleString("it-IT") ?? "—"} km</span></div>
      </div>
    </aside>
  );
};

/* ============== Dashboard view ============== */
const Dashboard = ({ setActive, setSelectedTripId }) => {
  const recent = TRIPS.slice(0, 5);
  const obd = TRIPS.filter(t => t.sources.includes("obd"));
  const myop = TRIPS.filter(t => t.sources.includes("myopel"));
  const totalKm = TRIPS.reduce((a, t) => a + (t.distanceKm || 0), 0);
  const totalMin = TRIPS.reduce((a, t) => a + (t.durationMin || 0), 0);
  const totalFuel = TRIPS.reduce((a, t) => a + (t.fuelConsumedL || 0), 0);
  const avgCons = totalFuel > 0 ? (totalFuel / totalKm * 100) : 0;
  const cost = TRIPS.reduce((a, t) => a + (t.costEur || 0), 0);
  const fuelPriced = myop.filter(t => t.priceFuel);
  const avgFuelPrice = fuelPriced.length > 0
    ? fuelPriced.reduce((a, t) => a + t.priceFuel, 0) / fuelPriced.length
    : null;

  return (
    <div className="page" style={{ display: "flex", flexDirection: "column", gap: 24 }}>
      <div className="stat-grid stagger" style={{ gridTemplateColumns: "repeat(5, 1fr)" }}>
        <StatCard label="Viaggi totali" value={TRIPS.length} sub={`${obd.length} OBD · ${myop.length} MyOpel`} />
        <StatCard label="Distanza" value={totalKm.toFixed(1)} unit="km" sub="tutti i viaggi" />
        <StatCard label="Tempo guida" value={(totalMin / 60).toFixed(1)} unit="h" sub={`${Math.round(totalMin)} minuti`} />
        <StatCard label="Consumo medio" value={avgCons.toFixed(2)} unit="L/100km" sub="da PID L/h integrato" />
        <StatCard label="Spesa MyOpel" value={`€${cost.toFixed(2)}`} sub={`${myop.length} viaggi · €${avgFuelPrice?.toFixed(3) ?? "—"}/L`} />
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
        <div className="dpf-block">
          <RadialGauge value={VEHICLE.dpfClosedSoot ?? 0} max={10} label="g/L" strokeColor="var(--warn)" />
          <div className="dpf-meta">
            <div><div className="lbl">Closed soot</div><div className="v">{VEHICLE.dpfClosedSoot != null ? VEHICLE.dpfClosedSoot + " g/L" : "—"}</div></div>
            <div><div className="lbl">Km dall'ultima regen</div><div className="v">{VEHICLE.dpfSinceRegenKm} <span className="muted">/ {VEHICLE.dpfAvgRegenKm}</span></div></div>
            <div><div className="lbl">Vita residua DPF</div><div className="v">{VEHICLE.dpfReplaceKm ? (VEHICLE.dpfReplaceKm / 1000).toFixed(1) + "k km" : "—"}</div></div>
            <div><div className="lbl">Stato</div><div className="v"><DpfPill state={VEHICLE.dpfRegenState || "idle"} /></div></div>
          </div>
        </div>

        <div className="dpf-block">
          <div style={{ width: 120, display: "flex", flexDirection: "column", gap: 8, alignItems: "center" }}>
            <div style={{ fontSize: 11, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: "0.08em" }}>Tank</div>
            <div style={{ fontFamily: "var(--font-mono)", fontSize: 28, color: "var(--fg-0)" }}>{VEHICLE.fuelLevel}%</div>
            <div style={{ width: 80 }}><AnimatedBar value={VEHICLE.fuelLevel} max={100} color="var(--accent)" /></div>
            <div className="muted mono" style={{ fontSize: 11 }}>{VEHICLE.fuelAutonomy} km</div>
          </div>
          <div className="dpf-meta">
            <div><div className="lbl">AdBlue range</div><div className="v">{VEHICLE.adblueRange?.toLocaleString("it-IT") ?? "—"} km</div></div>
            <div><div className="lbl">Batteria avvio</div><div className="v">{VEHICLE.battery?.toFixed(2) ?? "—"} V</div></div>
            <div><div className="lbl">Prox. tagliando</div><div className="v">{VEHICLE.nextService?.days ?? "—"} g</div></div>
            <div><div className="lbl">Olio dilution</div><div className="v">{VEHICLE.oilDilutionPct != null ? VEHICLE.oilDilutionPct + " %" : "—"}</div></div>
          </div>
        </div>
      </div>

      <div>
        <div className="section-head">
          <span className="section-title">Viaggi recenti</span>
          <span className="section-sub">{recent.length} di {TRIPS.length}</span>
          <span style={{ flex: 1 }} />
          <button className="icon-btn" onClick={() => setActive("trips")}>Vedi tutti <Icon name="chevron" size={12} /></button>
        </div>
        <div className="stagger" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: 12 }}>
          {recent.map(t => (
            <TripCard key={t.id} trip={t} onClick={() => { setSelectedTripId(t.id); setActive("trips"); }} />
          ))}
        </div>
      </div>

      <div>
        <div className="section-head">
          <span className="section-title">Insights trasversali</span>
          <span className="section-sub">{TREND_INSIGHTS.length} regole attive</span>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", gap: 12 }}>
          {TREND_INSIGHTS.map((ins, i) => <InsightCard key={i} insight={ins} />)}
        </div>
      </div>
    </div>
  );
};

/* ============== Trips view (list + detail) ============== */
const TripsView = ({ selectedId, setSelectedId }) => {
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");

  const filtered = useMemo(() => {
    return TRIPS.filter(t => {
      if (filter === "obd"    && !t.sources.includes("obd")) return false;
      if (filter === "myopel" && !t.sources.includes("myopel")) return false;
      if (filter === "alerts" && (!t.alerts || t.alerts.length === 0)) return false;
      if (filter === "regen"  && !["active", "requested", "completed", "post_regen"].includes(t.dpfRegenState)) return false;
      if (search) {
        const q = search.toLowerCase();
        return (t.filename || "").toLowerCase().includes(q) ||
               t.start.toLowerCase().includes(q) ||
               ("" + (t.myopId || "")).includes(q);
      }
      return true;
    });
  }, [filter, search]);

  const trip = TRIPS.find(t => t.id === selectedId) || filtered[0];

  return (
    <div className="page-grid">
      <div className="trip-list">
        <div className="filter-bar">
          <div className="search" style={{ width: "100%" }}>
            <Icon name="search" size={14} />
            <input placeholder="Filtra viaggi…" value={search} onChange={e => setSearch(e.target.value)} />
          </div>
          <div className="filter-row">
            {[
              ["all",    "Tutti",   TRIPS.length],
              ["obd",    "OBD",     TRIPS.filter(t => t.sources.includes("obd")).length],
              ["myopel", "MyOpel",  TRIPS.filter(t => t.sources.includes("myopel")).length],
              ["regen",  "Regen DPF",  TRIPS.filter(t => ["active","requested","completed","post_regen"].includes(t.dpfRegenState)).length],
              ["alerts", "Con alert", TRIPS.filter(t => t.alerts && t.alerts.length > 0).length],
            ].map(([id, lbl, n]) => (
              <button key={id} className={`chip ${filter === id ? "active" : ""}`} onClick={() => setFilter(id)}>
                {lbl} <span className="mono muted" style={{ fontSize: 10 }}>{n}</span>
              </button>
            ))}
          </div>
        </div>
        <div className="stagger" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {filtered.map(t => (
            <TripCard key={t.id} trip={t} active={trip && t.id === trip.id} onClick={() => setSelectedId(t.id)} />
          ))}
        </div>
        {filtered.length === 0 && (
          <div className="muted" style={{ padding: 20, textAlign: "center" }}>Nessun viaggio corrisponde ai filtri.</div>
        )}
      </div>

      {trip ? <TripDetail trip={trip} /> : <div className="empty-state">Seleziona un viaggio</div>}
    </div>
  );
};

/* ============== Trip detail panel ============== */
const TripDetail = ({ trip }) => {
  const [tab, setTab] = useState("overview");
  const isObd = trip.sources.includes("obd");
  const startDate = new Date(trip.start);

  return (
    <div className="detail">
      <div className="detail-head">
        <div style={{ flex: 1 }}>
          <h2 className="detail-title">
            {startDate.toLocaleDateString("it-IT", { weekday: "long", day: "numeric", month: "long", year: "numeric" })}
            <span style={{ marginLeft: 12, color: "var(--fg-2)", fontWeight: 400 }}>
              {startDate.toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit" })}
              {" → "}
              {new Date(trip.end).toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit" })}
            </span>
          </h2>
          <div className="detail-sub">
            {trip.filename && <span>📄 {trip.filename}</span>}
            {trip.myopId && (
              <span>MyOpel #{trip.myopId}
                {trip.myopLegIds && trip.myopLegIds.length > 1 && ` +${trip.myopLegIds.length - 1} tratte`}
              </span>
            )}
            {trip.dpfRegenState && <DpfPill state={trip.dpfRegenState} />}
            {trip.alerts && trip.alerts.map(c => <AlertChip key={c} code={c} />)}
          </div>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button className="icon-btn"><Icon name="download" size={12} /> CSV</button>
          <button className="icon-btn"><Icon name="settings" size={12} /></button>
        </div>
      </div>

      <div className="tabs">
        {[
          ["overview", "Panoramica"],
          ...(isObd ? [["dpf", "DPF / FAP"], ["pids", "PID Explorer"]] : []),
          ["insights", `Insights${trip.insights?.length ? " · " + trip.insights.length : ""}`],
        ].map(([id, lbl]) => (
          <div key={id} className={`tab ${tab === id ? "active" : ""}`} onClick={() => setTab(id)}>{lbl}</div>
        ))}
      </div>

      {tab === "overview" && <TripOverview trip={trip} />}
      {tab === "dpf" && <TripDpf trip={trip} />}
      {tab === "pids" && <TripPids trip={trip} />}
      {tab === "insights" && <TripInsights trip={trip} />}
    </div>
  );
};

const TripOverview = ({ trip }) => {
  const isObd = trip.sources.includes("obd");
  return (
    <>
      <div className="hero-grid">
        <div className="map-wrap">
          {trip.track ? (
            <>
              <TripMap trip={trip} height={340} />
              <div className="map-overlay">
                <div className="lbl">GPS Track</div>
                <div className="v">{trip.track.length} punti · {trip.distanceKm} km</div>
              </div>
              <div className="map-legend">
                <div className="row"><span className="sw" style={{background:"oklch(0.78 0.14 152)"}}></span>partenza</div>
                <div className="row"><span className="sw" style={{background:"oklch(0.86 0.16 200)"}}></span>traccia</div>
                <div className="row"><span className="sw" style={{background:"oklch(0.68 0.20 22)"}}></span>arrivo</div>
              </div>
            </>
          ) : (
            <div className="empty-state" style={{height: 340}}>
              <Icon name="map" size={36} className="icon" />
              <div>Nessun tracciato GPS</div>
              <div className="muted" style={{fontSize: 12}}>Viaggio solo MyOpel: la TCU non condivide waypoint</div>
            </div>
          )}
        </div>

        <div className="stat-grid">
          <StatCard label="Distanza"   value={trip.distanceKm?.toFixed(1) ?? "—"} unit="km" />
          <StatCard label="Durata"     value={trip.durationMin?.toFixed(1) ?? "—"} unit="min" />
          <StatCard label="Vel. media" value={trip.avgSpeedKmh?.toFixed(0) ?? "—"} unit="km/h" />
          <StatCard label="Vel. max"   value={trip.maxSpeedKmh?.toFixed(0) ?? "—"} unit="km/h" />
          <StatCard label="Consumo"    value={trip.consumptionL100km?.toFixed(2) ?? "—"} unit="L/100km" />
          <StatCard label="Carburante" value={trip.fuelConsumedL?.toFixed(2) ?? "—"} unit="L" />
        </div>
      </div>

      {isObd && (
        <div>
          <div className="section-head">
            <span className="section-title">PID critici</span>
            <span className="section-sub">curated</span>
          </div>
          <div className="spark-grid stagger">
            {[
              { slug: "rpm",     name: "RPM",       unit: "rpm",  color: "var(--accent)" },
              { slug: "speed",   name: "Velocità",  unit: "km/h", color: "var(--accent)" },
              { slug: "coolant", name: "Liquido",   unit: "°C",   color: "var(--warn)" },
              { slug: "egt_a",   name: "EGT post",  unit: "°C",   color: "var(--crit)" },
              { slug: "closed_soot", name: "Closed soot", unit: "g/L", color: "var(--warn)" },
            ].map(p => {
              const stats = trip.pidValues?.[p.slug];
              const series = trip.pidSeriesFull?.[p.slug];
              if (!stats) return null;
              return (
                <div className="spark-tile" key={p.slug}>
                  <div className="head">
                    <span className="name">{p.name}</span>
                    <span className="val">{stats.last?.toFixed?.(stats.kind === "number" ? 0 : 0) ?? stats.last}<span style={{ color: "var(--fg-3)", fontSize: 11, marginLeft: 2 }}>{p.unit}</span></span>
                  </div>
                  <Sparkline data={series} color={p.color} />
                  <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: "var(--fg-3)", fontFamily: "var(--font-mono)" }}>
                    <span>min {stats.min}</span>
                    <span>avg {stats.mean}</span>
                    <span>max {stats.max}</span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {trip.sources.includes("myopel") && (
        <div>
          <div className="section-head">
            <span className="section-title">Dati MyOpel (Stellantis)</span>
            <span className="section-sub">.myop · canale ufficiale TCU</span>
          </div>
          <div className="stat-grid" style={{ gridTemplateColumns: "repeat(4, 1fr)" }}>
            <StatCard label="ID Stellantis" value={trip.myopId ?? "—"}
                      sub={trip.myopLegIds && trip.myopLegIds.length > 1
                           ? `${trip.myopLegIds.length} tratte unite` : undefined} />
            <StatCard label="Odometro fine" value={trip.odometerKm?.toLocaleString("it-IT") ?? "—"} unit="km" />
            {trip.fuelLevel && <StatCard label="Tank a fine" value={trip.fuelLevel} unit="%" />}
            {trip.fuelAutonomy && <StatCard label="Autonomia" value={trip.fuelAutonomy} unit="km" />}
            {trip.costEur && <StatCard label="Costo stimato" value={`€${trip.costEur.toFixed(2)}`} sub={`@ €${trip.priceFuel}/L`} />}
            {trip.priceFuel && <StatCard label="Prezzo carburante" value={`€${trip.priceFuel}`} unit="/L" />}
          </div>
        </div>
      )}
    </>
  );
};

const TripDpf = ({ trip }) => {
  const stages = ["idle", "requested", "active", "completed", "post_regen"];
  const labels = {
    idle: "Idle", requested: "Richiesta", active: "Attiva",
    completed: "Completata", post_regen: "Post-regen",
  };
  const currentIdx = stages.indexOf(trip.dpfRegenState);

  return (
    <>
      <div className="dpf-block">
        <RadialGauge value={trip.dpfClosedSoot ?? 0} max={10} label="g/L" strokeColor="var(--warn)" />
        <div className="dpf-meta">
          <div><div className="lbl">Stato</div><div className="v"><DpfPill state={trip.dpfRegenState} /></div></div>
          <div><div className="lbl">Closed soot</div><div className="v">{trip.dpfClosedSoot != null ? trip.dpfClosedSoot + " g/L" : "—"}</div></div>
          <div><div className="lbl">Km dall'ultima regen</div><div className="v">{trip.dpfSinceRegenKm} <span className="muted">/ {trip.dpfAvgRegenKm} avg</span></div></div>
          <div><div className="lbl">EGT post-cat (peak)</div><div className="v">{trip.exhaustAfterCatC} <span className="muted">°C</span></div></div>
          <div><div className="lbl">NOx cat (peak)</div><div className="v">{trip.noxCatTempMaxC} <span className="muted">°C</span></div></div>
          <div><div className="lbl">Vita residua DPF</div><div className="v">{trip.dpfReplaceKm != null ? (trip.dpfReplaceKm / 1000).toFixed(1) + "k" : "—"} <span className="muted">km</span></div></div>
          <div><div className="lbl">Olio dilution</div><div className="v">{trip.oilDilutionPct != null ? trip.oilDilutionPct + " %" : "—"}</div></div>
        </div>
      </div>

      <div>
        <div className="section-head">
          <span className="section-title">DPF state machine</span>
          <span className="section-sub">§8 — derivata da regen_status + EGT + Δkm</span>
        </div>
        <div className="dpf-state-row" style={{ display: "flex", gap: 0, alignItems: "center", flexWrap: "wrap" }}>
          {stages.map((s, i) => (
            <React.Fragment key={s}>
              <div style={{
                padding: "10px 14px", borderRadius: 999,
                background: i <= currentIdx ? "var(--bg-2)" : "var(--bg-1)",
                border: `1px solid ${i === currentIdx ? "var(--accent)" : "var(--line-soft)"}`,
                color: i === currentIdx ? "var(--fg-0)" : (i < currentIdx ? "var(--fg-1)" : "var(--fg-3)"),
                fontFamily: "var(--font-mono)", fontSize: 12,
                position: "relative",
              }}>
                {i <= currentIdx && <span className="dpf-pill" style={{
                  padding: 0, border: 0, background: "transparent",
                  position: "absolute", left: 8, top: "50%", transform: "translateY(-50%)",
                }}><span className="dot" style={{ background: i === currentIdx && trip.dpfRegenState === "active" ? "var(--crit)" : "var(--accent)" }}></span></span>}
                <span style={{ paddingLeft: i <= currentIdx ? 14 : 0 }}>{labels[s]}</span>
              </div>
              {i < stages.length - 1 && <div style={{ width: 24, height: 2, background: i < currentIdx ? "var(--accent)" : "var(--line-soft)" }}></div>}
            </React.Fragment>
          ))}
        </div>
      </div>

      <div>
        <div className="section-head">
          <span className="section-title">EGT post-catalizzatore</span>
          <span className="section-sub">soglia regen attiva &gt; 550 °C</span>
        </div>
        <div style={{ background: "var(--bg-1)", border: "1px solid var(--line-soft)", borderRadius: "var(--r)", padding: 12 }}>
          <LineChart data={trip.pidSeriesFull?.egt_a || []} color="var(--crit)" yLabel="°C" />
        </div>
      </div>

      <div>
        <div className="section-head">
          <span className="section-title">Andamento closed soot</span>
          <span className="section-sub">g/L — campioni DPF durante il viaggio</span>
        </div>
        <div style={{ background: "var(--bg-1)", border: "1px solid var(--line-soft)", borderRadius: "var(--r)", padding: 12 }}>
          <LineChart data={trip.pidSeriesFull?.closed_soot || trip.pidSeriesFull?.soot || []} color="var(--warn)" yLabel="g/L" />
        </div>
      </div>
    </>
  );
};

const TripPids = ({ trip }) => {
  return <PidExplorerInner trip={trip} catalog={trip.pidCatalog || []} />;
};

const TripInsights = ({ trip }) => {
  const ins = trip.insights || [];
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {ins.length === 0 && <div className="muted">Nessun insight per questo viaggio.</div>}
      {ins.map((i, idx) => <InsightCard key={idx} insight={i} />)}
    </div>
  );
};

/* ============== PID Explorer (full catalog) ============== */
const PidExplorer = () => {
  const obdTrips = TRIPS.filter(t => t.sources.includes("obd"));
  const [tripId, setTripId] = useState(obdTrips[0]?.id);
  const trip = obdTrips.find(t => t.id === tripId) || obdTrips[0];
  return (
    <div className="page" style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div className="row" style={{ gap: 8 }}>
        <span className="muted">Sessione OBD:</span>
        <select className="icon-btn" style={{ background: "var(--bg-2)" }}
                value={tripId} onChange={e => setTripId(e.target.value)}>
          {obdTrips.map(t => <option key={t.id} value={t.id}>{t.start.replace("T", " ")} · {t.durationMin}m · {t.distanceKm}km</option>)}
        </select>
        <span className="muted" style={{ marginLeft: "auto" }}>
          {PID_CATALOG.length} PID monitorati · {Object.keys(trip?.pidValues || {}).length} con dati
        </span>
      </div>
      <PidExplorerInner trip={trip} catalog={PID_CATALOG} />
    </div>
  );
};

const PidExplorerInner = ({ trip, catalog = PID_CATALOG }) => {
  const [search, setSearch] = useState("");
  const [group, setGroup] = useState("Tutti");
  const [kind, setKind] = useState("Tutti");
  const [selected, setSelected] = useState("rpm");
  const [sortBy, setSortBy] = useState("name");
  const [showAll, setShowAll] = useState(false);

  const usefulCount = useMemo(
    () => catalog.filter(p => p.useful !== false && trip?.pidValues?.[p.slug]).length,
    [catalog, trip]
  );
  const totalCount = useMemo(
    () => catalog.filter(p => trip?.pidValues?.[p.slug]).length,
    [catalog, trip]
  );

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    let list = catalog.filter(p => {
      if (!showAll && p.useful === false) return false;
      if (group !== "Tutti" && p.group !== group) return false;
      if (kind !== "Tutti" && p.kind !== kind) return false;
      if (q && !p.name.toLowerCase().includes(q) && !p.short.toLowerCase().includes(q)
            && !p.slug.toLowerCase().includes(q)) return false;
      if (!trip?.pidValues?.[p.slug]) return false;
      return true;
    });
    list.sort((a, b) => {
      if (sortBy === "name") return a.name.localeCompare(b.name);
      if (sortBy === "group") return a.group.localeCompare(b.group) || a.name.localeCompare(b.name);
      const sa = trip.pidValues[a.slug], sb = trip.pidValues[b.slug];
      if (sortBy === "samples") return (sb?.samples || 0) - (sa?.samples || 0);
      if (sortBy === "rate") return (sb?.sample_rate_hz || 0) - (sa?.sample_rate_hz || 0);
      return 0;
    });
    return list;
  }, [search, group, kind, sortBy, trip, catalog, showAll]);

  const selPid = catalog.find(p => p.slug === selected);
  const selStats = trip?.pidValues?.[selected];
  const selSeries = trip?.pidSeriesFull?.[selected];

  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 420px", gap: 16, alignItems: "flex-start" }} className="pid-explorer-grid">
      <div style={{ background: "var(--bg-1)", border: "1px solid var(--line-soft)", borderRadius: "var(--r)", overflow: "hidden" }}>
        <div style={{ padding: 12, borderBottom: "1px solid var(--line-soft)", display: "flex", flexDirection: "column", gap: 10 }}>
          <div className="row" style={{ gap: 10 }}>
            <div className="search" style={{ flex: 1 }}>
              <Icon name="search" size={14} />
              <input placeholder="Cerca PID…" value={search} onChange={e => setSearch(e.target.value)} />
            </div>
            <div className="seg" title="Mostra solo i PID significativi o tutti quelli registrati">
              <button className={showAll ? "" : "active"} onClick={() => setShowAll(false)}>Utili {usefulCount}</button>
              <button className={showAll ? "active" : ""} onClick={() => setShowAll(true)}>Tutti {totalCount}</button>
            </div>
          </div>
          <div className="filter-row">
            <button className={`chip ${group === "Tutti" ? "active" : ""}`} onClick={() => setGroup("Tutti")}>Tutti</button>
            {Object.keys(PID_GROUPS).map(g => (
              <button key={g} className={`chip ${group === g ? "active" : ""}`} onClick={() => setGroup(g)}>{g}</button>
            ))}
          </div>
          <div className="filter-row">
            {["Tutti", "number", "discrete", "bool"].map(k => (
              <button key={k} className={`chip ${kind === k ? "active" : ""}`} onClick={() => setKind(k)}>
                {k === "Tutti" ? "Ogni kind" : k}
              </button>
            ))}
            <span style={{ flex: 1 }} />
            <select className="icon-btn" style={{ background: "var(--bg-2)" }} value={sortBy} onChange={e => setSortBy(e.target.value)}>
              <option value="name">Ordina: nome</option>
              <option value="group">Ordina: gruppo</option>
              <option value="samples">Ordina: samples</option>
              <option value="rate">Ordina: sample rate</option>
            </select>
          </div>
        </div>

        <div className="pid-scroll">
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
            <thead style={{ position: "sticky", top: 0, background: "var(--bg-2)", zIndex: 2 }}>
              <tr style={{ textAlign: "left", color: "var(--fg-3)" }}>
                <th style={{ padding: "8px 12px", fontWeight: 500, fontSize: 10, textTransform: "uppercase", letterSpacing: "0.08em" }}>PID</th>
                <th style={{ padding: "8px 8px", fontWeight: 500, fontSize: 10, textTransform: "uppercase", letterSpacing: "0.08em" }}>Group</th>
                <th style={{ padding: "8px 8px", fontWeight: 500, fontSize: 10, textTransform: "uppercase", letterSpacing: "0.08em", textAlign: "right" }}>Last</th>
                <th style={{ padding: "8px 8px", fontWeight: 500, fontSize: 10, textTransform: "uppercase", letterSpacing: "0.08em", textAlign: "right" }}>Min/Max</th>
                <th style={{ padding: "8px 8px", fontWeight: 500, fontSize: 10, textTransform: "uppercase", letterSpacing: "0.08em" }}>Trace</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map(p => {
                const s = trip.pidValues[p.slug];
                const series = trip.pidSeriesFull?.[p.slug];
                const isSel = p.slug === selected;
                return (
                  <tr key={p.slug}
                      onClick={() => setSelected(p.slug)}
                      style={{
                        borderTop: "1px solid var(--line-soft)",
                        background: isSel ? "var(--accent-soft)" : "transparent",
                        cursor: "pointer",
                      }}>
                    <td style={{ padding: "6px 12px", color: isSel ? "var(--accent-strong)" : "var(--fg-0)" }}>
                      <div style={{ fontWeight: 500 }}>{p.name.replace(/^\[(ECM|TCU)\]\s*/i, "")}</div>
                      <div className="muted mono" style={{ fontSize: 10 }}>{p.slug}</div>
                    </td>
                    <td style={{ padding: "6px 8px", color: "var(--fg-2)" }}>{p.group}</td>
                    <td style={{ padding: "6px 8px", fontFamily: "var(--font-mono)", textAlign: "right", color: "var(--fg-0)" }}>
                      {typeof s.last === "number" ? s.last : "—"}
                      <span style={{ color: "var(--fg-3)", marginLeft: 3, fontSize: 10 }}>{p.unit}</span>
                    </td>
                    <td style={{ padding: "6px 8px", fontFamily: "var(--font-mono)", textAlign: "right", color: "var(--fg-2)", fontSize: 11 }}>
                      {s.min}/{s.max}
                    </td>
                    <td style={{ padding: "6px 8px", width: 100 }}>
                      <Sparkline data={series} height={20} color={isSel ? "var(--accent-strong)" : "var(--fg-3)"} showFill={false} />
                    </td>
                  </tr>
                );
              })}
              {filtered.length === 0 && (
                <tr><td colSpan="5" style={{ padding: 20, textAlign: "center", color: "var(--fg-3)" }}>
                  {trip && !trip.pidValues ? "Nessun dato PID — viaggio solo MyOpel o CSV non importato correttamente." : "Nessun PID trovato."}
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {selPid && selStats && (
        <div className="pid-detail-card" style={{ position: "sticky", top: 0, display: "flex", flexDirection: "column", gap: 12 }}>
          <div style={{ background: "var(--bg-1)", border: "1px solid var(--line-soft)", borderRadius: "var(--r)", padding: 16 }}>
            <div style={{ fontSize: 11, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: "0.08em" }}>
              {selPid.group} · {selPid.kind}
            </div>
            <div style={{ fontSize: 16, color: "var(--fg-0)", fontWeight: 600, marginTop: 4 }}>{selPid.name.replace(/^\[(ECM|TCU)\]\s*/i, "")}</div>
            <div className="muted mono" style={{ fontSize: 11, marginTop: 2 }}>{selPid.name}</div>
            <div className="muted mono" style={{ fontSize: 11 }}>slug: <span style={{ color: "var(--fg-1)" }}>{selPid.slug}</span></div>

            <div style={{ margin: "16px 0" }}>
              <LineChart data={selSeries} height={140} color="var(--accent)" yLabel={selPid.unit} />
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, fontSize: 12 }}>
              {[
                ["last", selStats.last],
                ["first", selStats.first],
                ["min", selStats.min],
                ["max", selStats.max],
                ["mean", selStats.mean],
                ["mode", selStats.mode],
              ].map(([k, v]) => (
                <div key={k} style={{ display: "flex", justifyContent: "space-between", padding: "4px 0", borderBottom: "1px solid var(--line-soft)" }}>
                  <span className="muted">{k}</span>
                  <span className="mono" style={{ color: "var(--fg-0)" }}>{v}<span className="muted" style={{ marginLeft: 3 }}>{selPid.unit}</span></span>
                </div>
              ))}
            </div>

            <div style={{ marginTop: 12, fontSize: 11, color: "var(--fg-3)", display: "flex", flexDirection: "column", gap: 3 }}>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span>samples</span><span className="mono" style={{ color: "var(--fg-1)" }}>{selStats.samples}</span>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span>sample rate</span><span className="mono" style={{ color: "var(--fg-1)" }}>{selStats.sample_rate_hz} Hz</span>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span>coverage</span><span className="mono" style={{ color: "var(--fg-1)" }}>{selStats.coverage_pct}%</span>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span>first / last seen</span><span className="mono" style={{ color: "var(--fg-1)" }}>{selStats.first_seen_s}s / {selStats.last_seen_s}s</span>
              </div>
              {selStats.is_stale && (
                <div style={{ marginTop: 4, color: "var(--warn)" }}>⚠ Sample stale (&gt; 60s dalla fine)</div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

/* ============== Map view (all trips) ============== */
const MapView = () => {
  const [selected, setSelected] = useState(TRIPS.find(t => t.track)?.id);
  const obdTrips = TRIPS.filter(t => t.track);
  const trip = obdTrips.find(t => t.id === selected);
  return (
    <div className="page-grid">
      <div className="trip-list">
        <div className="section-head" style={{ padding: "8px 4px" }}>
          <span className="section-title">Tracciati GPS</span>
          <span className="section-sub">{obdTrips.length} con GPS</span>
        </div>
        {obdTrips.map(t => (
          <TripCard key={t.id} trip={t} active={t.id === selected} onClick={() => setSelected(t.id)} />
        ))}
      </div>
      <div className="detail">
        <div className="map-wrap map-fullpage">
          <TripMap trip={trip} allTrips={obdTrips} height={"100%"} />
          <div className="map-overlay">
            <div className="lbl">Provincia di Salerno</div>
            <div className="v">{trip?.distanceKm ?? "—"} km · {trip?.track?.length ?? 0} punti</div>
            <div className="v" style={{ color: "var(--fg-3)" }}>bbox 40.45–40.70°N · 14.71–15.00°E</div>
          </div>
          <div className="map-legend">
            <div className="row"><span className="sw" style={{background:"oklch(0.86 0.16 200)"}}></span>traccia selezionata</div>
            <div className="row"><span className="sw" style={{background:"oklch(0.5 0.04 240)"}}></span>altri viaggi</div>
          </div>
        </div>
      </div>
    </div>
  );
};

/* ============== DPF / FAP view ============== */
const DpfView = () => {
  const obdTrips = TRIPS.filter(t => t.sources.includes("obd")).sort((a, b) => a.start.localeCompare(b.start));
  const sootSeries = obdTrips.map(t => t.dpfClosedSoot);
  const regenDistSeries = obdTrips.map(t => t.dpfSinceRegenKm);
  const egtSeries = obdTrips.map(t => t.exhaustAfterCatC);

  // Last regen event
  const lastRegen = obdTrips.filter(t => t.dpfRegenState === "completed").slice(-1)[0];

  return (
    <div className="page" style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 16 }}>
        <div className="dpf-block">
          <RadialGauge value={VEHICLE.dpfClosedSoot ?? 0} max={10} label="g/L" strokeColor="var(--warn)" />
          <div className="dpf-meta">
            <div><div className="lbl">Stato attuale</div><div className="v"><DpfPill state={VEHICLE.dpfRegenState || "idle"} /></div></div>
            <div><div className="lbl">Closed soot</div><div className="v">{VEHICLE.dpfClosedSoot != null ? VEHICLE.dpfClosedSoot + " g/L" : "—"}</div></div>
            <div><div className="lbl">Km da regen</div><div className="v">{VEHICLE.dpfSinceRegenKm}</div></div>
            <div><div className="lbl">Avg interval</div><div className="v">{VEHICLE.dpfAvgRegenKm} km</div></div>
          </div>
        </div>

        <div className="trend-card">
          <div className="section-head"><span className="section-title">Closed soot trend</span><span className="section-sub">g/L · ultimi {obdTrips.length} viaggi OBD</span></div>
          <div className="big-num">{sootSeries.filter(v => v != null).slice(-1)[0] ?? "—"}<span className="unit"> g/L</span></div>
          <Sparkline data={sootSeries} color="var(--warn)" height={70} />
          <div className="muted mono" style={{ fontSize: 11 }}>soglia rigenerazione: ~5 g/L</div>
        </div>

        <div className="trend-card">
          <div className="section-head"><span className="section-title">EGT post-cat</span><span className="section-sub">picchi per viaggio</span></div>
          <div className="big-num">{Math.max(...egtSeries)}<span className="unit">°C</span></div>
          <Sparkline data={egtSeries} color="var(--crit)" height={70} />
          <div className="muted mono" style={{ fontSize: 11 }}>soglia regen attiva: 550 °C</div>
        </div>
      </div>

      <div>
        <div className="section-head">
          <span className="section-title">Storia rigenerazioni</span>
          <span className="section-sub">solo viaggi con regen rilevata</span>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {obdTrips.filter(t => t.dpfRegenState !== "idle").map(t => (
            <div key={t.id} className="trip-card" style={{ cursor: "default" }}>
              <div className="trip-card-head">
                <span className="trip-date">{new Date(t.start).toLocaleDateString("it-IT", { day: "2-digit", month: "short" })}</span>
                <span className="trip-time">{new Date(t.start).toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit" })}</span>
                <DpfPill state={t.dpfRegenState} />
              </div>
              <div className="trip-card-stats">
                <div className="trip-stat"><span className="lbl">Closed soot</span>
                  <span className="val mono">{t.dpfClosedSoot ?? "—"}<span className="unit"> g/L</span></span>
                </div>
                <div className="trip-stat"><span className="lbl">EGT picco</span>
                  <span className="val mono">{t.exhaustAfterCatC}<span className="unit"> °C</span></span>
                </div>
                <div className="trip-stat"><span className="lbl">Km da ult. regen</span>
                  <span className="val mono">{t.dpfSinceRegenKm}<span className="unit"> km</span></span>
                </div>
                <div className="trip-stat"><span className="lbl">Durata viaggio</span>
                  <span className="val mono">{t.durationMin}<span className="unit"> min</span></span>
                </div>
                <div className="trip-stat" style={{ flex: 1, alignItems: "stretch" }}>
                  <span className="lbl">EGT curve</span>
                  <Sparkline data={t.pidSeriesFull?.egt_a || []} color="var(--crit)" height={30} />
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

/* ============== MyOpel view ============== */
const MyOpelView = () => {
  const myop = TRIPS.filter(t => t.sources.includes("myopel")).sort((a, b) => b.start.localeCompare(a.start));
  const totalCost = myop.reduce((a, t) => a + (t.costEur || 0), 0);
  const totalFuel = myop.reduce((a, t) => a + (t.fuelConsumedL || 0), 0);
  const totalKm = myop.reduce((a, t) => a + (t.distanceKm || 0), 0);
  const allAlerts = myop.flatMap(t => (t.alerts || []).map(c => ({ code: c, trip: t })));
  const fuelPriced = myop.filter(t => t.priceFuel);
  const avgPrice = fuelPriced.length > 0
    ? fuelPriced.reduce((a, t) => a + t.priceFuel, 0) / fuelPriced.length
    : null;

  return (
    <div className="page" style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      <div className="stat-grid" style={{ gridTemplateColumns: "repeat(4, 1fr)" }}>
        <StatCard label="Viaggi MyOpel" value={myop.length} sub="ultima sincronizzazione email: oggi 11:02" />
        <StatCard label="Spesa totale" value={`€${totalCost.toFixed(2)}`} sub={`${totalFuel.toFixed(2)} L · €${avgPrice?.toFixed(3) ?? "—"}/L`} />
        <StatCard label="Distanza" value={totalKm.toFixed(1)} unit="km" />
        <StatCard label="Alerts MyOpel" value={allAlerts.length} sub={`${new Set(allAlerts.map(a => a.code)).size} unici`} />
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 320px", gap: 16 }}>
        <div>
          <div className="section-head">
            <span className="section-title">Cronologia viaggi</span>
            <span className="section-sub">canale Stellantis · TCU</span>
          </div>
          <div className="table-wrap" style={{ background: "var(--bg-1)", border: "1px solid var(--line-soft)", borderRadius: "var(--r)" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
              <thead>
                <tr style={{ textAlign: "left", color: "var(--fg-3)", background: "var(--bg-2)" }}>
                  <th style={{ padding: "10px 12px", fontWeight: 500, fontSize: 10, textTransform: "uppercase", letterSpacing: "0.08em" }}>ID</th>
                  <th style={{ padding: "10px 12px", fontWeight: 500, fontSize: 10, textTransform: "uppercase", letterSpacing: "0.08em" }}>Data</th>
                  <th style={{ padding: "10px 12px", fontWeight: 500, fontSize: 10, textTransform: "uppercase", letterSpacing: "0.08em", textAlign: "right" }}>Distanza</th>
                  <th style={{ padding: "10px 12px", fontWeight: 500, fontSize: 10, textTransform: "uppercase", letterSpacing: "0.08em", textAlign: "right" }}>Durata</th>
                  <th style={{ padding: "10px 12px", fontWeight: 500, fontSize: 10, textTransform: "uppercase", letterSpacing: "0.08em", textAlign: "right" }}>Consumo</th>
                  <th style={{ padding: "10px 12px", fontWeight: 500, fontSize: 10, textTransform: "uppercase", letterSpacing: "0.08em", textAlign: "right" }}>Costo</th>
                  <th style={{ padding: "10px 12px", fontWeight: 500, fontSize: 10, textTransform: "uppercase", letterSpacing: "0.08em" }}>Alerts</th>
                </tr>
              </thead>
              <tbody>
                {myop.map(t => (
                  <tr key={t.id} style={{ borderTop: "1px solid var(--line-soft)" }}>
                    <td style={{ padding: "10px 12px", fontFamily: "var(--font-mono)", color: "var(--fg-2)" }}>#{t.myopId}</td>
                    <td style={{ padding: "10px 12px" }}>
                      {new Date(t.start).toLocaleDateString("it-IT", { day: "2-digit", month: "short" })}{" "}
                      <span className="muted mono" style={{ fontSize: 11 }}>
                        {new Date(t.start).toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit" })}
                      </span>
                    </td>
                    <td style={{ padding: "10px 12px", textAlign: "right", fontFamily: "var(--font-mono)" }}>{t.distanceKm?.toFixed(1) ?? "—"} <span className="muted">km</span></td>
                    <td style={{ padding: "10px 12px", textAlign: "right", fontFamily: "var(--font-mono)" }}>{t.durationMin?.toFixed(0) ?? "—"} <span className="muted">min</span></td>
                    <td style={{ padding: "10px 12px", textAlign: "right", fontFamily: "var(--font-mono)" }}>{t.consumptionL100km?.toFixed(2)} <span className="muted">L/100</span></td>
                    <td style={{ padding: "10px 12px", textAlign: "right", fontFamily: "var(--font-mono)" }}>{t.costEur ? `€${t.costEur.toFixed(2)}` : "—"}</td>
                    <td style={{ padding: "10px 12px" }}>
                      {t.alerts?.length > 0 ? (
                        <div className="row" style={{ gap: 4, flexWrap: "wrap" }}>
                          {t.alerts.map(c => <AlertChip key={c} code={c} />)}
                        </div>
                      ) : <span className="muted">—</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <div>
          <div className="section-head">
            <span className="section-title">Ingestione .myop</span>
            <span className="section-sub">watchdog /data/myop</span>
          </div>
          <div style={{ background: "var(--bg-1)", border: "1px solid var(--line-soft)", borderRadius: "var(--r)", padding: 14, fontSize: 12, display: "flex", flexDirection: "column", gap: 6 }}>
            <div className="row"><span className="dot" style={{ width: 6, height: 6, borderRadius: 3, background: "var(--ok)" }}></span>
              <span style={{ flex: 1 }}>Sorgente</span>
              <span className="mono muted">file .myop</span>
            </div>
            <div className="row"><span className="dot" style={{ width: 6, height: 6, borderRadius: 3, background: "var(--ok)" }}></span>
              <span style={{ flex: 1 }}>Viaggi caricati</span>
              <span className="mono muted">{myop.length}</span>
            </div>
            <div className="row"><span className="dot" style={{ width: 6, height: 6, borderRadius: 3, background: VEHICLE.vin ? "var(--ok)" : "var(--warn)" }}></span>
              <span style={{ flex: 1 }}>VIN identificato</span>
              <span className="mono muted">{VEHICLE.vin?.slice(-6) ?? "—"}</span>
            </div>
            <div className="divider"></div>
            <div className="muted" style={{ fontSize: 11, lineHeight: 1.5 }}>
              Ogni file .myop contiene <span className="mono">tutti</span> i viaggi cumulativamente.
              I duplicati sono dedotti tramite trip ID.
            </div>
          </div>

          <div className="section-head" style={{ marginTop: 20 }}>
            <span className="section-title">Alert ricorrenti</span>
          </div>
          <div className="alerts-list">
            {Array.from(new Set(allAlerts.map(a => a.code))).map(code => {
              const a = ALERTS[code];
              const n = allAlerts.filter(x => x.code === code).length;
              return (
                <div className="alert-row" key={code}>
                  <span className="sev-dot" style={{ background: a?.sev === "critical" ? "var(--crit)" : a?.sev === "warning" ? "var(--warn)" : "var(--info)" }}></span>
                  <div style={{ flex: 1 }}>
                    <div className="label">{a?.label || `Alert ${code}`}</div>
                    <div className="code">codice #{code} · {n} occorrenz{n === 1 ? "a" : "e"}</div>
                  </div>
                </div>
              );
            })}
            {allAlerts.length === 0 && <div className="muted">Nessun alert recente.</div>}
          </div>
        </div>
      </div>
    </div>
  );
};

/* ============== Trends & AI view ============== */
const TrendsView = () => {
  const obd = TRIPS.filter(t => t.sources.includes("obd")).sort((a, b) => a.start.localeCompare(b.start));
  const batteryTrend = obd.map(t => t.batteryStartupV);
  const consTrend = TRIPS.filter(t => t.consumptionL100km).map(t => t.consumptionL100km);
  const distTrend = TRIPS.map(t => t.distanceKm);
  const adblueTrend = obd.map(t => t.adblueRangeKm);
  const sootTrend = obd.map(t => t.dpfClosedSoot);
  const dilTrend = obd.filter(t => t.oilDilutionPct != null).map(t => ({ start: t.start, v: t.oilDilutionPct }));

  return (
    <div className="page" style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      <div className="trend-grid stagger">
        <div className="trend-card">
          <div className="section-head"><span className="section-title">Tensione batteria</span><span className="section-sub">all'avviamento, ultimi {batteryTrend.length} viaggi OBD</span></div>
          <div className="big-num">{batteryTrend[batteryTrend.length - 1]?.toFixed(2)}<span className="unit">V</span></div>
          <LineChart data={batteryTrend} color="var(--accent)" height={90} yLabel="V" />
        </div>
        <div className="trend-card">
          <div className="section-head"><span className="section-title">Consumo</span><span className="section-sub">L/100km · tutti i viaggi</span></div>
          <div className="big-num">{consTrend[consTrend.length - 1]?.toFixed(2)}<span className="unit">L/100km</span></div>
          <LineChart data={consTrend} color="var(--ok)" height={90} yLabel="L/100" />
        </div>
        <div className="trend-card">
          <div className="section-head"><span className="section-title">Distanza per viaggio</span><span className="section-sub">km</span></div>
          <div className="big-num">{distTrend[distTrend.length - 1]?.toFixed(1)}<span className="unit">km</span></div>
          <LineChart data={distTrend} color="var(--info)" height={90} yLabel="km" />
        </div>
        <div className="trend-card">
          <div className="section-head"><span className="section-title">Autonomia AdBlue</span><span className="section-sub">km residui</span></div>
          <div className="big-num">{adblueTrend[adblueTrend.length - 1]}<span className="unit">km</span></div>
          <LineChart data={adblueTrend} color="var(--warn)" height={90} yLabel="km" />
        </div>
        <div className="trend-card">
          <div className="section-head"><span className="section-title">Closed soot DPF</span><span className="section-sub">g/L — ciclo vita FAP</span></div>
          <div className="big-num">{sootTrend.filter(v => v != null).slice(-1)[0] ?? "—"}<span className="unit"> g/L</span></div>
          <LineChart data={sootTrend} color="var(--warn)" height={90} yLabel="g/L" />
        </div>
        {dilTrend.length >= 2 && (
          <div className="trend-card">
            <div className="section-head"><span className="section-title">Diluizione olio</span><span className="section-sub">% nel tempo — attenzione ai trend crescenti</span></div>
            <div className="big-num">{dilTrend[dilTrend.length - 1]?.v?.toFixed(1)}<span className="unit">%</span></div>
            <LineChart data={dilTrend.map(d => d.v)} color="var(--crit)" height={90} yLabel="%" />
          </div>
        )}
      </div>

      <div>
        <div className="section-head">
          <span className="section-title">Insights AI</span>
          <span className="section-sub">motore a regole — §11 del briefing</span>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(340px, 1fr))", gap: 12 }}>
          {TREND_INSIGHTS.map((ins, i) => <InsightCard key={i} insight={ins} />)}
        </div>
      </div>

      <div>
        <div className="section-head">
          <span className="section-title">Insights per viaggio</span>
          <span className="section-sub">raggruppati cronologicamente</span>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          {TRIPS.filter(t => t.insights && t.insights.length > 0).map(t => (
            <div key={t.id}>
              <div className="row" style={{ marginBottom: 6, color: "var(--fg-2)", fontSize: 12 }}>
                <span className="mono">{new Date(t.start).toLocaleString("it-IT", { dateStyle: "short", timeStyle: "short" })}</span>
                <span className="muted">·</span>
                <span>{t.distanceKm} km · {t.durationMin} min</span>
                {t.dpfRegenState && <DpfPill state={t.dpfRegenState} />}
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(340px, 1fr))", gap: 10 }}>
                {t.insights.map((i, idx) => <InsightCard key={idx} insight={i} />)}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

/* ============== Admin view ============== */
const AdminView = () => {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [correlating, setCorrelating] = useState(false);

  const check = async () => {
    setLoading(true);
    try {
      const r = await fetch("/api/v1/admin/uncorrelated");
      setData(await r.json());
    } catch(e) {
      setData({ error: e.message });
    } finally {
      setLoading(false);
    }
  };

  const correlate = async () => {
    setCorrelating(true);
    try {
      const r = await fetch("/api/v1/admin/correlate", { method: "POST" });
      const res = await r.json();
      await check();
      alert("Correlazione completata: " + JSON.stringify(res));
    } catch(e) {
      alert("Errore: " + e.message);
    } finally {
      setCorrelating(false);
    }
  };

  const fmt = (s) => s ? s.slice(0,16).replace("T"," ") : "—";

  return (
    <div className="page-single" style={{ maxWidth: 900 }}>
      <div className="section-head">
        <span className="section-title">Diagnostica correlazione</span>
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="row" style={{ gap: 10, flexWrap: "wrap" }}>
          <button className="icon-btn" onClick={check} disabled={loading}>
            {loading ? "…" : "Verifica trip non correlati"}
          </button>
          <button className="icon-btn" onClick={correlate} disabled={correlating} style={{ background: "var(--warn-soft)", color: "var(--warn)" }}>
            {correlating ? "…" : "Forza correlazione"}
          </button>
          {data && !data.error && (
            <span className="muted" style={{ fontSize: 12, alignSelf: "center" }}>
              OBD soli: <b>{data.standalone_obd}</b> · MyOpel soli: <b>{data.standalone_myop}</b> · Candidati: <b>{data.candidates?.length ?? 0}</b>
            </span>
          )}
        </div>
      </div>

      {data?.error && (
        <div className="insight-card critical"><b>Errore:</b> {data.error}</div>
      )}

      {data?.candidates?.length === 0 && (
        <div className="card" style={{ color: "var(--ok)", padding: 16 }}>
          Nessun trip sovrapposto non correlato trovato.
        </div>
      )}

      {data?.candidates?.map((c, i) => (
        <div key={i} className="card" style={{ marginBottom: 10, borderLeft: `3px solid ${c.would_correlate ? "var(--ok)" : "var(--warn)"}` }}>
          <div className="row" style={{ gap: 16, flexWrap: "wrap", alignItems: "flex-start" }}>
            <div style={{ flex: 1, minWidth: 200 }}>
              <div className="lbl" style={{ marginBottom: 4 }}>OBD</div>
              <div className="mono" style={{ fontSize: 12 }}>{c.obd.id}</div>
              <div style={{ fontSize: 13 }}>{fmt(c.obd.start)} → {fmt(c.obd.end)}</div>
              <div className="muted" style={{ fontSize: 12 }}>{c.obd.km?.toFixed(1) ?? "—"} km · {c.obd.min?.toFixed(0) ?? "—"} min</div>
            </div>
            <div style={{ flex: 1, minWidth: 200 }}>
              <div className="lbl" style={{ marginBottom: 4 }}>MyOpel</div>
              <div className="mono" style={{ fontSize: 12 }}>{c.myop.id}</div>
              <div style={{ fontSize: 13 }}>{fmt(c.myop.start)} → {fmt(c.myop.end)}</div>
              <div className="muted" style={{ fontSize: 12 }}>{c.myop.km?.toFixed(1) ?? "—"} km · {c.myop.min?.toFixed(0) ?? "—"} min</div>
            </div>
            <div style={{ textAlign: "right", minWidth: 100 }}>
              <div style={{ fontSize: 22, fontWeight: 700, color: c.score >= 0.5 ? "var(--ok)" : c.score >= 0.35 ? "var(--warn)" : "var(--muted)" }}>
                {(c.score * 100).toFixed(0)}%
              </div>
              <div className="muted" style={{ fontSize: 11 }}>punteggio</div>
              <div style={{ fontSize: 11, marginTop: 4, color: c.would_correlate ? "var(--ok)" : "var(--muted)" }}>
                {c.would_correlate ? "✓ sopra soglia" : "sotto soglia 50%"}
              </div>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
};

/* ============== Mobile bottom tab bar ============== */
const BottomNav = ({ active, setActive, onMenu }) => {
  const items = [
    { id: "dashboard", icon: "gauge", label: "Home" },
    { id: "trips",     icon: "list",  label: "Viaggi" },
    { id: "map",       icon: "map",   label: "Mappa" },
    { id: "dpf",       icon: "chart", label: "DPF" },
  ];
  const secondary = ["pids", "myopel", "trends", "admin"];
  return (
    <nav className="bottom-nav">
      {items.map(it => (
        <button key={it.id}
                className={`bn-item ${active === it.id ? "active" : ""}`}
                onClick={() => setActive(it.id)}>
          <span className="bn-ico"><Icon name={it.icon} size={18} /></span>
          <span>{it.label}</span>
        </button>
      ))}
      <button className={`bn-item ${secondary.includes(active) ? "active" : ""}`} onClick={onMenu}>
        <span className="bn-ico"><Icon name="list" size={18} /></span>
        <span>Altro</span>
      </button>
    </nav>
  );
};

/* ============== Root ============== */
const App = () => {
  const [t, setTweak] = useTweaks(TWEAK_DEFAULTS);
  const [view, setView] = useState("dashboard");
  const [selectedTripId, setSelectedTripId] = useState(TRIPS[0]?.id);
  const [drawerOpen, setDrawerOpen] = useState(false);

  // Apply tweaks to <html> as data-* attributes
  useEffect(() => {
    const r = document.documentElement;
    r.dataset.theme = t.theme;
    r.dataset.density = t.density;
    r.dataset.anim = t.anim;
    r.style.setProperty("--accent-h", ACCENT_HUES[t.accent] || 200);
  }, [t.theme, t.density, t.anim, t.accent]);

  // Close drawer on view change
  useEffect(() => { setDrawerOpen(false); }, [view]);

  const viewLabels = {
    dashboard: "Dashboard",
    trips: "Viaggi",
    map: "Mappa GPS",
    pids: "PID Explorer",
    dpf: "DPF / FAP",
    myopel: "MyOpel · Stellantis",
    trends: "Trend & AI Insights",
    admin: "Admin · Diagnostica",
  };

  // keyboard nav
  useEffect(() => {
    const fn = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        document.querySelector(".topbar .search input")?.focus();
      }
      if (e.key === "Escape") setDrawerOpen(false);
    };
    window.addEventListener("keydown", fn);
    return () => window.removeEventListener("keydown", fn);
  }, []);

  // Sidebar compact: auto = follows breakpoint (CSS); manual = override via class
  const sidebarClass = t.sidebar === "compact" ? "sidebar-compact" :
                       t.sidebar === "full" ? "" : "";
  const drawerClass = drawerOpen ? "drawer-open" : "";

  return (
    <div className={`app ${sidebarClass} ${drawerClass}`} onClick={(e) => {
      // close drawer when clicking outside sidebar
      if (drawerOpen && !e.target.closest(".sidebar") && !e.target.closest(".menu-btn")) {
        setDrawerOpen(false);
      }
    }}>
      <Sidebar active={view} setActive={setView} />
      <BottomNav active={view} setActive={setView} onMenu={() => setDrawerOpen(v => !v)} />
      <main className="main">
        <TopBar view={viewLabels[view]} onMenu={() => setDrawerOpen(v => !v)} />
        <div className="content">
          <div className="view-wrap" key={view}>
            {view === "dashboard" && <Dashboard setActive={setView} setSelectedTripId={setSelectedTripId} />}
            {view === "trips"     && <TripsView selectedId={selectedTripId} setSelectedId={setSelectedTripId} />}
            {view === "map"       && <MapView />}
            {view === "pids"      && <PidExplorer />}
            {view === "dpf"       && <DpfView />}
            {view === "myopel"    && <MyOpelView />}
            {view === "trends"    && <TrendsView />}
            {view === "admin"     && <AdminView />}
          </div>
        </div>
      </main>

      <TweaksPanel title="Tweaks">
        <TweakSection label="Aspetto" />
        <TweakRadio  label="Tema"     value={t.theme}
                     options={["midnight", "graphite", "warm", "lights-out"]}
                     onChange={v => setTweak("theme", v)} />
        <TweakColor  label="Accento"  value={ACCENT_COLORS[t.accent]}
                     options={Object.values(ACCENT_COLORS)}
                     onChange={v => {
                       const k = Object.keys(ACCENT_COLORS).find(k => ACCENT_COLORS[k] === v);
                       if (k) setTweak("accent", k);
                     }} />

        <TweakSection label="Layout" />
        <TweakRadio  label="Densità" value={t.density}
                     options={["compact", "regular", "spacious"]}
                     onChange={v => setTweak("density", v)} />
        <TweakSelect label="Sidebar"  value={t.sidebar}
                     options={["auto", "full", "compact"]}
                     onChange={v => setTweak("sidebar", v)} />

        <TweakSection label="Movimento" />
        <TweakRadio  label="Animazioni" value={t.anim}
                     options={["off", "subtle", "playful"]}
                     onChange={v => setTweak("anim", v)} />
      </TweaksPanel>
    </div>
  );
};

class ErrorBoundary extends React.Component {
  constructor(props) { super(props); this.state = { err: null }; }
  static getDerivedStateFromError(e) { return { err: e }; }
  render() {
    if (this.state.err) {
      return (
        <div style={{ padding: 32, fontFamily: "monospace", background: "#080c10", color: "#ff6b6b", minHeight: "100vh" }}>
          <div style={{ fontSize: 18, marginBottom: 12 }}>Errore JavaScript — dashboard non caricata</div>
          <pre style={{ whiteSpace: "pre-wrap", fontSize: 12, color: "#f8f8f8" }}>{String(this.state.err)}</pre>
          <pre style={{ whiteSpace: "pre-wrap", fontSize: 11, color: "#999", marginTop: 8 }}>{this.state.err?.stack}</pre>
        </div>
      );
    }
    return this.props.children;
  }
}

ReactDOM.createRoot(document.getElementById("root")).render(
  <ErrorBoundary><App /></ErrorBoundary>
);
