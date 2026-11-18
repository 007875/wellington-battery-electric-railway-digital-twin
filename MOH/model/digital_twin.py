"""
DIGITAL TWIN (DT) layer.

The DT is the system-level, data-driven representation of the physical railway: the asset registry (sections,
overhead-line status, chargers, speed limits, weather) plus the live state of every train unit. It is the single
source of truth for the *environment* that the Petri net reacts to, and it receives the *state* of the units that the
Petri net simulates (two-way coupling: "continuous state updating", research question 2).

    physical system / data feed  --events-->  DigitalTwin  <--queries--  PN engine (rail_net.py)
                                                     ^------observe(unit state)------'

Events are plain records (see data/dt_feed_example.csv). In a real deployment they would arrive from SCADA / traction
power telemetry / charger OCPP status / weather API; in this prototype they come from synthetic scenarios.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

KINDS = ("ohl_outage", "speed_restriction", "charger_derate", "cold_weather")


@dataclass(frozen=True)
class Event:
    kind: str                # one of KINDS
    asset: str               # "WAI:0" (line:section idx) | "MAS" (station) | "*" (network wide)
    start_min: float
    end_min: float
    value: float = 1.0       # speed_restriction: speed factor; charger_derate: power factor; cold_weather: consumption factor


class DigitalTwin:
    def __init__(self, events: Optional[List[Event]] = None):
        self.events: List[Event] = []
        self.unit_state: Dict[str, dict] = {}        # latest observed state of every unit
        self.state_log: List[tuple] = []             # (t, unit, node, soc) observations (optional)
        self.keep_log = False
        for e in events or []:
            self.add_event(e)

    # ------------------------------------------------------------ data feed
    def add_event(self, e: Event) -> None:
        if e.kind not in KINDS:
            raise ValueError(f"unknown event kind {e.kind!r}")
        self.events.append(e)

    @classmethod
    def from_csv(cls, path: Path) -> "DigitalTwin":
        df = pd.read_csv(path)
        return cls([Event(r.kind, r.asset, float(r.start_min), float(r.end_min), float(r.value)) for r in df.itertuples()])

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame([e.__dict__ for e in self.events])

    # ------------------------------------------------------------ asset state queries (used by the PN)
    def _active(self, kind: str, asset: str, t: float):
        for e in self.events:
            if e.kind == kind and e.start_min <= t < e.end_min and (e.asset == asset or e.asset == "*"):
                yield e

    def ohl_ok(self, line: str, sec_idx: int, t: float) -> bool:
        return next(self._active("ohl_outage", f"{line}:{sec_idx}", t), None) is None

    def speed_factor(self, line: str, sec_idx: int, t: float) -> float:
        f = 1.0
        for e in self._active("speed_restriction", f"{line}:{sec_idx}", t):
            f = min(f, e.value)
        return f

    def charger_factor(self, station: str, line: str, t: float, elec_sec_idx: Optional[int] = None) -> float:
        """Available charging power fraction at a station. Wellington charging needs the overhead line of the
        line's first section; terminal chargers use grid power and only suffer explicit derate events."""
        if elec_sec_idx is not None and not self.ohl_ok(line, elec_sec_idx, t):
            return 0.0
        f = 1.0
        for e in self._active("charger_derate", station, t):
            f = min(f, e.value)
        return f

    def consumption_factor(self, t: float) -> float:
        f = 1.0
        for e in self._active("cold_weather", "*", t):
            f = max(f, e.value)
        return f

    # ------------------------------------------------------------ state updating (PN -> DT)
    def observe(self, unit: str, t: float, node: str, soc: float, extra: Optional[dict] = None) -> None:
        self.unit_state[unit] = dict(t=t, node=node, soc=soc, **(extra or {}))
        if self.keep_log:
            self.state_log.append((t, unit, node, soc))

    def snapshot(self) -> pd.DataFrame:
        return pd.DataFrame([dict(unit=u, **s) for u, s in self.unit_state.items()])
