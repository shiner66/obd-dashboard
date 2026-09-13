"""Deterministic insight memory driven only by dated diagnostic observations.

`transition` returns a JSON-compatible state record, never a persistence flag.
`reconcile` additionally returns changed records and appendable history snapshots.
Neither function reads a clock, mutates its inputs, or resolves absent evidence.
Naive timestamps follow the application's Europe/Rome wall-clock convention.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import math
from zoneinfo import ZoneInfo

_STATES = {"new", "persistent", "improving", "resolved", "observing"}
_FINDINGS = {"anomaly", "normal", "insufficient", "information"}
_GRADES = {"moderate", "high"}
_SEVERITIES = {"info": 0, "warning": 1, "critical": 2}
_ACTIVE = {"new", "persistent", "improving"}
_BASIS_FIELDS = ("state", "lastFinding", "lastDeviation", "lastConfidenceGrade",
                 "lastSeverity", "lastEvidenceTripIds", "normalStreak", "adverseCount",
                 "hadAnomaly", "episodeStartedAt", "unconfirmed")


def _time(value) -> datetime | None:
    """Normalize valid ISO measurements for ordering, retaining source strings in state."""
    if not isinstance(value, str) or "T" not in value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo("Europe/Rome"))
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def _json_safe(value):
    """Keep JSON-shaped evidence deterministic, replacing non-finite measurements with null."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError("Insight memory accepts JSON-compatible cards only")


def _deviation(card: dict) -> dict | None:
    """Accept a comparable nonnegative deviation only with explicit metric and unit."""
    raw = card.get("deviation")
    if not isinstance(raw, dict):
        return None
    value = raw.get("value")
    metric, unit = raw.get("metric"), raw.get("unit")
    if (not isinstance(metric, str) or not metric.strip() or not isinstance(unit, str)
            or isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0):
        return None
    return {"metric": metric, "value": value, "unit": unit}


def _trip_ids(card: dict) -> list[str]:
    """Use explicit diagnostic trip references, never title text or poll timestamps."""
    evidence = card.get("evidence") or {}
    ids = evidence.get("tripIds", []) if isinstance(evidence, dict) else []
    return list(dict.fromkeys(item for item in ids if isinstance(item, str) and item)) if isinstance(ids, list) else []


def _grade(card: dict) -> str:
    """Read the qualitative evidence grade without inventing a confidence percentage."""
    confidence = card.get("confidence") or {}
    return confidence.get("grade", "low") if isinstance(confidence, dict) else "low"


def _adequate(card: dict) -> bool:
    """Require dated, sufficient diagnostic evidence and reject explicit stale/manual inputs."""
    return (card.get("finding") in {"anomaly", "normal"} and _time(card.get("observedAt")) is not None
            and _grade(card) in _GRADES and bool(_trip_ids(card))
            and not card.get("stale") and not card.get("isStale")
            and card.get("observationKind") not in {"manual", "poll", "configuration"})


def _was_adequate(state: dict) -> bool:
    """Only compare an improvement against an earlier supported adverse measurement."""
    return (state.get("lastFinding") == "anomaly" and state.get("lastConfidenceGrade") in _GRADES
            and bool(state.get("lastEvidenceTripIds")) and not state.get("unconfirmed"))


def _improvement(previous: dict, card: dict) -> bool:
    """Require a 20% decrease in the same deviation or a supported drop in severity."""
    if not _adequate(card) or not _was_adequate(previous):
        return False
    before, after = previous.get("lastDeviation"), _deviation(card)
    if (isinstance(before, dict) and after and before.get("metric") == after["metric"]
            and before.get("unit") == after["unit"] and isinstance(before.get("value"), (int, float))
            and before["value"] > 0 and after["value"] <= before["value"] * 0.8):
        return True
    old_level, new_level = _SEVERITIES.get(previous.get("lastSeverity")), _SEVERITIES.get(card.get("level"))
    return old_level is not None and new_level is not None and new_level < old_level


def _initial(rule_id: str) -> dict:
    """Create a neutral record with no fabricated observation or event date."""
    return {"ruleId": rule_id, "state": "observing", "firstSeen": None, "lastSeen": None,
            "observations": 0, "lastObservationAt": None, "lastFinding": None,
            "lastDeviation": None, "normalStreak": 0, "adverseCount": 0,
            "unconfirmed": True, "unconfirmedReason": "no_measurement", "hadAnomaly": False,
            "episodeStartedAt": None, "lastConfidenceGrade": "low", "lastSeverity": None,
            "lastEvidenceTripIds": [], "lastCard": None, "observationBasis": None}


