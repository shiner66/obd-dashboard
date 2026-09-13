/* global React, Icon, Sparkline, LineChart, RadialGauge, TripMap,
          DpfPill, AlertChip, StatCard, TripCard, InsightCard,
          VEHICLE, TRIPS, ALERTS, TREND_INSIGHTS, PID_CATALOG, PID_GROUPS,
          SETTINGS, FUEL, AnimatedBar,
          TweaksPanel, useTweaks, TweakSection, TweakRadio, TweakColor,
          TweakToggle, TweakSlider, TweakSelect */
const { useState, useMemo, useEffect, useRef } = React;

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "theme": "midnight",
  "accent": "cyan",
  "density": "regular",
  "anim": "subtle",
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

/* Shared refresh state: bootstrap and subsequent snapshots use the same globals. */
const DataRevision = React.createContext(0);
const _tripCache = {};
let _snapshotText = null;
let _refreshPending = null;
let _dataVersion = 0;
let _dashboardMeta = null;
let _dashboardAggregates = null;
let _periodQuery = null;
let _appliedPeriodQuery = null;
let _periodLabel = "";
let _requestedRange = null;
const _periodGate = OBD.requestGate();
const PeriodScope = React.createContext({ label: "", query: "", fromDate: null, toDate: null });

/** Read an API response with a finite timeout and a useful user-facing error. */
async function apiJson(url, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetch(url, { ...options, cache: "no-store", signal: controller.signal });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(OBD.apiError(error.detail, `Servizio non disponibile (HTTP ${response.status})`));
    }
    return await response.json();
  } catch (error) {
    if (error.name === "AbortError") throw new Error("Il server non ha risposto entro 15 secondi");
    throw error;
  } finally { clearTimeout(timeout); }
}

/** Select a global period before starting any refresh for that selection. */
function selectDashboardPeriod(range) {
  _periodGate.select(range.query);
  _periodQuery = range.query; _periodLabel = range.label; _requestedRange = range;
  return refreshDashboard();
}

/** Commit only the snapshot matching the latest selected period. */
function refreshDashboard(force = false) {
  if (_periodQuery === null) return Promise.resolve(null);
  const ticket = _periodGate.ticket(), query = _periodQuery, label = _periodLabel, range = _requestedRange;
  if (_refreshPending?.ticket === ticket) return force
    ? _refreshPending.promise.catch(() => {}).then(() => refreshDashboard())
    : _refreshPending.promise;
  window.dispatchEvent(new CustomEvent("dashboard-status", { detail: { loading: true, periodPending: _appliedPeriodQuery !== query } }));
  const promise = apiJson("/api/v1/dashboard" + query).then(raw => {
    if (!_periodGate.current(ticket)) return refreshDashboard();
    const data = OBD.validateSnapshot(raw), serialized = JSON.stringify(data);
    const changed = serialized !== _snapshotText || _appliedPeriodQuery !== query;
    if (changed) {
      VEHICLE = data.vehicle; TRIPS = data.trips; ALERTS = data.alerts;
      PID_CATALOG = data.pidCatalog; PID_GROUPS = data.pidGroups; TREND_INSIGHTS = data.trendInsights;
      SETTINGS = data.settings; FUEL = data.fuel; _dashboardMeta = data.meta || null;
      _dashboardAggregates = data.aggregates || null;
      Object.keys(_tripCache).forEach(key => delete _tripCache[key]);
      _snapshotText = serialized; _dataVersion += 1;
      window.DASHBOARD_BOOTSTRAP_ERROR = false;
    }
    _appliedPeriodQuery = query;
    window.dispatchEvent(new CustomEvent("dashboard-status", { detail: { loading: false, periodPending: false, error: null, checked: new Date(), changed, query, label, range } }));
    return data;
  }).catch(error => {
    if (!_periodGate.current(ticket)) return refreshDashboard();
    window.dispatchEvent(new CustomEvent("dashboard-status", { detail: { loading: false, periodPending: _appliedPeriodQuery !== query, error: error.message } }));
    throw error;
  }).finally(() => { if (_refreshPending?.promise === promise) _refreshPending = null; });
  _refreshPending = { ticket, promise };
  return promise;
}
window.OBD_API = { json: apiJson };

/** Keep list rendering bounded while preserving pages across background refreshes. */
function usePagination(items, resetKey, size = 20) {
  const [page, setPage] = useState(1);
  useEffect(() => setPage(1), [resetKey]);
  return { ...OBD.pageItems(items, page, size), setPage };
}

/** Readable page controls shared by long lists and tables. */
const Pagination = ({ page, pages, start, end, total, setPage }) => total > 0 ? (
  <nav className="pagination" aria-label="Pagine dei risultati">
    <span>{start}–{end} di {OBD.measurement(total, 0)}</span>
    <div><button className="icon-btn" disabled={page <= 1} onClick={() => setPage(page - 1)} aria-label="Pagina precedente">←</button>
      <span>{page} / {pages}</span>
      <button className="icon-btn" disabled={page >= pages} onClick={() => setPage(page + 1)} aria-label="Pagina successiva">→</button></div>
  </nav>
) : null;

/** Select one inclusive calendar period used by every analytical view and export. */
const PeriodToolbar = ({ selection, onChange, range, loading }) => (
  <section className="period-toolbar" aria-label="Periodo dei dati">
    <span className="period-title">Periodo</span>
    <div className="period-presets">
      {[["7d", "7 giorni"], ["30d", "30 giorni"], ["month", "Questo mese"], ["custom", "Personalizzato"], ["all", "Tutto"]].map(([preset, label]) =>
        <button key={preset} className={`chip ${selection.preset === preset ? "active" : ""}`} aria-pressed={selection.preset === preset}
          onClick={() => onChange(preset === "custom" ? { preset, fromDate: range.fromDate || OBD.localDateTime().slice(0, 10), toDate: range.toDate || OBD.localDateTime().slice(0, 10) } : { preset })}>{label}</button>)}
    </div>
    {selection.preset === "custom" && <div className="period-dates">
      <label>Dal <input type="date" value={selection.fromDate || ""} onChange={e => onChange({ ...selection, fromDate: e.target.value })} /></label>
      <label>Al <input type="date" value={selection.toDate || ""} onChange={e => onChange({ ...selection, toDate: e.target.value })} /></label>
    </div>}
    <span className={`period-caption ${range.error ? "error" : ""}`} role={range.error ? "alert" : undefined}>{range.error || `${range.label}${loading ? " · aggiornamento…" : ""}`}</span>
  </section>
);

/** Hydrate one trip, exposing loading/errors separately from genuinely absent data. */
function useHydratedTrip(tripId) {
  const revision = React.useContext(DataRevision);
  const summary = TRIPS.find(t => t.id === tripId);
  const [state, setState] = useState({ id: null, data: null, error: null, loading: true });
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    if (!tripId) return;
    let cancelled = false;
    if (_tripCache[tripId]) { setState({ id: tripId, data: _tripCache[tripId], loading: false, error: null }); return; }
    setState({ id: tripId, data: null, loading: true, error: null });
    apiJson(`/api/v1/trips/${encodeURIComponent(tripId)}`)
      .then(data => { if (!cancelled) { _tripCache[tripId] = data; setState({ id: tripId, data, error: null, loading: false }); } })
      .catch(error => { if (!cancelled) setState({ id: tripId, data: null, error: error.message, loading: false }); });
    return () => { cancelled = true; };
  }, [tripId, revision, retry]);
  if (!summary) return null;
  const current = state.id === tripId ? state : {};
  return { ...(current.data || summary), detailLoading: state.id !== tripId || current.loading,
    detailError: current.error, retryDetail: () => setRetry(n => n + 1) };
}

/** Explain whether detailed data is still loading or could not be read. */
const DetailStatus = ({ trip }) => trip?.detailLoading
  ? <div className="data-notice" role="status">Caricamento dei dettagli…</div>
  : trip?.detailError ? <div className="data-notice error" role="alert">{trip.detailError} <button className="icon-btn" onClick={trip.retryDetail}>Riprova</button></div> : null;

/* Sparkline for a single PID series of a not-yet-hydrated trip (DPF view). */
const LazySeries = ({ tripId, slug, ...props }) => {
  const trip = useHydratedTrip(tripId);
  return <><DetailStatus trip={trip} />{!trip?.detailLoading && !trip?.detailError && <Sparkline data={trip?.pidSeriesFull?.[slug] || []} times={trip?.pidSeriesTimes?.[slug]} {...props} />}</>;
};

/* Some signals exist under different slugs depending on the CarScanner profile
   (e.g. coolant vs coolant_c). Picks the first slug with series data. */
const pickSeries = (trip, slugs) => slugs.map(s => trip?.pidSeriesFull?.[s]).find(a => a && a.length > 1) || [];

const fmtInt = (n) => n?.toLocaleString?.("it-IT") ?? "—";

/* Where the current fuel level came from — MyOpel, the OBD sender, or the
   refuel ledger − OBD consumption estimate. */
const FUEL_SOURCE_LABEL = { myopel: "MyOpel", obd: "sonda OBD", obd_rate: "portata OBD", obd_mass: "massa OBD", ledger: "rifornimenti − consumi" };
const fuelSourceLabel = (src) => FUEL_SOURCE_LABEL[src] || null;
const DISTANCE_SOURCE_LABEL = { trip_counter: "contatore viaggio OBD", odometer: "odometro OBD", gps: "traccia GPS", myopel: "MyOpel" };
/** Read the original MyOpel quantities independently from selected OBD consumption. */
const myopFuel = trip => trip.myopFuelConsumedL ?? (!trip.sources.includes("obd") ? trip.fuelConsumedL : null);
const myopDistance = trip => trip.sources.includes("obd") ? trip.myopDistanceKm : trip.distanceKm;

/* ============== Top bar ============== */
const exportTrips = async () => {
  try {
    const r = await fetch("/api/v1/trips" + (_appliedPeriodQuery || ""));
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `obd-viaggi-${OBD.localDateTime().slice(0, 10)}.json`;
    a.click();
    URL.revokeObjectURL(url);
  } catch (e) { alert("Export fallito: " + e.message); }
};

const TopBar = ({ view, onMenu, status, onRefresh, onAppearance }) => {
  const scope = React.useContext(PeriodScope);
  return (
    <div className="topbar">
      <button className="menu-btn" onClick={onMenu} aria-label="Apri menu">
        <Icon name="list" size={18} />
      </button>
      <div>
        <div className="crumb">{VEHICLE.name} · {VEHICLE.ecu}</div>
        <h1>{view}</h1>
      </div>
      <div className="topbar-spacer" />
      <span className="topbar-meta muted">{TRIPS.length} viaggi nel periodo</span>
      <button className="icon-btn" onClick={onRefresh} disabled={status.loading} aria-label="Aggiorna i dati" title="Aggiorna i dati">
        <Icon name="trend" size={14} /><span>{status.loading ? "Aggiorno…" : "Aggiorna"}</span>
      </button>
      <button className="icon-btn" onClick={onAppearance} aria-label="Aspetto e preferenze" title="Aspetto e preferenze"><Icon name="settings" size={14} /></button>
      <button className="icon-btn" onClick={exportTrips} disabled={status.periodPending} title={`Esporta i viaggi del periodo: ${scope.label}`}>
        <Icon name="download" size={14} /><span>Esporta</span>
      </button>
    </div>
  );
};

