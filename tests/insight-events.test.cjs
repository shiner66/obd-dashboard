"use strict";
const test = require("node:test"), assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const babel = require("@babel/standalone");
const OBD = require("../frontend/dashboard-core.js"), OBDExploration = require("../frontend/exploration-core.js");

/** Evaluate real JSX components with an inspectable React element tree and hook state. */
function componentRuntime(filename, initialState = []) {
  let index = 0;
  const state = initialState.slice(), window = {};
  const React = {
    createElement: (type, props, ...children) => ({ type, props: { ...props, children } }),
    useState(initial) { const slot = index++; if (!(slot in state)) state[slot] = typeof initial === "function" ? initial() : initial;
      return [state[slot], value => { state[slot] = typeof value === "function" ? value(state[slot]) : value; }]; },
    useEffect() {}, useRef: initial => ({ current: initial }), useMemo: fn => fn(),
  };
  const context = vm.createContext({ React, window, OBD, OBDExploration, Icon: "Icon", localStorage: { getItem() { return null; }, setItem() {} } });
  const source = fs.readFileSync(path.join(__dirname, "../frontend", filename), "utf8") + (filename === "insight-events.jsx" ? "\nwindow.TestMaintenanceForm = MaintenanceForm;" : "");
  vm.runInContext(babel.transform(source, { presets: ["react"] }).code, context);
  return { components: window, render(name, props) { index = 0; return window[name](props); }, state };
}
/** Walk JSX elements without treating hidden detail text as absent from the document. */
function elements(node) {
  if (Array.isArray(node)) return node.flatMap(elements);
  return node && typeof node === "object" ? [node, ...elements(node.props?.children)] : [];
}
/** Extract user-visible copy from an inspectable JSX tree. */
function copy(node) {
  if (Array.isArray(node)) return node.map(copy).join(" ");
  return node && typeof node === "object" ? copy(node.props?.children) : typeof node === "string" || typeof node === "number" ? String(node) : "";
}

test("insufficient data never becomes an anomaly or normal finding because of its old severity", () => {
  const model = OBD.insightPresentation({ finding: "insufficient", level: "critical", confidence: { grade: "low" } });
  assert.equal(model.finding, "insufficient"); assert.equal(model.severity, "info"); assert.equal(model.gradeLabel, "Limitata");
  assert.equal(OBD.insightPresentation({ level: "info" }).finding, "information");
  assert.equal(OBD.insightPresentation({ finding: "normal" }).gradeLabel, "Non valutata");
});

test("high severity and evidence strength remain independent and unknown grades stay unknown", () => {
  const model = OBD.insightPresentation({ finding: "anomaly", level: "critical", confidence: { grade: "low" } });
  assert.equal(model.severity, "critical"); assert.equal(model.grade, "low");
  const unknown = OBD.insightPresentation({ confidence: { grade: 0.99 }, lifecycle: { state: "invented" } });
  assert.equal(unknown.grade, "unknown"); assert.equal(unknown.lifecycleLabel, null);
});

test("real InsightCard uses observation once, includes caveats, and opens actual baseline evidence", () => {
  const runtime = componentRuntime("components.jsx"); let opened;
  const card = runtime.render("InsightCard", { insight: { finding: "anomaly", level: "warning", title: "Confronto batteria", category: "battery",
    body: "VECCHIO TESTO DA NON DUPLICARE", observation: "Avvio sotto la mediana personale.", action: "Verifica dopo una notte di sosta.", observedAt: "2026-09-13T09:20:00",
    confidence: { grade: "moderate", tripCount: 5, dayCount: 3, reasons: ["Cinque avvii osservati"] },
    baseline: { tripIds: ["baseline-real"], median: 12.6, deltaPct: -5, conditions: ["temperatura"], missingConditions: ["tempo di sosta"] },
    hypotheses: ["Sosta breve"], limitations: ["Temperatura incompleta"], counterEvidence: ["Avvio successivo regolare"],
    lifecycle: { state: "historical", firstSeen: "2026-09-01T09:00:00", lastSeen: "2026-09-13T09:20:00", observations: 5, unconfirmed: true },
    evidence: { tripIds: ["actual-trip"], pidSlugs: ["battery_v"] },
  }, onEvidence: value => { opened = value; } });
  const text = copy(card);
  assert.equal(text.includes("VECCHIO TESTO"), false);
  assert.equal(text.match(/Avvio sotto la mediana personale\./g).length, 1);
  for (const phrase of ["Solidità delle prove", "moderata", "Cosa verificare", "Prove contrarie", "tempo di sosta", "non descrive lo stato attuale", "non è una probabilità di guasto", "Esito non confermato"]) assert.ok(text.includes(phrase), phrase);
  const baseline = elements(card).find(element => element.type === "button" && copy(element) === "baseline-real");
  baseline.props.onClick(); assert.equal(opened.tripId, "baseline-real"); assert.equal(opened.pidSlug, "battery_v");
});

test("missing observation dates are explicit rather than replaced by the current date", () => {
  assert.equal(OBD.recordedDate(null), "Data non disponibile");
  assert.equal(OBD.recordedDate("invalid"), "Data non disponibile");
});

test("maintenance draft and payload preserve local timestamps, seconds and archive state", () => {
  const record = { id: 3, ts: "2026-09-13 12:39:25", type: "oil_change", odometerKm: 21500, note: "  olio e filtro  ", archived: true };
  const draft = OBD.maintenanceDraft(record);
  assert.equal(draft.ts, "2026-09-13T12:39:25");
  assert.deepEqual(OBD.maintenancePayload(draft), { ts: "2026-09-13T12:39:25", type: "oil_change", odometerKm: 21500, note: "olio e filtro", archived: true });
  assert.deepEqual(OBD.maintenancePayload({ ts: "2026-09-13T12:39", type: "other", odometerKm: "", note: "" }), { ts: "2026-09-13T12:39:00", type: "other", odometerKm: null, note: "", archived: false });
});

