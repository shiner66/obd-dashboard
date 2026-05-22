# OBD Trip Platform

Dashboard locale per la gestione dei viaggi della tua **Peugeot 308 SW 1.5 BlueHDi (ECU MD1CS003)**.
Aggrega ed elabora i log esportati da:

- **CarScanner** (BTLE IOS-Vlink) → file `.csv` / `.brc`
- **MyOpel** (Stellantis) → file `.myop` ricevuti via email

Costruito per essere **self-hosted via Docker su Unraid**, in **un singolo container**.

---

## Architettura

```
┌──────────────────────────────────────────────────────┐
│  Container: obd-dashboard (porta 8080)               │
│                                                      │
│  ┌──────────────┐   /api/   ┌──────────────────┐    │
│  │  nginx :80   │ ────────► │  uvicorn :8001   │    │
│  │  React + JSX │  (proxy)  │  FastAPI         │    │
│  │  Leaflet     │           │  SQLite          │    │
│  └──────────────┘           │  watchdog        │    │
│                             └────────┬─────────┘    │
└──────────────────────────────────────┼──────────────┘
                                       │
                  ┌────────────────────┼────────────────────┐
                  ▼                    ▼                    ▼
        /data/obd (CSV/BRC)  /data/myop (.myop)    /data/db (SQLite)
```

nginx e uvicorn girano nello stesso container, supervisionati dal `entrypoint.sh`:
se uno dei due processi muore, il container si ferma e Docker lo riavvia.

Il backend:

- elabora automaticamente i file droppati nelle directory montate (watchdog)
- applica correzione **RBS byte-swap** per i PID errati del profilo MD1CS003 (briefing §7)
- esegue la **DPF state machine** a 5 stati (`idle / requested / active / completed / post_regen`)
- genera **AI Insights** in italiano (per-viaggio e cross-trip)
- correla i viaggi OBD con quelli MyOpel (±10 min di partenza, ±25% di distanza)
- serve `/api/v1/data.js` con i globali JS che il frontend si aspetta — zero modifiche ai componenti React

---

## Installazione su Unraid

### Metodo 1 — Template Unraid (consigliato)

1. In Unraid: **Docker → Add Container**
2. Nel campo **Template** incolla:
   ```
   https://raw.githubusercontent.com/shiner66/obd-dashboard/main/unraid-templates/obd-dashboard.xml
   ```
3. Verifica i path delle 3 directory (default: `/mnt/user/data/obd-files/`, `/mnt/user/data/myop-files/`, `/mnt/user/appdata/obd-dashboard/db/`) e clicca **Apply**.
4. Apri `http://<unraid-ip>:8080/`

L'immagine `ghcr.io/shiner66/obd-dashboard:latest` viene scaricata automaticamente
(build multiplatform amd64 + arm64 prodotti dal workflow GitHub Actions).

### Metodo 2 — Docker Compose (plugin Compose Manager)

```bash
mkdir -p /mnt/user/data/obd-files /mnt/user/data/myop-files /mnt/user/appdata/obd-dashboard/db

git clone https://github.com/shiner66/obd-dashboard.git
cd obd-dashboard
docker compose up -d
```

Il `docker-compose.yml` referenzia l'immagine GHCR ma include anche la sezione `build:`,
quindi se vuoi fare modifiche locali basta:
```bash
docker compose up -d --build
```

### Metodo 3 — `docker run`

```bash
docker run -d --name obd-dashboard --restart unless-stopped \
  -p 8080:80 \
  -v /mnt/user/data/obd-files:/data/obd \
  -v /mnt/user/data/myop-files:/data/myop \
  -v /mnt/user/appdata/obd-dashboard/db:/data/db \
  ghcr.io/shiner66/obd-dashboard:latest
```

---

## Caricare i file

Due modi:

**A. Drop diretto** (più comodo): copia i file via SMB/UnRAID share nelle directory mappate.
Il watchdog li elabora entro 1-2 secondi.

**B. Upload HTTP**:
```bash
curl -F "file=@2026-05-20 19-57-16.csv" \
     http://<unraid-ip>:8080/api/v1/upload/obd

curl -F "file=@trips-2026-05.myop" \
     http://<unraid-ip>:8080/api/v1/upload/myop
```

---

## Endpoint API

