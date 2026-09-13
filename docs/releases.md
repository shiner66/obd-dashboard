# Rilasci e recupero dei dati

Il push su `main` esegue test Python, regressioni frontend e compilazione JSX,
poi costruisce e pubblica `ghcr.io/shiner66/obd-dashboard`. L'immagine include
`APP_VERSION` e il commit `APP_REVISION`; il workflow produce attualmente
`linux/amd64`. La pubblicazione non aggiorna automaticamente Unraid.

## Prima del rilascio

1. Eseguire la suite e gli smoke con Python 3.12 e le dipendenze bloccate in
   `backend/requirements.txt`. Documentare le nuove funzioni, aggiornare changelog
   e versione UI/API insieme.
2. Ottenere un backup consistente SQLite tramite l'API backup di SQLite oppure
   a container fermo. Conservare sorgenti originali, archivi, impostazioni,
   rifornimenti e configurazione del container. Copiare solo `trips.db` mentre
   il DB è aperto in WAL non è un backup consistente.
3. Verificare le migrazioni e l'eventuale ricostruzione su una copia isolata.
   Non puntare mai prove di importazione al database reale.
4. Registrare digest/ID dell'immagine precedente e confrontare lo storico prima
   e dopo: conteggi per fonte, viaggi uniti, duplicati, copertura consumi, date,
   metriche e rifornimenti. Un conteggio diverso richiede una spiegazione.

## Recupero v0.7 → v0.8

La migrazione dati v9 è additiva e conserva una fotografia delle righe precedenti.
Non cancella automaticamente lo storico per riparsarlo. Le correzioni dei parser
e del merge richiedono una ricostruzione esplicita e verificata dai CSV/MyOpel
disponibili. La correzione del codice non dimostra da sola che le metriche
storiche siano state rigenerate.

La ricostruzione deve produrre un nuovo DB, mantenere quello originale, conservare
eventuali record senza sorgente recuperabile e preservare impostazioni/rifornimenti.
Un errore di parsing deve comparire nel resoconto, senza eliminare la sorgente.
I file `.brc` binari non sono supportati: esportare CSV da CarScanner.

Con la venv Python 3.12 del progetto, eseguire su copie locali:

```sh
python scripts/rebuild-archive.py \
  --source-db backup/trips.db \
  --output-db recovery/trips.db \
  --obd backup/obd-files \
  --myop backup/myop-files \
  --report recovery/report.json
```

DB e report di destinazione devono essere nuovi. Lo script conserva le revisioni
raw, sceglie l'osservazione OBD più completa e ricostruisce correlazioni e insight.
Controllare `parseErrors`, `preservedLegacy`, `withheldRawIds`, date e metriche:
un file vuoto dopo i filtri non prova che un viaggio precedente sia ricostruibile.
I record legacy incompleti restano nel DB e sono segnalati in Admin. Confrontare
inoltre il risultato prima e dopo l'avvio del container candidato: il replay
degli archivi già noti non deve cambiare lo storico.

## Aggiornamento Unraid

Attendere il successo del workflow sul commit esatto. Scaricare l'immagine e
ricreare soltanto il container della dashboard, mantenendo mount, porta e
impostazioni correnti. Il semplice riavvio non scarica l'immagine nuova.

Per un'installazione gestita dal Compose del repo:

```sh
docker compose pull obd-dashboard
docker compose up -d --no-build obd-dashboard
docker compose ps obd-dashboard
```

Per un container gestito dal template Unraid, utilizzare il suo aggiornamento
oppure una ricreazione che preservi la configurazione effettiva; non avviare
un secondo stack sugli stessi dati.

Verificare health, versione/commit, caricamento dashboard, viaggi recenti,
impostazioni e rifornimenti. Se il DB è stato ricostruito, il passaggio al nuovo
file va fatto a container fermo, conservando il vecchio DB e i relativi file WAL.

Il rollback comprende immagine precedente e, quando necessario, il relativo DB
consistente. Non assumere che una versione precedente legga correttamente uno
schema o dati migrati. Non rimuovere volumi né backup durante il rilascio.
