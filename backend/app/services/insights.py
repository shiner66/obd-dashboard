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
    soot = trip.get("dpfSootPct")
    if soot is not None:
        if soot >= 90:
            out.append(_ins("dpf", "critical", f"DPF intasato al {soot:.0f}%",
                "Rigenerazione urgente. Pianifica un viaggio extraurbano di ≥30 min."))
        elif soot >= 70:
            out.append(_ins("dpf", "warning", f"DPF al {soot:.0f}%",
                "Presto necessaria rigenerazione. Evita tragitti urbani brevi consecutivi."))

    # Short-term regen capability drop — only fire when ST is notably lower than LT
    # and the absolute value is below threshold (avoids spurious alerts on normal operation)
    st = trip.get("dpfRegenCapabilityST")
    lt = trip.get("dpfRegenCapability")
    if st is not None and lt is not None and lt > 0 and st < lt * 0.7 and st < 80:
        out.append(_ins("dpf", "warning",
            "Capacità rigenerazione a breve termine ridotta",
            f"ST {st:.0f}% vs LT {lt:.0f}%. Possibile problema con la rigenerazione post-guida."))

    # EGT spike
    egt = trip.get("exhaustAfterCatC")
    if egt is not None and egt > 700:
        out.append(_ins("engine", "warning", "Picco EGT elevato",
            f"Temperatura gas scarico {egt:.0f}°C. Compatibile con rigenerazione attiva."))

    # Oil dilution
    dil = trip.get("oilDilutionPct")
    if dil is not None:
        if dil > 3.0:
            out.append(_ins("engine", "critical", f"Diluizione olio {dil:.1f}%",
                "Verifica se le rigenerazioni DPF sono frequenti o incomplete."))
        elif dil > 1.5:
            out.append(_ins("engine", "warning", f"Diluizione olio {dil:.1f}%",
                "Monitorare nel tempo."))

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

    # Regen interval stability
    intervals = [t["dpfAvgRegenKm"] for t in obd if t.get("dpfAvgRegenKm") is not None]
    if len(intervals) >= 3:
        out.append(_ins("dpf", "info", "Intervallo medio rigenerazioni",
            f"Stabile a {intervals[-1]:.0f} km. "
            f"Range ultimi {len(intervals)}: {min(intervals):.0f}–{max(intervals):.0f} km."))

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

    return out
