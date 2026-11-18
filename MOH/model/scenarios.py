"""
Disruption scenarios expressed as Digital Twin events (the same record format as data/dt_feed_example.csv).

Severity x duration are the "scenarios of varying severity and duration" used for the resilience analysis.
Section keys are "LINE:section_index" (index 0 = the electrified section next to Wellington).
"""
from __future__ import annotations

from typing import List

from . import config as C
from .digital_twin import DigitalTwin, Event


def events_for(severity: str, duration_h: float, start_min: float = C.EVENT_START_MIN) -> List[Event]:
    end = start_min + duration_h * 60.0
    if severity == "L1":        # localised: Rimutaka speed restriction + Masterton charger at 35 %
        return [Event("speed_restriction", "WAI:1", start_min, end, 0.60),
                Event("charger_derate", "MAS", start_min, end, 0.35)]
    if severity == "L2":        # overhead-line outage Wellington-Upper Hutt (units run on battery, no Wellington charging)
        return [Event("ohl_outage", "WAI:0", start_min, end),
                Event("cold_weather", "*", start_min, end, 1.10)]
    if severity == "L3":        # regional power/overhead-line failure on BOTH lines + PNO charger at 50 % + cold snap
        return ([Event("ohl_outage", f"WAI:0", start_min, end)]
                + [Event("ohl_outage", f"MAN:{i}", start_min, end) for i in range(3)]
                + [Event("charger_derate", "PNO", start_min, end, 0.50),
                   Event("cold_weather", "*", start_min, end, 1.20)])
    if severity == "BASE":      # undisturbed day
        return []
    raise ValueError(severity)


def twin_for(severity: str, duration_h: float) -> DigitalTwin:
    return DigitalTwin(events_for(severity, duration_h))


def window(duration_h: float):
    """KPI window (minutes): event start -> event end + recovery window."""
    return C.EVENT_START_MIN, C.EVENT_START_MIN + duration_h * 60.0 + C.RECOVERY_WINDOW_H * 60.0
