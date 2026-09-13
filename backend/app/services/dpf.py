"""DPF events inferred from consecutive, contemporaneous ECU observations.

The end state describes the last *observed* condition, never proof of an engine
shutdown. Missing or stale signals produce an explicit unknown state.
"""
from __future__ import annotations

_REGEN_STATUS = "[ECM] DPF regeneration status"
_REGEN_ENABLE = "[ECM] Regeneration enable"
_EGT_AFTER = "[ECM] Exhaust gas temperature after pre-catalytic converter"
_NOX_CAT = "[ECM] Temperature of the NOx catalytic converter"
_EGT_DPF_INLET = "[ECM] EGT at DPF inlet"
_EGT_DPF_OUTLET = "[ECM] EGT at DPF outlet"
_SOOT_CL = "[ECM] Closed loop soot load assessment of the diesel particulate filter"
_DIST_REGEN = "[ECM] Distance traveled since the last regeneration"
_MIN_REGEN_SAMPLES = 3
_MAX_SAMPLE_AGE_S = 30.0
_MIN_REQUEST_DURATION_S = 10.0


def _last_at(series: list, timestamp: float) -> float | None:
    """Read a past sample only within the freshness bound; never look ahead."""
    for ts, value in reversed(series):
        if ts <= timestamp:
            return value if timestamp - ts <= _MAX_SAMPLE_AGE_S else None
    return None


def assess_state(pid_series: dict[str, list[tuple[float, float]]]) -> dict:
    """Return observed DPF state, historical activity and quality warnings."""
    series = {key: sorted(value) for key, value in pid_series.items() if value}
    status = series.get(_REGEN_STATUS, [])
    temperatures = ((_EGT_AFTER, 550), (_NOX_CAT, 550),
                    (_EGT_DPF_INLET, 500), (_EGT_DPF_OUTLET, 500))
    requested = active = False
    first_active = None
    run_start = previous = None
    run_length = 0
    last_run_active = False
    for timestamp, value in status:
        contiguous = previous is not None and timestamp - previous <= _MAX_SAMPLE_AGE_S
        if value >= 1:
            if not contiguous or run_length == 0:
                run_start, run_length = timestamp, 1
            else:
                run_length += 1
        else:
            run_start, run_length = None, 0
        valid = run_length >= _MIN_REGEN_SAMPLES and timestamp - run_start >= _MIN_REQUEST_DURATION_S
        requested = requested or valid
        thermal = any((v := _last_at(series.get(name, []), timestamp)) is not None and v > threshold
                      for name, threshold in temperatures)
        enable = _last_at(series.get(_REGEN_ENABLE, []), timestamp)
        last_run_active = valid and thermal and (enable is None or enable >= 1)
        if last_run_active:
            active = True
            if first_active is None:
                first_active = timestamp
        previous = timestamp

    end = max((rows[-1][0] for rows in series.values()), default=None)
    status_fresh = bool(status and end is not None and end - status[-1][0] <= _MAX_SAMPLE_AGE_S)
    end_active = last_run_active if status_fresh else None
    distance = series.get(_DIST_REGEN, [])
    soot = series.get(_SOOT_CL, [])
    completed = False
    if active:
        before = [v for ts, v in distance if ts <= first_active]
        after = [(ts, v) for ts, v in distance if ts >= first_active]
        if before and before[-1] > 20:
            count = 0
            prev = None
            for ts, value in after:
                if value < before[-1] * 0.1 and (prev is None or ts - prev <= _MAX_SAMPLE_AGE_S):
                    count += 1
                else:
                    count = 1 if value < before[-1] * 0.1 else 0
                if count >= _MIN_REGEN_SAMPLES:
                    completed = True
                prev = ts
        if soot and soot[-1][0] >= first_active and soot[-1][1] <= 0.5:
            completed = True
    warnings = []
    if not status_fresh:
        warnings.append("Stato ECU DPF assente o non recente alla fine della registrazione.")
    if completed:
        state = "completed"
    elif end_active:
        state = "active"
    elif active:
        state = "unknown"
        warnings.append("Rigenerazione osservata, esito finale non confermato dai contatori.")
    elif requested and status_fresh and status[-1][1] >= 1:
        state = "requested"
    elif distance and distance[-1][1] < 20:
        state = "post_regen"
    elif status_fresh:
        state = "idle"
    else:
        state = "unknown"
    return {"state": state, "active": int(active), "endObservedActive": end_active, "warnings": warnings}


def compute_state(pid_series: dict[str, list[tuple[float, float]]]) -> tuple[str, int]:
    """Compatibility wrapper returning (state, activity observed during session)."""
    result = assess_state(pid_series)
    return result["state"], result["active"]
