"""
DPF state machine — briefing §8.
States: idle | requested | active | completed | post_regen
"""
from __future__ import annotations

_REGEN_STATUS    = "[ECM] DPF regeneration status"
_REGEN_ENABLE    = "[ECM] Regeneration enable"
_EGT_AFTER       = "[ECM] Exhaust gas temperature after pre-catalytic converter"
_NOX_CAT         = "[ECM] Temperature of the NOx catalytic converter"
_EGT_DPF_INLET   = "[ECM] EGT at DPF inlet"
_EGT_DPF_OUTLET  = "[ECM] EGT at DPF outlet"
_SOOT_CL         = "[ECM] Closed loop soot load assessment of the diesel particulate filter"
_DIST_REGEN      = "[ECM] Distance traveled since the last regeneration"

# Minimum number of consecutive regen_status samples ≥ 1 required before
# treating the ECU signal as a real regen request.  A single spike (e.g.
# one poll cycle with value=1) is filtered out as noise.
_MIN_REGEN_SAMPLES = 3


def compute_state(pid_series: dict[str, list[tuple[float, float]]]) -> tuple[str, int]:
    """
    Returns (state_str, regen_active_int) from per-PID (ts, value) series.
    regen_active is 0 or 1.
    """
    def vals(name: str) -> list[float]:
        return [v for _, v in pid_series.get(name, [])]

    regen_status_vals = vals(_REGEN_STATUS)
    regen_enable_vals = vals(_REGEN_ENABLE)
    egt_after_vals    = vals(_EGT_AFTER)
    nox_cat_vals      = vals(_NOX_CAT)
    egt_dpf_i_vals    = vals(_EGT_DPF_INLET)
    egt_dpf_o_vals    = vals(_EGT_DPF_OUTLET)
    soot_cl_vals      = vals(_SOOT_CL)
    dist_regen_recs   = pid_series.get(_DIST_REGEN, [])

    # Require at least _MIN_REGEN_SAMPLES above threshold to reject noise spikes.
    regen_requested = sum(1 for v in regen_status_vals if v >= 1) >= _MIN_REGEN_SAMPLES
    regen_enabled   = (not regen_enable_vals) or max(regen_enable_vals, default=0) >= 1
    thermal_regen   = (
        (bool(egt_after_vals)   and max(egt_after_vals)   > 550) or
        (bool(nox_cat_vals)     and max(nox_cat_vals)     > 550) or
        (bool(egt_dpf_i_vals)   and max(egt_dpf_i_vals)   > 500) or
        (bool(egt_dpf_o_vals)   and max(egt_dpf_o_vals)   > 500)
    )
    regen_active = regen_requested and regen_enabled and thermal_regen

    soot_end = soot_cl_vals[-1] if soot_cl_vals else None

    dist_reset_in_trip = (
        len(dist_regen_recs) >= 2
        and dist_regen_recs[0][1] > 20
        and any(v < dist_regen_recs[0][1] * 0.1 for _, v in dist_regen_recs[1:])
    )

    cooldown_started = (
        bool(dist_regen_recs)
        and dist_regen_recs[0][1] < 1.0
        and thermal_regen
        and not regen_requested
    )

    dist_end = dist_regen_recs[-1][1] if dist_regen_recs else None

    if regen_active and (dist_reset_in_trip or (soot_end is not None and soot_end <= 0.5)):
        state = "completed"
    elif regen_active:
        state = "active"
    elif regen_requested and not thermal_regen:
        state = "requested"
    elif ((dist_end is not None and dist_end < 20 and not regen_active)
          or cooldown_started):
        state = "post_regen"
    else:
        state = "idle"

    return state, (1 if regen_active else 0)