def _remember_card(state: dict, card: dict) -> None:
    """Update the evidence snapshot without advancing observation counters."""
    state["lastCard"] = deepcopy(card)
    state["lastFinding"] = card["finding"]
    state["lastDeviation"] = _deviation(card)
    state["lastConfidenceGrade"] = _grade(card)
    state["lastSeverity"] = card.get("level")
    state["lastEvidenceTripIds"] = _trip_ids(card)


def _revise(state: dict, card: dict) -> dict:
    """Keep same-measurement corrections without producing recovery or persistence progress."""
    if state.get("lastCard") == card:
        return state
    old_finding, old_state = state.get("lastFinding"), state["state"]
    adequate, finding = _adequate(card), card["finding"]
    basis = state.get("observationBasis") or {}
    if finding == "anomaly":
        state["hadAnomaly"] = True
        state["normalStreak"] = 0
        if old_state == "resolved" and basis.get("state") in _ACTIVE:
            # Correcting the second normal measurement invalidates its resolution;
            # it does not start another episode or erase the earlier adverse count.
            state["state"] = basis["state"]
            state["episodeStartedAt"] = basis.get("episodeStartedAt")
        elif old_state in {"observing", "resolved"}:
            state["state"] = "new"
            state["episodeStartedAt"] = state["lastObservationAt"]
            if old_state == "resolved":
                state["adverseCount"] = 0
        elif old_state == "improving" and not _improvement(basis, card):
            state["state"] = "persistent" if state.get("adverseCount", 0) > 1 else "new"
    elif not adequate or finding != "normal":
        state["normalStreak"] = 0
        # Invalidate a resolution whose confirming measurement has been corrected.
        if old_state == "resolved" and basis.get("state") in _ACTIVE:
            state["state"] = basis["state"]
    elif old_finding != "normal":
        # The earlier measurement can be corrected to normal, but it cannot count
        # as an additional normal observation nor establish an improving state.
        state["normalStreak"] = 0
    if not adequate or old_finding != finding:
        state["unconfirmed"] = True
        state["unconfirmedReason"] = "revised_measurement" if adequate else "insufficient"
    _remember_card(state, card)
    return state


def transition(previous: dict | None, card: dict) -> dict:
    """Return a persistable lifecycle after one diagnostic card.

    A new `observedAt` advances observations exactly once. Moderate/high evidence
    with diagnostic trip IDs can confirm persistence or recovery. Two consecutive
    new sufficient normal observations are required to resolve an earlier anomaly.
    Revisions at the same instant may correct the finding but never create a new
    confirmation. Older measurements do not alter the current record.
    """
    if not isinstance(card, dict) or not isinstance(card.get("ruleId"), str) or not card["ruleId"]:
        raise ValueError("A stable ruleId is required")
    card = _json_safe(card)
    if card.get("finding") not in _FINDINGS:
        raise ValueError("Unsupported diagnostic finding")
    if previous is not None and previous.get("ruleId") != card["ruleId"]:
        raise ValueError("Cannot mix different diagnostic rules")
    state = deepcopy(previous) if previous is not None else _initial(card["ruleId"])
    if state.get("state") not in _STATES:
        raise ValueError("Unsupported lifecycle state")
    if card.get("observationKind") in {"manual", "poll", "configuration"}:
        # Administrative timestamps are not measurements and cannot move the
        # diagnostic cursor beyond later real trips or create a warning by themselves.
        if previous is None:
            _remember_card(state, card)
        state["unconfirmed"], state["unconfirmedReason"] = True, "non_diagnostic"
        return state
    observed = _time(card.get("observedAt"))
    last = _time(state.get("lastObservationAt"))
    if observed is not None and last is not None and observed < last:
        return state
    if observed is not None and last is not None and observed == last:
        return _revise(state, card)
    if observed is None:
        # Missing dates cannot replace the last known measurement or clear it.
        if previous is None:
            if card["finding"] == "anomaly":
                state["state"], state["hadAnomaly"] = "new", True
            _remember_card(state, card)
        state["unconfirmed"], state["unconfirmedReason"] = True, "no_measurement"
        return state

    basis = {key: deepcopy(state.get(key)) for key in _BASIS_FIELDS}
    state["observationBasis"] = basis
    state["observations"] = state.get("observations", 0) + 1
    state["firstSeen"] = state.get("firstSeen") or card["observedAt"]
    state["lastSeen"] = state["lastObservationAt"] = card["observedAt"]
    adequate, finding = _adequate(card), card["finding"]
    state["unconfirmed"] = not adequate
    state["unconfirmedReason"] = None if adequate else "insufficient"
    if finding == "anomaly":
        reopening = state["state"] == "resolved"
        first_anomaly = not state.get("hadAnomaly") or reopening
        state["hadAnomaly"] = True
        state["normalStreak"] = 0
        if first_anomaly:
            state["state"], state["episodeStartedAt"] = "new", card["observedAt"]
            state["adverseCount"] = 1 if adequate else 0
        elif adequate:
            already_confirmed = state.get("adverseCount", 0) > 0 or _was_adequate(basis)
            state["adverseCount"] = state.get("adverseCount", 0) + 1
            state["episodeStartedAt"] = state.get("episodeStartedAt") or card["observedAt"]
            state["state"] = ("improving" if _improvement(basis, card) else "persistent") if already_confirmed else "new"
        # Weak evidence never upgrades an existing warning to persistence/improvement.
    elif finding == "normal" and adequate:
        state["normalStreak"] = state.get("normalStreak", 0) + 1 if state.get("hadAnomaly") else 0
        if state.get("hadAnomaly"):
            if state["normalStreak"] >= 2:
                state["state"] = "resolved"
            elif _improvement(basis, card):
                state["state"] = "improving"
        else:
            state["state"] = "observing"
    else:
        state["normalStreak"] = 0
        if not state.get("hadAnomaly"):
            state["state"] = "observing"
    _remember_card(state, card)
    return state


