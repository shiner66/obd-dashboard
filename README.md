# OBD Trip Platform

Dashboard locale per la gestione dei viaggi della tua **Opel Corsa F 1.5d BlueHDi (ECU MD1CS003)**.
Aggrega ed elabora i log esportati da:

- **CarScanner** (BTLE IOS-Vlink) → file `.csv` (anche archivi `.csv.gz` già presenti)
- **MyOpel** (Stellantis) → file `.myop` ricevuti via email

Costruito per essere **self-hosted via Docker su Unraid**, in **un singolo container**.

### v0.10 — insight verificabili e registro eventi

Gli insight separano osservazioni, ipotesi, prove contrarie, limiti e azioni.
La solidità delle prove è qualitativa (limitata/moderata/elevata), non una
probabilità di guasto; la gravità resta un indicatore distinto. Le soglie
meccaniche esistenti sono euristiche e non certificano lo stato del veicolo.

Il confronto dei consumi usa almeno cinque viaggi precedenti negli ultimi
180 giorni, con fonte, copertura, distanza e velocità compatibili. Temperatura
iniziale, ambiente e stato DPF affinano il confronto quando disponibili;
le condizioni mancanti sono dichiarate e riducono la solidità. La mediana e
la dispersione robusta descrivono il riferimento, senza provare una causa.
La diagnostica usa fino a 90 giorni di storia terminanti all'ultimo viaggio
selezionato; i riepiloghi di distanza, consumi e costi restano del periodo.
Cambiare l'inizio del filtro non cambia il riferimento dello stesso viaggio.
Le prove esterne al periodo si aprono in un pannello mantenendo il filtro.

Il **Registro eventi** unisce interventi manuali, rifornimenti e osservazioni
DPF/diagnostiche con data e fonte. Cambio olio, tagliando, batteria e pneumatici
si possono annotare, modificare, archiviare e ripristinare. Gli interventi
pertinenti separano le serie diagnostiche prima/dopo senza attribuire effetti
causali. Non vengono aggiunti interventi ipotetici. Le osservazioni DPF non
sono un conteggio certificato di cicli e non provano uno spegnimento.

La memoria persistente degli avvisi inizia con le valutazioni di questa versione:
prima osservazione, ricorrente, in miglioramento, non più rilevato, in osservazione.
Il miglioramento richiede nuove prove sufficienti e una deviazione comparabile
ridotta almeno del 20% (oppure gravità inferiore). La risoluzione richiede due
nuove osservazioni normali sufficienti. Un refresh, un intervento annotato,
una correzione della stessa osservazione o dati mancanti non confermano un
recupero. La cronologia usa la data delle prove, non quella di apertura pagina;
le viste storiche sono esplicitamente distinte dallo stato memorizzato corrente.
Il motore resta deterministico e locale; non richiede un servizio LLM.

API aggiunte (timestamp locali ISO, `T`, senza fuso):

- `GET /api/v1/events?from_date=YYYY-MM-DD&to_date=YYYY-MM-DD`: cronologia e
  interventi del periodo, compreso l'archivio manuale separato.
- `GET /api/v1/maintenance`: interventi registrati; `include_archived=false`
  esclude quelli archiviati. Supporta gli stessi filtri data.
- `POST /api/v1/maintenance`, `PUT /api/v1/maintenance/{id}`: corpo completo
  `{ts, type, odometerKm?, note?, archived?}`; `type` tra `oil_change`, `service`,
  `battery`, `tyres`, `other`. Data e tipo obbligatori; nota vuota `""`, massimo
  2000 caratteri. `archived` predefinito `false`.
- `DELETE /api/v1/maintenance/{id}`: archiviazione reversibile; ripristino con
  `PUT` e `archived:false`. Identificatore inesistente: 404; payload invalido: 422.

Le nuove tabelle SQLite `maintenance_events`, `insight_states`, `insight_history`
sono additive. Backup e rollback includono il DB completo insieme al container.

### v0.9 — periodi, confronti e lettura dei dati

Il selettore condiviso permette di consultare 7 giorni, 30 giorni, il mese
corrente, un intervallo personalizzato o tutto lo storico. Viaggi, riepiloghi,
trend, mappa ed esportazione seguono lo stesso periodo. Lo stato del veicolo e
il livello stimato del serbatoio restano gli ultimi disponibili sull'intero
archivio e sono indicati come tali. La scelta del periodo viene salvata nel browser.

La dashboard è più compatta, con indicatori leggibili e accesso alle osservazioni
che sostengono gli insight. L'analisi di un viaggio permette di esplorare gli
intervalli temporali e confrontare i segnali; la vista di confronto affianca due
viaggi e rende visibili le differenze di distanza, durata, temperatura e copertura.
La vicinanza degli estremi GPS, quando disponibile, non certifica lo stesso percorso.
Un confronto osservato non dimostra la causa di una variazione di consumo.