| Metodo | Path | Descrizione |
|--------|------|-------------|
| GET  | `/api/v1/data.js`       | JavaScript con `window.TRIPS`, `window.VEHICLE`, `window.PID_CATALOG`, ecc. |
| GET  | `/api/v1/trips`         | JSON di tutti i viaggi (lista completa) |
| GET  | `/api/v1/trips/{id}`    | Dettaglio singolo viaggio (include `pidSeriesFull`, `track`, `pidValues`) |
| POST | `/api/v1/upload/obd`    | Upload `.csv` / `.brc` (multipart) |
| POST | `/api/v1/upload/myop`   | Upload `.myop` (multipart) |
| GET  | `/api/v1/health`        | Healthcheck |

---

## Struttura del repo

```
Dockerfile                  Immagine unica: python:3.12-slim + nginx
entrypoint.sh               Avvio supervisionato di uvicorn + nginx
nginx.conf                  Config nginx (proxy /api/ → 127.0.0.1:8001)
docker-compose.yml          Stack a un servizio per Unraid

backend/
├── requirements.txt
└── app/
    ├── main.py               FastAPI + routing + correlazione viaggi
    ├── database.py           SQLite (1 tabella + blob JSON)
    ├── parsers/
    │   ├── csv_parser.py     CarScanner CSV → trip dict (RBS, DPF, PID stats)
    │   └── myop_parser.py    MyOpel .myop → trip list
    └── services/
        ├── rbs.py            Correzione byte-swap MD1CS003 (§7)
        ├── dpf.py            DPF state machine (§8)
        ├── insights.py       Regole insight in italiano
        └── watcher.py        Watchdog file-system

frontend/                   Asset statici React/JSX
├── index.html
├── app.jsx
├── components.jsx
├── tweaks-panel.jsx
└── styles.css

unraid-templates/
└── obd-dashboard.xml       Template Unraid Community Apps

.github/workflows/
└── docker-publish.yml      Build multiplatform → GHCR su push a main

project/                    Mockup originale di Claude Design (riferimento)
```

---

## Sviluppo locale (senza Docker)

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
OBD_FILES_DIR=./data/obd MYOP_FILES_DIR=./data/myop DB_PATH=./data/trips.db \
    uvicorn app.main:app --reload --port 8001

# Frontend: qualsiasi static server, con proxy /api/ verso :8001
# Esempio con caddy in un altro terminale:
caddy run --config - <<EOF
:8080 {
    root * ./frontend
    file_server
    reverse_proxy /api/* localhost:8001
}
EOF
```

---

## Variabili d'ambiente

| Variabile          | Default               | Descrizione                              |
|--------------------|-----------------------|------------------------------------------|
| `OBD_FILES_DIR`    | `/data/obd`           | Directory watch per file CarScanner      |
| `MYOP_FILES_DIR`   | `/data/myop`          | Directory watch per file MyOpel          |
| `DB_PATH`          | `/data/db/trips.db`   | Path del file SQLite                     |

---

## Note tecniche

- **Timezone bug Stellantis**: i timestamp `.myop` hanno suffisso `Z` ma sono in realtà locali (CET/CEST). Risolto in `myop_parser.py` con offset configurabile (`_TZ_OFFSET_H = 2` per CEST estivo).
- **fuelConsumption MyOpel**: valore grezzo `/1_000_000` per ottenere i litri.
- **Slug PID**: il backend mappa i nomi lunghi dei PID (es. `[ECM] Crankshaft speed`) a slug brevi (`rpm`, `egt_a`, `soot`, ecc.) tramite `CURATED_SLUG` in `csv_parser.py`, in modo che combaciaino con quelli hardcoded nel frontend.
- **Correlazione OBD↔MyOpel**: due viaggi vengono fusi se partono entro ±10 min e la differenza di distanza è <25%. Mantengono lo stesso `id` OBD; i campi MyOpel (alerts, fuelLevel, costEur, manutenzione) vengono aggiunti retroattivamente.

---

## Briefing tecnico completo

Lo specifico tecnico di partenza (formato CSV CarScanner, lista PID, formule RBS, schema DPF) è in [`project/uploads/obd-platform-briefing.md`](project/uploads/obd-platform-briefing.md).

## Licenza

Personal use — non distribuire i log o il VIN.