/* ============== Sidebar ============== */
const Sidebar = ({ active, setActive }) => {
  const usefulPids = PID_CATALOG.filter(p => p.useful !== false).length;
  const myopOn = VEHICLE.myopEnabled !== false;
  const items = [
    { id: "dashboard", icon: "gauge",    label: "Dashboard" },
    { id: "trips",     icon: "list",     label: "Viaggi",       badge: TRIPS.length },
    { id: "map",       icon: "map",      label: "Mappa" },
    { id: "compare",   icon: "chart",    label: "Confronta viaggi" },
    { id: "fuel",      icon: "droplet",  label: "Carburante" },
    { id: "pids",      icon: "pid",      label: "PID Explorer", badge: usefulPids },
    { id: "dpf",       icon: "chart",    label: "DPF / FAP" },
    { id: "myopel",    icon: "fuel",     label: myopOn ? "MyOpel" : "MyOpel · off" },
    { id: "trends",    icon: "trend",    label: "Trend e controlli" },
    { id: "events",    icon: "clock",    label: "Registro eventi" },
    { id: "admin",     icon: "settings", label: "Admin" },
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
          <div className="brand-sub"><span className="live-dot"></span>v0.10 · dati registrati</div>
        </div>
      </div>

      <div className="nav-section">Workspace</div>
      {items.map(it => (
        <button type="button" key={it.id} aria-current={active === it.id ? "page" : undefined}
             className={`nav-item ${active === it.id ? "active" : ""}`}
             onClick={() => setActive(it.id)}>
          <Icon name={it.icon} size={15} className="nav-icon" />
          <span>{it.label}</span>
          {it.badge != null && <span className="nav-badge">{it.badge}</span>}
        </button>
      ))}

      <div className="nav-section">Sorgenti nel periodo</div>
      <div className="nav-item" style={{ cursor: "default" }}>
        <span className="src-tag obd">OBD</span>
        <span style={{ fontSize: 12, color: "var(--fg-2)" }}>CarScanner CSV</span>
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
        <div className="global-scope-label">Stato veicolo · ultimo dato globale</div>
        <div className="muted mono" style={{ fontSize: 11, marginBottom: 8 }}>{VEHICLE.ecu}</div>
        <div className="veh-row"><span>Odometro</span><span className="v">{VEHICLE.odometer?.toLocaleString("it-IT") ?? "—"} km</span></div>
        <div className="veh-row"><span>Carburante</span><span className="v">{VEHICLE.fuelLevel ?? "—"}% · {VEHICLE.fuelAutonomy ?? "—"} km</span></div>
        <div className="veh-row"><span>AdBlue</span><span className="v">{VEHICLE.adblueRange?.toLocaleString("it-IT") ?? "—"} km</span></div>
        <div className="veh-row"><span>Batteria</span><span className="v">{VEHICLE.battery?.toLocaleString("it-IT", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) ?? "—"} V</span></div>
        <div className="veh-row"><span>Service</span><span className="v">{VEHICLE.nextService?.days ?? "—"} g · {VEHICLE.nextService?.km?.toLocaleString("it-IT") ?? "—"} km</span></div>
      </div>
    </aside>
  );
};

/* ============== Dashboard view ============== */
/** Compact overview: selected-period totals alongside explicitly global vehicle status. */
const Dashboard = ({ setActive, setSelectedTripId }) => {
  const scope = React.useContext(PeriodScope);
  const metrics = OBD.periodMetrics(TRIPS, _dashboardAggregates);
  const fueled = TRIPS.filter(trip => OBD.usableForAnalysis(trip) && trip.fuelConsumedL > 0 && trip.fuelDistanceKm > 0);
  const activity = useMemo(() => OBD.activitySeries(TRIPS, scope), [TRIPS, scope.fromDate, scope.toDate]);
  const consumption = [...fueled].sort((a, b) => a.start.localeCompare(b.start));
  const attention = TREND_INSIGHTS.filter(insight => OBD.insightPresentation(insight).finding === "anomaly" && ["critical", "warning"].includes(insight.level))
    .sort((a, b) => (a.level === "critical" ? 0 : 1) - (b.level === "critical" ? 0 : 1));
  const recent = TRIPS.slice(0, 4);
  return (
    <div className="page dashboard-compact">
      <section className="vehicle-strip" aria-label="Ultimo stato globale del veicolo">
        <div className="vehicle-strip-title"><span className="eyebrow">Stato veicolo · ultimo dato globale</span>
          <h2>{VEHICLE.name}</h2><span className="mono">{OBD.measurement(VEHICLE.odometer, 0)} km</span></div>
        <div className="vehicle-reading"><span>Carburante</span><strong>{OBD.measurement(VEHICLE.fuelLevel, 0)} <small>%</small></strong><small>{fuelSourceLabel(VEHICLE.fuelSource) || "fonte non disponibile"}</small></div>
        <div className="vehicle-reading"><span>Carico DPF</span><strong>{OBD.measurement(VEHICLE.dpfClosedSoot, 2)} <small>g/L</small></strong><DpfPill state={VEHICLE.dpfRegenState} /></div>
        <div className="vehicle-reading"><span>Batteria all'avvio</span><strong>{OBD.measurement(VEHICLE.battery, 2)} <small>V</small></strong><small>ultima osservazione OBD</small></div>
        <div className="vehicle-reading"><span>Tagliando tra</span><strong>{OBD.measurement(VEHICLE.nextService?.km, 0)} <small>km</small></strong><small>{OBD.measurement(VEHICLE.nextService?.days, 0)} giorni</small></div>
      </section>

      <section aria-label="Riepilogo del periodo">
        <div className="section-head"><h2 className="section-title">Nel periodo</h2><span className="section-sub">{scope.label}</span></div>
        <div className="kpi-grid stat-grid compact-kpis">
          <StatCard label="Viaggi" icon="list" value={OBD.measurement(TRIPS.length, 0)} sub={`${TRIPS.filter(t => t.sources.includes("obd")).length} con OBD`} />
          <StatCard label="Distanza registrata" icon="road" value={OBD.measurement(metrics.totalKm, 1)} unit="km" sub={metrics.excludedFromComparisons ? "include lo storico non ricalcolato" : "viaggi registrati"} />
          <StatCard label="Tempo osservato" icon="clock" value={OBD.measurement(metrics.observedTripCount ? metrics.observedMinutes / 60 : null, 1)} unit="h" sub="escluse lacune e storico non ricalcolato" />
          <StatCard label="Consumo medio" icon="fuel" value={OBD.measurement(metrics.consumptionKmL, 1)} unit="km/L" sub={`${metrics.fuelTripCount}/${TRIPS.length} viaggi · ${OBD.measurement(metrics.fuelDistanceKm, 0)} km coperti`} />
          <StatCard label="Costo viaggi stimato" icon="euro" value={OBD.measurement(metrics.costTripCount ? metrics.costEur : null, 2)} unit="€" sub={`${metrics.costTripCount}/${TRIPS.length} viaggi valorizzati`} />
        </div>
        {metrics.excludedFromComparisons > 0 && <div className="data-notice">{metrics.excludedFromComparisons} viaggi storici nel periodo non sono ricalcolabili in modo attendibile: restano nel conteggio e nella distanza registrata, ma sono esclusi da tempi, consumi, costi e confronti diagnostici.</div>}
      </section>

      <div className="overview-columns">
        <section className="overview-attention">
          <div className="section-head"><h2 className="section-title">Da tenere d'occhio</h2><button className="text-action" onClick={() => setActive("trends")}>Tutti i controlli →</button></div>
          <p className="muted" style={{ fontSize: 11, lineHeight: 1.5, margin: "0 0 10px" }}>Controlli fino a 90 giorni prima dell'ultimo viaggio del periodo.</p>
          {attention.length ? <div className="attention-stack">{attention.slice(0, 3).map((insight, i) => <InsightCard key={insight.ruleId || i} insight={insight} />)}
            {attention.length > 3 && <button className="text-action" onClick={() => setActive("trends")}>Altri {attention.length - 3} avvisi</button>}</div>
            : <div className="card attention-empty"><Icon name="info" size={20} /><div>{TREND_INSIGHTS.some(insight => OBD.insightPresentation(insight).finding === "normal") ? "Nessuno scostamento nei controlli valutabili." : "Le prove disponibili non bastano per una valutazione diagnostica."}<small>I controlli dipendono dai sensori e dalla copertura dei viaggi registrati.</small></div></div>}
        </section>
        <section>
          <div className="section-head"><h2 className="section-title">Ultimi viaggi del periodo</h2><button className="text-action" onClick={() => setActive("trips")}>Vedi tutti →</button></div>
          <div className="recent-rows">{recent.map(trip => <button key={trip.id} className="recent-row" onClick={() => { setSelectedTripId(trip.id); setActive("trips"); }}>
            <span><strong>{new Date(trip.start).toLocaleDateString("it-IT", { day: "2-digit", month: "short" })}</strong><small>{new Date(trip.start).toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit" })}</small></span>
            <span>{OBD.measurement(trip.distanceKm)} <small>km</small></span><span>{OBD.measurement(trip.consumptionKmL)} <small>km/L</small></span><Icon name="chevron" size={14} />
          </button>)}{!recent.length && <div className="card muted">Nessun viaggio nel periodo selezionato.</div>}</div>
        </section>
      </div>

      <section>
        <div className="section-head"><h2 className="section-title">Andamento</h2><span className="section-sub">tutti i dati del periodo selezionato</span></div>
        <div className="overview-charts">
          <div className="card"><h3>Distanza registrata per {activity.unit}</h3><BarChart data={activity.data} color="var(--accent)" height={145} yLabel="km" /></div>
          <div className="card"><h3>Consumo per viaggio</h3><LineChart title="Resa per viaggio" data={consumption.map(trip => trip.consumptionKmL)}
            labels={consumption.map(trip => new Date(trip.start).toLocaleString("it-IT", { dateStyle: "short", timeStyle: "short" }))} color="var(--ok)" height={145} yLabel="km/L" /></div>
        </div>
      </section>
    </div>
  );
};

/* ============== Trips view (list + detail) ============== */
const TripsView = ({ selectedId, setSelectedId, evidence }) => {
  const scope = React.useContext(PeriodScope);
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
  }, [filter, search, TRIPS]);

  const page = usePagination(filtered, scope.query + filter + search, 20);
  const trip = OBD.selectedTrip(filtered, selectedId);
  useEffect(() => { const index = filtered.findIndex(t => t.id === selectedId); if (index >= 0) page.setPage(Math.floor(index / 20) + 1); }, [selectedId]);
  useEffect(() => { if (trip?.id !== selectedId) setSelectedId(trip?.id || null); }, [trip?.id, selectedId]);

  // On phones the list sits above the detail: bring the detail into view on tap.
  const selectTrip = (id) => {
    setSelectedId(id);
    if (window.matchMedia("(max-width: 760px)").matches) {
      setTimeout(() => document.querySelector(".page-grid .detail")
        ?.scrollIntoView({ behavior: "smooth", block: "start" }), 80);
    }
  };

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
        <div className="trip-scroller stagger">
          {page.items.map(t => (
            <TripCard key={t.id} trip={t} active={trip && t.id === trip.id} onClick={() => selectTrip(t.id)} />
          ))}
        </div>
        <Pagination {...page} setPage={number => { page.setPage(number); setSelectedId(filtered[(number - 1) * 20]?.id || null); }} />
        {filtered.length === 0 && (
          <div className="muted" style={{ padding: 20, textAlign: "center" }}>Nessun viaggio corrisponde ai filtri.</div>
        )}
      </div>

      {trip ? <TripDetail trip={trip} evidence={evidence?.tripId === trip.id ? evidence : null} /> : <div className="empty-state">Seleziona un viaggio</div>}
    </div>
  );
};

