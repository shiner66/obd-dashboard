"""
AI Insight Engine.

Two layers:
  • per_trip(trip, ctx)   — concrete, per-trip notes shown in the trip detail.
  • cross_trip(trips)     — the dashboard "trend" cards: predictive DPF, fuel
                            economy (km/L) vs your own history, cost, battery,
                            AdBlue, oil dilution, service.

Design principle: a healthy car rarely trips an absolute alarm threshold, which
is why fixed-threshold insights felt empty. Instead we compare each value to the
vehicle's *own* history and always surface something useful and quantified.
All text is Italian. Consumption is expressed in km/L (chilometri al litro).
"""
from __future__ import annotations

import statistics
from datetime import datetime


# ── helpers ───────────────────────────────────────────────────────────────────

def _ins(category: str, level: str, title: str, body: str) -> dict:
    return {"category": category, "level": level, "title": title, "body": body}


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s[:19])
    except (ValueError, TypeError):
        return None


def _km_l(trip: dict) -> float | None:
    """km/L for a trip, from the stored value or distance/fuel."""
    if trip.get("consumptionKmL"):
        return trip["consumptionKmL"]
    l100 = trip.get("consumptionL100km")
    if l100 and l100 > 0:
        return round(100.0 / l100, 2)
    d, f = trip.get("distanceKm"), trip.get("fuelConsumedL")
    if d and f and f > 0:
        return round(d / f, 2)
    return None


def _fmt_date(s: str | None) -> str:
    dt = _parse_dt(s)
    if not dt:
        return "—"
    months = ["gen", "feb", "mar", "apr", "mag", "giu",
              "lug", "ago", "set", "ott", "nov", "dic"]
    return f"{dt.day} {months[dt.month - 1]}"


# ── shared context (computed once, fed to per_trip for personal comparison) ───

def build_context(trips: list[dict]) -> dict:
    """Aggregate stats used to compare a single trip against the car's history."""
    kml = [k for t in trips if (k := _km_l(t)) and 2 < k < 60]
    obd = [t for t in trips if "obd" in t.get("sources", [])]
    avg_regen = next((t["dpfAvgRegenKm"] for t in reversed(obd)
                      if t.get("dpfAvgRegenKm")), None)
    return {
        "median_kml": round(statistics.median(kml), 1) if kml else None,
        "avg_regen_km": avg_regen,
        "n": len(trips),
    }


# ── per-trip ──────────────────────────────────────────────────────────────────

def per_trip(trip: dict, ctx: dict | None = None) -> list[dict]:
    """Per-trip insights. Quantified and compared to the car's own history."""
    out: list[dict] = []
    ctx = ctx or {}
    is_obd = "obd" in trip.get("sources", [])

    # ── Fuel economy vs your median (works for OBD and MyOpel trips) ──────────
    kml = _km_l(trip)
    med = ctx.get("median_kml")
    dist = trip.get("distanceKm") or 0
    if kml:
        if med and dist >= 3:
            delta = (kml - med) / med * 100
            if delta >= 12:
                out.append(_ins("fuel", "info", f"Viaggio efficiente · {kml:.1f} km/L",
                    f"Il {abs(delta):.0f}% meglio della tua media ({med:.1f} km/L). "
                    "Guida fluida e percorso scorrevole."))
            elif delta <= -18:
                out.append(_ins("fuel", "warning", f"Consumo elevato · {kml:.1f} km/L",
                    f"Il {abs(delta):.0f}% sotto la tua media ({med:.1f} km/L). "
                    "Tipico di traffico, partenza a freddo o tragitto urbano."))
            else:
                out.append(_ins("fuel", "info", f"Consumo nella norma · {kml:.1f} km/L",
                    f"In linea con la tua media ({med:.1f} km/L)."))
        else:
            out.append(_ins("fuel", "info", f"Consumo · {kml:.1f} km/L",
                f"≈ {100/kml:.1f} L/100 km su questo viaggio."))

    if not is_obd:
        return out

    # ── Cold-start warm-up ───────────────────────────────────────────────────
    air = trip.get("airTempC")
    coolant_max = trip.get("coolantMaxC")
    if air is not None and air < 18 and coolant_max:
        warmed = coolant_max >= 80
        out.append(_ins("engine", "info", "Partenza a freddo",
            f"Avvio a ~{air:.0f}°C ambiente; liquido salito a {coolant_max:.0f}°C — "
            f"{'temperatura di esercizio raggiunta' if warmed else 'motore non del tutto in temperatura, consumo penalizzato'}."))

    # ── DPF regeneration narrative ───────────────────────────────────────────
    state = trip.get("dpfRegenState") or "idle"
    since = trip.get("dpfSinceRegenKm")
    egt = trip.get("exhaustAfterCatC")
    if state == "active":
        out.append(_ins("dpf", "info", "Rigenerazione DPF in corso",
            f"Rigenerazione attiva durante il viaggio"
            + (f", EGT fino a {egt:.0f}°C." if egt else ".")
            + " Non spegnere il motore se possibile, per farla completare."))
    elif state == "completed":
        out.append(_ins("dpf", "info", "Rigenerazione DPF completata",
            "Ciclo completato con successo: il soot è stato bruciato. "
            "Il contatore km riparte da zero."))
    elif state == "requested":
        if dist < 8:
            out.append(_ins("dpf", "warning", "Rigenerazione richiesta ma viaggio breve",
                "L'ECU ha richiesto una rigenerazione ma il tragitto è troppo corto per "
                "raggiungere le temperature. Ripetuti viaggi brevi accumulano soot."))
        else:
            out.append(_ins("dpf", "warning", "Rigenerazione DPF richiesta",
                f"Richiesta in corso{f', EGT post-cat {egt:.0f}°C' if egt else ''}. "
                "Un tratto a velocità costante (extraurbano) aiuta a completarla."))
    elif state == "post_regen":
        out.append(_ins("dpf", "info", "Post-rigenerazione",
            "Rigenerazione completata poco prima di questo viaggio."))

    # ── Short trip + soot building (the #1 diesel/DPF risk) ───────────────────
    soot = trip.get("dpfClosedSoot")
    if dist < 5 and state in ("idle", "requested") and soot is not None and soot >= 4:
        out.append(_ins("dpf", "warning", "Viaggio breve con soot in accumulo",
            f"Solo {dist:.1f} km e closed soot a {soot:.1f} g/L senza rigenerazione. "
            "Programma ogni tanto un tratto extraurbano per far pulire il filtro."))
    elif soot is not None and soot >= 7:
        out.append(_ins("dpf", "warning", f"Closed soot alto · {soot:.1f} g/L",
            "Vicino alla soglia di rigenerazione: attesa a breve."))

    # ── EGT spike outside regen ──────────────────────────────────────────────
    if egt is not None and egt > 700 and state not in ("active", "completed"):
        out.append(_ins("engine", "info", f"Picco EGT {egt:.0f}°C",
            "Temperatura gas di scarico elevata, compatibile con guida sostenuta."))

    # ── Oil dilution (diesel: fuel dilutes oil during regens) ────────────────
    dil = trip.get("oilDilutionPct")
    if dil is not None and dil > 5.0:
        out.append(_ins("engine", "critical", f"Diluizione olio {dil:.1f}%",
            "Livello elevato: verifica frequenza/completamento rigenerazioni e valuta cambio olio."))

    # ── Stop&Start genuine fault only ────────────────────────────────────────
    if trip.get("ssState") == 7:
        out.append(_ins("engine", "warning", "Stop&Start — guasto",
            "Il sistema Stop&Start ha riportato un guasto. Diagnostica consigliata."))

    return out


