/* global React, OBD, Icon, Pagination, TripExploration */

/** Record only interventions explicitly entered by the user. */
const MaintenanceForm = ({ editing, api, onSaved, onCancel }) => {
  const [form, setForm] = React.useState(() => OBD.maintenanceDraft(editing));
  const [busy, setBusy] = React.useState(false), [error, setError] = React.useState(null);
  React.useEffect(() => { setForm(OBD.maintenanceDraft(editing)); setError(null); }, [editing?.id]);
  const set = (key, value) => setForm(previous => ({ ...previous, [key]: value }));
  const submit = async event => {
    event.preventDefault(); setError(null);
    let body;
    try { body = OBD.maintenancePayload(form); } catch (error) { setError(error.message); return; }
    setBusy(true);
    try {
      await api(editing ? `/api/v1/maintenance/${encodeURIComponent(editing.id)}` : "/api/v1/maintenance", {
        method: editing ? "PUT" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      if (!editing) setForm(OBD.maintenanceDraft(null));
      onSaved(body, editing ? "Intervento aggiornato." : "Intervento registrato.");
    } catch (error) { setError(error.message); }
    finally { setBusy(false); }
  };
  return <form className="card maintenance-form" id="maintenance-form" aria-label={editing ? "Modifica intervento" : "Registra intervento"} onSubmit={submit} noValidate>
    <div className="section-head"><h2 className="section-title">{editing ? "Modifica intervento" : "Registra un intervento"}</h2><span className="section-sub">inserimento manuale · data e ora locali</span></div>
    {error && <div className="data-notice error" role="alert">{error}</div>}
    {editing?.archived && <div className="data-notice">Questo intervento è archiviato. La modifica conserva il suo stato; puoi ripristinarlo dall'archivio.</div>}
    <div className="form-grid">
      <label className="form-field"><span>Data / ora *</span><input type="datetime-local" step="1" value={form.ts} required onChange={event => set("ts", event.target.value)} /></label>
      <label className="form-field"><span>Intervento *</span><select value={form.type} onChange={event => set("type", event.target.value)}>{Object.entries(OBD.maintenanceTypes).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <label className="form-field"><span>Odometro (km)</span><input type="number" min="0" max="2000000" step="0.1" inputMode="decimal" value={form.odometerKm} onChange={event => set("odometerKm", event.target.value)} placeholder="opzionale" /></label>
      <label className="form-field maintenance-note"><span>Nota</span><textarea rows="2" maxLength="2000" value={form.note} onChange={event => set("note", event.target.value)} placeholder="Cosa è stato fatto, eventuali ricambi o verifiche" /></label>
    </div>
    <div className="event-actions"><button className="icon-btn" type="submit" disabled={busy}>{busy ? "Salvataggio…" : editing ? "Salva modifiche" : "Registra intervento"}</button>
      {editing && <button className="text-action" type="button" disabled={busy} onClick={onCancel}>Annulla modifica</button>}</div>
  </form>;
};

/** Load the selected event history only while its view is open; retain manual archive access. */
const EventsView = ({ scope, revision, api, onMutation, PageControls }) => {
  const [data, setData] = React.useState(null), [error, setError] = React.useState(null), [loading, setLoading] = React.useState(true);
  const [retry, setRetry] = React.useState(0), [page, setPage] = React.useState(1), [editing, setEditing] = React.useState(null);
  const [notice, setNotice] = React.useState(null), [confirmId, setConfirmId] = React.useState(null), [busy, setBusy] = React.useState(false);
  const [filter, setFilter] = React.useState(() => {
    try { const saved = localStorage.getItem("obd-event-filter"); return ["all", "automatic", "manual", "archived"].includes(saved) ? saved : "all"; } catch (_) { return "all"; }
  });
  React.useEffect(() => {
    let cancelled = false;
    setLoading(true); setError(null);
    api("/api/v1/events" + scope.query).then(OBD.validateEvents).then(value => { if (!cancelled) setData(value); })
      .catch(error => { if (!cancelled) setError(error.message); }).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [api, scope.query, revision, retry]);
  React.useEffect(() => { setPage(1); setEditing(null); setConfirmId(null); }, [filter, scope.query]);
  React.useEffect(() => { try { localStorage.setItem("obd-event-filter", filter); } catch (_) { /* Optional preference. */ } }, [filter]);
  const records = OBD.eventRecords(data, filter), paged = OBD.pageItems(records, page, 20);
  const reload = () => { setRetry(value => value + 1); onMutation().catch(() => {}); };
  const saved = (body, message) => {
    const day = body.ts.slice(0, 10), outside = (scope.fromDate && day < scope.fromDate) || (scope.toDate && day > scope.toDate);
    setEditing(null); setNotice(message + (outside ? " La data è fuori dal periodo selezionato: il filtro è rimasto invariato." : "")); reload();
  };
  const edit = record => { setEditing(record); setNotice(null); requestAnimationFrame(() => document.getElementById("maintenance-form")?.scrollIntoView({ behavior: "smooth", block: "start" })); };
  const archive = async (record, archived) => {
    setBusy(true); setError(null);
    try {
      await api(`/api/v1/maintenance/${encodeURIComponent(record.id)}`, archived ? { method: "DELETE" } : {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
          ts: record.ts.replace(" ", "T"), type: record.type, odometerKm: record.odometerKm ?? null, note: record.note ?? "", archived: false,
        }),
      });
      setConfirmId(null); setEditing(null); setNotice(archived ? "Intervento archiviato. È consultabile e ripristinabile dal filtro Archivio." : "Intervento ripristinato."); reload();
    } catch (error) { setError(error.message); }
    finally { setBusy(false); }
  };
  return <div className="page events-page">
    <div className="data-notice">Il registro raccoglie interventi inseriti da te, rifornimenti e osservazioni ricavate dai dati del periodo. La vicinanza a un intervento non dimostra che ne sia la causa.</div>
    {notice && <div className="data-notice" role="status">{notice}</div>}
    <MaintenanceForm editing={editing} api={api} onSaved={saved} onCancel={() => setEditing(null)} />
    <section aria-label="Cronologia eventi">
      <div className="section-head event-list-head"><h2 className="section-title">Cronologia del periodo</h2><span className="section-sub">{scope.label}</span></div>
      <div className="filter-row event-filters">{[["all", "Tutti"], ["automatic", "Da dati e rifornimenti"], ["manual", "Interventi manuali"], ["archived", "Archivio"]].map(([value, label]) =>
        <button className={`chip ${filter === value ? "active" : ""}`} type="button" key={value} aria-pressed={filter === value} onClick={() => setFilter(value)}>{label}</button>)}</div>
      {filter === "archived" && <p className="muted">Interventi archiviati la cui data rientra nel periodo selezionato. L'archiviazione conserva tutti i dati.</p>}
      {loading && <div className="data-notice" role="status">Caricamento del registro eventi…</div>}
      {error && <div className="data-notice error" role="alert">{error}{data ? " · Gli eventi visibili provengono dall'ultimo caricamento riuscito." : ""} <button className="icon-btn" onClick={() => setRetry(value => value + 1)}>Riprova</button></div>}
      {!loading && !error && records.length === 0 && <div className="card muted">Nessun evento in questa categoria nel periodo selezionato.</div>}
      <div className="event-list">{paged.items.map(event => {
        const manual = OBD.maintenanceForEvent(data, event);
        const typeLabel = { maintenance: "Intervento manuale", refuel: "Rifornimento", dpf: "Osservazione DPF", insight: "Osservazione diagnostica" }[event.kind] || "Evento registrato";
        const state = event.lifecycle ? OBD.insightPresentation({ lifecycle: event.lifecycle }) : null;
        const source = { obd: "OBD", refuel: "registro rifornimenti", user: "utente", manual: "utente", myopel: "MyOpel" }[event.source] || event.source || "non dichiarata";
        return <article className={`card event-card ${event.archived ? "event-archived" : ""}`} key={`${event.kind}:${event.id}`}>
          <div className="event-meta"><time dateTime={event.ts}>{OBD.recordedDate(event.ts)}</time><span>{typeLabel}</span><span>Fonte: {source}</span>{event.archived && <span className="lifecycle-badge">Archiviato</span>}</div>
          <h3>{event.title}</h3>{event.body && <p>{event.body}</p>}
          {event.kind === "dpf" && <p className="muted">Osservazione durante una registrazione, non conteggio di cicli completi.</p>}
          {event.odometerKm != null && <p className="mono">{OBD.measurement(event.odometerKm, 1)} km</p>}
          {state?.lifecycleLabel && <p className="muted">Stato dell'osservazione: {state.lifecycleLabel.toLowerCase()}{state.unconfirmed ? " · esito non confermato" : ""}. Riferito alla data dell'evento.</p>}
          <div className="event-actions">
            {event.tripId && <button type="button" className="icon-btn" onClick={() => window.dispatchEvent(new CustomEvent("open-evidence", { detail: { tripId: event.tripId } }))}>Apri viaggio</button>}
            {manual && <><button className="text-action" disabled={busy} onClick={() => edit(manual)}>Modifica</button>
              {manual.archived ? <button className="text-action" disabled={busy} onClick={() => archive(manual, false)}>Ripristina</button>
                : confirmId === manual.id ? <><span>Archiviare questo intervento?</span><button className="text-action" disabled={busy} onClick={() => archive(manual, true)}>Conferma archiviazione</button><button className="text-action" disabled={busy} onClick={() => setConfirmId(null)}>Annulla</button></>
                  : <button className="text-action" disabled={busy} onClick={() => setConfirmId(manual.id)}>Archivia</button>}</>}
          </div>
        </article>;
      })}</div>
      <PageControls {...paged} setPage={setPage} />
    </section>
  </div>;
};

/** Inspect an actual historical trip without widening the global date filter. */
const HistoricalEvidenceDialog = ({ evidence, api, onClose }) => {
  const dialog = React.useRef(null), [trip, setTrip] = React.useState(null), [error, setError] = React.useState(null), [retry, setRetry] = React.useState(0);
  React.useEffect(() => { dialog.current?.showModal(); return () => dialog.current?.close(); }, []);
  React.useEffect(() => {
    let cancelled = false;
    setTrip(null); setError(null);
    api(`/api/v1/trips/${encodeURIComponent(evidence.tripId)}`).then(value => { if (!cancelled) setTrip(value); })
      .catch(error => { if (!cancelled) setError(error.message); });
    return () => { cancelled = true; };
  }, [api, evidence.tripId, retry]);
  return <dialog ref={dialog} className="historical-evidence-dialog" aria-labelledby="historical-evidence-title" onCancel={onClose} onClose={onClose}>
    <div className="historical-evidence-head"><div><span className="eyebrow">Viaggio fuori dal periodo selezionato</span><h2 id="historical-evidence-title">Prove storiche</h2></div><button className="icon-btn" aria-label="Chiudi prove storiche" autoFocus onClick={onClose}>Chiudi</button></div>
    <p className="muted">Il filtro globale è rimasto invariato. Queste osservazioni descrivono il viaggio registrato, non lo stato attuale del veicolo.</p>
    {error ? <div className="data-notice error" role="alert">{error} <button className="icon-btn" onClick={() => setRetry(value => value + 1)}>Riprova</button></div>
      : !trip ? <div className="data-notice" role="status">Caricamento delle prove storiche…</div> : <>
        <div className="card historical-trip-summary"><strong>{OBD.recordedDate(trip.start)}</strong><span>Fonti: {(trip.sources || []).map(source => source === "obd" ? "OBD" : source === "myopel" ? "MyOpel" : source).join(" + ") || "non disponibili"}</span>
          <span>{OBD.measurement(trip.distanceKm, 1)} km · {OBD.measurement(trip.durationMin, 1)} min osservati</span><span className="muted">{trip.filename || trip.id}</span></div>
        {trip.legacyIncomplete && <div className="data-notice">Storico non ricalcolabile in modo attendibile: escluso dai confronti quantitativi.</div>}
        {trip.sources?.includes("obd") ? <TripExploration trip={trip} initialPid={evidence.pidSlug} /> : <div className="data-notice">Questo viaggio contiene dati MyOpel; non sono disponibili serie PID OBD.</div>}
      </>}
  </dialog>;
};

Object.assign(window, { EventsView, HistoricalEvidenceDialog });
