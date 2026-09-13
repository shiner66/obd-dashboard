"""
AI Insight Engine — predictive maintenance from the vehicle's own history.

Two layers:
  • per_trip(trip, ctx)   — concrete, per-trip notes shown in the trip detail.
  • cross_trip(trips)     — the "Trend & AI" diagnosis cards: every rule is a
                            trend against the car's own baseline, quantified,
                            with a projection when the math supports one.

Design principles
  • A healthy car rarely trips an absolute threshold — compare to the car's
    own history (median baselines, least-squares slopes) instead.
  • Physical plausibility filters first: a single glitched sample (e.g. an
    oil-dilution reading of 419 %) must never fire an alert.
  • Every card that tracks a series ships the series, so the UI can draw it.
  • Say what the evidence supports, no more: an unstable idle is reported as
    such, with the typical causes (dual-mass flywheel, EGR, mounts) listed —
    not as a definitive diagnosis.

All text is Italian. Consumption is expressed in km/L.
"""
from __future__ import annotations

import statistics
from datetime import datetime, timedelta


# ── helpers ───────────────────────────────────────────────────────────────────

def _it_number(value: float, spec: str = ".1f") -> str:
    """Localize only an explicitly formatted number, preserving text and metadata."""
    return format(value, spec).translate(str.maketrans({".": ",", ",": "."}))


def _ins(category: str, level: str, title: str, body: str,
         series: list | None = None, unit: str = "", evidence: dict | None = None) -> dict:
    """Build one card with explicit traceability to the inputs used by its rule."""
    d = {"category": category, "level": level, "title": title, "body": body,
         "evidence": evidence or {"tripIds": [], "pidSlugs": []}}
    if series and len(series) >= 3:
        d["series"] = [round(v, 3) for v in series[-40:]]
        d["unit"] = unit
    return d


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s[:19])
    except (ValueError, TypeError):
        return None


def _linreg(xs: list[float], ys: list[float]) -> tuple[float, float] | None:
    """Least-squares fit → (slope, intercept), or None if degenerate."""
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    slope = sxy / sxx
    return slope, my - slope * mx


def _clean(pairs: list[tuple[float, float]], lo: float, hi: float) -> list[tuple[float, float]]:
    """Keep (x, y) points whose y is physically plausible."""
    return [(x, y) for x, y in pairs if y is not None and lo <= y <= hi]


def _median(vals: list[float]) -> float | None:
    return round(statistics.median(vals), 3) if vals else None


def _km_l(trip: dict) -> float | None:
    """Use only consumption selected with known provenance and coverage."""
    from .fuel import select_consumption
    if trip.get("fuelSource") and trip.get("consumptionKmL"):
        return trip["consumptionKmL"]
    return select_consumption(trip)["consumptionKmL"]


def _fmt_date(s: str | None) -> str:
    dt = _parse_dt(s)
    if not dt:
        return "—"
    months = ["gen", "feb", "mar", "apr", "mag", "giu",
              "lug", "ago", "set", "ott", "nov", "dic"]
    return f"{dt.day} {months[dt.month - 1]}"


def _fmt_future(days: float | None) -> str:
    if days is None or days <= 0 or days > 730:
        return ""
    dt = datetime.now() + timedelta(days=days)
    months = ["gen", "feb", "mar", "apr", "mag", "giu",
              "lug", "ago", "set", "ott", "nov", "dic"]
    return f"≈ {dt.day} {months[dt.month - 1]}"


def _daily_km(trips: list[dict]) -> float | None:
    """Average km/day over the most recent ~30 days of activity."""
    dated = [(d, t.get("distanceKm") or 0) for t in trips
             if (d := _parse_dt(t.get("start")))]
    if not dated:
        return None
    last = max(d for d, _ in dated)
    window = [(d, km) for d, km in dated if (last - d).days <= 30]
    if not window:
        return None
    span_days = max((last - min(d for d, _ in window)).days, 1)
    total = sum(km for _, km in window)
    return total / span_days if total > 0 else None


def _pid_stat(trip: dict, slug: str, key: str) -> float | None:
    v = ((trip.get("pidValues") or {}).get(slug) or {}).get(key)
    return v if isinstance(v, (int, float)) else None


_LEVEL_RANK = {"critical": 0, "warning": 1, "info": 2}


# ── shared context (computed once, fed to per_trip for personal comparison) ───

def usable_for_analysis(trip: dict) -> bool:
    """Keep incomplete legacy observations visible but out of numerical comparisons."""
    if trip.get("legacyIncomplete"):
        return False
    return not ("obd" in trip.get("sources", []) and trip.get("parserVersion", 0) < 2)


def _evidence(trips: list[dict], slugs: tuple | list = ()) -> dict:
    """List only supplied contributing trips and PID statistics actually present.

    sampleCount counts available PID records, not independent drives or re-created
    aligned samples. It is omitted when no count was measured in the source stats.
    """
    unique = {t["id"]: t for t in trips if t.get("id")}
    available = set()
    count = 0
    for trip in unique.values():
        for slug in slugs:
            stats = (trip.get("pidValues") or {}).get(slug)
            if stats is not None:
                available.add(slug)
                if isinstance(stats.get("samples"), int):
                    count += stats["samples"]
    result = {"tripIds": list(unique), "pidSlugs": sorted(available)}
    if count:
        result["sampleCount"] = count
        result["sampleCountBasis"] = "available_pid_records"
    return result


