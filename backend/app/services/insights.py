"""
AI Insight Engine — briefing §11.
Per-trip and cross-trip rules producing human-readable insights in Italian.
All field names match the camelCase trip dict returned by parsers/database.
"""
from __future__ import annotations


def per_trip(trip: dict) -> list[dict]:
    """Generate per-trip insights from a trip dict (camelCase keys)."""
    out = []
    sources = trip.get("sources", [])
    if "obd" not in sources:
        return out

    def _ins(category: str, level: str, title: str, body: str) -> dict:
        return {"category": category, "level": level, "title": title, "body": body}

    # Engine warm-up
    air = trip.get("airTempC")
    coolant_max = trip.get("coolantMaxC")
    if air is not None and air < 22 and coolant_max:
        out.append(_ins("engine", "info", "Profilo di riscaldamento",
            f"Motore partito freddo (~{air:.0f}°C ambiente). "
            f"Liquido raffreddamento ha raggiunto {coolant_max}°C."))

    # DPF state narrative
    state = trip.get("dpfRegenState") or "idle"
    if state == "requested":
        egt = trip.get("exhaustAfterCatC")
        if egt and egt > 450:
            regen_body = (
                f"Rigenerazione DPF richiesta. EGT massima {egt:.0f}°C — "
                "temperature FAP quasi raggiunte ma viaggio terminato prima del completamento."
            )
        else:
            regen_body = (
                "Rigenerazione DPF richiesta ma non completata — "
                "viaggio troppo breve o temperature FAP insufficienti."
            )
        out.append(_ins("dpf", "warning", "Rigenerazione DPF richiesta", regen_body))
    elif state == "active":
        out.append(_ins("dpf", "info", "Rigenerazione DPF in corso",
            "Rigenerazione attiva durante il viaggio."))
    elif state == "completed":
        out.append(_ins("dpf", "info", "Rigenerazione DPF completata",
            "Rigenerazione completata con successo durante il viaggio."))
    elif state == "post_regen":
        out.append(_ins("dpf", "info", "Post-rigenerazione",
            "Rigenerazione completata prima di questo viaggio."))

    # Soot level alert
    soot = trip.get("dpfClosedSoot")
    if soot is not None:
        if soot >= 7.0:
            out.append(_ins("dpf", "critical", f"Closed soot alto: {soot:.1f} g/L",
                "Livello soot critico. Rigenerazione urgente."))
        elif soot >= 5.0:
            out.append(_ins("dpf", "warning", f"Closed soot in aumento: {soot:.1f} g/L",
                "Livello soot elevato. Presto necessaria rigenerazione."))

    # EGT spike
    egt = trip.get("exhaustAfterCatC")
    if egt is not None and egt > 700:
        out.append(_ins("engine", "warning", "Picco EGT elevato",
            f"Temperatura gas scarico {egt:.0f}°C. Compatibile con rigenerazione attiva."))

    # Oil dilution
    dil = trip.get("oilDilutionPct")
    if dil is not None and dil > 4.0:
        out.append(_ins("engine", "critical", f"Diluizione olio {dil:.1f}%",
            "Livello critico. Verifica frequenza e completamento rigenerazioni DPF."))

    # Stop-and-Start state — only report actual faults (state 7).
    # State 9 = inibito temporaneo (normale: termica, A/C, batteria); non è un guasto.
    ss = trip.get("ssState")
    if ss == 7:
        out.append(_ins("engine", "warning", "Stop&Start — guasto rilevato",
            "Il sistema Stop&Start ha riportato un guasto. Diagnostica raccomandata."))

    # Fuel consumption context
    l100 = trip.get("consumptionL100km")
    dist = trip.get("distanceKm")
    if l100 is not None and dist is not None:
        if dist < 5 and l100 > 6.5:
            out.append(_ins("fuel", "info", f"Tragitto breve: {l100:.1f} L/100km",
                "Consumo elevato tipico del riscaldamento motore."))
        elif l100 < 4.7:
            out.append(_ins("fuel", "info", f"Consumo eccellente: {l100:.1f} L/100km",
                "Guida fluida e percorso extraurbano favoriscono l'efficienza."))

    return out


def cross_trip(trips: list[dict]) -> list[dict]:
    """Generate cross-trip trend insights from a list of trip dicts (camelCase keys)."""
    out = []
    obd = sorted(
        [t for t in trips if "obd" in t.get("sources", [])],
        key=lambda t: t.get("start") or ""
    )

    def _ins(category: str, level: str, title: str, body: str) -> dict:
        return {"category": category, "level": level, "title": title, "body": body}

    # Battery startup voltage trend
    volts = [t["batteryStartupV"] for t in obd if t.get("batteryStartupV") is not None]
    if len(volts) >= 3:
        slope = (volts[-1] - volts[0]) / len(volts)
        if slope < -0.02:
            out.append(_ins("battery", "warning", "Tensione di avviamento in calo",
                f"Ultime {len(volts)} partenze: {volts[0]:.2f}V → {volts[-1]:.2f}V. "
                "Considera diagnosi batteria."))

    # Oil dilution trend — alert only when increasing
    dil_series = [(t.get("start",""), t.get("oilDilutionPct")) for t in obd if t.get("oilDilutionPct") is not None]
    if len(dil_series) >= 3:
        vals = [v for _, v in dil_series]
        if vals[-1] > vals[0] + 0.5 and vals[-1] > 1.5:
            out.append(_ins("engine", "warning", "Diluizione olio in aumento",
                f"Trend: {vals[0]:.1f}% → {vals[-1]:.1f}% sugli ultimi {len(vals)} viaggi OBD. "
                "Verificare completamento rigenerazioni DPF."))

    # AdBlue low
    adblue = [t["adblueRangeKm"] for t in trips if t.get("adblueRangeKm") is not None]
    if adblue:
        last = adblue[-1]
        if last < 500:
            out.append(_ins("adblue", "critical", f"Autonomia AdBlue: {last:.0f} km",
                "Rifornisci a breve per evitare blocco del motore."))

    # MyOpel fuel cost summary — use costEur when already computed, or compute from fuelConsumedL
    costed = [t for t in trips if t.get("costEur") is not None]
    if len(costed) >= 3:
        total = sum(t["costEur"] for t in costed)
        out.append(_ins("fuel", "info", "Spesa carburante (MyOpel)",
            f"Ultimi {len(costed)} viaggi: €{total:.2f} totali, "
            f"media €{total/len(costed):.2f}/viaggio."))

    # Service countdown
    km_svc_vals = [t.get("kmToService") for t in trips if t.get("kmToService") is not None]
    if km_svc_vals:
        km_svc = km_svc_vals[-1]
        if km_svc is not None and km_svc < 500:
            level = "critical" if km_svc <= 0 else "warning"
            out.append(_ins("service", level, f"Tagliando tra {km_svc:.0f} km",
                "Programma il prossimo intervento presso l'officina."))

    # Consumo medio trend (ultimi 10 vs precedenti)
    l100_all = [t["consumptionL100km"] for t in trips if t.get("consumptionL100km") and t["consumptionL100km"] < 20]
    if len(l100_all) >= 10:
        recent_avg = sum(l100_all[-5:]) / 5
        older_avg  = sum(l100_all[:-5]) / (len(l100_all) - 5)
        if recent_avg > older_avg * 1.15:
            out.append(_ins("fuel", "warning", "Consumo in aumento",
                f"Media recente {recent_avg:.1f} L/100 vs storico {older_avg:.1f} L/100. "
                "Possibile usura, gonfiaggio pneumatici o guida diversa."))

    return out