test("maintenance rejects impossible dates, unbounded values and invented intervention types", () => {
  const good = { ts: "2026-09-13T12:39", type: "service", odometerKm: "", note: "" };
  for (const ts of ["2026-02-30T12:39", "2026-09-13T24:00", "2026-09-13T12:39:99", "2026-09-13T12:39Z"]) assert.throws(() => OBD.maintenancePayload({ ...good, ts }), /data/);
  for (const odometerKm of [-1, "not a number", Infinity, 2000001]) assert.throws(() => OBD.maintenancePayload({ ...good, odometerKm }), /Odometro/);
  assert.throws(() => OBD.maintenancePayload({ ...good, type: "automatic_repair" }), /tipo/);
  assert.throws(() => OBD.maintenancePayload({ ...good, note: "x".repeat(2001) }), /2000/);
});

test("real maintenance form submits PUT against the ledger id and preserves a blank note", async () => {
  const runtime = componentRuntime("insight-events.jsx");
  const calls = []; let saved;
  const form = runtime.render("TestMaintenanceForm", { editing: { id: 7, ts: "2026-09-13T12:39:25", type: "battery", odometerKm: null, note: "" },
    api: async (...args) => calls.push(args), onSaved: value => { saved = value; } });
  await form.props.onSubmit({ preventDefault() {} });
  assert.equal(calls[0][0], "/api/v1/maintenance/7"); assert.equal(calls[0][1].method, "PUT");
  assert.deepEqual(JSON.parse(calls[0][1].body), { ts: "2026-09-13T12:39:25", type: "battery", odometerKm: null, note: "", archived: false });
  assert.equal(saved.type, "battery");
});

test("archive and restore controls use explicit manual ids and refresh after a successful mutation", async () => {
  const row = { id: 7, ts: "2026-09-13T12:39:25", type: "battery", odometerKm: null, note: "", archived: false };
  const payload = { maintenance: [row], events: [{ id: "maintenance:7", maintenanceId: 7, kind: "maintenance", ts: row.ts, title: "Batteria" }] };
  const initial = []; initial[0] = payload; initial[2] = false;
  const runtime = componentRuntime("insight-events.jsx", initial), calls = []; let refreshes = 0;
  const props = { scope: { query: "?from_date=2026-09-01&to_date=2026-09-13" }, api: async (...args) => calls.push(args), onMutation: async () => { refreshes += 1; }, PageControls: "Pagination" };
  let view = runtime.render("EventsView", props);
  elements(view).find(element => element.type === "button" && copy(element) === "Archivia").props.onClick();
  view = runtime.render("EventsView", props);
  await elements(view).find(element => element.type === "button" && copy(element) === "Conferma archiviazione").props.onClick();
  assert.equal(calls[0][0], "/api/v1/maintenance/7"); assert.equal(calls[0][1].method, "DELETE"); assert.equal(refreshes, 1);
  runtime.state[0] = { events: [], maintenance: [{ ...row, archived: true }] }; runtime.state[9] = "archived";
  view = runtime.render("EventsView", props);
  await elements(view).find(element => element.type === "button" && copy(element) === "Ripristina").props.onClick();
  assert.equal(calls[1][0], "/api/v1/maintenance/7"); assert.equal(calls[1][1].method, "PUT");
  assert.deepEqual(JSON.parse(calls[1][1].body), { ts: row.ts, type: "battery", odometerKm: null, note: "", archived: false });
  assert.equal(refreshes, 2);
});

test("event filters retain manual archives without inventing interventions from automatic observations", () => {
  const maintenance = [{ id: 1, ts: "2026-09-12T12:00:00", type: "service", note: "Tagliando", archived: false }, { id: 2, ts: "2026-09-11T12:00:00", type: "battery", note: "Sostituita", archived: true }];
  const payload = OBD.validateEvents({ maintenance, events: [{ id: "maintenance:1", maintenanceId: 1, kind: "maintenance", ts: maintenance[0].ts }, { id: "dpf:1", kind: "dpf", ts: "2026-09-13T12:00:00" }, { id: "refuel:1", kind: "refuel", ts: "2026-09-10T12:00:00" }] });
  assert.deepEqual(OBD.eventRecords(payload, "manual").map(event => event.id), ["maintenance:1"]);
  assert.deepEqual(OBD.eventRecords(payload, "automatic").map(event => event.id), ["dpf:1", "refuel:1"]);
  const archived = OBD.eventRecords(payload, "archived");
  assert.equal(archived.length, 1); assert.equal(archived[0].id, 2); assert.equal(archived[0].body, "Sostituita");
  assert.equal(OBD.maintenanceForEvent(payload, payload.events[0]), maintenance[0]);
  assert.equal(OBD.maintenanceForEvent(payload, archived[0]), maintenance[1]);
  assert.equal(OBD.maintenanceForEvent(payload, { kind: "dpf", id: 1 }), null);
  assert.equal(OBD.maintenanceForEvent(payload, { kind: "maintenance", id: "maintenance:1" }), null);
  assert.equal(payload.events.length, 3); assert.equal(payload.maintenance.length, 2);
});

test("incomplete event loads fail visibly instead of erasing a previously loaded history", () => {
  for (const payload of [null, {}, { events: [] }, { maintenance: [] }]) assert.throws(() => OBD.validateEvents(payload), /incompleta/);
  assert.deepEqual(OBD.eventRecords(OBD.validateEvents({ events: [], maintenance: [] })), []);
});
