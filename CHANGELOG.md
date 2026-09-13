# Changelog

Tutte le modifiche rilevanti al progetto sono annotate qui.

Formato basato su [Keep a Changelog](https://keepachangelog.com/it/1.1.0/);
il versionamento segue [SemVer](https://semver.org/lang/it/).

## [0.9.0] — 2026-09-13

### Aggiunto
- Periodo condiviso tra le viste e l'export: 7/30 giorni, mese corrente,
  intervallo personalizzato e tutto lo storico; preferenza salvata nel browser.
- Analisi dei segnali con selezione dell'intervallo, confronto tra PID e
  indicazioni degli eventi osservati e dei buchi di registrazione.
- Confronto tra viaggi con distanza, durata, temperatura e qualità dei consumi;
  la somiglianza delle condizioni è distinta da una spiegazione causale.
- Collegamenti dagli insight ai viaggi e ai PID che sostengono le conclusioni.
- Modifica dei rifornimenti tramite interfaccia e API `PUT /refuels/{id}`.

### Corretto
- Formato della data dei rifornimenti allineato tra modulo e validazione API;
  errori leggibili nel modulo, senza perdere i dati inseriti.
- Riepiloghi del carburante nel periodo costruiti dopo il calcolo pieno-pieno
  sull'intero ledger; lo stato corrente del serbatoio resta distinto dal periodo.
- Avvisi legacy persistenti: le sorgenti possono essere presenti ma insufficienti
  per un ricalcolo attendibile. Gli originali e i record precedenti sono preservati.

### Modificato
- Dashboard compatta, gerarchia visiva, contrasto e testi italiani; liste suddivise
  in pagine per limitare il lavoro di rendering.
- Riepiloghi SQLite separati dai blob PID/GPS, invalidazione al cambio dei dati
  e riuso delle importazioni già verificate per ridurre letture ripetute.
- Stato veicolo globale distinto dai dati del periodo; risposte tardive di un
  vecchio filtro non sostituiscono il periodo più recente selezionato.

## [0.8.0] — 2026-09-13

### Corretto
- Consumi e insight: i litri di una tratta MyOpel parziale non diventano più il
  consumo dell'intera sessione OBD; fonti e copertura accompagnano la selezione.
- Merge e correlazione: conservazione delle revisioni originali e aggregazione
  delle metriche dei segmenti; le tratte MyOpel ricevute in momenti diversi
  restano recuperabili e possono essere ricalcolate.
- Parsing: gestione dei nomi con suffissi d'importazione, buchi temporali
  distinti dal tempo osservato e segnali DPF allineati nel tempo.
- Importazione: archivi per hash verificati, originali conservati, attesa di
  stabilità delle copie, elaborazione serializzata e destinazione upload validata.
- API: rifiuto di valori negativi/non finiti, impostazioni atomiche, aggiornamento
  del watcher MyOpel al cambio sorgente e segnalazione esplicita degli errori.
- Grafici: rimossa la pulizia statistica indiscriminata che eliminava picchi EGT
  validi; dati assenti distinti dallo zero e selezione coerente con i filtri.
- Rifornimenti: ora locale nel modulo e media pieno-pieno ponderata per litri.

### Aggiunto
- Endpoint JSON per aggiornare i dati delle viste, versione/revisione API,
  informazioni sulla qualità delle osservazioni e controllo di disponibilità.
- Script di recupero su un nuovo database, con conservazione del precedente e
  report dei file elaborati e delle righe recuperate.
- Suite di regressione backend/frontend e controlli obbligatori prima del push GHCR.

### Modificato
- Hero con spazio distinto per l'auto, controlli accessibili, preferenze salvate,
  aggiornamento dei dati tra viste e indicazione dell'ultima acquisizione.
- Dipendenze runtime fissate alle versioni verificate nel container esistente.
- Documentazione allineata a Opel Corsa F e build linux/amd64. Il formato binario
  BRC non è supportato: usare l'esportazione CSV.
- La policy gzip conserva anche l'originale per proteggere dalle copie incomplete;
  il vecchio valore `SOURCE_ARCHIVE=delete` usa lo stesso comportamento conservativo.
  I nuovi import possono quindi occupare più spazio delle sole copie compresse.

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