/* ============== Trip detail panel ============== */
const TripDetail = ({ trip: summaryTrip, evidence }) => {
  const [requestedTab, setTab] = useState("overview");
  const trip = useHydratedTrip(summaryTrip.id);
  const tab = OBD.tripTab(requestedTab, trip);
  useEffect(() => setTab(current => OBD.tripTab(current, trip)), [trip.id, trip.sources.join(",")]);
  useEffect(() => { if (evidence?.tripId === trip.id) setTab(evidence.pidSlug ? "analysis" : "overview"); }, [evidence]);
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
            {trip.filename && <span>📄 {trip.filename.replace(/\.gz$/i, "")}</span>}
            {trip.myopId && (
              <span>MyOpel #{trip.myopId}
                {trip.myopLegIds && trip.myopLegIds.length > 1 && ` +${trip.myopLegIds.length - 1} tratte`}
              </span>
            )}
            {trip.dpfRegenState && <DpfPill state={trip.dpfRegenState} />}
            {trip.alerts && trip.alerts.map(c => <AlertChip key={c} code={c} />)}
          </div>
        </div>
      </div>

      <div className="tabs">
        {[
          ["overview", "Panoramica"],
          ...(isObd ? [["analysis", "Analisi segnali"], ["dpf", "DPF / FAP"], ["pids", "Catalogo PID"]] : []),
          ["insights", `Osservazioni${trip.insights?.length ? " · " + trip.insights.length : ""}`],
        ].map(([id, lbl]) => (
          <button type="button" key={id} className={`tab ${tab === id ? "active" : ""}`} aria-pressed={tab === id} onClick={() => setTab(id)}>{lbl}</button>
        ))}
      </div>

      <DetailStatus trip={trip} />
      {[...(trip.qualityWarnings || []), ...(trip.dpfQualityWarnings || [])].length > 0 && <div className="data-notice">Qualità dati: {[...(trip.qualityWarnings || []), ...(trip.dpfQualityWarnings || [])].join(" · ")}</div>}
      {tab === "overview" && <TripOverview trip={trip} />}
      {tab === "analysis" && <TripExploration trip={trip} initialPid={evidence?.pidSlug} />}
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
          {trip.track?.length >= 2 ? (
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
          ) : trip.hasTrack && trip.detailLoading ? (
            <div className="empty-state" style={{height: 340}}>
              <Icon name="map" size={36} className="icon" />
              <div>Carico tracciato GPS…</div>
            </div>
          ) : (
            <div className="empty-state" style={{height: 340}}>
              <Icon name="map" size={36} className="icon" />
              <div>{trip.detailError ? "Tracciato non caricato" : "Nessun tracciato GPS disponibile"}</div>
              {!isObd && <div className="muted" style={{fontSize: 12}}>MyOpel non fornisce il percorso GPS</div>}
            </div>
          )}
        </div>

        <div className="stat-grid">
          <StatCard label="Distanza"   value={trip.distanceKm?.toLocaleString("it-IT", { minimumFractionDigits: 1, maximumFractionDigits: 1 }) ?? "—"} unit="km" sub={trip.distanceSource ? `Fonte: ${DISTANCE_SOURCE_LABEL[trip.distanceSource] || trip.distanceSource}` : "Fonte non documentata"} />
          <StatCard label="Tempo osservato" value={trip.durationMin?.toLocaleString("it-IT", { minimumFractionDigits: 1, maximumFractionDigits: 1 }) ?? "—"} unit="min" sub={trip.recordingGapSeconds > 0 ? `${OBD.measurement(trip.recordingGapSeconds / 60)} min senza campioni · intervallo ${OBD.measurement(trip.elapsedDurationMin)} min` : "durata coperta dai dati"} />
          <StatCard label="Velocità media osservata" value={trip.avgSpeedKmh?.toLocaleString("it-IT", { minimumFractionDigits: 0, maximumFractionDigits: 0 }) ?? "—"} unit="km/h" />
          <StatCard label="Vel. max"   value={trip.maxSpeedKmh?.toLocaleString("it-IT", { minimumFractionDigits: 0, maximumFractionDigits: 0 }) ?? "—"} unit="km/h" />
          <StatCard label="Consumo"    value={trip.consumptionKmL?.toLocaleString("it-IT", { minimumFractionDigits: 1, maximumFractionDigits: 1 }) ?? "—"} unit="km/L"
                    sub={trip.consumptionL100km ? `${trip.consumptionL100km.toLocaleString("it-IT", { minimumFractionDigits: 1, maximumFractionDigits: 1 })} L/100 km` : undefined} />
          <StatCard label="Carburante" value={trip.fuelConsumedL?.toLocaleString("it-IT", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) ?? "—"} unit="L" sub={`${fuelSourceLabel(trip.fuelSource) || "Fonte non disponibile"} · copertura ${trip.fuelCoveragePct != null ? OBD.measurement(trip.fuelCoveragePct) + "%" : "non disponibile"}${trip.fuelDensityGL != null ? " · conversione " + trip.fuelDensityGL + " g/L" : ""}`} />
        </div>
      </div>

      {isObd && (
        <div>
          <div className="section-head">
            <span className="section-title">Segnali chiave</span>
            <span className="section-sub">andamento durante il viaggio</span>
          </div>
          <div className="spark-grid stagger">
            {[
              { slugs: ["rpm"],                          name: "Giri motore",   unit: "rpm",  color: "var(--accent)", agg: "mean" },
              { slugs: ["speed", "speed_v"],             name: "Velocità",      unit: "km/h", color: "var(--accent)", agg: "mean" },
              { slugs: ["coolant", "coolant_c"],         name: "Liquido raffr.",unit: "°C",   color: "var(--warn)",   agg: "max" },
              { slugs: ["egt_a"],                        name: "Temperatura scarico",  unit: "°C",   color: "var(--crit)",   agg: "max" },
              { slugs: ["soot_cl", "closed_soot", "soot"], name: "Soot DPF",    unit: "g/L",  color: "var(--warn)",   agg: "last" },
              { slugs: ["boost"],                        name: "Pressione turbo", unit: "bar", color: "var(--info)",  agg: "max" },
            ].map(p => {
              const slug = p.slugs.find(s => trip?.pidValues?.[s]);
              if (!slug) return null;
              const stats = trip.pidValues[slug];
              const series = pickSeries(trip, p.slugs);
              const shown = p.agg === "max" ? stats.max : p.agg === "mean" ? stats.mean : stats.last;
              const unit = (PID_CATALOG.find(c => c.slug === slug)?.unit || p.unit).replace(/_/g, "/");
              const f = v => typeof v === "number" ? OBD.measurement(v, 1) : v ?? "—";
              return (
                <div className="spark-tile" key={p.name}>
                  <div className="head">
                    <span className="name">{p.name}</span>
                    <span className="val">{f(shown)}<span style={{ color: "var(--fg-3)", fontSize: 11, marginLeft: 2 }}>{unit}</span></span>
                  </div>
                  <Sparkline data={series} times={trip.pidSeriesTimes?.[slug]} color={p.color} />
                  <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: "var(--fg-3)", fontFamily: "var(--font-mono)" }}>
                    <span>min {f(stats.min)}</span>
                    <span>avg {f(stats.mean)}</span>
                    <span>max {f(stats.max)}</span>
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
            <StatCard label="Carburante MyOpel" value={OBD.measurement(myopFuel(trip), 3)} unit="L" sub="solo tratte TCU registrate" />
            <StatCard label="Distanza MyOpel" value={OBD.measurement(myopDistance(trip))} unit="km" />
            <StatCard label="Odometro fine" value={trip.odometerKm?.toLocaleString("it-IT") ?? "—"} unit="km" />
            {trip.fuelLevel != null && <StatCard label="Serbatoio a fine" value={trip.fuelLevel} unit="%" />}
            {trip.fuelAutonomy != null && <StatCard label="Autonomia" value={trip.fuelAutonomy} unit="km" />}
            {trip.costEur && <StatCard label="Costo stimato" value={`€${trip.costEur.toLocaleString("it-IT", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`} sub={`@ €${trip.priceFuel}/L`} />}
            {trip.priceFuel && <StatCard label="Prezzo carburante" value={`€${trip.priceFuel}`} unit="/L" />}
          </div>
          {trip.sources.includes("obd") && trip.myopDistanceKm != null && trip.distanceKm > 0 &&
           (trip.myopDistanceKm / trip.distanceKm) < 0.6 && (
            <div className="insight info" style={{ marginTop: 10 }}>
              <div className="insight-ico"><Icon name="info" size={17} /></div>
              <div className="insight-body">
                <div className="insight-title">Copertura Stellantis parziale</div>
                <div className="insight-text">
                  MyOpel ha registrato {trip.myopDistanceKm.toLocaleString("it-IT", { minimumFractionDigits: 1, maximumFractionDigits: 1 })} km dei {trip.distanceKm.toLocaleString("it-IT", { minimumFractionDigits: 1, maximumFractionDigits: 1 })} km
                  della sessione OBD: i valori MyOpel si riferiscono alle sole tratte registrate. Il consumo principale indica la propria fonte e copertura.
                </div>
              </div>
            </div>
          )}
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
        <RadialGauge value={trip.dpfClosedSoot} max={10} label="g/L" strokeColor="var(--warn)" decimals={1} />
        <div className="dpf-meta">
          <div><div className="lbl">Evento nel viaggio</div><div className="v"><DpfPill state={trip.dpfRegenState} /></div></div>
          <div><div className="lbl">Ultimo campione ECU</div><div className="v">{trip.dpfEndObservedActive === true ? "Rigenerazione attiva" : trip.dpfEndObservedActive === false ? "Rigenerazione non attiva" : "Non determinabile"}</div></div>
          <div><div className="lbl">Carico DPF</div><div className="v">{trip.dpfClosedSoot != null ? trip.dpfClosedSoot + " g/L" : "—"}</div></div>
          <div><div className="lbl">Km dall'ultima regen</div><div className="v">{trip.dpfSinceRegenKm} <span className="muted">/ {trip.dpfAvgRegenKm} avg</span></div></div>
          <div><div className="lbl">Temperatura scarico massima</div><div className="v">{trip.exhaustAfterCatC} <span className="muted">°C</span></div></div>
          <div><div className="lbl">Temperatura NOx massima</div><div className="v">{trip.noxCatTempMaxC} <span className="muted">°C</span></div></div>
          <div><div className="lbl">Vita residua DPF</div><div className="v">{trip.dpfReplaceKm != null ? (trip.dpfReplaceKm / 1000).toLocaleString("it-IT", { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + "k" : "—"} <span className="muted">km</span></div></div>
          <div><div className="lbl">Diluizione olio</div><div className="v">{trip.oilDilutionPct != null ? trip.oilDilutionPct + " %" : "—"}</div></div>
        </div>
      </div>

      <div>
        <div className="section-head">
          <span className="section-title">Fasi della rigenerazione</span>
          <span className="section-sub">stato dedotto dai segnali disponibili nel viaggio</span>
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
          <span className="section-title">Temperatura scaricoalizzatore</span>
          <span className="section-sub">temperatura indicativa di rigenerazione: oltre 550 °C</span>
        </div>
        <div style={{ background: "var(--bg-1)", border: "1px solid var(--line-soft)", borderRadius: "var(--r)", padding: 12 }}>
          <LineChart data={trip.pidSeriesFull?.egt_a || []} times={trip.pidSeriesTimes?.egt_a} color="var(--crit)" yLabel="°C" />
        </div>
      </div>

      <div>
        <div className="section-head">
          <span className="section-title">Andamento soot (closed loop)</span>
          <span className="section-sub">g/L — campioni DPF durante il viaggio</span>
        </div>
        <div style={{ background: "var(--bg-1)", border: "1px solid var(--line-soft)", borderRadius: "var(--r)", padding: 12 }}>
          <LineChart data={pickSeries(trip, ["soot_cl", "closed_soot", "soot"])} times={trip.pidSeriesTimes?.[["soot_cl", "closed_soot", "soot"].find(s => trip.pidSeriesFull?.[s]?.length > 1)]} color="var(--warn)" yLabel="g/L" />
        </div>
      </div>
    </>
  );
};

const TripPids = ({ trip }) => {
  // Use the global catalog; PidExplorerInner filters to slugs this trip has data for.
  return <PidExplorerInner trip={trip} catalog={PID_CATALOG} />;
};

const TripInsights = ({ trip }) => {
  const ins = trip.insights || [];
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {ins.length === 0 && <div className="muted">Nessun insight per questo viaggio.</div>}
      {ins.map((i, idx) => <InsightCard key={idx} insight={{ ...i, evidence: i.evidence || { tripIds: [trip.id], pidSlugs: [] } }} />)}
    </div>
  );
};

/* ============== PID Explorer (full catalog) ============== */
const PidExplorer = () => {
  const obdTrips = TRIPS.filter(t => t.sources.includes("obd"));
  const [tripId, setTripId] = useState(obdTrips[0]?.id);
  const summary = obdTrips.find(t => t.id === tripId) || obdTrips[0];
  const trip = useHydratedTrip(tripId || obdTrips[0]?.id);
  const withData = trip?.pidValues ? Object.keys(trip.pidValues).length : (summary?.pidCount ?? 0);
  return (
    <div className="page" style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div className="row" style={{ gap: 8 }}>
        <span className="muted">Sessione OBD:</span>
        <select className="icon-btn" style={{ background: "var(--bg-2)" }}
                value={tripId} onChange={e => setTripId(e.target.value)}>
          {obdTrips.map(t => <option key={t.id} value={t.id}>{t.start.replace("T", " ")} · {t.durationMin}m · {t.distanceKm}km</option>)}
        </select>
        <span className="muted" style={{ marginLeft: "auto" }}>
          {PID_CATALOG.length} PID monitorati · {withData} con dati
        </span>
      </div>
      <DetailStatus trip={trip} />
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

  const selectedSlug = filtered.find(p => p.slug === selected)?.slug || filtered[0]?.slug;
  const selPid = catalog.find(p => p.slug === selectedSlug);
  const selStats = trip?.pidValues?.[selectedSlug];
  const selSeries = trip?.pidSeriesFull?.[selectedSlug];

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
                {({Tutti:"Tutti i tipi",number:"Numerici",discrete:"Stati",bool:"Sì / no"})[k]}
              </button>
            ))}
            <span style={{ flex: 1 }} />
            <select className="icon-btn" style={{ background: "var(--bg-2)" }} value={sortBy} onChange={e => setSortBy(e.target.value)}>
              <option value="name">Ordina: nome</option>
              <option value="group">Ordina: gruppo</option>
              <option value="samples">Ordina: campioni</option>
              <option value="rate">Ordina: frequenza</option>
            </select>
          </div>
        </div>

        <div className="pid-scroll">
          <table className="data-table pid-table">
            <thead>
              <tr>
                <th>PID</th>
                <th>Gruppo</th>
                <th className="num">Ultimo</th>
                <th className="num">Min/Max</th>
                <th>Andamento</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map(p => {
                const s = trip.pidValues[p.slug];
                const series = trip.pidSeriesFull?.[p.slug];
                const isSel = p.slug === selectedSlug;
                return (
                  <tr key={p.slug} onClick={() => setSelected(p.slug)}
                      className={isSel ? "selected" : ""}>
                    <td style={{ color: isSel ? "var(--accent-strong)" : "var(--fg-0)" }}>
                      <button type="button" className="pid-select" aria-pressed={isSel} onClick={() => setSelected(p.slug)}>{p.name.replace(/^\[(ECM|TCU)\]\s*/i, "")}</button>
                      <div className="muted mono" style={{ fontSize: 10 }}>{p.slug}</div>
                    </td>
                    <td style={{ color: "var(--fg-2)" }}>{p.group}</td>
                    <td className="num mono" style={{ color: "var(--fg-0)" }}>
                      {OBD.measurement(s.last, 2)}
                      <span style={{ color: "var(--fg-3)", marginLeft: 3, fontSize: 10 }}>{p.unit}</span>
                    </td>
                    <td className="num mono" style={{ color: "var(--fg-2)", fontSize: 11 }}>
                      {OBD.measurement(s.min, 2)} / {OBD.measurement(s.max, 2)}
                    </td>
                    <td style={{ width: 100 }}>
                      <Sparkline data={series} height={20} color={isSel ? "var(--accent-strong)" : "var(--fg-3)"} showFill={false} animate={false} />
                    </td>
                  </tr>
                );
              })}
              {filtered.length === 0 && (
                <tr><td colSpan="5" style={{ padding: 20, textAlign: "center", color: "var(--fg-3)" }}>
                  {trip?.detailLoading ? "Caricamento PID…" : trip?.detailError ? "PID non caricati: riprova usando il pulsante sopra." : !trip ? "Nessuna sessione OBD disponibile." : "Nessun PID corrisponde ai filtri."}
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
            <div className="muted mono" style={{ fontSize: 11, marginTop: 2 }}>{selPid.slug}{selPid.unit ? ` · ${selPid.unit}` : ""}</div>

            <div style={{ margin: "16px 0" }}>
              <LineChart data={selSeries} times={trip?.pidSeriesTimes?.[selectedSlug]} height={140} color="var(--accent)" yLabel={selPid.unit} />
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, fontSize: 12 }}>
              {[
                ["Ultimo", selStats.last],
                ["Primo", selStats.first],
                ["Minimo", selStats.min],
                ["Massimo", selStats.max],
                ["Media", selStats.mean],
                ["Valore frequente", selStats.mode],
              ].map(([k, v]) => (
                <div key={k} style={{ display: "flex", justifyContent: "space-between", padding: "4px 0", borderBottom: "1px solid var(--line-soft)" }}>
                  <span className="muted">{k}</span>
                  <span className="mono" style={{ color: "var(--fg-0)" }}>{OBD.measurement(v, 2)}<span className="muted" style={{ marginLeft: 3 }}>{selPid.unit}</span></span>
                </div>
              ))}
            </div>

            <div style={{ marginTop: 12, fontSize: 11, color: "var(--fg-3)", display: "flex", flexDirection: "column", gap: 3 }}>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span>Campioni</span><span className="mono" style={{ color: "var(--fg-1)" }}>{selStats.samples}</span>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span>Frequenza</span><span className="mono" style={{ color: "var(--fg-1)" }}>{OBD.measurement(selStats.sample_rate_hz, 3)} Hz</span>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span>Copertura</span><span className="mono" style={{ color: "var(--fg-1)" }}>{OBD.measurement(selStats.coverage_pct)} %</span>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span>Primo / ultimo campione</span><span className="mono" style={{ color: "var(--fg-1)" }}>{selStats.first_seen_s}s / {selStats.last_seen_s}s</span>
              </div>
              {selStats.is_stale && (
                <div style={{ marginTop: 4, color: "var(--warn)" }}>⚠ Ultimo campione distante oltre 60 s dalla fine</div>
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
  const scope = React.useContext(PeriodScope);
  const revision = React.useContext(DataRevision);
  const trackTrips = useMemo(() => TRIPS.filter(t => t.hasTrack), [TRIPS]);
  const [selected, setSelected] = useState(trackTrips[0]?.id);
  const [tracks, setTracks] = useState(null);   // {tripId: [[lat,lon],...]}
  const [error, setError] = useState(null);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let cancelled = false;
    setError(null); setTracks(null);
    apiJson("/api/v1/tracks" + scope.query).then(data => { if (!cancelled) setTracks(data); })
      .catch(error => { if (!cancelled) setError(error.message); });
    return () => { cancelled = true; };
  }, [revision, retry]);
  // Merge fetched tracks into trip summaries
  const obdTrips = useMemo(
    () => trackTrips.map(t => ({ ...t, track: tracks?.[t.id] })).filter(t => t.track),
    [trackTrips, tracks]
  );
  const trip = OBD.selectedTrip(obdTrips, selected);
  const page = usePagination(obdTrips.length ? obdTrips : trackTrips, scope.query, 20);
  return (
    <div className="page-grid">
      <div className="trip-list">
        <div className="section-head" style={{ padding: "8px 4px" }}>
          <span className="section-title">Tracciati GPS</span>
          <span className="section-sub">{trackTrips.length} con GPS{tracks === null && !error ? " · carico…" : ""}</span>
        </div>
        <div className="trip-scroller">
          {page.items.map(t => (
            <TripCard key={t.id} trip={t} active={t.id === selected} onClick={() => setSelected(t.id)} />
          ))}
        </div>
        <Pagination {...page} setPage={number => { page.setPage(number); setSelected((obdTrips.length ? obdTrips : trackTrips)[(number - 1) * 20]?.id); }} />
      </div>
      <div className="detail">
        <div className="map-wrap map-fullpage">
          {error ? <div className="empty-state" role="alert">{error}<button className="icon-btn" onClick={() => setRetry(n => n + 1)}>Riprova</button></div> : trackTrips.length === 0 ? <div className="empty-state">Nessun viaggio con tracciato GPS</div> : tracks === null
            ? <div className="empty-state"><Icon name="map" size={40} className="icon" /><div>Carico tracciati GPS…</div></div>
            : <TripMap trip={trip} allTrips={obdTrips} height={"100%"} />}
          <div className="map-overlay">
            <div className="lbl">Viaggio selezionato</div>
            <div className="v">
              {trip ? new Date(trip.start).toLocaleString("it-IT", { dateStyle: "short", timeStyle: "short" }) : "—"}
            </div>
            <div className="v" style={{ color: "var(--fg-3)" }}>{trip?.distanceKm ?? "—"} km · {trip?.track?.length ?? 0} punti</div>
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
  const scope = React.useContext(PeriodScope);
  const allObd = TRIPS.filter(t => t.sources.includes("obd"));
  const obdTrips = allObd.filter(OBD.usableForAnalysis).sort((a, b) => (a.start || "").localeCompare(b.start || ""));
  const sootSeries = obdTrips.map(t => t.dpfClosedSoot);
  const egtSeries = obdTrips.map(t => t.exhaustAfterCatC);
  const egtMax = Math.max(...egtSeries.filter(v => v != null && isFinite(v)), 0);
  const regenTrips = [...obdTrips].reverse().filter(t => ["requested", "active", "completed", "post_regen"].includes(t.dpfRegenState));
  const page = usePagination(regenTrips, scope.query, 12);

  return (
    <div className="page" style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      {allObd.length > obdTrips.length && <div className="data-notice">{allObd.length - obdTrips.length} viaggi storici non ricalcolabili sono esclusi dagli andamenti DPF. Le registrazioni restano consultabili in Viaggi.</div>}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 16 }}>
        <div className="dpf-block">
          <RadialGauge value={VEHICLE.dpfClosedSoot} max={10} label="g/L" strokeColor="var(--warn)" decimals={1} />
          <div className="dpf-meta">
            <div><div className="lbl">Ultimo stato globale</div><div className="v"><DpfPill state={VEHICLE.dpfRegenState} /></div></div>
            <div><div className="lbl">Carico DPF</div><div className="v">{OBD.measurement(VEHICLE.dpfClosedSoot, 2)} g/L</div></div>
            <div><div className="lbl">Km da regen</div><div className="v">{VEHICLE.dpfSinceRegenKm}</div></div>
            <div><div className="lbl">Intervallo medio</div><div className="v">{VEHICLE.dpfAvgRegenKm} km</div></div>
          </div>
        </div>

        <div className="trend-card">
          <div className="section-head"><span className="section-title">Andamento del carico DPF</span><span className="section-sub">g/L · ultimi {obdTrips.length} viaggi OBD</span></div>
          <div className="big-num">{OBD.measurement(sootSeries.filter(v => v != null).slice(-1)[0], 2)}<span className="unit"> g/L</span></div>
          <Sparkline data={sootSeries} color="var(--warn)" height={70} />
          <div className="muted mono" style={{ fontSize: 11 }}>carico stimato · nessuna soglia universale</div>
        </div>

        <div className="trend-card">
          <div className="section-head"><span className="section-title">Temperatura scarico</span><span className="section-sub">picchi per viaggio</span></div>
          <div className="big-num">{egtMax > 0 ? egtMax.toLocaleString("it-IT", { minimumFractionDigits: 0, maximumFractionDigits: 0 }) : "—"}<span className="unit">°C</span></div>
          <Sparkline data={egtSeries} color="var(--crit)" height={70} />
          <div className="muted mono" style={{ fontSize: 11 }}>soglia regen attiva: 550 °C</div>
        </div>
      </div>

      <div>
        <div className="section-head">
          <span className="section-title">Storia rigenerazioni</span>
          <span className="section-sub">richieste ed eventi osservati nei viaggi</span>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {page.items.map(t => (
            <div key={t.id} className="trip-card" style={{ cursor: "default" }}>
              <div className="trip-card-head">
                <span className="trip-date">{new Date(t.start).toLocaleDateString("it-IT", { day: "2-digit", month: "short" })}</span>
                <span className="trip-time">{new Date(t.start).toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit" })}</span>
                <DpfPill state={t.dpfRegenState} />
              </div>
              <div className="trip-card-stats">
                <div className="trip-stat"><span className="lbl">Carico DPF</span>
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
                  <span className="lbl">Andamento temperatura scarico</span>
                  <LazySeries tripId={t.id} slug="egt_a" color="var(--crit)" height={30} />
                </div>
              </div>
            </div>
          ))}
        </div>
        <Pagination {...page} />
        {!regenTrips.length && <div className="muted">Nessuna rigenerazione osservata nel periodo selezionato.</div>}
      </div>
    </div>
  );
};

/* ============== Carburante (Fuel) view ============== */
/* Refuel ledger + fuel-level-by-subtraction — works with the MyOpel feed off.
   The level is reconstructed from the last full-tank refuel minus OBD-measured
   consumption ("soluzione estrema"); tank-to-tank economy is pump litres over
   odometer km, refuel-to-refuel only (briefing §1). */
const FUEL_TYPES = ["B7", "HVO"];

/** Create or edit a refuel with local timestamps and actionable inline errors. */
const RefuelForm = ({ onSaved, editing, onCancel }) => {
  const [form, setForm] = useState(() => OBD.refuelDraft(editing, VEHICLE.odometer));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  useEffect(() => { setForm(OBD.refuelDraft(editing, VEHICLE.odometer)); setError(null); }, [editing?.id]);
  const set = (key, value) => setForm(previous => ({ ...previous, [key]: value }));
  const submit = async event => {
    event.preventDefault(); setError(null);
    let body;
    try { body = OBD.refuelPayload(form); } catch (error) { setError(error.message); return; }
    setBusy(true);
    try {
      await apiJson(editing ? `/api/v1/refuels/${encodeURIComponent(editing.id)}` : "/api/v1/refuels", {
        method: editing ? "PUT" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      if (!editing) setForm(OBD.refuelDraft(null, body.odometerKm));
      await onSaved(editing ? "Rifornimento aggiornato." : "Rifornimento registrato.");
    } catch (error) { setError(error.message); }
    finally { setBusy(false); }
  };
  return (
    <form className="card fuel-form" id="refuel-form" onSubmit={submit} noValidate aria-label={editing ? "Modifica rifornimento" : "Registra rifornimento"}>
      <div className="section-head"><h2 className="section-title">{editing ? "Modifica rifornimento" : "Registra rifornimento"}</h2>
        <span className="section-sub">data e ora locali</span></div>
      {error && <div className="data-notice error" role="alert" style={{ marginBottom: 12 }}>{error}</div>}
      <div className="form-grid">
        <label className="form-field"><span>Data / ora *</span><input type="datetime-local" required value={form.ts} onChange={e => set("ts", e.target.value)} /></label>
        <label className="form-field"><span>Odometro (km)</span><input type="number" min="0" max="2000000" step="0.1" inputMode="decimal" value={form.odometerKm} onChange={e => set("odometerKm", e.target.value)} placeholder="es. 21500" /></label>
        <label className="form-field"><span>Litri erogati *</span><input type="number" min="0.01" max="200" step="0.01" inputMode="decimal" required value={form.liters} onChange={e => set("liters", e.target.value)} placeholder="es. 32,14" /></label>
        <label className="form-field"><span>Prezzo (€/L)</span><input type="number" min="0" max="100" step="0.001" inputMode="decimal" value={form.pricePerL} onChange={e => set("pricePerL", e.target.value)} placeholder="es. 1,899" /></label>
        <label className="form-field"><span>Carburante</span><select value={form.fuelType} onChange={e => set("fuelType", e.target.value)}>{FUEL_TYPES.map(type => <option key={type} value={type}>{type}</option>)}</select></label>
        <label className="form-field"><span>Note</span><input value={form.note} onChange={e => set("note", e.target.value)} placeholder="opzionale" /></label>
      </div>
      <div className="row" style={{ gap: 14, marginTop: 14, flexWrap: "wrap" }}>
        <label className="check-inline"><input type="checkbox" checked={form.fullTank} onChange={e => set("fullTank", e.target.checked)} /><span>Pieno completo <small className="muted">(per il calcolo pieno-pieno)</small></span></label>
        <span style={{ flex: 1 }} />
        {editing && <button type="button" className="icon-btn" disabled={busy} onClick={onCancel}>Annulla modifica</button>}
        <button className="btn-primary" type="submit" disabled={busy}>{busy ? "Salvataggio…" : editing ? "Salva modifiche" : "Aggiungi rifornimento"}</button>
      </div>
    </form>
  );
};

const FuelView = () => {
  const scope = React.useContext(PeriodScope);
  const [editing, setEditing] = useState(null);
  const [notice, setNotice] = useState(null);
  const [deleting, setDeleting] = useState(null);
  const revision = React.useContext(DataRevision);
  const [data, setData] = useState(window.DASHBOARD_BOOTSTRAP_ERROR ? null : FUEL);
  const [dataError, setDataError] = useState(null);
  const [loading, setLoading] = useState(false);
  const load = async () => {
    setLoading(true); setDataError(null);
    try { const snapshot = await refreshDashboard(true); setData(snapshot.fuel); }
    catch (error) { setDataError(error.message); }
    finally { setLoading(false); }
  };
  useEffect(() => { if (!window.DASHBOARD_BOOTSTRAP_ERROR) setData(FUEL); }, [revision]);

  const level = data?.level || {};
  const refuels = data?.refuels || [];
  const t2t = data?.tankToTank || [];
  const suspects = data?.fcSuspects || [];
  const cap = data?.capacityL || 43.5;
  const pct = level.pct;
  const since = level.sinceRefuel;

  const del = async (id) => {
    setDeleting(id);
    try {
      const response = await fetch(`/api/v1/refuels/${id}`, { method: "DELETE" });
      if (!response.ok) throw new Error(`Eliminazione non riuscita (HTTP ${response.status})`);
      setNotice("Rifornimento eliminato."); setDeleting(null);
      await load();
    } catch (error) { setDataError(error.message); setDeleting(null); }
  };

  const avgKmL = OBD.weightedEconomy(t2t);
  const refuelPage = usePagination([...refuels].reverse(), scope.query, 20);
  const t2tPage = usePagination(t2t, scope.query, 20);
  const edit = refuel => { setEditing(refuel); setNotice(null); requestAnimationFrame(() => document.getElementById("refuel-form")?.scrollIntoView({ behavior: "smooth", block: "start" })); };
  const saved = async message => { setEditing(null); setNotice(message); await load(); };

  return (
    <div className="page" style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      {notice && <div className="data-notice" role="status">{notice}</div>}
      {loading && <div className="data-notice" role="status">Aggiornamento rifornimenti…</div>}
      {dataError && <div className="data-notice error" role="alert">{dataError} <button className="icon-btn" onClick={load}>Riprova</button></div>}
      {level.qualityWarnings?.length > 0 && <div className="data-notice">{level.qualityWarnings.join(" · ")}</div>}
      {!data && !dataError && <div className="data-notice">Dati carburante non disponibili <button className="icon-btn" onClick={load}>Riprova</button></div>}
      {/* ── Current level with explicit source and availability ── */}
      <div style={{ display: "grid", gridTemplateColumns: "260px 1fr", gap: 16, alignItems: "stretch" }}
           className="fuel-hero">
        <div className="card" style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 8, justifyContent: "center" }}>
          <RadialGauge value={pct} max={100} label="%" thresholds={false} />
          <div className="hg-lbl">Stima da registro / OBD · globale</div>
          <div className="hg-sub mono">
            {level.liters != null ? `${OBD.measurement(level.liters, 2)} / ${OBD.measurement(cap, 1)} L` : "—"}
          </div>
          <div className="src-chip">
            {level.source === "ledger" ? "registro − consumi OBD" :
             level.source === "obd" ? "sonda OBD" :
             level.source === "myopel" ? "MyOpel" : "stima registro / OBD non disponibile"}
          </div>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div className="stat-grid" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
            <StatCard icon="droplet" label="Litri stimati nel serbatoio"
                      value={OBD.measurement(level.liters, 2)} unit="L"
                      sub={`capacità utile ${OBD.measurement(cap, 1)} L`} />
            <StatCard icon="road" label="Dall'ultimo pieno"
                      value={since?.km != null ? since.km.toLocaleString("it-IT", { minimumFractionDigits: 0, maximumFractionDigits: 0 }) : "—"} unit="km"
                      sub={since?.litersBurned != null ? `${OBD.measurement(since.litersBurned, 2)} L consumati (OBD)` : "—"} />
            <StatCard icon="fuel" label="Consumo dall'ultimo pieno"
                      value={since?.kmL != null ? since.kmL.toLocaleString("it-IT", { minimumFractionDigits: 1, maximumFractionDigits: 1 }) : "—"} unit="km/L"
                      sub={since?.l100 != null ? `≈ ${since.l100.toLocaleString("it-IT", { minimumFractionDigits: 1, maximumFractionDigits: 1 })} L/100 km` : undefined} />
          </div>
          {VEHICLE.fuelLevel != null && <div className="data-notice">Ultimo livello registrato globale: <b>{OBD.measurement(VEHICLE.fuelLevel, 0)} %</b> · {fuelSourceLabel(VEHICLE.fuelSource) || "fonte non disponibile"}. Questo dato è distinto dalla stima del registro rifornimenti.</div>}
          {level.source !== "myopel" && level.obdPct != null && (
            <div className="muted" style={{ fontSize: 12 }}>
              Sonda OBD (<span className="mono">[ECM] Fuel tank level</span>): <b>{OBD.measurement(level.obdPct, 1)} %</b>
              {level.source === "ledger" && pct != null && ` · stima rifornimenti: ${OBD.measurement(pct, 1)} % · confronto con la sonda.`}
            </div>
          )}
          <div className="muted" style={{ fontSize: 12, lineHeight: 1.5 }}>
            La fonte del livello è indicata accanto al valore. La stima da rifornimenti sottrae i consumi osservati dall'ultimo <b>pieno completo</b>. Registra i pieni qui sotto: bastano
            due pieni completi consecutivi per avere anche la resa reale tank-to-tank.
          </div>
        </div>
      </div>

      <div className="stat-grid fuel-period-summary">
        <StatCard label="Rifornimenti nel periodo" value={OBD.measurement(data?.period?.refuelCount ?? refuels.length, 0)} sub={scope.label} />
        <StatCard label="Litri erogati nel periodo" value={OBD.measurement(data?.period?.liters, 2)} unit="L" />
        <StatCard label="Spesa registrata alla pompa" value={OBD.measurement(data?.period?.costEur, 2)} unit="€" />
      </div>
      <RefuelForm onSaved={saved} editing={editing} onCancel={() => setEditing(null)} />

      {/* ── Tank-to-tank economy ── */}
      {t2t.length > 0 && (
        <div>
          <div className="section-head">
            <span className="section-title">Resa pieno-pieno</span>
            <span className="section-sub">km odometro ÷ litri pompa · pieno-a-pieno</span>
            {avgKmL != null && (
              <><span style={{ flex: 1 }} />
                <span className="big-num" style={{ fontSize: 22 }}>{avgKmL.toLocaleString("it-IT", { minimumFractionDigits: 1, maximumFractionDigits: 1 })}<span className="unit">km/L complessivi</span></span></>
            )}
          </div>
          <div className="table-wrap card-flat">
            <table className="data-table">
              <thead><tr>
                <th>Periodo</th><th className="num">km</th><th className="num">Litri</th>
                <th className="num">km/L</th><th className="num">L/100</th>
                <th>Carburante</th><th className="num">€/km</th>
              </tr></thead>
              <tbody>
                {t2tPage.items.map((x, i) => (
                  <tr key={i}>
                    <td className="mono muted" style={{ fontSize: 12 }}>{fmtInt(x.fromOdo)} → {fmtInt(x.toOdo)}</td>
                    <td className="num mono">{x.km.toLocaleString("it-IT", { minimumFractionDigits: 1, maximumFractionDigits: 1 })}</td>
                    <td className="num mono">{x.liters.toLocaleString("it-IT", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
                    <td className="num mono" style={{ fontWeight: 600 }}>{x.kmL.toLocaleString("it-IT", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
                    <td className="num mono">{x.l100.toLocaleString("it-IT", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
                    <td><span className="src-tag" style={{ background: "var(--accent-soft)", color: "var(--accent)" }}>{x.fuelType || "—"}</span></td>
                    <td className="num mono">{x.eurKm != null ? `€${x.eurKm.toLocaleString("it-IT", { minimumFractionDigits: 3, maximumFractionDigits: 3 })}` : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="muted" style={{ fontSize: 11, marginTop: 6 }}>
            <Pagination {...t2tPage} />
            La resa è attribuita al carburante che era <i>nel</i> serbatoio (immesso al pieno
            precedente): con HVO (~780 g/L) il contatore volumetrico ECU sottostima ~4-6%, la pompa resta il riferimento.
          </div>
        </div>
      )}

      {/* ── Refuel ledger ── */}
      <div>
        <div className="section-head">
          <span className="section-title">Rifornimenti registrati</span>
          <span className="section-sub">{refuels.length} nel periodo · ordinati per odometro</span>
        </div>
        {!data ? <div className="card muted">Rifornimenti non ancora caricati.</div> : refuels.length === 0 ? (
          <div className="card muted" style={{ padding: 16 }}>
            Nessun rifornimento nel periodo selezionato. Il livello globale può utilizzare pieni precedenti.
          </div>
        ) : (
          <div className="table-wrap card-flat">
            <table className="data-table">
              <thead><tr>
                <th>Data</th><th className="num">Odometro</th><th className="num">Litri</th>
                <th className="num">€/L</th><th>Tipo</th><th>Pieno</th><th>Note</th><th></th>
              </tr></thead>
              <tbody>
                {refuelPage.items.map(r => (
                  <tr key={r.id}>
                    <td className="mono" style={{ fontSize: 12 }}>{r.ts ? r.ts.slice(0, 16).replace("T", " ") : "—"}</td>
                    <td className="num mono">{r.odometerKm != null ? fmtInt(Math.round(r.odometerKm)) : "—"}</td>
                    <td className="num mono">{r.liters?.toLocaleString("it-IT", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) ?? "—"}</td>
                    <td className="num mono">{r.pricePerL != null ? `€ ${OBD.measurement(r.pricePerL, 3)}` : "—"}</td>
                    <td>{r.fuelType || "—"}</td>
                    <td>{r.fullTank ? "✓" : "parziale"}</td>
                    <td className="muted" style={{ fontSize: 12 }}>{r.note || ""}</td>
                    <td className="num"><div className="refuel-actions"><button className="text-action" onClick={() => edit(r)}>Modifica</button>
                      {deleting === r.id ? <><span>Eliminare?</span><button className="link-del" onClick={() => del(r.id)}>Conferma</button><button className="text-action" onClick={() => setDeleting(null)}>Annulla</button></>
                        : <button className="link-del" onClick={() => setDeleting(r.id)}>Elimina</button>}</div></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <Pagination {...refuelPage} />

      {/* ── Stale fuelConsumption suspects (§1 API bug) ── */}
      {suspects.length > 0 && (
        <div className="card" style={{ borderLeft: "3px solid var(--warn)" }}>
          <div className="section-title" style={{ marginBottom: 6 }}>Valori MyOpel da verificare</div>
          <div className="muted" style={{ fontSize: 12, marginBottom: 8 }}>
            {suspects.length} viaggi con fuelConsumption quasi identico al precedente su &gt;5 km —
            l'API Stellantis a volte restituisce il valore in cache. Da validare con l'integrale OBD.
          </div>
          {suspects.slice(0, 8).map((s, i) => (
            <div key={i} className="mono" style={{ fontSize: 12 }}>
              {s.start?.slice(0, 16).replace("T", " ")} · {s.km} km · Δ {s.deltaUl} µL
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

/* ============== MyOpel view ============== */
const MyOpelView = () => {
  const scope = React.useContext(PeriodScope);
  const myop = TRIPS.filter(t => t.sources.includes("myopel")).sort((a, b) => b.start.localeCompare(a.start));
  const comparable = myop.filter(OBD.usableForAnalysis);
  const metrics = OBD.periodMetrics(myop);
  const totalFuel = comparable.reduce((a, t) => a + (myopFuel(t) || 0), 0);
  const totalKm = myop.reduce((a, t) => a + (myopDistance(t) || 0), 0);
  const allAlerts = myop.flatMap(t => (t.alerts || []).map(c => ({ code: c, trip: t })));
  const fuelPriced = comparable.filter(t => t.priceFuel);
  const avgPrice = fuelPriced.length > 0
    ? fuelPriced.reduce((a, t) => a + t.priceFuel, 0) / fuelPriced.length
    : null;

  const lastSync = myop[0]?.start;
  const page = usePagination(myop, scope.query, 25);

  return (
    <div className="page" style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      <div className="stat-grid" style={{ gridTemplateColumns: "repeat(4, 1fr)" }}>
        <StatCard label="Viaggi MyOpel" value={myop.length}
                  sub={lastSync ? `ultimo viaggio: ${new Date(lastSync).toLocaleDateString("it-IT", { day: "2-digit", month: "short" })}` : undefined} />
        <StatCard label="Costo viaggi stimato" value={OBD.measurement(metrics.costTripCount ? metrics.costEur : null, 2)} unit="€" sub={`${metrics.costTripCount}/${myop.length} viaggi valorizzati · ${OBD.measurement(totalFuel, 2)} L MyOpel`} />
        <StatCard label="Distanza registrata" value={totalKm.toLocaleString("it-IT", { minimumFractionDigits: 1, maximumFractionDigits: 1 })} unit="km" sub={metrics.excludedFromComparisons ? "include lo storico non ricalcolato" : undefined} />
        <StatCard label="Alerts MyOpel" value={allAlerts.length} sub={`${new Set(allAlerts.map(a => a.code)).size} unici`} />
      </div>
      {metrics.excludedFromComparisons > 0 && <div className="data-notice">{metrics.excludedFromComparisons} viaggi storici nel periodo non sono ricalcolabili in modo attendibile e sono esclusi dai riepiloghi di consumo e costo.</div>}

      <div style={{ display: "grid", gridTemplateColumns: "1fr 320px", gap: 16 }}>
        <div>
          <div className="section-head">
            <span className="section-title">Cronologia viaggi</span>
            <span className="section-sub">canale Stellantis · TCU</span>
          </div>
          <div className="table-wrap card-flat">
            <table className="data-table">
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Data</th>
                  <th className="num">Distanza</th>
                  <th className="num">Durata</th>
                  <th className="num">Consumo</th>
                  <th className="num">Costo</th>
                  <th>Alerts</th>
                </tr>
              </thead>
              <tbody>
                {page.items.map(t => (
                  <tr key={t.id}>
                    <td className="mono muted">#{t.myopId}</td>
                    <td>
                      {new Date(t.start).toLocaleDateString("it-IT", { day: "2-digit", month: "short" })}{" "}
                      <span className="muted mono" style={{ fontSize: 11 }}>
                        {new Date(t.start).toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit" })}
                      </span>
                    </td>
                    <td className="num mono">{OBD.measurement(myopDistance(t))} <span className="muted">km</span></td>
                    <td className="num mono">{t.durationMin?.toLocaleString("it-IT", { minimumFractionDigits: 0, maximumFractionDigits: 0 }) ?? "—"} <span className="muted">min</span></td>
                    <td className="num mono">{OBD.measurement(OBD.weightedEconomy([{ km: myopDistance(t), liters: myopFuel(t) }]))} <span className="muted">km/L</span></td>
                    <td className="num mono">{t.costEur ? `€${t.costEur.toLocaleString("it-IT", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : "—"}</td>
                    <td>
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
          <Pagination {...page} />
          {!myop.length && <div className="muted">Nessun viaggio MyOpel nel periodo.</div>}
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

/* ============== Evidence-based observations in the selected period ============== */
/** Separate insufficient coverage from normal findings in period summaries. */
const TrendsView = () => {
  const scope = React.useContext(PeriodScope);
  const [filter, setFilter] = useState("all");
  const counts = { anomaly: 0, normal: 0, insufficient: 0, information: 0 };
  TREND_INSIGHTS.forEach(insight => { counts[OBD.insightPresentation(insight).finding] += 1; });
  const shown = TREND_INSIGHTS.filter(insight => filter === "all" || OBD.insightPresentation(insight).finding === filter);
  const observationPage = usePagination(TRIPS.filter(t => t.insights?.length > 0), scope.query, 8);
  const insightPage = usePagination(shown, scope.query + filter, 12);
  return (
    <div className="page" style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      <div className={`diag-banner ${counts.anomaly ? "warning" : "neutral"}`}>
        <div className="diag-ico"><Icon name={counts.anomaly ? "warn" : "info"} size={22} /></div>
        <div style={{ flex: 1, minWidth: 200 }}>
          <div className="diag-title">{counts.anomaly ? "Scostamenti osservati nel periodo"
            : counts.normal ? "Nessuno scostamento nei controlli valutabili"
              : "Dati insufficienti per una valutazione diagnostica"}</div>
          <div className="diag-sub">{TREND_INSIGHTS.length} risultati: i riepiloghi seguono il periodo; i controlli diagnostici considerano fino a 90 giorni prima della sua ultima registrazione. Le prove possono precedere il filtro e sono consultabili senza cambiarlo. La solidità delle prove è distinta dalla gravità.</div>
        </div>
        <div className="filter-row">
          {[["all", `Tutti ${TREND_INSIGHTS.length}`], ["anomaly", `Scostamenti ${counts.anomaly}`], ["normal", `Nei riferimenti ${counts.normal}`],
            ["insufficient", `Dati insufficienti ${counts.insufficient}`], ["information", `Informazioni ${counts.information}`]].map(([id, label]) => (
            <button key={id} className={`chip ${filter === id ? "active" : ""}`} aria-pressed={filter === id} onClick={() => setFilter(id)}>{label}</button>
          ))}
        </div>
      </div>

      <div className="stagger" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(min(100%, 330px), 1fr))", gap: 12 }}>
        {insightPage.items.map((ins, i) => <InsightCard key={ins.ruleId || i} insight={ins} />)}
        {shown.length === 0 && <div className="muted" style={{ padding: 20 }}>Nessun controllo in questa categoria.</div>}
      </div>

      <Pagination {...insightPage} />
      <div>
        <div className="section-head">
          <span className="section-title">Osservazioni per viaggio</span>
          <span className="section-sub">solo viaggi con note</span>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          {observationPage.items.map(t => (
            <div key={t.id}>
              <div className="row" style={{ marginBottom: 6, color: "var(--fg-2)", fontSize: 12 }}>
                <span className="mono">{new Date(t.start).toLocaleString("it-IT", { dateStyle: "short", timeStyle: "short" })}</span>
                <span className="muted">·</span>
                <span>{t.distanceKm} km · {t.durationMin} min</span>
                {t.dpfRegenState && <DpfPill state={t.dpfRegenState} />}
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 300px), 1fr))", gap: 10 }}>
                {t.insights.map((i, idx) => <InsightCard key={idx} insight={{ ...i, evidence: i.evidence || { tripIds: [t.id], pidSlugs: [] } }} />)}
              </div>
            </div>
          ))}
        </div>
        <Pagination {...observationPage} />
      </div>
    </div>
  );
};

/* ============== Admin view ============== */
const fmtBytes = (n) => {
  if (n == null) return "—";
  if (n >= 1e9) return (n / 1e9).toLocaleString("it-IT", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " GB";
  if (n >= 1e6) return (n / 1e6).toLocaleString("it-IT", { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + " MB";
  if (n >= 1e3) return (n / 1e3).toLocaleString("it-IT", { minimumFractionDigits: 0, maximumFractionDigits: 0 }) + " KB";
  return n + " B";
};

const StoragePanel = () => {
  const revision = React.useContext(DataRevision);
  const [s, setS] = useState(null);
  const [error, setError] = useState(null);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let cancelled = false;
    setError(null);
    apiJson("/api/v1/admin/storage").then(value => { if (!cancelled) setS(value); })
      .catch(error => { if (!cancelled) setError(error.message); });
    return () => { cancelled = true; };
  }, [revision, retry]);
  if (error) return <div className="data-notice error" role="alert">Spazio su disco: {error} <button className="icon-btn" onClick={() => setRetry(n => n + 1)}>Riprova</button></div>;
  if (!s) return <div className="data-notice" role="status">Caricamento spazio su disco…</div>;
  const archived = s.obd_archive_bytes + s.myop_archive_bytes;
  const saved = Math.max(0, s.ledger_original_bytes - archived);
  const savedPct = s.ledger_original_bytes > 0 ? saved / s.ledger_original_bytes * 100 : 0;
  return (
    <div style={{ marginBottom: 24 }}>
      <div className="section-head">
        <span className="section-title">Spazio su disco</span>
        <span className="section-sub">policy sorgenti: {s.archive_mode}</span>
      </div>
      <div className="stat-grid" style={{ gridTemplateColumns: "repeat(4, 1fr)" }}>
        <StatCard icon="archive" label="Database" value={fmtBytes(s.db_bytes)}
                  sub="trip + PID compressi (zlib)" />
        <StatCard icon="archive" label="Archivio sorgenti" value={fmtBytes(archived)}
                  sub={`${s.ledger_archived} di ${s.ledger_files} file gzippati`} />
        <StatCard icon="download" label="File fuori archivio" value={fmtBytes(s.obd_pending_bytes + s.myop_pending_bytes)}
                  sub="include gli originali conservati; non indica la coda" />
        <StatCard icon="trend" label="Compressione archivio" value={fmtBytes(saved)}
                  sub={`−${savedPct.toLocaleString("it-IT", { minimumFractionDigits: 0, maximumFractionDigits: 0 })}% rispetto alle copie non compresse`} />
      </div>
      <div className="muted" style={{ fontSize: 12, marginTop: 8, lineHeight: 1.5 }}>
        Dopo l'elaborazione i CSV/.myop vengono compressi in <span className="mono">archive/</span> e
        registrati nel ledger (sha256): restano ri-analizzabili dalle migrazioni future.
        Imposta <span className="mono">SOURCE_ARCHIVE=keep</span> per conservare solo gli originali.
        Con gzip anche gli originali restano disponibili; il vecchio valore <span className="mono">delete</span> usa ora lo stesso comportamento.
      </div>
    </div>
  );
};

/* ============== Settings panel (MyOpel toggle, tank capacity) ============== */
const SettingsPanel = () => {
  const s = typeof SETTINGS !== "undefined" ? SETTINGS : {};
  const [myopOn, setMyopOn] = useState(s.myop_enabled !== false);
  const [cap, setCap] = useState(s.tank_capacity_l ?? 43.5);
  const [busy, setBusy] = useState(false);

  const save = async (patch, reload) => {
    setBusy(true);
    try {
      await apiJson("/api/v1/settings", {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      });
      if (reload) location.reload();
    } catch (e) { alert("Errore: " + e.message); }
    finally { setBusy(false); }
  };

  const toggleMyop = () => {
    const next = !myopOn;
    // Full reload: the toggle changes the whole dashboard's data source.
    save({ myop_enabled: next }, true);
  };

  return (
    <div style={{ marginBottom: 24 }}>
      <div className="section-head">
        <span className="section-title">Impostazioni piattaforma</span>
        <span className="section-sub">sorgente dati &amp; serbatoio</span>
      </div>
      <div className="card" style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        <div className="row" style={{ gap: 14, alignItems: "flex-start" }}>
          <button className={`switch ${myopOn ? "on" : ""}`} onClick={toggleMyop} disabled={busy}
                  role="switch" aria-checked={myopOn} title="Attiva/disattiva la sorgente MyOpel">
            <span className="knob" />
          </button>
          <div style={{ flex: 1 }}>
            <div style={{ fontWeight: 600 }}>Sorgente MyOpel (.myop)</div>
            <div className="muted" style={{ fontSize: 12, lineHeight: 1.5, marginTop: 2 }}>
              {myopOn
                ? "Attiva: i file .myop vengono importati e usati per livello carburante, costi e service."
                : "Disattivata: la piattaforma gira in modalità solo-OBD. Livello carburante da sonda OBD + ledger rifornimenti, service dalla distanza al cambio olio."}
              {" "}I viaggi .myop già importati restano consultabili.
            </div>
          </div>
          <span className={`state-tag ${myopOn ? "ok" : "off"}`}>{myopOn ? "ON" : "OFF"}</span>
        </div>

        <div className="divider" />

        <div className="row" style={{ gap: 12, flexWrap: "wrap", alignItems: "flex-end" }}>
          <label className="form-field" style={{ maxWidth: 180 }}>
            <span>Capacità utile serbatoio (L)</span>
            <input type="number" min="0.1" max="500" step="0.1" value={cap} onChange={e => setCap(e.target.value)} />
          </label>
          <button className="icon-btn" disabled={busy}
                  onClick={() => save({ tank_capacity_l: parseFloat(cap) }, true)}>
            Salva capacità
          </button>
          <span className="muted" style={{ fontSize: 12, alignSelf: "center" }}>
            Riferimento validato: ~43,5 L (0,435 L per punto %).
          </span>
        </div>
      </div>
    </div>
  );
};

/** Show queue failures and legacy recovery requirements from the latest snapshot. */
const ImportStatusPanel = () => {
  React.useContext(DataRevision);
  const status = OBD.ingestionStatus(_dashboardMeta);
  return (
    <section className="card" style={{ marginBottom: 24 }} aria-label="Stato importazioni">
      <div className="section-head" style={{ flexWrap: "wrap" }}>
        <span className="section-title">Stato importazioni</span>
        <button className="icon-btn" onClick={() => refreshDashboard().catch(() => {})}>Aggiorna stato</button>
      </div>
      {!status ? <div className="muted">Stato importazioni non disponibile. Aggiorna i dati per verificarlo.</div> : <>
        <div className="row" style={{ flexWrap: "wrap", marginBottom: 10 }}>
          <span>Servizio: <b>{status.running === true ? "attivo" : status.running === false ? "non attivo" : "non disponibile"}</b></span>
          <span>File in attesa: <b>{OBD.measurement(status.pending, 0)}</b></span>
          <span>Errori registrati: <b>{status.errors == null ? "—" : status.errors.length}</b></span>
        </div>
        {status.errors?.length > 0 && <div className="data-notice error" role="status">
          <div>Questi file richiedono una verifica:</div>
          <ul>{status.errors.map((error, index) => <li key={`${error.file}-${index}`}>
            <strong className="mono">{error.file}</strong>: {error.message}
          </li>)}</ul>
        </div>}
        <div className="muted" style={{ marginTop: 10 }}>
          Viaggi legacy ancora da ricostruire: <b>{OBD.measurement(status.legacyCount, 0)}</b>.
          {status.legacyCount > 0 && " Le sorgenti mancanti o insufficienti per un ricalcolo attendibile richiedono un controllo; i metadati non disponibili restano indicati come tali."}
        </div>
        {status.legacyIds.length > 0 && <details style={{ marginTop: 8 }}>
          <summary>Mostra gli ID dei viaggi da ricostruire</summary>
          <ul>{status.legacyIds.map(id => <li key={id} className="mono">{id}</li>)}</ul>
        </details>}
      </>}
    </section>
  );
};

const AdminView = () => {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [correlating, setCorrelating] = useState(false);

  const check = async () => {
    setLoading(true);
    try {
      setData(await apiJson("/api/v1/admin/uncorrelated"));
    } catch(e) {
      setData({ error: e.message });
    } finally {
      setLoading(false);
    }
  };

  const correlate = async () => {
    setCorrelating(true);
    try {
      const res = await apiJson("/api/v1/admin/correlate", { method: "POST" });
      await refreshDashboard(true);
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
      <SettingsPanel />
      <ImportStatusPanel />
      <StoragePanel />

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

      {data?.suspects?.length > 0 && (
        <div style={{ marginBottom: 20 }}>
          <div className="section-head">
            <span className="section-title">Correlazioni da verificare</span>
            <span className="section-sub">copertura km MyOpel / km OBD fuori norma</span>
          </div>
          {data.suspects.map((s, i) => (
            <div key={i} className="card" style={{ marginBottom: 8, borderLeft: "3px solid var(--warn)" }}>
              <div className="row" style={{ gap: 14, flexWrap: "wrap" }}>
                <span className="mono" style={{ fontSize: 12 }}>{s.id}</span>
                <span className="muted" style={{ fontSize: 12 }}>{fmt(s.start)}</span>
                <span className="mono" style={{ fontSize: 12 }}>
                  OBD {s.obd_km} km · MyOpel {s.myop_km} km · copertura {(s.coverage * 100).toLocaleString("it-IT", { minimumFractionDigits: 0, maximumFractionDigits: 0 })}%
                </span>
                <span className="mono muted" style={{ fontSize: 11 }}>tratte {s.leg_ids.join(", ")}</span>
              </div>
              <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>{s.reason}</div>
            </div>
          ))}
        </div>
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
              <div className="muted" style={{ fontSize: 12 }}>{c.obd.km?.toLocaleString("it-IT", { minimumFractionDigits: 1, maximumFractionDigits: 1 }) ?? "—"} km · {c.obd.min?.toLocaleString("it-IT", { minimumFractionDigits: 0, maximumFractionDigits: 0 }) ?? "—"} min</div>
            </div>
            <div style={{ flex: 1, minWidth: 200 }}>
              <div className="lbl" style={{ marginBottom: 4 }}>MyOpel</div>
              <div className="mono" style={{ fontSize: 12 }}>{c.myop.id}</div>
              <div style={{ fontSize: 13 }}>{fmt(c.myop.start)} → {fmt(c.myop.end)}</div>
              <div className="muted" style={{ fontSize: 12 }}>{c.myop.km?.toLocaleString("it-IT", { minimumFractionDigits: 1, maximumFractionDigits: 1 }) ?? "—"} km · {c.myop.min?.toLocaleString("it-IT", { minimumFractionDigits: 0, maximumFractionDigits: 0 }) ?? "—"} min</div>
            </div>
            <div style={{ textAlign: "right", minWidth: 100 }}>
              <div style={{ fontSize: 22, fontWeight: 700, color: c.score >= 0.5 ? "var(--ok)" : c.score >= 0.35 ? "var(--warn)" : "var(--muted)" }}>
                {(c.score * 100).toLocaleString("it-IT", { minimumFractionDigits: 0, maximumFractionDigits: 0 })}%
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
    { id: "trends",    icon: "trend", label: "Trend" },
    { id: "map",       icon: "map",   label: "Mappa" },
  ];
  const secondary = ["fuel", "pids", "myopel", "dpf", "compare", "events", "admin"];
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
  const [periodSelection, setPeriodSelection] = useState(() => {
    try { return OBD.restorePeriod(JSON.parse(localStorage.getItem("obd-period") || "null")); }
    catch (_) { return { preset: "30d" }; }
  });
  const [appliedRange, setAppliedRange] = useState(null);
  const [evidence, setEvidence] = useState(null);
  const [historicalEvidence, setHistoricalEvidence] = useState(null);
  const range = OBD.periodRange(periodSelection);
  const [revision, setRevision] = useState(_dataVersion);
  const [status, setStatus] = useState({ loading: false, periodPending: true, checked: null,
    error: window.DASHBOARD_BOOTSTRAP_ERROR ? "Dati iniziali non caricati. Riprovo la connessione…" : null });
  useEffect(() => {
    const changed = event => {
      setStatus(previous => ({ ...previous, ...event.detail }));
      if (event.detail.changed) setRevision(_dataVersion);
      if (event.detail.range) setAppliedRange(event.detail.range);
    };
    const refresh = () => { if (!document.hidden) refreshDashboard().catch(() => {}); };
    window.addEventListener("dashboard-status", changed);
    document.addEventListener("visibilitychange", refresh);
    const timer = setInterval(refresh, 60000);
    return () => { clearInterval(timer); window.removeEventListener("dashboard-status", changed); document.removeEventListener("visibilitychange", refresh); };
  }, []);

  useEffect(() => {
    if (!range.error) try { localStorage.setItem("obd-period", JSON.stringify(periodSelection)); } catch (_) { /* Optional persistence. */ }
  }, [periodSelection]);
  useEffect(() => {
    if (!range.error) selectDashboardPeriod(range).catch(() => {});
  }, [range.query]);

  useEffect(() => {
    const open = event => {
      const { tripId, pidSlug } = event.detail || {};
      if (!tripId) return;
      if (!TRIPS.some(trip => trip.id === tripId)) { setHistoricalEvidence({ tripId, pidSlug }); return; }
      setSelectedTripId(tripId); setEvidence({ tripId, pidSlug, openedAt: Date.now() }); setView("trips");
    };
    window.addEventListener("open-evidence", open);
    return () => window.removeEventListener("open-evidence", open);
  }, []);
  const periodPending = !!range.error || status.periodPending || appliedRange?.query !== range.query;

  // Apply tweaks to <html> as data-* attributes
  useEffect(() => {
    const r = document.documentElement;
    r.dataset.theme = t.theme;
    r.dataset.density = t.density;
    r.dataset.anim = t.anim;
    r.style.setProperty("--accent-h", ACCENT_HUES[t.accent] ?? 200);
  }, [t.theme, t.density, t.anim, t.accent]);

  // Close drawer on view change
  useEffect(() => { setDrawerOpen(false); }, [view]);

  const viewLabels = {
    dashboard: "Dashboard",
    trips: "Viaggi",
    map: "Mappa GPS",
    fuel: "Carburante · Serbatoio & Rifornimenti",
    pids: "Catalogo PID",
    compare: "Confronta viaggi",
    dpf: "DPF / FAP",
    myopel: "MyOpel · Stellantis",
    trends: "Trend e controlli",
    events: "Registro eventi",
    admin: "Admin · Impostazioni",
  };

  // keyboard nav
  useEffect(() => {
    const fn = (e) => {
      if (e.key === "Escape") { setDrawerOpen(false); window.postMessage({type:"__deactivate_edit_mode"}, window.location.origin); }
    };
    window.addEventListener("keydown", fn);
    return () => window.removeEventListener("keydown", fn);
  }, []);

  // Sidebar compact: auto = follows breakpoint (CSS); manual = override via class
  const sidebarClass = t.sidebar === "compact" ? "sidebar-compact" :
                       t.sidebar === "full" ? "sidebar-full" : "";
  const drawerClass = drawerOpen ? "drawer-open" : "";

  return (
    <DataRevision.Provider value={revision}>
    <PeriodScope.Provider value={appliedRange || range}>
    <div className={`app ${sidebarClass} ${drawerClass}`} onClick={(e) => {
      // close drawer when clicking outside sidebar
      if (drawerOpen && !e.target.closest(".sidebar") && !e.target.closest(".menu-btn")) {
        setDrawerOpen(false);
      }
    }}>
      <Sidebar active={view} setActive={setView} />
      <BottomNav active={view} setActive={setView} onMenu={() => setDrawerOpen(v => !v)} />
      <main className="main">
        <TopBar view={viewLabels[view]} onMenu={() => setDrawerOpen(v => !v)} status={{ ...status, periodPending }}
          onRefresh={() => refreshDashboard().catch(() => {})}
          onAppearance={() => window.postMessage({ type: "__activate_edit_mode" }, window.location.origin)} />
        <div className="content">
          <PeriodToolbar selection={periodSelection} onChange={setPeriodSelection} range={range} loading={status.loading} />
          <div className={`data-status ${status.error ? "error" : ""}`} role={status.error ? "alert" : "status"}>
            {status.error ? `${status.error}${status.checked && !periodPending ? " · restano visibili gli ultimi dati caricati" : ""}`
              : status.loading ? "Aggiornamento dati…" : `Riepiloghi ed esportazione: periodo selezionato. Stato veicolo e livello serbatoio: ultimi dati globali.${status.checked ? " Verifica " + status.checked.toLocaleTimeString("it-IT", {hour:"2-digit",minute:"2-digit"}) : ""}`}
          </div>
          {periodPending ? <div className="period-loading" role={status.error || range.error ? "alert" : "status"}>
            <Icon name={status.error || range.error ? "warn" : "clock"} size={28} />
            <p>{range.error || status.error || "Caricamento del periodo selezionato…"}</p>
            {status.error && !range.error && <button className="icon-btn" onClick={() => selectDashboardPeriod(range).catch(() => {})}>Riprova</button>}
          </div> : <div className="view-wrap" key={view}>
            {view === "dashboard" && <Dashboard setActive={setView} setSelectedTripId={setSelectedTripId} />}
            {view === "trips"     && <TripsView key={evidence?.openedAt || "trips"} selectedId={selectedTripId} setSelectedId={setSelectedTripId} evidence={evidence} />}
            {view === "map"       && <MapView />}
            {view === "compare"   && <CompareView trips={TRIPS} periodLabel={appliedRange?.label} />}
            {view === "fuel"      && <FuelView />}
            {view === "pids"      && <PidExplorer />}
            {view === "dpf"       && <DpfView />}
            {view === "myopel"    && <MyOpelView />}
            {view === "trends"    && <TrendsView />}
            {view === "events"    && <EventsView scope={appliedRange} revision={revision} api={apiJson} onMutation={() => refreshDashboard(true)} PageControls={Pagination} />}
            {view === "admin"     && <AdminView />}
          </div>}
        </div>
      </main>
      {historicalEvidence && <HistoricalEvidenceDialog evidence={historicalEvidence} api={apiJson} onClose={() => setHistoricalEvidence(null)} />}

      <TweaksPanel title="Aspetto e preferenze">
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
    </PeriodScope.Provider>
    </DataRevision.Provider>
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