I rifornimenti possono essere aggiunti e modificati mantenendo l'ora locale.
I dati dei pieni vengono calcolati sull'intero ledger prima di selezionare gli
intervalli chiusi nel periodo: un filtro non deve perdere il pieno di partenza.
I record legacy con sorgenti insufficienti restano consultabili con un avviso.

Il backend conserva riepiloghi aggiornabili separati dai blob PID/GPS e riusa
le importazioni già verificate. I nuovi contenuti continuano a essere conservati
e processati. Cache e riepiloghi non sostituiscono gli originali né il backup.

### v0.8 — dati verificabili e recupero dello storico

Le metriche distinguono la fonte e la copertura disponibile: i consumi MyOpel
parziali non vengono applicati all'intera sessione OBD. Le revisioni originali
dei viaggi sono conservate separatamente dalle sessioni correlate. I grafici
preservano i picchi, segnalano i dati mancanti e usano i timestamp quando disponibili.

Gli import conservano gli originali e una copia compressa verificata per hash.
Una copia ancora in corso può essere elaborata nuovamente senza perdere la coda.
Questo aumenta lo spazio dei nuovi import rispetto alla precedente conservazione
della sola copia gzip. La migrazione v9 è additiva: il recupero delle metriche
storiche dai sorgenti richiede una procedura esplicita su copia, descritta in
[`docs/releases.md`](docs/releases.md).

**Formato BRC:** il briefing contiene una specifica binaria, ma il parser di
questa applicazione supporta CSV; esportare CSV da CarScanner. Le sorgenti non
leggibili devono essere conservate e segnalate, non considerate import riusciti.

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
        /data/obd (CSV)      /data/myop (.myop)    /data/db (SQLite)
```

nginx e uvicorn girano nello stesso container, supervisionati dal `entrypoint.sh`:
se uno dei due processi muore, il container si ferma. L'eventuale riavvio dipende
dalla restart policy effettivamente configurata in Docker.

Il backend:

- elabora automaticamente i file droppati nelle directory montate (watchdog)
- applica correzione **RBS byte-swap** per i PID errati del profilo MD1CS003 (briefing §7)
- classifica il DPF (`idle / requested / active / completed / post_regen`), con
  `unknown` quando le osservazioni non permettono una conclusione
- genera insight deterministici in italiano (per viaggio e tra viaggi)
- correla i viaggi OBD con quelli MyOpel raggruppando per sessione motore-acceso (una registrazione OBD può contenere più tratte MyOpel)
- serve `/api/v1/dashboard` per i dati delle viste e mantiene `/api/v1/data.js`
  per il caricamento iniziale e la compatibilità

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
(build linux/amd64 prodotta dal workflow GitHub Actions dopo i test).

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
Il watcher attende che la copia sia stabile, quindi elabora i file in coda.
Tempi e avanzamento dipendono dalle dimensioni dei file; lo stato è visibile in Admin.

**B. Upload HTTP**:
```bash
curl -F "file=@2026-05-20 19-57-16.csv" \
     http://<unraid-ip>:8080/api/v1/upload/obd

curl -F "file=@trips-2026-05.myop" \
     http://<unraid-ip>:8080/api/v1/upload/myop