def _fuel_evidence(trips: list[dict]) -> dict:
    """Use the selected fuel source to identify its actual contributing PID family."""
    evidence = {"tripIds": [], "pidSlugs": []}
    count = 0
    for trip in {t["id"]: t for t in trips if t.get("id")}.values():
        slugs = {"obd_mass": ("inj_q", "rpm"), "obd_rate": ("fuel_rate",)}.get(trip.get("fuelSource"), ())
        part = _evidence([trip], slugs)
        evidence["tripIds"].extend(part["tripIds"])
        evidence["pidSlugs"].extend(part["pidSlugs"])
        count += part.get("sampleCount", 0)
    evidence["pidSlugs"] = sorted(set(evidence["pidSlugs"]))
    if count:
        evidence.update(sampleCount=count, sampleCountBasis="available_pid_records")
    return evidence


def build_context(trips: list[dict]) -> dict:
    """Aggregate stats used to compare a single trip against the car's history."""
    trips = [t for t in trips if usable_for_analysis(t)]
    baseline = [t for t in trips if (k := _km_l(t)) and 2 < k < 60]
    kml = [_km_l(t) for t in baseline]
    obd = [t for t in trips if "obd" in t.get("sources", [])]
    avg_regen = next((t["dpfAvgRegenKm"] for t in reversed(obd)
                      if t.get("dpfAvgRegenKm")), None)
    return {
        "median_kml": round(statistics.median(kml), 1) if kml else None,
        "avg_regen_km": avg_regen,
        "n": len(trips),
        "baselineTrips": baseline,
    }


# ── per-trip ──────────────────────────────────────────────────────────────────

def per_trip(trip: dict, ctx: dict | None = None) -> list[dict]:
    """Per-trip insights. Quantified and compared to the car's own history."""
    out: list[dict] = []
    ctx = ctx or {}
    is_obd = "obd" in trip.get("sources", [])
    if not usable_for_analysis(trip):
        return [_ins("data", "info", "Storico conservato: dati insufficienti",
                     "Sorgenti mancanti o insufficienti per ricalcolare questo viaggio; escluso dai confronti.",
                     evidence=_evidence([trip]))]
    evidence = _fuel_evidence([trip])

    def emit(*args, **kwargs):
        """Attach the exact input selection of the current per-trip rule."""
        return _ins(*args, **kwargs, evidence=evidence)

    # ── Fuel economy vs your median (works for OBD and MyOpel trips) ──────────
    kml = _km_l(trip)
    med = ctx.get("median_kml")
    dist = trip.get("distanceKm") or 0
    if kml:
        if med and dist >= 3:
            evidence = _fuel_evidence([trip, *ctx.get("baselineTrips", [])])
            delta = (kml - med) / med * 100
            if delta >= 12:
                out.append(emit("fuel", "info", f"Viaggio efficiente · {_it_number(kml, '.1f')} km/L",
                    f"Il {abs(delta):.0f}% meglio della tua media ({_it_number(med, '.1f')} km/L). "
                    "Guida fluida e percorso scorrevole."))
            elif delta <= -18:
                out.append(emit("fuel", "warning", f"Consumo elevato · {_it_number(kml, '.1f')} km/L",
                    f"Il {abs(delta):.0f}% sotto la tua media ({_it_number(med, '.1f')} km/L). "
                    "Tipico di traffico, partenza a freddo o tragitto urbano."))
            else:
                out.append(emit("fuel", "info", f"Consumo nella norma · {_it_number(kml, '.1f')} km/L",
                    f"In linea con la tua media ({_it_number(med, '.1f')} km/L)."))
        else:
            out.append(emit("fuel", "info", f"Consumo · {_it_number(kml, '.1f')} km/L",
                f"≈ {_it_number(100/kml, '.1f')} L/100 km su questo viaggio."))

    if not is_obd:
        return out

    evidence = _evidence([trip], ("coolant", "coolant_c"))
    # ── Cold-start warm-up ───────────────────────────────────────────────────
    air = trip.get("airTempC")
    coolant_max = trip.get("coolantMaxC")
    if air is not None and air < 18 and coolant_max:
        warmed = coolant_max >= 80
        out.append(emit("engine", "info", "Partenza a freddo",
            f"Avvio a ~{air:.0f}°C ambiente; liquido salito a {coolant_max:.0f}°C — "
            f"{'temperatura di esercizio raggiunta' if warmed else 'motore non del tutto in temperatura, consumo penalizzato'}."))

    evidence = _evidence([trip], ("regen_st", "regen_dist", "soot_cl", "egt_a", "egt_dpf_i", "egt_dpf_o", "nox_t"))
    # ── DPF regeneration narrative ───────────────────────────────────────────
    state = trip.get("dpfRegenState") or "idle"
    egt = trip.get("exhaustAfterCatC")
    if state == "active":
        out.append(emit("dpf", "warning", "Rigenerazione attiva all’ultima osservazione",
            f"I sensori indicavano una rigenerazione all’ultima osservazione disponibile"
            + (f" (EGT {egt:.0f}°C)" if egt else "")
            + ". Interromperla immette gasolio nell'olio: se possibile prosegui "
              "la marcia finché non termina (5–15 min a velocità costante)."))
    elif state == "completed":
        out.append(emit("dpf", "info", "Rigenerazione DPF completata",
            "Ciclo completato con successo: il soot è stato bruciato. "
            "Il contatore km riparte da zero."))
    elif state == "requested":
        if dist < 8:
            out.append(emit("dpf", "warning", "Rigenerazione richiesta ma viaggio breve",
                "L'ECU ha richiesto una rigenerazione ma il tragitto è troppo corto per "
                "raggiungere le temperature. Ripetuti viaggi brevi accumulano soot."))
        else:
            out.append(emit("dpf", "warning", "Rigenerazione DPF richiesta",
                f"Richiesta in corso{f', EGT post-cat {egt:.0f}°C' if egt else ''}. "
                "Un tratto a velocità costante (extraurbano) aiuta a completarla."))
    elif state == "post_regen":
        out.append(emit("dpf", "info", "Post-rigenerazione",
            "Rigenerazione completata poco prima di questo viaggio."))

    evidence = _evidence([trip], ("soot_cl",))
    # ── Short trip + soot building (the #1 diesel/DPF risk) ───────────────────
    soot = trip.get("dpfClosedSoot")
    if dist < 5 and state in ("idle", "requested") and soot is not None and soot >= 4:
        out.append(emit("dpf", "warning", "Viaggio breve con soot in accumulo",
            f"Solo {_it_number(dist, '.1f')} km e closed soot a {_it_number(soot, '.1f')} g/L senza rigenerazione. "
            "Programma ogni tanto un tratto extraurbano per far pulire il filtro."))
    elif soot is not None and soot >= 7:
        out.append(emit("dpf", "warning", f"Closed soot alto · {_it_number(soot, '.1f')} g/L",
            "Vicino alla soglia di rigenerazione: attesa a breve."))

    evidence = _evidence([trip], ("rpm",))
    # ── Near-stall idle dip (mount/flywheel/EGR clue, aggregated in Trend&AI) ─
    rpm_min = _pid_stat(trip, "rpm", "min")
    if rpm_min is not None and 150 < rpm_min < 600:
        out.append(emit("engine", "info", f"Calo di giri sotto il minimo · {rpm_min:.0f} rpm",
            "Il regime è sceso ben sotto il minimo durante il viaggio. Occasionale è "
            "normale (spunto in salita, A/C); se ricorre, vedi la scheda «Minimo motore» in Trend & AI."))

    evidence = _evidence([trip], ("egt_a",))
    # ── EGT spike outside regen ──────────────────────────────────────────────
    if egt is not None and egt > 700 and state not in ("active", "completed"):
        out.append(emit("engine", "info", f"Picco EGT {egt:.0f}°C",
            "Temperatura gas di scarico elevata, compatibile con guida sostenuta."))

    evidence = _evidence([trip], ("oil_dil",))
    # ── Oil dilution (diesel: fuel dilutes oil during regens) ────────────────
    dil = trip.get("oilDilutionPct")
    if dil is not None and 5.0 < dil < 15:
        out.append(emit("engine", "critical", f"Diluizione olio {_it_number(dil, '.1f')}%",
            "Livello elevato: verifica frequenza/completamento rigenerazioni e valuta cambio olio."))

    evidence = _evidence([trip], ("ss_state",))
    # ── Stop&Start genuine fault only ────────────────────────────────────────
    if trip.get("ssState") == 7:
        out.append(emit("engine", "warning", "Stop&Start — guasto",
            "Il sistema Stop&Start ha riportato un guasto. Diagnostica consigliata."))

    evidence = _evidence([trip], ("rpm", "speed", "speed_v", "inj_q"))
    # ── Idle share (OBD-derived, §3) ─────────────────────────────────────────
    idle_share = trip.get("idleSharePct")
    idle_s = trip.get("idleSeconds") or 0
    if idle_share is not None and idle_share >= 20 and idle_s >= 120:
        gtxt = f", ~{trip['idleFuelG']:.0f} g bruciati fermo" if trip.get("idleFuelG") else ""
        out.append(emit("fuel", "info", f"Molto tempo al minimo · {idle_share:.0f}%",
            f"{idle_s/60:.0f} min a motore acceso e fermo{gtxt}. In città il minimo pesa "
            "sul consumo: spegnere il motore alle soste lunghe aiuta."))

    evidence = _evidence([trip], ("ac_amp",))
    # ── A/C usage (compressor solenoid current, §2) ──────────────────────────
    ac_share = trip.get("acActivePct")
    if ac_share is not None and ac_share >= 40:
        out.append(emit("fuel", "info", f"Clima attivo · {ac_share:.0f}% del viaggio",
            f"Compressore inserito per gran parte del tragitto (corrente media "
            f"{trip.get('acCurrentMa') or 0:.0f} mA). Il pacchetto clima costa ~0,1 L/h: "
            "in coda o in città incide sui consumi più che in autostrada."))

    return out


