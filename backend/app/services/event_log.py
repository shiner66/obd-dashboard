"""Read-only timeline joining manual interventions and dated source observations."""
from __future__ import annotations

TITLES = {"oil_change": "Cambio olio", "service": "Tagliando", "battery": "Intervento batteria",
          "tyres": "Intervento pneumatici", "other": "Altro intervento"}


def build_timeline(maintenance: list[dict], refuels: list[dict], trips: list[dict], history: list[dict]) -> list[dict]:
    """Build stable event identities without claiming inferred DPF episodes or manual work."""
    events = [{"id": f"maintenance:{event['id']}", "maintenanceId": event["id"], "kind": "maintenance",
               "ts": event["ts"], "title": TITLES[event["type"]], "body": event.get("note", ""),
               "eventType": event["type"], "odometerKm": event.get("odometerKm"), "note": event.get("note", ""),
               "source": "utente", "archived": False, "editable": True}
              for event in maintenance if not event.get("archived")]
    for refuel in refuels:
        if refuel.get("ts"):
            events.append({"id": f"refuel:{refuel['id']}", "kind": "refuel", "ts": refuel["ts"],
                           "title": "Rifornimento registrato", "body": f"{refuel['liters']:g}".replace(".", ",") + f" L · {refuel.get('fuelType') or 'carburante non indicato'}",
                           "source": "registro rifornimenti", "editable": False})
    labels = {"active": "Rigenerazione attiva all’ultima osservazione", "requested": "Richiesta rigenerazione osservata",
              "completed": "Completamento rigenerazione rilevato", "post_regen": "Fase successiva alla rigenerazione rilevata"}
    for trip in trips:
        state = trip.get("dpfRegenState")
        if (state not in labels or trip.get("legacyIncomplete") or "obd" not in trip.get("sources", [])
                or not trip.get("start")):
            continue
        events.append({"id": f"dpf:{trip['id']}", "kind": "dpf", "ts": trip.get("end") or trip["start"],
                       "title": labels[state], "body": "Osservazione dai sensori di questo viaggio. Non equivale al conteggio dei cicli né prova uno spegnimento del motore.",
                       "source": "sensori OBD", "tripId": trip["id"], "editable": False})
    dates = {trip["id"]: trip.get("start") or "" for trip in trips}
    for event in history:
        card = event.get("card") or event.get("lastCard") or {}
        ids = (card.get("evidence") or {}).get("tripIds", [])
        measured_ids = [identifier for identifier in ids if dates.get(identifier) and dates[identifier] <= event["observedAt"]]
        trip_id = max(measured_ids, key=lambda identifier: dates[identifier]) if measured_ids else None
        events.append({"id": f"insight:{event['historyId']}", "kind": "insight", "ts": event["observedAt"],
                       "title": card.get("title", "Osservazione diagnostica"), "body": card.get("observation") or card.get("body", ""),
                       "source": "analisi dati", "tripId": trip_id, "editable": False,
                       "lifecycle": {"state": event["state"], "unconfirmed": event.get("unconfirmed", True)},
                       "ruleId": event["ruleId"]})
    return sorted(events, key=lambda event: (event["ts"], event["id"]), reverse=True)
