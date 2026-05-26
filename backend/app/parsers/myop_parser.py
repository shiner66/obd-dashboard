"""
MyOpel .myop parser — briefing §4.
A .myop file is plain JSON with a list of {vin, trips[]} objects.
"""
from __future__ import annotations
import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

# Trips below this distance are noise (parking maneuvers, accidental triggers).
_MIN_DISTANCE_KM = 1.0


def _to_local_iso(date_str: str) -> str:
    """Return the Stellantis timestamp as local ISO, stripping the spurious Z suffix.

    Stellantis sends Italian local time but marks it as UTC (Z). The Z is wrong —
    the value is already in local time — so we just strip it and use it as-is.
    """
    return date_str.rstrip("Z")


# Alert code → {sev, label}  (briefing §4 + data.js ALERTS — full 167-entry mapping)
ALERT_DICT: dict[int, dict] = {
    0:   {"sev": "critical", "label": "Pressione olio motore anomala"},
    1:   {"sev": "critical", "label": "Temperatura motore troppo elevata"},
    3:   {"sev": "critical", "label": "Anomalia impianto frenante"},
    4:   {"sev": "warning",  "label": "Livello liquido freni insufficiente"},
    5:   {"sev": "warning",  "label": "Anomalia servosterzo"},
    6:   {"sev": "warning",  "label": "Liquido raffreddamento insufficiente"},
    7:   {"sev": "warning",  "label": "Anomalia assistenza cambio corsia"},
    8:   {"sev": "warning",  "label": "Livello olio motore insufficiente"},
    10:  {"sev": "info",     "label": "Porta anteriore sinistra aperta"},
    11:  {"sev": "info",     "label": "Porta anteriore destra aperta"},
    12:  {"sev": "info",     "label": "Porta posteriore sinistra aperta"},
    13:  {"sev": "critical", "label": "Anomalia motore"},
    14:  {"sev": "info",     "label": "Tappo serbatoio carburante non bloccato"},
    15:  {"sev": "info",     "label": "Cofano aperto"},
    16:  {"sev": "info",     "label": "Lunotto posteriore aperto"},
    17:  {"sev": "warning",  "label": "Anomalia ESP / ASR"},
    18:  {"sev": "warning",  "label": "Anomalia batteria / alimentazione elettrica"},
    20:  {"sev": "warning",  "label": "Anomalia filtro gasolio (acqua)"},
    21:  {"sev": "warning",  "label": "Usura avanzata pastiglie freno"},
    22:  {"sev": "info",     "label": "Livello carburante basso"},
    23:  {"sev": "critical", "label": "Anomalia airbag / cinture sicurezza"},
    25:  {"sev": "warning",  "label": "Anomalia sistema antinquinamento"},
    26:  {"sev": "warning",  "label": "Anomalia ABS"},
    27:  {"sev": "warning",  "label": "Rischio intasamento FAP"},
    29:  {"sev": "warning",  "label": "Additivo FAP insufficiente"},
    30:  {"sev": "warning",  "label": "Temperatura olio cambio eccessiva"},
    31:  {"sev": "warning",  "label": "Anomalia sospensioni"},
    32:  {"sev": "info",     "label": "Preriscaldamento disattivato: batteria scarica"},
    33:  {"sev": "info",     "label": "Preriscaldamento disattivato: carburante basso"},
    34:  {"sev": "info",     "label": "Controllare luce stop centrale"},
    35:  {"sev": "warning",  "label": "Anomalia meccanismo tetto apribile"},
    36:  {"sev": "critical", "label": "Anomalia bloccasterzo"},
    37:  {"sev": "critical", "label": "Anomalia antiavviamento elettronico"},
    38:  {"sev": "warning",  "label": "Livello olio troppo alto"},
    39:  {"sev": "info",     "label": "Manovra tetto impossibile: temperatura eccessiva"},
    40:  {"sev": "info",     "label": "Manovra tetto impossibile: avviare il motore"},
    41:  {"sev": "info",     "label": "Manovra tetto impossibile: inserire freno a mano"},
    42:  {"sev": "critical", "label": "Guasto sistema ibrido — fermare il veicolo"},
    43:  {"sev": "warning",  "label": "Regolazione automatica fari difettosa"},
    44:  {"sev": "critical", "label": "Anomalia trazione ibrida"},
    45:  {"sev": "critical", "label": "Anomalia trazione ibrida — limitare velocità"},
    46:  {"sev": "info",     "label": "Liquido lavacristalli insufficiente"},
    47:  {"sev": "info",     "label": "Sostituire batteria telecomando"},
    48:  {"sev": "warning",  "label": "Anomalia presa d'aria controllata"},
    49:  {"sev": "info",     "label": "Preriscaldamento disattivato: regolare orologio"},
    50:  {"sev": "warning",  "label": "Anomalia collegamento rimorchio"},
    52:  {"sev": "warning",  "label": "Pressione pneumatici insufficiente"},
    53:  {"sev": "info",     "label": "Sensore radar di guida sporco"},
    57:  {"sev": "info",     "label": "Rigenerazione FAP in corso — elettrico non disponibile"},
    59:  {"sev": "warning",  "label": "Pneumatico anteriore sinistro forato"},
    60:  {"sev": "warning",  "label": "Pneumatico anteriore destro forato"},
    61:  {"sev": "warning",  "label": "Pneumatico posteriore sinistro forato"},
    62:  {"sev": "warning",  "label": "Pneumatico posteriore destro forato"},
    63:  {"sev": "info",     "label": "Anomalia luce posizione ant. sinistra"},
    64:  {"sev": "info",     "label": "Anomalia luce posizione ant. destra"},
    65:  {"sev": "info",     "label": "Anomalia luce posizione post. destra"},
    66:  {"sev": "info",     "label": "Anomalia luce posizione post. sinistra"},
    67:  {"sev": "info",     "label": "Anomalia anabbaglianti sinistra"},
    68:  {"sev": "info",     "label": "Anomalia anabbaglianti destra"},
    69:  {"sev": "info",     "label": "Anomalia abbaglianti sinistra"},
    70:  {"sev": "info",     "label": "Anomalia abbaglianti destra"},
    71:  {"sev": "info",     "label": "Anomalia luce stop post. destra"},
    72:  {"sev": "info",     "label": "Anomalia luce stop post. sinistra"},
    73:  {"sev": "info",     "label": "Anomalia fendinebbia posteriori"},
    74:  {"sev": "info",     "label": "Anomalia fendinebbia anteriori"},
    75:  {"sev": "info",     "label": "Anomalia fendinebbia post. destra"},
    76:  {"sev": "info",     "label": "Anomalia fendinebbia posteriori"},
    77:  {"sev": "info",     "label": "Anomalia indicatore direzione ant. destro"},
    78:  {"sev": "info",     "label": "Anomalia indicatore direzione post. destro"},
    79:  {"sev": "info",     "label": "Anomalia indicatore direzione ant. sinistro"},
    80:  {"sev": "info",     "label": "Anomalia indicatore direzione post. sinistro"},
    81:  {"sev": "info",     "label": "Anomalia luce retromarcia"},
    82:  {"sev": "info",     "label": "Anomalia lampada luce retromarcia sinistra"},
    90:  {"sev": "warning",  "label": "Cofano attivo difettoso"},
    91:  {"sev": "warning",  "label": "Anomalia sistema parcheggio assistito"},
    92:  {"sev": "warning",  "label": "Sistema rilevamento parcheggio difettoso"},
    94:  {"sev": "warning",  "label": "Pressione insufficiente pneumatico ant. sinistro"},
    95:  {"sev": "warning",  "label": "Pressione insufficiente pneumatico ant. destro"},
    96:  {"sev": "warning",  "label": "Pressione insufficiente pneumatico post. destro"},
    97:  {"sev": "warning",  "label": "Pressione insufficiente pneumatico post. sinistro"},
    98:  {"sev": "info",     "label": "Spegnere i fari"},
    100: {"sev": "warning",  "label": "Anomalia antinquinamento"},
    101: {"sev": "critical", "label": "Anomalia antinquinamento — non avviare il veicolo"},
    102: {"sev": "info",     "label": "Anomalia antinquinamento (TCU)"},
    103: {"sev": "info",     "label": "Cintura passeggero post. sinistra non allacciata"},
    104: {"sev": "info",     "label": "Cintura passeggero post. centrale non allacciata"},
    106: {"sev": "info",     "label": "Inserire cambio automatico in posizione P"},
    107: {"sev": "info",     "label": "Rischio ghiaccio"},
    108: {"sev": "info",     "label": "Porta anteriore destra aperta"},
    109: {"sev": "info",     "label": "Porta anteriore sinistra aperta"},
    110: {"sev": "info",     "label": "Porta posteriore destra aperta"},
    111: {"sev": "info",     "label": "Porta posteriore sinistra aperta"},
    112: {"sev": "info",     "label": "Porta bagagliaio aperta"},
    113: {"sev": "warning",  "label": "Anomalia sistema rilevamento collisione"},
    114: {"sev": "info",     "label": "Lunotto posteriore aperto"},
    115: {"sev": "info",     "label": "Tappo serbatoio carburante non bloccato"},
    123: {"sev": "critical", "label": "Anomalia freno stazionamento / antiarretramento"},
    124: {"sev": "warning",  "label": "Alettone mobile difettoso — velocità limitata"},
    125: {"sev": "warning",  "label": "Anomalia sistema frenata automatica"},
    126: {"sev": "warning",  "label": "Anomalia luci di curva"},
    133: {"sev": "warning",  "label": "Anomalia cambio"},
    137: {"sev": "warning",  "label": "Anomalia avviso superamento corsia"},
    139: {"sev": "warning",  "label": "Pressione pneumatico insufficiente"},
    140: {"sev": "warning",  "label": "Comando freno stazionamento difettoso"},
    141: {"sev": "critical", "label": "Malfunzionamento sistema controllo motore"},
    142: {"sev": "warning",  "label": "Anomalia sospensione — velocità max 90 km/h"},
    148: {"sev": "warning",  "label": "Anomalia sensore pressione pneumatico ant. sx"},
    149: {"sev": "warning",  "label": "Anomalia sensore pressione pneumatico ant. dx"},
    150: {"sev": "warning",  "label": "Anomalia sensore pressione pneumatico post. dx"},
    151: {"sev": "warning",  "label": "Anomalia sensore pressione pneumatico post. sx"},
    152: {"sev": "warning",  "label": "Anomalia sospensione — limitare a 90 km/h"},
    153: {"sev": "warning",  "label": "Anomalia servosterzo"},
    156: {"sev": "warning",  "label": "Anomalia misurazione distanza intervehicolare"},
    157: {"sev": "critical", "label": "Anomalia motore — fermare il veicolo"},
    158: {"sev": "warning",  "label": "Anomalia sistema mantenimento in corsia"},
    159: {"sev": "warning",  "label": "Anomalia monitoraggio pressione pneumatici"},
    160: {"sev": "warning",  "label": "Pneumatico posteriore destro sgonfio"},
    161: {"sev": "warning",  "label": "Pneumatico posteriore sinistro sgonfio"},
    162: {"sev": "warning",  "label": "Pneumatico anteriore destro sgonfio"},
    163: {"sev": "warning",  "label": "Pneumatico anteriore sinistro sgonfio"},
    164: {"sev": "info",     "label": "Frenata automatica disattivata"},
    165: {"sev": "critical", "label": "Additivo antinquinamento insufficiente — non avviare"},
    166: {"sev": "warning",  "label": "Additivo antinquinamento insufficiente — rabboccare"},
    167: {"sev": "warning",  "label": "Additivo antinquinamento insufficiente"},
}


