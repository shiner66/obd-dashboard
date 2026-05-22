"""
RBS byte-swap correction for CarScanner MD1CS003 PIDs.
See briefing §7 for background and reversal formula.
"""

# PID name → {div, mul, ofs} correction parameters.
# Corrected value = swapped_raw * mul / div + ofs
_RBS_FIXES: dict[str, dict] = {
    "[ECM] Distance traveled since the last regeneration":
        {"div": 16, "mul": 1, "ofs": 0},
    "[ECM] Average mileage for the last 10 regenerations":
        {"div": 16, "mul": 1, "ofs": 0},
    "[ECM] Soot clogging level of diesel particulate filter":
        {"div": 10.24, "mul": 1, "ofs": 0},
    "[ECM] Open loop soot load assessment of the diesel particulate filter":
        {"div": 1024, "mul": 1, "ofs": 0},
    "[ECM] EGR valve position":
        {"div": 100, "mul": 1, "ofs": 0},
    "[ECM] Air metering valve position":
        {"div": 100, "mul": 1, "ofs": 0},
    "[ECM] NOx content measured at the inlet of the NOx catalytic converter":
        {"div": 1, "mul": 0.1, "ofs": 0},
    "[ECM] Total mass of additive accumulated in the diesel particulate filter":
        {"div": 128, "mul": 1, "ofs": 0},
    "[ECM] Mileage remaining before diesel particulate filter replacement":
        {"div": 1, "mul": 16, "ofs": 0},
}


def needs_correction(pid_name: str) -> bool:
    return pid_name in _RBS_FIXES


def correct(pid_name: str, value: float) -> float:
    fix = _RBS_FIXES.get(pid_name)
    if fix is None:
        return value
    return _swap(value, fix["div"], fix["mul"], fix["ofs"])


def _swap(value: float, div: float, mul: float, ofs: float) -> float:
    raw = round((value - ofs) * div / mul)
    if raw < 0:
        raw += 0x10000
    if not 0 <= raw <= 0xFFFF:
        return value
    swapped = ((raw & 0xFF) << 8) | ((raw >> 8) & 0xFF)
    return swapped * mul / div + ofs