# ── cross-trip (Trend & AI diagnosis cards) ───────────────────────────────────

def cross_trip(trips: list[dict]) -> list[dict]:
    """Predictive-maintenance cards: trends vs the car's own baseline."""
    out: list[dict] = []
    trips = [t for t in trips if usable_for_analysis(t)]
    evidence = _evidence([])
    daily_inputs = []

    def emit(category, level, title, body, *args, **kwargs):
        """Attach measured rule inputs; a calendar projection also records km/day context."""
        used = dict(evidence)
        if "≈" in body and daily_inputs:
            context_ids = [t["id"] for t in daily_inputs]
            used["contextTripIds"] = context_ids
            used["tripIds"] = list(dict.fromkeys([*used["tripIds"], *context_ids]))
        return _ins(category, level, title, body, *args, **kwargs, evidence=used)

    obd = sorted([t for t in trips if "obd" in t.get("sources", [])],
                 key=lambda t: t.get("start") or "")
    chrono = sorted(trips, key=lambda t: t.get("start") or "")
    daily = _daily_km(chrono)
    dated = [t for t in chrono if _parse_dt(t.get("start"))]
    if dated:
        last_date = _parse_dt(dated[-1]["start"])
        daily_inputs = [t for t in dated if (last_date - _parse_dt(t["start"])).days <= 30]

    # ── 1. Oil dilution trend (regression over km) ────────────────────────────
    dil_pts = _clean([( (t.get("odometerKm") or 0), t.get("oilDilutionPct") )
                      for t in obd if t.get("odometerKm")], 0.0, 15.0)
    dil_pts.sort(key=lambda p: p[0])
    evidence = _evidence([t for t in obd if t.get("odometerKm") and t.get("oilDilutionPct") is not None and 0 <= t["oilDilutionPct"] <= 15], ("oil_dil",))
    if len(dil_pts) >= 8 and (dil_pts[-1][0] - dil_pts[0][0]) >= 300:
        xs = [p[0] for p in dil_pts]
        ys = [p[1] for p in dil_pts]
        fit = _linreg(xs, ys)
        last = ys[-1]
        if fit:
            per_1000 = fit[0] * 1000.0
            km_to_5 = (5.0 - last) / per_1000 * 1000.0 if per_1000 > 0.01 else None
            days_to_5 = (km_to_5 / daily) if (km_to_5 and daily) else None
            if last >= 5:
                out.append(emit("engine", "critical", f"Diluizione olio {_it_number(last, '.1f')}% — cambio olio",
                    "Oltre la soglia del 5%: il gasolio nell'olio ne riduce il potere "
                    "lubrificante. Cambio olio consigliato a breve.", ys, "%"))
            elif per_1000 >= 0.15 and km_to_5 and km_to_5 < 4000:
                out.append(emit("engine", "warning", "Diluizione olio in aumento",
                    f"Da {_it_number(ys[0], '.1f')}% a {_it_number(last, '.1f')}% (+{_it_number(per_1000, '.2f')}%/1000 km). Di questo passo "
                    f"la soglia del 5% arriva tra ~{_it_number(km_to_5, ',.0f')} km"
                     + (f" ({_fmt_future(days_to_5)})" if days_to_5 else "") +
                    ". Causa tipica: rigenerazioni DPF interrotte. Anticipa il cambio olio.",
                    ys, "%"))
            elif per_1000 >= 0.10:
                body = (f"{_it_number(last, '.1f')}% attuale, in lento aumento (+{_it_number(per_1000, '.2f')}%/1000 km)")
                if km_to_5:
                    body += f" — soglia 5% tra ~{_it_number(km_to_5, ',.0f')} km"
                    if days_to_5:
                        body += f" ({_fmt_future(days_to_5)})"
                body += ". Fisiologico per un diesel usato in città; completa le rigenerazioni per rallentarlo."
                out.append(emit("engine", "info", "Diluizione olio sotto controllo", body, ys, "%"))
            else:
                out.append(emit("engine", "info", f"Diluizione olio stabile · {_it_number(last, '.1f')}%",
                    "Nessuna deriva significativa nel periodo osservato (allarme oltre il 5%).",
                    ys, "%"))

    # ── 2. Interrupted regenerations (root cause of dilution) ─────────────────
    # A trip that ends while state == "active" means the engine was switched
    # off mid-regen. What matters is the *share* of regens that get cut short.
    window = obd[-40:]
    regen_done = [t for t in window if t.get("dpfRegenState") == "completed"]
    regen_cut  = [t for t in window if t.get("dpfRegenState") == "active"]
    regen_events = len(regen_done) + len(regen_cut)
    evidence = _evidence([*regen_done, *regen_cut], ("regen_st", "regen_dist", "soot_cl", "egt_a", "egt_dpf_i", "egt_dpf_o", "nox_t"))
    if regen_cut and regen_events >= 2:
        share = len(regen_cut) / regen_events * 100
        when = _fmt_date(regen_cut[-1].get("start"))
        if share >= 30 and len(regen_cut) >= 2:
            out.append(emit("dpf", "warning",
                f"{len(regen_cut)} rigenerazioni su {regen_events} ancora attive all’ultima osservazione ({share:.0f}%)",
                f"Ultima osservazione attiva il {when}. Il dato non dimostra uno spegnimento. Spegnere il motore a rigenerazione in corso "
                "immette gasolio nell'olio (è la causa principale della diluizione) e fa "
                "ripartire il ciclo da capo: quando il minimo sale e la ventola resta attiva, "
                "prosegui la marcia 5–15 minuti finché non termina."))
        else:
            cut_txt = "1 con esito non osservato" if len(regen_cut) == 1 else f"{len(regen_cut)} con esito non osservato"
            out.append(emit("dpf", "info",
                f"Rigenerazioni: {len(regen_done)} completate, {cut_txt}",
                f"Ultima osservazione attiva il {when}. L’esito successivo non è disponibile; "
                "completarle mantiene bassa la diluizione dell'olio."))

    # ── 3. DPF — next regen prediction + interval trend ───────────────────────
    since = next((t.get("dpfSinceRegenKm") for t in reversed(obd)
                  if t.get("dpfSinceRegenKm") is not None), None)
    avg_regen = next((t.get("dpfAvgRegenKm") for t in reversed(obd)
                      if t.get("dpfAvgRegenKm")), None)
    interval_series = [t.get("dpfAvgRegenKm") for t in obd[-20:] if t.get("dpfAvgRegenKm")]
    regen_inputs = [t for t in obd[-20:] if t.get("dpfAvgRegenKm")]
    for field in ("dpfSinceRegenKm", "dpfAvgRegenKm"):
        latest_input = next((t for t in reversed(obd) if t.get(field) is not None), None)
        if latest_input:
            regen_inputs.append(latest_input)
    evidence = _evidence(regen_inputs, ("regen_dist", "regen_avg"))
    if since is not None and avg_regen and avg_regen > 20:
        remaining = avg_regen - since
        pct = since / avg_regen * 100
        trend_txt = ""
        level = "info"
        if len(interval_series) >= 8:
            fit = _linreg(list(range(len(interval_series))), interval_series)
            if fit:
                delta_pct = fit[0] * len(interval_series) / interval_series[0] * 100
                if delta_pct <= -8:
                    level = "warning"
                    trend_txt = (f" Intervallo medio in calo del {abs(delta_pct):.0f}% "
                                 "nel periodo: il filtro si intasa più in fretta (più urbano o filtro che invecchia).")
                elif delta_pct >= 8:
                    trend_txt = f" Intervallo medio in aumento ({delta_pct:+.0f}%): rigenera meno spesso, buon segno."
        if remaining > 0:
            lvl = level if pct < 90 else "warning"
            out.append(emit("dpf", lvl, "Prossima rigenerazione DPF",
                f"{since:.0f} km dall'ultima · intervallo medio {avg_regen:.0f} km "
                f"({pct:.0f}% del ciclo). Stimata tra ~{remaining:.0f} km"
                + (f" ({_fmt_future(remaining / daily)})" if daily else "") + "." + trend_txt,
                interval_series, "km"))
        else:
            out.append(emit("dpf", "warning", "Rigenerazione DPF imminente",
                f"Già {since:.0f} km dall'ultima, oltre l'intervallo medio "
                f"({avg_regen:.0f} km). Favorisci un tratto extraurbano." + trend_txt,
                interval_series, "km"))

    # ── 4. DPF soot now ───────────────────────────────────────────────────────
    soot = next((t.get("dpfClosedSoot") for t in reversed(obd)
                 if t.get("dpfClosedSoot") is not None), None)
    soot_series = [t.get("dpfClosedSoot") for t in obd[-25:] if t.get("dpfClosedSoot") is not None]
    soot_inputs = [t for t in obd[-25:] if t.get("dpfClosedSoot") is not None]
    latest_soot = next((t for t in reversed(obd) if t.get("dpfClosedSoot") is not None), None)
    evidence = _evidence([*soot_inputs, *([latest_soot] if latest_soot else [])], ("soot_cl",))
    if soot is not None:
        if soot >= 7:
            out.append(emit("dpf", "warning", f"Closed soot {_it_number(soot, '.1f')} g/L",
                "Carico soot alto: rigenerazione attesa a breve. Evita di interrompere il viaggio se parte.",
                soot_series, "g/L"))
        else:
            out.append(emit("dpf", "info", f"Closed soot {_it_number(soot, '.1f')} g/L",
                "Il filtro si svuota con la rigenerazione intorno a ~8 g/L. Valore nella norma.",
                soot_series, "g/L"))

    # ── 5. Battery cranking voltage (trend + projection) ─────────────────────
    bat_pts = [(d, t["batteryStartupV"]) for t in obd
               if t.get("batteryStartupV") is not None
               and 6 <= t["batteryStartupV"] <= 14
               and (d := _parse_dt(t.get("start")))]
    evidence = _evidence([t for t in obd if t.get("batteryStartupV") is not None and 6 <= t["batteryStartupV"] <= 14 and _parse_dt(t.get("start"))], ("bat_v",))
    if len(bat_pts) >= 6:
        t0 = bat_pts[0][0]
        xs = [(d - t0).days + (d - t0).seconds / 86400 for d, _ in bat_pts]
        ys = [v for _, v in bat_pts]
        med_v = _median(ys)
        fit = _linreg(xs, ys)
        slope_month = fit[0] * 30 if fit else 0.0
        last = ys[-1]
        if med_v is not None and med_v < 9.0:
            out.append(emit("battery", "warning", f"Tensione di spunto bassa · mediana {_it_number(med_v, '.2f')} V",
                "Il minimo all'avviamento è basso in modo persistente: batteria da testare "
                "sotto carico (tipico segnale di fine vita).", ys, "V"))
        elif slope_month <= -0.15 and len(bat_pts) >= 12:
            months_to_88 = (last - 8.8) / abs(slope_month) if slope_month < 0 else None
            out.append(emit("battery", "warning", "Batteria in lento declino",
                f"Spunto in calo di {_it_number(abs(slope_month), '.2f')} V/mese (ora {_it_number(last, '.2f')} V, mediana {_it_number(med_v, '.2f')} V)."
                + (f" A questo ritmo scende sotto 8,8 V tra ~{months_to_88:.0f} mesi." if months_to_88 and months_to_88 < 24 else "")
                + " Un test batteria in officina è economico e toglie il dubbio.", ys, "V"))
        else:
            out.append(emit("battery", "info", f"Spunto batteria stabile · {_it_number(last, '.2f')} V",
                f"Minimo all'avviamento stabile (mediana {_it_number(med_v, '.2f')} V su {len(ys)} avvii, "
                f"deriva {_it_number(slope_month, '+.2f')} V/mese).", ys, "V"))

    # ── 6. Common-rail pressure regulation drift ─────────────────────────────
    rail_pts = []
    rail_inputs = []
    for t in obd:
        des = _pid_stat(t, "fuel_p_d", "mean")
        mea = _pid_stat(t, "ecm_measured_high_pressure_common_rail_fuel_pressure", "mean")
        if des and mea and des > 50:
            rail_pts.append(mea / des)
            rail_inputs.append(t)
    evidence = _evidence(rail_inputs, ("fuel_p_d", "ecm_measured_high_pressure_common_rail_fuel_pressure"))
    if len(rail_pts) >= 10:
        baseline = statistics.median(rail_pts)
        recent = statistics.median(rail_pts[-5:])
        drift = (recent / baseline - 1) * 100 if baseline else 0
        series = [r * 100 for r in rail_pts]
        if abs(drift) >= 4:
            out.append(emit("engine", "warning", "Pressione rail in deriva",
                f"Il rapporto misurata/richiesta si è spostato del {_it_number(drift, '+.1f')}% rispetto allo storico. "
                "Possibili cause: regolatore di pressione, pompa alta pressione o iniettori in usura. "
                "Da verificare se compaiono anche spunti irregolari o fumo.", series, "%"))
        else:
            out.append(emit("engine", "info", "Regolazione rail stabile",
                f"Pressione carburante misurata allineata alla richiesta "
                f"(deriva {_it_number(drift, '+.1f')}% sulle ultime uscite). Non è un test diagnostico di pompa e regolatore.",
                series, "%"))

    # ── 7. Turbo peak boost vs baseline ───────────────────────────────────────
    # Compare only trips where power was actually demanded (max rpm ≥ 2600),
    # otherwise a run of relaxed drives would fake a "turbo in decline".
    boost_pts = [b for t in obd
                 if (t.get("maxRpm") or 0) >= 2600
                 and (b := _pid_stat(t, "boost", "max")) and 1200 <= b <= 3200]
    evidence = _evidence([t for t in obd if (t.get("maxRpm") or 0) >= 2600 and (b := _pid_stat(t, "boost", "max")) and 1200 <= b <= 3200], ("boost", "rpm"))
    if len(boost_pts) >= 10:
        baseline = statistics.median(boost_pts)
        recent = statistics.median(boost_pts[-5:])
        ratio = recent / baseline if baseline else 1
        if ratio <= 0.90:
            out.append(emit("engine", "warning", "Picco turbo in calo",
                f"Boost massimo recente {recent:.0f} mbar contro i {baseline:.0f} tipici "
                f"({(1-ratio)*100:.0f}% in meno), a parità di richiesta (giri > 2600). "
                "Possibili cause: geometria variabile sporca, attuatore, tubi/intercooler "
                "che perdono. Se noti meno spinta, falla vedere.", boost_pts, "mbar"))
        else:
            out.append(emit("engine", "info", f"Turbo in forma · picco tipico {baseline:.0f} mbar",
                f"Pressione di sovralimentazione massima costante nel tempo "
                f"(ultime uscite a pieno carico: {recent:.0f} mbar).", boost_pts, "mbar"))

    # ── 8. Idle stability (dual-mass flywheel / EGR / mounts clue) ────────────
    idle_modes = [m for t in obd
                  if (m := _pid_stat(t, "rpm", "mode")) and 600 <= m <= 1000]
    dips = [t for t in obd[-15:]
            if (mn := _pid_stat(t, "rpm", "min")) and 150 < mn < 600]
    evidence = _evidence([t for t in obd if (m := _pid_stat(t, "rpm", "mode")) and 600 <= m <= 1000] + dips, ("rpm",))
    if len(idle_modes) >= 8:
        med_idle = statistics.median(idle_modes)
        spread = statistics.pstdev(idle_modes[-10:]) if len(idle_modes) >= 10 else 0
        if spread > 25 or len(dips) >= 3:
            reasons = []
            if spread > 25:
                reasons.append(f"regime tipico che varia tra i viaggi (±{spread:.0f} rpm)")
            if len(dips) >= 3:
                reasons.append(f"{len(dips)} cali sotto i 600 rpm negli ultimi 15 viaggi")
            out.append(emit("engine", "warning", "Minimo motore irregolare",
                f"Rilevato: {'; '.join(reasons)}. Su un 1,5 diesel le cause tipiche sono "
                "volano bimassa in usura (vibrazioni al minimo/spegnimento), valvola EGR sporca, "
                "supporti motore o iniettori. Vale un controllo se senti vibrazioni.",
                idle_modes, "rpm"))
        else:
            out.append(emit("engine", "info", f"Minimo osservato stabile · ~{med_idle:.0f} rpm",
                f"Regime di minimo regolare su {len(idle_modes)} viaggi"
                + (f" (1 calo isolato sotto i 600 rpm)" if len(dips) == 1 else "") +
                ". Questi dati da soli non escludono guasti meccanici.", idle_modes, "rpm"))

    # ── 9. Thermostat / warm-up check ─────────────────────────────────────────
    warm_candidates = [t for t in obd[-12:]
                       if (t.get("durationMin") or 0) >= 15 and (t.get("distanceKm") or 0) >= 8
                       and t.get("coolantMaxC") is not None]
    cold_runs = [t for t in warm_candidates if t["coolantMaxC"] < 75]
    evidence = _evidence(cold_runs, ("coolant", "coolant_c"))
    if len(cold_runs) >= 2:
        out.append(emit("engine", "warning", "Motore che non va in temperatura",
            f"In {len(cold_runs)} viaggi recenti da 15+ minuti il liquido non ha superato i 75°C. "
            "Sintomo classico di termostato bloccato aperto: consumi più alti, più diluizione olio, "
            "riscaldamento debole. Ricambio economico, vale la verifica."))

    # ── 10. AdBlue — consumption rate and refill forecast ─────────────────────
    ad_pts = [((t.get("odometerKm") or 0), t["adblueRangeKm"]) for t in obd
              if t.get("adblueRangeKm") is not None and t.get("odometerKm")]
    ad_pts = [(o, r) for o, r in ad_pts if 0 <= r <= 30000]
    evidence = _evidence([t for t in obd if t.get("adblueRangeKm") is not None and t.get("odometerKm") and 0 <= t["adblueRangeKm"] <= 30000], ("urea_km",))
    if len(ad_pts) >= 5:
        # use the segment after the last refill (range jumping up > 400 km)
        seg_start = 0
        for i in range(1, len(ad_pts)):
            if ad_pts[i][1] - ad_pts[i-1][1] > 400:
                seg_start = i
        seg = ad_pts[seg_start:]
        last_range = seg[-1][1]
        rate_txt = ""
        km_left = last_range
        if len(seg) >= 4 and (seg[-1][0] - seg[0][0]) >= 200:
            fit = _linreg([p[0] for p in seg], [p[1] for p in seg])
            if fit and fit[0] < -0.2:
                rate = -fit[0]            # km of range per km driven
                km_left = last_range / rate
                rate_txt = f" Consumo effettivo: {_it_number(rate, '.2f')} km di autonomia per km percorso."
        days_left = (km_left / daily) if daily else None
        lvl = "critical" if km_left < 500 else "warning" if km_left < 1500 else "info"
        out.append(emit("adblue", lvl, f"AdBlue · {last_range:.0f} km dichiarati",
            (f"Al tuo ritmo reale bastano per ~{_it_number(km_left, ',.0f')} km"
             + (f" ({_fmt_future(days_left)})" if days_left else "") + "." + rate_txt
             + (" Pianifica il rabbocco per evitare il blocco avviamento." if lvl != "info" else "")),
            [r for _, r in ad_pts], "km"))

    # ── 11. Fuel economy — normalized by route type, traffic-aware ────────────
    # Raw km/L depends mostly on the *kind* of trip (distance bucket + average
    # speed). Comparing recent trips only against historical peers of the same
    # kind separates "more traffic / more urban" (info, not the car's fault)
    # from "worse at equal conditions" (warning: tyres, air filter, brakes…).
    def _bucket(km: float):
        for lo, hi in ((0, 4), (4, 8), (8, 15), (15, 40), (40, 1e9)):
            if lo <= km < hi:
                return (lo, hi)
        return None

    recs = [{"kml": k, "km": t.get("distanceKm") or 0,
             "spd": t.get("avgSpeedKmh"), "temp": t.get("airTempC"),
             "start": t.get("start")}
            for t in chrono if (k := _km_l(t)) and 2 < k < 60]
    evidence = _fuel_evidence([t for t in chrono if (k := _km_l(t)) and 2 < k < 60])
    if len(recs) >= 8:
        vals = [r["kml"] for r in recs]
        n_recent = 8 if len(recs) >= 25 else 5
        recent, hist = recs[-n_recent:], recs[:-n_recent]
        raw_drift = (statistics.median([r["kml"] for r in recent])
                     / statistics.median([r["kml"] for r in hist]) - 1)

        # normalized drift: each recent trip vs historical peers of the same
        # distance bucket and comparable average speed (±25 %, min ±6 km/h)
        deltas = []
        for r in recent:
            if not r["spd"]:
                continue
            tol = max(6.0, r["spd"] * 0.25)
            peers = [h["kml"] for h in hist
                     if h["spd"] and _bucket(h["km"]) == _bucket(r["km"])
                     and abs(h["spd"] - r["spd"]) <= tol]
            if len(peers) >= 5:
                deltas.append(r["kml"] / statistics.median(peers) - 1)
        norm_drift = statistics.median(deltas) if len(deltas) >= 3 else None

        # context shifts, for the explanation
        rec_buckets = {_bucket(r["km"]) for r in recent}
        spd_hist = statistics.median([h["spd"] for h in hist
                                      if h["spd"] and _bucket(h["km"]) in rec_buckets] or [0])
        spd_rec = statistics.median([r["spd"] for r in recent if r["spd"]] or [0])
        t_hist = [h["temp"] for h in hist if h["temp"] is not None]
        t_rec = [r["temp"] for r in recent if r["temp"] is not None]
        temp_up = (statistics.median(t_rec) - statistics.median(t_hist)
                   if t_hist and t_rec else 0)

        med = statistics.median(vals)
        if norm_drift is not None and norm_drift <= -0.12:
            out.append(emit("fuel", "warning", "Consumo alto a parità di percorso",
                f"Sugli ultimi {n_recent} viaggi il motore rende il {abs(norm_drift)*100:.0f}% in meno "
                "(km/L) rispetto a viaggi storici dello stesso tipo — stessa distanza e velocità "
                "media. Traffico e carico possono comunque differire. Possibili fattori: pressione gomme (controlla a freddo), "
                "filtro aria, freno che rimane appoggiato, carburante diverso.", vals, "km/L"))
        elif raw_drift <= -0.10:
            body = (f"Ultimi {n_recent} viaggi al {abs(raw_drift)*100:.0f}% sotto la tua media "
                    f"({_it_number(med, '.1f')} km/L); condizioni di percorso e campionamento possono incidere")
            if norm_drift is not None:
                body += f": a parità di condizioni il motore è in linea ({norm_drift*100:+.0f}%)"
            body += "."
            if spd_hist and spd_rec and spd_rec <= spd_hist * 0.85:
                body += (f" Velocità media scesa da {spd_hist:.0f} a {spd_rec:.0f} km/h "
                         "— più traffico o tragitti più urbani.")
            if temp_up >= 3:
                body += f" Fa anche più caldo (+{temp_up:.0f}°C): il clima incide in città."
            out.append(emit("fuel", "info", "Consumi in aumento: confronto da approfondire", body, vals, "km/L"))
        else:
            best = max(recs, key=lambda r: r["kml"])
            body = (f"Media {_it_number(med, '.1f')} km/L su {len(vals)} viaggi · "
                    f"miglior viaggio {_it_number(best['kml'], '.1f')} km/L ({_fmt_date(best['start'])}).")
            if raw_drift >= 0.08:
                body += f" In miglioramento: ultimi {n_recent} viaggi {raw_drift*100:+.0f}%."
            out.append(emit("fuel", "info", "Efficienza carburante", body, vals, "km/L"))
    elif len(recs) >= 3:
        vals = [r["kml"] for r in recs]
        out.append(emit("fuel", "info", "Efficienza carburante",
            f"Media {_it_number(statistics.median(vals), '.1f')} km/L su {len(vals)} viaggi.", vals, "km/L"))

    # ── 12. Fuel cost ─────────────────────────────────────────────────────────
    costed = [t for t in chrono if t.get("costEur") and t.get("costDistanceKm")]
    evidence = _evidence(costed)
    if len(costed) >= 4:
        total_cost = sum(t["costEur"] for t in costed)
        total_km = sum(t["costDistanceKm"] for t in costed)
        per100 = total_cost / total_km * 100 if total_km else 0
        last_dt = _parse_dt(costed[-1].get("start"))
        recent = [t for t in costed
                  if last_dt and (sd := _parse_dt(t.get("start")))
                  and (last_dt - sd).days <= 30]
        recent_cost = sum(t["costEur"] for t in recent)
        body = f"€{_it_number(per100, '.2f')}/100 km in media · €{total_cost:.0f} totali su {len(costed)} viaggi."
        if recent:
            body += f" Ultimi 30 giorni: €{recent_cost:.0f} ({len(recent)} viaggi)."
        out.append(emit("fuel", "info", "Spesa carburante", body))

    # ── 13. Service countdown with date estimate ──────────────────────────────
    km_svc = next((t.get("kmToService") for t in reversed(chrono)
                   if t.get("kmToService") is not None), None)
    days_svc = next((t.get("daysToService") for t in reversed(chrono)
                     if t.get("daysToService") is not None), None)
    service_inputs = []
    for field in ("kmToService", "daysToService"):
        last_input = next((t for t in reversed(chrono) if t.get(field) is not None), None)
        if last_input:
            service_inputs.append(last_input)
    evidence = _evidence(service_inputs)
    if km_svc is not None:
        by_km_days = (km_svc / daily) if daily else None
        eta_days = min([d for d in (by_km_days, days_svc) if d is not None], default=None)
        eta = _fmt_future(eta_days)
        if km_svc < 1500 or (days_svc is not None and days_svc < 30):
            lvl = "critical" if (km_svc <= 0 or (days_svc or 99) <= 0) else "warning"
            out.append(emit("service", lvl, "Tagliando in avvicinamento",
                f"Mancano {_it_number(km_svc, ',.0f')} km"
                + (f" o {days_svc} giorni" if days_svc else "")
                + (f" ({eta})" if eta else "") + ". Prenota l'intervento in officina."))
        else:
            out.append(emit("service", "info", "Tagliando",
                f"Prossimo tra {_it_number(km_svc, ',.0f')} km"
                + (f" / {days_svc} giorni" if days_svc else "")
                + (f" — al tuo ritmo {eta}" if eta else "") + "."))

    # ── 14. Fuel economy in mass (g/km) — density-independent, OBD-only ───────
    # km/L and L/100 assume diesel density; with HVO (~780 g/L) they mislead.
    # g/km comes straight from the injector-mass integral (§2) and is immune to
    # the fuel blend — the honest cross-fuel economy number, no MyOpel needed.
    gkm_pts = [(d, t["gPerKm"]) for t in obd
               if t.get("gPerKm") and 20 < t["gPerKm"] < 200
               and (d := _parse_dt(t.get("start")))]
    evidence = _evidence([t for t in obd if t.get("gPerKm") and 20 < t["gPerKm"] < 200 and _parse_dt(t.get("start"))], ("inj_q", "rpm"))
    if len(gkm_pts) >= 8:
        vals = [g for _, g in gkm_pts]
        med = statistics.median(vals)
        recent = statistics.median(vals[-8:])
        drift = (recent / med - 1) if med else 0
        l100_b7  = med / 835.0 * 100
        l100_hvo = med / 780.0 * 100
        body = (f"Consumo in massa {med:.0f} g/km (mediana su {len(vals)} viaggi) — "
                f"indipendente dalla densità. Equivale a {_it_number(l100_b7, '.1f')} L/100 km con gasolio B7 "
                f"o {_it_number(l100_hvo, '.1f')} L/100 km con HVO. La pompa resta il riferimento volumetrico.")
        if drift >= 0.12:
            out.append(emit("fuel", "warning", f"Consumo in massa in aumento · {recent:.0f} g/km",
                body + f" Ultimi 8 viaggi +{drift*100:.0f}% sulla mediana.", vals, "g/km"))
        else:
            out.append(emit("fuel", "info", f"Consumo in massa · {med:.0f} g/km", body, vals, "g/km"))

    # ── 15. Tyre-pressure monitor via wheel/GPS speed ratio (§3) ──────────────
    # ratio_wg = wheel speed / GPS speed. Deflation shrinks the rolling radius →
    # the wheel turns faster for the same ground speed → the ratio drifts up.
    # Alert on a > +0.3% rise over ~4 weeks (≈ −0.4/0.5 bar). Only per-trip
    # ratios with ≥ 60 samples reach here, so a short log can't fake a spike.
    ratio_pts = [(d, t["ratioWg"]) for t in obd
                 if t.get("ratioWg") and 0.95 < t["ratioWg"] < 1.05
                 and (d := _parse_dt(t.get("start")))]
    evidence = _evidence([t for t in obd if t.get("ratioWg") and 0.95 < t["ratioWg"] < 1.05 and _parse_dt(t.get("start"))], ("speed", "speed_v"))
    if len(ratio_pts) >= 8 and (ratio_pts[-1][0] - ratio_pts[0][0]).days >= 21:
        t0 = ratio_pts[0][0]
        xs = [(d - t0).days for d, _ in ratio_pts]
        ys = [r for _, r in ratio_pts]
        fit = _linreg(xs, ys)
        series = [r * 1000 for r in ys]   # ×1000 so the sparkline shows the sub-% drift
        if fit:
            drift_4w = fit[0] * 28 / statistics.median(ys) * 100   # % change over 28 days
            if drift_4w >= 0.30:
                out.append(emit("tyres", "warning", "Possibile calo pressione gomme",
                    f"Il rapporto ruota/GPS sale di +{_it_number(drift_4w, '.2f')}% su 4 settimane: compatibile "
                    "con uno sgonfiamento gomme (≈ −0,4/0,5 bar). Controlla la pressione a freddo "
                    "col manometro — questo monitor rileva le variazioni, non una pressione bassa costante.",
                    series, "‰"))
            else:
                out.append(emit("tyres", "info", "Gomme · pressione stabile",
                    f"Rapporto ruota/GPS senza deriva significativa ({_it_number(drift_4w, '+.2f')}%/4 settimane "
                    f"su {len(ys)} viaggi). Nessun segnale di sgonfiamento; il manometro resta "
                    "l'unico test assoluto.", series, "‰"))

    out.sort(key=lambda i: _LEVEL_RANK.get(i.get("level"), 3))
    return out