# ── cross-trip (dashboard trend cards) ────────────────────────────────────────

def cross_trip(trips: list[dict]) -> list[dict]:
    """The dashboard's trend insights — quantified, predictive, always useful."""
    out: list[dict] = []
    obd = sorted([t for t in trips if "obd" in t.get("sources", [])],
                 key=lambda t: t.get("start") or "")
    chrono = sorted(trips, key=lambda t: t.get("start") or "")

    # ── 1. DPF — predict next regeneration ───────────────────────────────────
    since = next((t.get("dpfSinceRegenKm") for t in reversed(obd)
                  if t.get("dpfSinceRegenKm") is not None), None)
    avg_regen = next((t.get("dpfAvgRegenKm") for t in reversed(obd)
                      if t.get("dpfAvgRegenKm")), None)
    if since is not None and avg_regen and avg_regen > 20:
        remaining = avg_regen - since
        pct = since / avg_regen * 100
        if remaining > 0:
            lvl = "info" if pct < 90 else "warning"
            out.append(_ins("dpf", lvl, "Prossima rigenerazione DPF",
                f"{since:.0f} km dall'ultima · intervallo medio {avg_regen:.0f} km "
                f"({pct:.0f}% del ciclo). Stimata tra ~{remaining:.0f} km."))
        else:
            out.append(_ins("dpf", "warning", "Rigenerazione DPF imminente",
                f"Già {since:.0f} km dall'ultima, oltre l'intervallo medio "
                f"({avg_regen:.0f} km). Favorisci un tratto extraurbano."))

    # ── 2. DPF — soot health ─────────────────────────────────────────────────
    soot = next((t.get("dpfClosedSoot") for t in reversed(obd)
                 if t.get("dpfClosedSoot") is not None), None)
    if soot is not None:
        if soot >= 7:
            out.append(_ins("dpf", "warning", f"Closed soot {soot:.1f} g/L",
                "Carico soot alto: rigenerazione attesa a breve. Evita di interrompere il viaggio se parte."))
        else:
            out.append(_ins("dpf", "info", f"Closed soot {soot:.1f} g/L",
                "Il filtro si svuota con la rigenerazione intorno a ~8 g/L. Valore nella norma."))

    # ── 3. Fuel economy in km/L vs your history ──────────────────────────────
    kml_series = [(t.get("start"), k) for t in chrono if (k := _km_l(t)) and 2 < k < 60]
    if len(kml_series) >= 3:
        vals = [v for _, v in kml_series]
        med = statistics.median(vals)
        best_v, best_s = max(((v, s) for s, v in kml_series), key=lambda x: x[0])
        body = (f"Media {med:.1f} km/L su {len(vals)} viaggi · "
                f"miglior viaggio {best_v:.1f} km/L ({_fmt_date(best_s)}).")
        level = "info"
        if len(vals) >= 8:
            recent = statistics.mean(vals[-5:])
            older = statistics.mean(vals[:-5])
            if recent < older * 0.9:
                level = "warning"
                body += (f" Ultimi 5 viaggi {recent:.1f} km/L, "
                         f"{(older-recent)/older*100:.0f}% sotto lo storico ({older:.1f}): "
                         "controlla pneumatici, filtri o stile di guida.")
            elif recent > older * 1.08:
                body += f" In miglioramento: ultimi 5 a {recent:.1f} km/L."
        out.append(_ins("fuel", level, "Efficienza carburante", body))

    # ── 4. Fuel cost ─────────────────────────────────────────────────────────
    costed = [t for t in chrono if t.get("costEur") and t.get("distanceKm")]
    if len(costed) >= 4:
        total_cost = sum(t["costEur"] for t in costed)
        total_km = sum(t["distanceKm"] for t in costed)
        per100 = total_cost / total_km * 100 if total_km else 0
        # last 30 calendar days relative to the most recent trip
        last_dt = _parse_dt(costed[-1].get("start"))
        recent = [t for t in costed
                  if last_dt and (sd := _parse_dt(t.get("start")))
                  and (last_dt - sd).days <= 30]
        recent_cost = sum(t["costEur"] for t in recent)
        body = f"€{per100:.2f}/100 km in media · €{total_cost:.0f} totali su {len(costed)} viaggi."
        if recent:
            body += f" Ultimi 30 giorni: €{recent_cost:.0f} ({len(recent)} viaggi)."
        out.append(_ins("fuel", "info", "Spesa carburante", body))

    # ── 5. Battery cranking voltage ──────────────────────────────────────────
    volts = [t["batteryStartupV"] for t in obd if t.get("batteryStartupV") is not None]
    if len(volts) >= 4:
        last = volts[-1]
        med_v = statistics.median(volts)
        recent_v = statistics.mean(volts[-4:])
        older_v = statistics.mean(volts[:-4]) if len(volts) > 4 else recent_v
        if recent_v < older_v - 0.25 or med_v < 9.3:
            out.append(_ins("battery", "warning", f"Tensione di spunto {last:.2f} V",
                f"Minimo all'avviamento in calo (media {med_v:.2f} V). "
                "Batteria possibilmente in invecchiamento: valuta un test di carico."))
        else:
            out.append(_ins("battery", "info", f"Batteria · spunto {last:.2f} V",
                f"Minimo all'avviamento stabile (media {med_v:.2f} V su {len(volts)} avvii)."))

    # ── 6. Oil dilution trend ────────────────────────────────────────────────
    dil = [t.get("oilDilutionPct") for t in obd if t.get("oilDilutionPct") is not None]
    if dil:
        last = dil[-1]
        if last > 5 or (len(dil) >= 3 and dil[-1] > dil[0] + 1.0):
            out.append(_ins("engine", "warning", f"Diluizione olio {last:.1f}%",
                "In aumento o elevata: spesso legata a rigenerazioni interrotte. Monitora il livello olio."))
        else:
            out.append(_ins("engine", "info", f"Diluizione olio {last:.1f}%",
                "Carburante nell'olio sotto controllo (allarme oltre ~5%)."))

    # ── 7. AdBlue range ──────────────────────────────────────────────────────
    adblue = next((t.get("adblueRangeKm") for t in reversed(obd)
                   if t.get("adblueRangeKm") is not None), None)
    if adblue is not None:
        if adblue < 1500:
            lvl = "critical" if adblue < 500 else "warning"
            out.append(_ins("adblue", lvl, f"AdBlue · {adblue:.0f} km",
                "Autonomia in calo: pianifica un rabbocco per evitare il blocco motore."))
        else:
            out.append(_ins("adblue", "info", f"AdBlue · {adblue:.0f} km",
                "Autonomia urea ampia, nessuna azione necessaria."))

    # ── 8. Service countdown ─────────────────────────────────────────────────
    km_svc = next((t.get("kmToService") for t in reversed(chrono)
                   if t.get("kmToService") is not None), None)
    days_svc = next((t.get("daysToService") for t in reversed(chrono)
                     if t.get("daysToService") is not None), None)
    if km_svc is not None:
        if km_svc < 1500 or (days_svc is not None and days_svc < 30):
            lvl = "critical" if (km_svc <= 0 or (days_svc or 99) <= 0) else "warning"
            out.append(_ins("service", lvl, "Tagliando in avvicinamento",
                f"Mancano {km_svc:.0f} km" + (f" o {days_svc} giorni" if days_svc else "") +
                ". Prenota l'intervento in officina."))
        else:
            out.append(_ins("service", "info", "Tagliando",
                f"Prossimo tra {km_svc:.0f} km" + (f" / {days_svc} giorni" if days_svc else "") + "."))

    return out
