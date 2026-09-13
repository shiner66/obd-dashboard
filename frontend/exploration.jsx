/* global React, OBD, OBDExploration, LineChart, DpfPill, TripMap, Icon */

/** Format the observed trip start without inventing a date for legacy records. */
const comparisonTripLabel = trip => {
  const date = new Date(trip?.start);
  const stamp = Number.isNaN(date.valueOf()) ? "Data non disponibile" : date.toLocaleString("it-IT", {dateStyle: "short", timeStyle: "short"});
  return `${stamp} · ${OBD.measurement(trip?.distanceKm)} km`;
};
const EXPLORATION_COLORS = ["var(--accent)", "var(--warn)", "var(--info)"];
const EXPLORATION_FUEL = {obd_rate: "OBD · portata", obd_mass: "OBD · massa", myopel: "MyOpel"};

/** Compare up to three independently scaled PIDs on the same observed time range. */
const TripExploration = ({trip, initialPid}) => {
  const available = Object.keys(trip?.pidSeriesFull || {}).filter(slug => trip.pidSeriesFull[slug]?.length > 0);
  const availableKey = available.join("|");
  const preferred = [initialPid, "egt_a", "soot_cl", "rpm"].filter((slug,index,array) => slug && available.includes(slug) && array.indexOf(slug) === index);
  const [requested, setRequested] = React.useState(preferred.slice(0,2));
  const [range, setRange] = React.useState(null);
  const [focusedEvent, setFocusedEvent] = React.useState(null);
  React.useEffect(() => { setRequested(preferred.slice(0,2)); setRange(null); setFocusedEvent(null); }, [trip?.id, initialPid, availableKey]);
  const selected = requested.filter(slug => available.includes(slug));
  const slugs = selected.length ? selected : available.slice(0, Math.min(2,available.length));
  const gaps = React.useMemo(() => OBDExploration.recordingGaps(trip), [trip]);
  const events = React.useMemo(() => OBDExploration.dpfObservations(trip), [trip]);
  const descriptors = slugs.map(slug => ({slug, series: OBDExploration.prepareSeries(trip.pidSeriesFull[slug], trip.pidSeriesTimes?.[slug], gaps),
    catalog: (window.PID_CATALOG || []).find(pid => pid.slug === slug)}));
  const aligned = descriptors.length > 0 && descriptors.every(item => item.series.timed);
  const domain = aligned ? [Math.min(...descriptors.map(item => item.series.domain[0])), Math.max(...descriptors.map(item => item.series.domain[1]))] : null;
  const updateSlug = (index, slug) => { setRequested(slugs.map((current,at) => at === index ? slug : current)); setRange(null); };
  const timelineEnd = Math.max(...descriptors.filter(item => item.series.timed).map(item => item.series.domain[1]),
    ...(trip?.observedWindows || []).map(window => window[1]), ...events.map(event => event.from), 1);
  const focusEvent = event => {
    setFocusedEvent(event);
    if (domain && event.from >= domain[0] && event.from <= domain[1])
      setRange(OBDExploration.clampRange([Math.max(domain[0], event.from - 60), Math.min(domain[1], Math.max(event.to, event.from) + 60)], domain));
  };
  return <section className="trip-exploration" aria-label="Esplorazione dei segnali del viaggio">
    <div className="exploration-heading"><div><h3>Esplora i segnali</h3><p>Fino a tre PID, con unità e scale verticali separate.</p></div>
      {trip?.dpfRegenState && <DpfPill state={trip.dpfRegenState}/>}</div>
    {initialPid && !available.includes(initialPid) && !trip?.detailLoading && <div className="exploration-notice">
      Il PID richiesto «{(window.PID_CATALOG || []).find(pid => pid.slug === initialPid)?.name || initialPid}» non ha una sequenza temporale salvata.
      {trip?.pidValues?.[initialPid] && <span> Ultimo valore nel riepilogo: {OBD.measurement(trip.pidValues[initialPid].last)} {(window.PID_CATALOG || []).find(pid => pid.slug === initialPid)?.unit || ""} · {trip.pidValues[initialPid].samples ?? "—"} campioni originali.</span>}
    </div>}
    <div className="exploration-notice">{aligned ? "Asse X comune: secondi reali trascorsi dall’inizio. Zoom e intervallo sono condivisi."
      : "Tempi mancanti per almeno una serie: gli assi per campione restano separati e non sono sincronizzati."}
      {" "}La vicinanza delle curve non dimostra un rapporto causa-effetto.</div>
    {available.length === 0 ? <div className="empty-state">Nessuna sequenza PID consultabile per questo viaggio. I riepiloghi restano disponibili nella panoramica.</div> : <>
      <div className="pid-compare-selectors">
        {slugs.map((slug,index) => <label key={index}><span><i style={{background: EXPLORATION_COLORS[index]}}/>Segnale {index+1}</span>
          <select value={slug} onChange={event => updateSlug(index,event.target.value)}>
            {available.filter(option => option === slug || !slugs.includes(option)).map(option => {
              const pid = (window.PID_CATALOG || []).find(item => item.slug === option);
              return <option key={option} value={option}>{pid?.name?.replace(/^\[ECM\]\s*/, "") || option} · {pid?.unit || "unità non dichiarata"}</option>;
            })}
          </select>
          {slugs.length > 1 && <button type="button" className="evidence-link" onClick={() => setRequested(slugs.filter((_,at) => at !== index))}>Rimuovi segnale {index+1}</button>}
        </label>)}
        {slugs.length < 3 && available.length > slugs.length && <button type="button" className="icon-btn" onClick={() => setRequested([...slugs, available.find(slug => !slugs.includes(slug))])}>+ Aggiungi PID</button>}
      </div>
      <div className="pid-comparison-stack">{descriptors.map((item,index) => <article key={item.slug} className="pid-comparison-panel">
        <div className="pid-comparison-head"><strong style={{color: EXPLORATION_COLORS[index]}}>{item.catalog?.name?.replace(/^\[ECM\]\s*/, "") || item.slug}</strong>
          <span>{item.catalog?.unit || "Unità non dichiarata"} · scala indipendente</span></div>
        <LineChart data={trip.pidSeriesFull[item.slug]} times={trip.pidSeriesTimes?.[item.slug]} title={item.catalog?.name || item.slug}
          yLabel={item.catalog?.unit || ""} height={180} color={EXPLORATION_COLORS[index]} gaps={gaps} events={events}
          domain={aligned ? domain : undefined} range={aligned ? range : undefined} onRangeChange={aligned ? setRange : undefined}/>
        <p className="exploration-caption">{trip.pidValues?.[item.slug]?.samples != null && `${trip.pidValues[item.slug].samples} campioni originali · `}
          {item.series.values.length} visualizzati{item.series.values.length < (trip.pidValues?.[item.slug]?.samples || 0) ? " · serie ridotta, possibili eventi brevi non mostrati" : ""}</p>
      </article>)}</div>
    </>}
    <section className="observation-timeline" aria-label="Segnali DPF e intervalli non registrati">
      <h4>DPF e continuità della registrazione</h4>
      <p className="exploration-caption">I marcatori ambra indicano campioni ECU DPF ≥ 1. Non attestano da soli l’inizio, il completamento o l’esito di una rigenerazione.</p>
      {(gaps.length > 0 || events.length > 0) && <div className="observation-strip">
        {gaps.map((gap,index) => <button type="button" key={`gap-${index}`} className="observation-gap" style={{left:`${gap.from/timelineEnd*100}%`,width:`${Math.max(0.5,(gap.to-gap.from)/timelineEnd*100)}%`}}
          onClick={() => focusEvent(gap)} aria-label={`Buco registrazione da ${OBD.measurement(gap.from/60)} a ${OBD.measurement(gap.to/60)} minuti`}/>)}
        {events.map((event,index) => <button type="button" key={`dpf-${index}`} className="observation-event" style={{left:`${Math.min(99,event.from/timelineEnd*100)}%`}}
          onClick={() => focusEvent(event)} aria-label={`Segnale ECU DPF osservato a ${OBD.measurement(event.from/60)} minuti`}/>)}
      </div>}
      <div className="exploration-caption">{events.length ? `${events.length} campioni ECU evidenziati` : "Nessun campione ECU DPF positivo con tempo consultabile"}
        {" · "}{gaps.length ? `${gaps.length} intervalli non registrati` : "Nessun intervallo localizzabile nei dati disponibili"}</div>
      {focusedEvent && <div className="exploration-notice" role="status">{focusedEvent.label}: {OBD.measurement(focusedEvent.from/60)} min
        {focusedEvent.to > focusedEvent.from ? ` → ${OBD.measurement(focusedEvent.to/60)} min` : ""}</div>}
      {gaps.length > 0 && <details><summary>Dettaglio dei buchi di registrazione</summary><ul>{gaps.map((gap,index) => <li key={index}>
        {OBD.measurement(gap.from/60)} → {OBD.measurement(gap.to/60)} min · {OBD.measurement((gap.to-gap.from)/60)} min senza osservazioni
      </li>)}</ul></details>}
      {!gaps.length && (trip?.recordingGapSeconds || 0) > 0 && <p className="exploration-caption">Il riepilogo segnala {OBD.measurement(trip.recordingGapSeconds/60)} minuti non registrati; la posizione esatta non è disponibile.</p>}
    </section>
  </section>;
};