def _history_snapshot(previous: dict | None, state: dict) -> dict | None:
    """Create history only for new adverse/recovery observations or real availability changes."""
    before = previous or _initial(state["ruleId"])
    if state["observations"] <= before.get("observations", 0):
        return None
    finding = state["lastFinding"]
    meaningful = (finding == "anomaly" or state["state"] != before["state"]
                  or finding == "normal" and before.get("hadAnomaly") and before["state"] != "resolved"
                  or before.get("hadAnomaly") and not before.get("unconfirmed") and state["unconfirmed"])
    if not meaningful:
        return None
    return {"ruleId": state["ruleId"], "observedAt": state["lastObservationAt"],
            "state": state["state"], "finding": finding, "unconfirmed": state["unconfirmed"],
            "observations": state["observations"], "normalStreak": state["normalStreak"],
            "adverseCount": state["adverseCount"], "card": deepcopy(state["lastCard"])}


def reconcile(states: list, cards: list, as_of=None) -> dict:
    """Reconcile a complete rule evaluation without wall-clock-derived transitions.

    `as_of`, when supplied, is an ISO upper bound for diagnostic observation time;
    it does not invent a freshness deadline. Explicit `stale`/`isStale` or manual
    observation flags cannot confirm recovery. Missing rules retain their state
    and last real observation date, with `unconfirmed=True`. Changes contain only
    modified records; history excludes ordinary normal/information baselines.
    """
    if as_of is not None and _time(as_of) is None:
        raise ValueError("as_of must be an ISO datetime")
    cutoff = _time(as_of)
    originals = {state["ruleId"]: deepcopy(state) for state in states}
    current = deepcopy(originals)
    seen, history = set(), []
    ordered = sorted(enumerate(cards), key=lambda item: (_time(item[1].get("observedAt")) or datetime.min.replace(tzinfo=timezone.utc), item[0]))
    for _, card in ordered:
        observed = _time(card.get("observedAt"))
        if cutoff is not None and observed is not None and observed > cutoff:
            continue
        rule_id = card.get("ruleId")
        previous = current.get(rule_id)
        state = transition(previous, card)
        seen.add(rule_id)
        current[rule_id] = state
        snapshot = _history_snapshot(previous, state)
        if snapshot:
            history.append(snapshot)
    for rule_id, state in current.items():
        if rule_id not in seen:
            # A historical query must not downgrade memory from a later measurement.
            last = _time(state.get("lastObservationAt"))
            if cutoff is not None and last is not None and last > cutoff:
                continue
            state["unconfirmed"] = True
            state["unconfirmedReason"] = "missing"
    all_states = [current[rule_id] for rule_id in sorted(current)]
    changes = [state for state in all_states if originals.get(state["ruleId"]) != state]
    return {"states": all_states, "changes": changes, "history": history, "changed": bool(changes)}