def parse_file(path: str | Path) -> list[dict]:
    """
    Parse a .myop file and return a list of trip dicts in frontend format.
    """
    path = Path(path)
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        data = [data]

    trips: list[dict] = []
    skipped = 0
    for vehicle_block in data:
        vin = vehicle_block.get("vin", "")
        for raw in vehicle_block.get("trips", []):
            trip = _parse_trip(raw, vin)
            if (trip.get("distanceKm") or 0) < _MIN_DISTANCE_KM:
                skipped += 1
                continue
            trips.append(trip)

    if skipped:
        log.info("myop %s: skipped %d sub-%.0fkm trips", path.name, skipped, _MIN_DISTANCE_KM)
    return trips


def _parse_trip(raw: dict, vin: str) -> dict:
    trip_id = raw.get("id", 0)
    start_raw = raw.get("start", {}).get("date", "")
    end_raw   = raw.get("end",   {}).get("date", "")
    start_iso = _to_local_iso(start_raw)
    end_iso   = _to_local_iso(end_raw)

    start_odo = raw.get("start", {}).get("mileage", 0)
    end_odo   = raw.get("end",   {}).get("mileage", 0)

    travel_s   = raw.get("travelTime", 0) or 0
    distance   = raw.get("distance", 0.0) or 0.0
    fuel_raw   = raw.get("fuelConsumption", 0) or 0   # divide by 1_000_000 → litres
    fuel_l     = fuel_raw / 1_000_000
    price_fuel = raw.get("priceFuel")
    alerts_raw = raw.get("alerts") or []

    avg_speed = distance / (travel_s / 3600) if travel_s > 0 else None
    l100km    = fuel_l / distance * 100 if distance > 0 and fuel_l > 0 else None
    cost      = fuel_l * price_fuel if fuel_l and price_fuel else None

    return {
        "id":                f"myop-{trip_id}",
        "sources":           ["myopel"],
        "myopId":            trip_id,
        "vin":               vin,
        "start":             start_iso,
        "end":               end_iso,
        "durationMin":       round(travel_s / 60, 1),
        "distanceKm":        round(distance, 2),
        "avgSpeedKmh":       round(avg_speed, 1) if avg_speed else None,
        "fuelConsumedL":     round(fuel_l, 3),
        "consumptionL100km": round(l100km, 2) if l100km else None,
        "fuelLevel":         raw.get("fuelLevel"),
        "fuelAutonomy":      raw.get("fuelAutonomy"),
        "priceFuel":         price_fuel,
        "costEur":           round(cost, 2) if cost else None,
        "odometerKm":        end_odo or start_odo,
        "alerts":            [int(a) for a in alerts_raw if isinstance(a, (int, float))],
        "daysToService":     raw.get("daysUntilNextMaintenance"),
        "kmToService":       raw.get("distanceToNextMaintenance"),
        "maintenancePassed": raw.get("maintenancePassed", False),
        "track":             None,   # MyOpel has no GPS waypoints
        "pidValues":         None,
        "pidSeriesFull":     None,
        "pidCatalog":        [],
        "dpfRegenState":     None,
        "insights":          [],
    }
