# CLAUDE.md — istruzioni per il progetto

Dashboard self-hosted per i dati OBD di una **Opel Corsa F 1.5d BlueHDi (ECU
MD1CS003)**. Aggrega log **CarScanner** (`.csv`/`.brc`) e **MyOpel** (`.myop`),
li correla, applica correzioni RBS + una DPF state machine, e genera insight in
italiano. Container unico: nginx + uvicorn (FastAPI) + SQLite.

## Convenzioni di lavoro (richieste dall'utente — rispettarle SEMPRE)

1. **Commit su `main`.** Sviluppa sul branch designato dalla sessione ma fai
   atterrare il lavoro su `main` (fast-forward + push di entrambi quando sono
   allineati). Il push su `main` fa partire `docker-publish.yml` (build GHCR).
2. **Aggiorna sempre `CHANGELOG.md`** a ogni funzione/fix rilevante, prima del
   commit. Formato Keep a Changelog, in italiano, voce in cima.
3. **Documenta/spiega le nuove funzioni**: docstring su ogni nuova funzione, e
   aggiorna il `README.md` (endpoint, env, comportamento) quando cambia la
   superficie utente.
4. **Bump della versione** (stringa `vX.Y` nel brand in `frontend/app.jsx`) sui
   rilasci di rilievo, allineata alla voce del changelog.
5. **Lingua**: UI, insight e testi utente in **italiano**.

## Architettura (dove sta cosa)

- `backend/app/main.py` — FastAPI, endpoint, `data.js`, impostazioni, lifespan
  (scan directory + watcher). Settings effettive = override DB su default env
  (`effective_settings()`).
- `backend/app/database.py` — SQLite. Colonne extra aggiunte via `_EXTRA_TRIP_COLUMNS`
  (ALTER idempotente), migrazioni dati versionate con `PRAGMA user_version`.
  Tabelle: `trips`, `pid_catalog`, `ingested_files`, `settings`, `refuels`.
- `backend/app/parsers/` — `csv_parser.py` (CarScanner → trip + metriche derivate),
  `myop_parser.py` (.myop → trip).
- `backend/app/services/` — `correlator.py`, `dpf.py`, `rbs.py`, `insights.py`,
  `fuel.py` (livello per sottrazione + tank-to-tank).
- `frontend/` — React via Babel in-browser (niente build step). I dati arrivano
  come **globali JS** da `/api/v1/data.js`: `VEHICLE`, `TRIPS`, `SETTINGS`,
  `FUEL`, `PID_CATALOG`, `TREND_INSIGHTS`. Dopo una mutazione (settings/refuel)
  il frontend rilegge via `fetch` o `location.reload()`.

## Verifica prima di committare

Non c'è una suite di test formale; usa questo flusso (venv con `backend/requirements.txt`):

- **Backend**: `python -m py_compile` su tutti i file; test end-to-end con
  `fastapi.testclient.TestClient` (serve `httpx`) puntando `OBD_FILES_DIR` /
  `MYOP_FILES_DIR` / `DB_PATH` a directory temporanee. Esercita `data.js`,
  `/api/v1/fuel`, `/api/v1/settings`, i parser e la correlazione.
- **Frontend**: transpila i `.jsx` con `@babel/standalone` (preset `react`) — è
  esattamente ciò che fa il browser — per intercettare errori di sintassi JSX.

## Note dominio (dal briefing)

- `.myop` timestamp = ora locale con `Z` fittizio (non convertire da UTC).
- `fuelConsumption` = µL per-viaggio; capacità utile serbatoio ~43,5 L.
- Consumo cross-carburante in **g/km** (immune alla densità: B7 835, HVO 780 g/L).
- Il dato pompa resta il ground truth volumetrico; MyOpel/ECU assumono densità gasolio.