```

---

## Modalità solo-OBD (senza MyOpel)

Il file `.myop` non è obbligatorio per l'uso della piattaforma. Puoi
disattivare la sorgente MyOpel da **Admin → Impostazioni** (o con
`MYOP_ENABLED=false`): la piattaforma passa in modalità solo-OBD e usa i segnali
effettivamente presenti nei log CarScanner. Gli equivalenti sotto richiedono
che il profilo registri i PID necessari; un campo mancante resta non disponibile.

| Campo MyOpel | Equivalente OBD | Come |
|---|---|---|
| `fuelLevel` (%) | `[ECM] Fuel tank level` (sonda) **+ ledger − consumi** | stessa sonda lineare che legge MyOpel; oppure ricostruito dai rifornimenti |
| `fuelAutonomy` (km) | litri stimati × km/L recente | calcolato |
| `fuelConsumption` | integrale portata **+ metodo iniettori** `mdot = inj_q·rpm/30` | selezione secondo fonte e copertura; la pompa resta il riferimento |
| `priceFuel` / `costEur` | ledger rifornimenti (€/L manuale) | vista **Carburante** |
| `distanceToNextMaintenance` | `[ECM] Distance remaining until the next oil change` | proxy tagliando |
| `odometer` | `[ECM] Total mileage` | già nativo |
| `distance` | contatore tratta / delta odometro | già nativo |
| `alerts` | — (il CSV CarScanner non espone i DTC) | resta esclusivo MyOpel |

### Livello carburante & rifornimenti (la "soluzione estrema")

La vista **Carburante** ricostruisce il livello del serbatoio dall'ultimo
**pieno completo** meno il carburante misurato dall'OBD su ogni viaggio
successivo. Registra i pieni (litri, prezzo, tipo, odometro): con due pieni
completi consecutivi ottieni anche la **resa pieno-pieno** (km odometro ÷
litri pompa, mai i delta livello per viaggio — briefing §1), attribuita al
carburante che era *nel* serbatoio (off-by-one delle app rifornimenti corretto).
HVO e B7 sono distinti per densità (780 / 835 g/L); i confronti nel tempo usano
i **g/km** (immuni alla densità).

### Altri controlli derivati dall'OBD (briefing §2–§4)

- **Consumo in massa** `g/km` e validazione densità `g/L` per viaggio (flag fuori 750–950).
- **Minimo (idle)**: secondi, grammi e quota su tempo/carburante.
- **Ripartizione km per fasce di velocità** (0-5 / 5-30 / … / 90+ km/h).
- **Clima**: corrente media compressore e quota tempo attivo (soglia 300 mA).
- **Monitor gomme** via rapporto ruota/GPS (`ratio_wg`, min 60 campioni): alert su deriva > +0,3% in 4 settimane.
- **Filtri sanità**: `soot ∈ [0,100]`, `oil_dilution ∈ [0,20]`, coolant `> 130 °C` scartato (decode inaffidabile).

### Variabili d'ambiente aggiuntive

| Variabile | Default | Descrizione |
|---|---|---|
| `MYOP_ENABLED` | `true` | `false` → modalità solo-OBD (sovrascrivibile a runtime dalle Impostazioni) |
| `TANK_CAPACITY_L` | `43.5` | capacità utile serbatoio per il modello carburante |
| `FUEL_DENSITY_GL` | `835` | densità di riferimento per la conversione massa→volume |

---

## Endpoint API

| Metodo | Path | Descrizione |
|--------|------|-------------|
| GET  | `/api/v1/data.js`       | JavaScript con `window.TRIPS`, `window.VEHICLE`, `window.PID_CATALOG`, ecc. |
| GET  | `/api/v1/dashboard`     | Riepilogo JSON, filtro `from_date` / `to_date` inclusivo per giorno locale |
| GET  | `/api/v1/trips`         | Export completo dei viaggi, con gli stessi filtri di data opzionali |
| GET  | `/api/v1/trips/{id}`    | Dettaglio singolo viaggio (include `pidSeriesFull`, `track`, `pidValues`) |
| GET  | `/api/v1/tracks`        | Tutti i tracciati GPS, `{trip_id: [[lat,lon],…]}` (vista Mappa) |
| POST | `/api/v1/upload/obd`    | Upload `.csv` (multipart); BRC binario respinto esplicitamente |
| POST | `/api/v1/upload/myop`   | Upload `.myop` (multipart) |
| GET  | `/api/v1/settings`      | Impostazioni effettive (override DB su default env) + default |
| PUT  | `/api/v1/settings`      | Aggiorna `myop_enabled`, `tank_capacity_l`, `fuel_density_gl` |
| GET  | `/api/v1/fuel`          | Livello serbatoio (ledger − consumi / sonda OBD), resa tank-to-tank, ledger rifornimenti, valori MyOpel sospetti |
| POST | `/api/v1/refuels`       | Registra un rifornimento (`liters` obbligatorio; `odometerKm`, `pricePerL`, `fuelType`, `fullTank`, `ts`, `note`) |
| PUT | `/api/v1/refuels/{id}` | Modifica i campi del rifornimento, con validazione completa prima della scrittura |
| DELETE | `/api/v1/refuels/{id}` | Elimina un rifornimento dal ledger |
| POST | `/api/v1/admin/correlate` | Forza un passaggio di correlazione autonoma |
| GET  | `/api/v1/admin/uncorrelated` | Diagnostica: coppie candidate non correlate + correlazioni sospette (copertura km fuori norma) |
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
    ├── database.py           SQLite (trips + pid_catalog globale, blob JSON compattati)
    ├── parsers/
    │   ├── csv_parser.py     CarScanner CSV → trip dict (RBS, DPF, PID stats)
    │   └── myop_parser.py    MyOpel .myop → trip list
    └── services/
        ├── rbs.py            Correzione byte-swap MD1CS003 (§7)
        ├── dpf.py            DPF state machine (§8)
        ├── fuel.py           Ledger rifornimenti + livello per sottrazione, tank-to-tank (§1)
        ├── correlator.py     Correlazione/merge OBD↔MyOpel autonoma
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
└── docker-publish.yml      Test + build amd64 → GHCR su push a main

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
| `SOURCE_ARCHIVE`   | `gzip`                | Cosa fare dei sorgenti dopo l'ingestione: `gzip` (copia verificata in `archive/<sha256>/`, originali conservati), `keep` (non toccarli), `delete` (compatibilità: conserva gli originali e archivia come `gzip`) |

---

## Note tecniche

- **Timezone bug Stellantis**: i timestamp `.myop` hanno suffisso `Z` ma sono in realtà locali (CET/CEST). `myop_parser.py` rimuove la `Z`; il correlatore prova sia l'orario grezzo sia quello corretto di -1 h (bug DST "gruppo B" di Stellantis) per agganciare comunque la tratta alla sessione OBD giusta.
- **fuelConsumption MyOpel**: valore grezzo `/1_000_000` per ottenere i litri.
- **Slug PID**: il backend mappa i nomi lunghi dei PID (es. `[ECM] Crankshaft speed`) a slug brevi (`rpm`, `egt_a`, `soot`, ecc.) tramite `CURATED_SLUG` in `csv_parser.py`, in modo che combaciaino con quelli hardcoded nel frontend.
- **Curatela PID**: dei ~180 PID registrati dal profilo MD1CS003, ognuno è marcato `useful` se ha uno slug curato **oppure** se porta un'unità fisica reale e varia nel viaggio. I ~110 PID-rumore (flag interni ECU, segnali grezzi `MP_*`, contatori, valori costanti) restano accessibili tramite il toggle "Tutti" nel PID Explorer ma sono nascosti di default.
- **Spazio su disco**: il catalogo PID vive in una tabella globale `pid_catalog` (una riga per slug) invece di essere duplicato in ogni viaggio; le serie downsampled sono arrotondate a 2 decimali e le serie costanti non vengono salvate; le coordinate GPS sono arrotondate a 5 decimali (≈1 m). I blob JSON pesanti (track, statistiche e serie PID) sono compressi zlib in modo trasparente. Le migrazioni v6+v7 compattano i database esistenti e fanno `VACUUM` (nel corpus reale: ~20 MB → ~2 MB).
- **Ciclo di vita dei sorgenti**: dopo l'ingestione ogni CSV/.myop viene registrato nel ledger `ingested_files` (sha256 del contenuto non compresso) e, con la policy di default, copiato e verificato in `<watch_dir>/archive/<sha256>/`. Gli originali restano disponibili anche in caso di scritture tardive; file omonimi con contenuto diverso hanno archivi distinti. La scansione all'avvio legge anche gli archivi `.gz`, quindi le migrazioni future possono sempre ri-analizzare i sorgenti. La vista **Admin → Spazio su disco** mostra dimensioni e risparmio.
- **Distanza OBD**: calcolata dal contatore di tratta `Distanza percorsa:` se presente, altrimenti dal delta dell'odometro `[ECM] Total mileage` (affidabile ≥2 km), e solo come ultima risorsa dall'integrale della velocità GPS (rumoroso). Questo era la causa principale delle correlazioni mancate.
- **Correlazione OBD↔MyOpel** (3 passaggi): ① ogni tratta MyOpel viene assegnata alla sessione OBD la cui finestra temporale ne contiene l'inizio, usando **solo il timestamp grezzo**; ② le tratte rimaste orfane riprovano con il timestamp corretto di −1 h (bug DST "gruppo B" Stellantis); ③ le ultime orfane usano un punteggio pesato (tempo ±60 min + distanza ±30 % + durata) contro le sessioni OBD ancora libere — recupera le tratte gruppo B ai margini della finestra e le registrazioni partite ad adattatore già in marcia. In ogni passaggio vale un **budget di distanza**: né una singola tratta né la somma delle tratte può superare i km della sessione OBD oltre la tolleranza, così una guida estranea non viene più assorbita per caso. La somma dei km MyOpel viene salvata (`myop_distance_km`): se copre <60 % della sessione il consumo non viene derivato dal carburante Stellantis e la coppia compare tra le "correlazioni da verificare" nella vista Admin.

---

## Briefing tecnico completo

Lo specifico tecnico di partenza (formato CSV CarScanner, lista PID, formule RBS, schema DPF) è in [`project/uploads/obd-platform-briefing.md`](project/uploads/obd-platform-briefing.md).

## Licenza

Personal use — non distribuire i log o il VIN.
