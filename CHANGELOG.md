# Changelog

Tutte le modifiche rilevanti al progetto sono annotate qui.

Formato basato su [Keep a Changelog](https://keepachangelog.com/it/1.1.0/);
il versionamento segue [SemVer](https://semver.org/lang/it/).

## [0.7.0] — 2026-07-13

### Aggiunto — Modalità solo-OBD (il file MyOpel non è più necessario)
- **Toggle sorgente MyOpel** (`MYOP_ENABLED` env + interruttore a runtime in
  **Admin → Impostazioni**). A OFF la piattaforma gira in modalità solo-OBD: il
  watcher ignora i `.myop` e livello carburante/service arrivano dall'OBD.
  Endpoint `GET`/`PUT /api/v1/settings`, tabella `settings`.
- **Vista Carburante** con ledger rifornimenti e ricostruzione del livello per
  sottrazione: ultimo pieno completo − carburante misurato dall'OBD ("soluzione
  estrema"), con fallback sulla sonda `[ECM] Fuel tank level`. Resa **tank-to-tank**
  (litri pompa ÷ km odometro, pieno-a-pieno, attribuita al carburante precedente —
  off-by-one §1). Endpoint `GET /api/v1/fuel`, `POST`/`DELETE /api/v1/refuels`,
  tabella `refuels`, servizio `services/fuel.py`.
- **Alternative OBD ai campi MyOpel**: `fuelLevelObd` (sonda serbatoio),
  `oilKmToService` (distanza al cambio olio) come proxy tagliando.

### Aggiunto — Metriche derivate dall'OBD (briefing §2–§4)
- **Carburante in massa** (`fuelMassG`, `gPerKm`) col metodo iniettori
  `mdot = inj_q·rpm/30`, con validazione densità `g/L` (target ~835).
- **Idle** (`idleSeconds`, `idleSharePct`, `idleFuelG`), **fasce di velocità**
  (`speedBandsKm`), **clima** (`acCurrentMa`, `acActivePct`, soglia 300 mA),
  **rapporto ruota/GPS** (`ratioWg`, min 60 campioni).
- Nuove card insight: costo idle/clima per viaggio, consumo `g/km`
  indipendente dalla densità, monitor pressione gomme (alert +0,3%/4 settimane).

### Modificato
- `VEHICLE` in `data.js` ora espone `fuelSource`, `fuelLiters`, `fuelCapacityL`,
  `serviceSource`, `myopEnabled`; nuovi globali `SETTINGS` e `FUEL`.
- Il gauge serbatoio in dashboard mostra la sorgente del dato (MyOpel / sonda OBD / ledger).
- `fuelConsumption` MyOpel conservato in µL grezzi (`myopFuelUl`) senza perdita di precisione.

### Corretto — Filtri sanità in ingestione (briefing §4)
- Scartati i decode impossibili: soot fuori `[0,100]`, diluizione olio fuori
  `[0,20]`, coolant `> 130 °C` (decode inaffidabile) — non inquinano più le medie.
- Flag `fc_suspect` per i valori `fuelConsumption` MyOpel stale (bug API §1).

### Documentazione
- README: sezione «Modalità solo-OBD», tabella equivalenti OBD↔MyOpel, nuovi
  endpoint e variabili d'ambiente. `docker-compose.yml` e template Unraid
  aggiornati con `MYOP_ENABLED` / `TANK_CAPACITY_L`.

---

## Storico precedente (antecedente al changelog)

Le versioni prima della 0.7.0 non erano tracciate qui; le modifiche principali,
dallo storico Git:

- **2026-07-03** — Insight consumo traffic-aware (normalizzato per tipo di percorso).
- **2026-07-03** — Motore di diagnosi predittiva, carosello viaggi mobile, redesign Trend & AI.
- **2026-07-02** — Hero cockpit UI, archiviazione sorgenti con ledger, DB compresso zlib.
- **2026-07-02** — Correlazione OBD↔MyOpel irrobustita, DB dimezzato, redesign UI.
- **2026-06-13** — Lazy-load dei dati pesanti per viaggio (data.js 11,5 MB → 0,38 MB).
- **2026-06-13** — Consumo in km/L, PID curati, correzione correlazione OBD↔MyOpel.

[0.7.0]: https://github.com/shiner66/obd-dashboard/commits/main