/** Fetch both chosen records independently, retaining loading and retryable errors. */
function useComparisonDetail(trip) {
  const [state,setState] = React.useState({id:null, data:null, error:null, loading:false});
  const [retry,setRetry] = React.useState(0);
  React.useEffect(() => {
    if (!trip?.id) { setState({id:null,data:null,error:null,loading:false}); return; }
    let cancelled = false;
    setState({id:trip.id,data:null,error:null,loading:true});
    window.OBD_API.json(`/api/v1/trips/${encodeURIComponent(trip.id)}`)
      .then(data => { if (!cancelled) setState({id:trip.id,data,error:null,loading:false}); })
      .catch(error => { if (!cancelled) setState({id:trip.id,data:null,error:error.message,loading:false}); });
    return () => { cancelled = true; };
  }, [trip, retry]);
  const current = state.id === trip?.id ? state : {data:null,error:null,loading:!!trip};
  return {...current, trip:current.data || trip, retry:() => setRetry(value => value+1)};
}

/** Compare source-aware trip observations and suggest candidates using visible summaries. */
const CompareView = ({trips = [], periodLabel = "Periodo selezionato"}) => {
  const [firstId,setFirstId] = React.useState(trips[0]?.id || "");
  const [secondId,setSecondId] = React.useState(trips[1]?.id || "");
  const firstSummary = trips.find(trip => trip.id === firstId) || trips[0] || null;
  const secondSummary = trips.find(trip => trip.id === secondId && trip.id !== firstSummary?.id) || trips.find(trip => trip.id !== firstSummary?.id) || null;
  const first = useComparisonDetail(firstSummary), second = useComparisonDetail(secondSummary);
  const candidates = React.useMemo(() => OBDExploration.similarTrips(firstSummary,trips), [firstSummary,trips]);
  const a = first.trip, b = second.trip;
  const limits = OBDExploration.comparisonLimits(a,b);
  const route = OBDExploration.routeEvidence(first.data,second.data);
  const delta = (key,decimals=1) => {
    const x = a?.[key], y = b?.[key];
    if (!Number.isFinite(x) || !Number.isFinite(y)) return "—";
    const difference = y-x;
    return `${difference > 0 ? "+" : ""}${OBD.measurement(difference,decimals)}`;
  };
  const numericRows = [["Distanza","distanceKm","km"], ["Durata osservata","durationMin","min"],
    ["Tempo trascorso","elapsedDurationMin","min"], ["Temperatura esterna","airTempC","°C"],
    ["Velocità media","avgSpeedKmh","km/h"], ["Consumo in massa","gPerKm","g/km"], ["Carburante misurato","fuelConsumedL","L"]];
  return <div className="compare-view">
    <div className="exploration-heading"><div><h2>Confronta due viaggi</h2><p>{periodLabel} · confronti descrittivi con fonti e condizioni visibili.</p></div></div>
    {trips.length < 2 ? <div className="empty-state">Servono almeno due viaggi nel periodo selezionato. Amplia il periodo per confrontarli.</div> : <>
      <div className="comparison-pickers">
        <label><span className="compare-badge">A</span> Viaggio di riferimento<select value={firstSummary.id} onChange={event => setFirstId(event.target.value)}>
          {trips.map(trip => <option key={trip.id} value={trip.id}>{comparisonTripLabel(trip)}</option>)}</select></label>
        <label><span className="compare-badge secondary">B</span> Viaggio da confrontare<select value={secondSummary.id} onChange={event => setSecondId(event.target.value)}>
          {trips.filter(trip => trip.id !== firstSummary.id).map(trip => <option key={trip.id} value={trip.id}>{comparisonTripLabel(trip)}</option>)}</select></label>
      </div>
      <section className="comparison-candidates"><h3>Candidati con misure vicine</h3><p>Distanza, durata osservata e temperatura esterna quando presenti. Inizi e fini GPS entrambi entro 500 m possono favorire una proposta; la mancanza di GPS non penalizza. Estremi vicini non dimostrano lo stesso percorso.</p>
        {candidates.length === 0 && <p>Nessun candidato con misure confrontabili. I viaggi legacy incompleti sono esclusi dai suggerimenti.</p>}
        <div className="candidate-list">{candidates.map(candidate => <button type="button" key={candidate.trip.id} onClick={() => setSecondId(candidate.trip.id)}
          className={secondSummary.id === candidate.trip.id ? "selected" : ""} aria-pressed={secondSummary.id === candidate.trip.id}>
          <strong>{comparisonTripLabel(candidate.trip)}</strong><span>{candidate.compared.length}/3 condizioni disponibili · {OBD.measurement(candidate.trip.durationMin)} min · {OBD.measurement(candidate.trip.airTempC)} °C</span>
          {candidate.endpoints?.available && <span>Estremi GPS: inizi a {OBD.measurement(candidate.endpoints.startMeters,0)} m · fini a {OBD.measurement(candidate.endpoints.endMeters,0)} m{candidate.endpointBonus > 0 ? " · prossimità considerata" : ""}</span>}
        </button>)}</div>
      </section>
      {[first,second].map((state,index) => state.loading ? <div key={index} className="exploration-notice" role="status">Caricamento dettaglio viaggio {index ? "B" : "A"}…</div>
        : state.error ? <div key={index} className="exploration-notice error" role="alert">Dettaglio {index ? "B" : "A"}: {state.error} <button type="button" className="icon-btn" onClick={state.retry}>Riprova</button></div> : null)}
      <div className="comparison-table-scroll"><table className="comparison-table"><caption>Valori osservati. La differenza è B meno A.</caption>
        <thead><tr><th scope="col">Misura</th><th scope="col">A</th><th scope="col">B</th><th scope="col">Differenza</th></tr></thead>
        <tbody>{numericRows.map(([label,key,unit]) => <tr key={key}><th scope="row">{label} <small>{unit}</small></th>
          <td>{OBD.measurement(a?.[key],key === "fuelConsumedL" ? 3 : 1)}</td><td>{OBD.measurement(b?.[key],key === "fuelConsumedL" ? 3 : 1)}</td><td>{delta(key,key === "fuelConsumedL" ? 3 : 1)}</td></tr>)}
          <tr><th scope="row">Fonte del consumo</th><td>{EXPLORATION_FUEL[limits.first.source] || "Non identificata"}</td><td>{EXPLORATION_FUEL[limits.second.source] || "Non identificata"}</td><td>—</td></tr>
          <tr><th scope="row">Copertura consumo <small>%</small></th><td>{OBD.measurement(limits.first.coverage)}</td><td>{OBD.measurement(limits.second.coverage)}</td><td>—</td></tr>
          <tr><th scope="row">Distanza coperta dal consumo <small>km</small></th><td>{OBD.measurement(limits.first.distance)}</td><td>{OBD.measurement(limits.second.distance)}</td><td>—</td></tr>
          <tr><th scope="row">Resa sulla distanza coperta <small>km/L</small></th><td>{OBD.measurement(limits.first.kmL)}</td><td>{OBD.measurement(limits.second.kmL)}</td>
            <td>{limits.economyComparable ? `${limits.second.kmL-limits.first.kmL > 0 ? "+" : ""}${OBD.measurement(limits.second.kmL-limits.first.kmL)}` : "Confronto limitato"}</td></tr>
        </tbody></table></div>
      <section className="comparison-limitations"><h3>Come leggere il confronto</h3>
        {limits.notes.length > 0 ? <ul>{limits.notes.map(note => <li key={note}>{note}.</li>)}</ul> : <p>Distanza, durata, temperatura, fonte e copertura rientrano nei criteri indicati. Traffico, carico, vento e stile di guida restano non controllati.</p>}
        <p>Una differenza tra questi due viaggi non dimostra un effetto del carburante. La resa è calcolata sulla distanza effettivamente coperta dalla fonte.</p>
      </section>
      <section className="comparison-route"><h3>Confronto dei tracciati</h3>
        {route.available ? <><p>A in colore acceso, B in grigio, sullo stesso riquadro. Confronto visivo dei punti registrati.</p>
          <TripMap trip={first.data} allTrips={[first.data,second.data]} height={320}/>
          <p className="exploration-caption">Distanza tra gli inizi registrati: {OBD.measurement(route.startMeters,0)} m · tra le fini: {OBD.measurement(route.endMeters,0)} m. Inizi e fini vicini non dimostrano un percorso identico.</p></>
          : <p className="exploration-notice">{first.loading || second.loading ? "Verifica dei tracciati dopo il caricamento dei dettagli…" : first.error || second.error ? "Tracciati non verificabili: il caricamento dei dettagli non è riuscito." : "GPS non disponibile per entrambi i viaggi: non è possibile affermare che il percorso sia simile."}</p>}
      </section>
    </>}
  </div>;
};

Object.assign(window, {TripExploration, CompareView});
