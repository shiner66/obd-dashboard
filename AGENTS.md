# OBD Dashboard

Leggere e applicare `CLAUDE.md`: le convenzioni valgono anche per Codex.
Prima dei rilasci seguire `docs/releases.md`. Gli originali importati e le loro
revisioni sono dati da preservare; il database materializzato è ricostruibile
solo quando la disponibilità delle sorgenti è stata verificata.

Eseguire i test Python con `PYTHONPATH=backend python -m pytest -q`, i test
frontend con `node --test tests/frontend.test.cjs`, quindi la compilazione Python
e `node scripts/check-frontend.cjs`. Il push su main pubblica un'immagine GHCR
solo dopo il job di verifica; verificare il commit effettivo nell'immagine.
