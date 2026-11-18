"""Network description loaded from data/stations.csv and data/sections.csv (editable, see data/README.md)."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, List

import pandas as pd

from . import config as C

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


@dataclass(frozen=True)
class Section:
    line: str
    idx: int                 # 0 = Wellington end
    from_id: str
    to_id: str
    dist_km: float
    speed_kmh: float
    electrified: bool
    tracks: int
    block: str
    grade_out: float
    grade_in: float

    @property
    def run_min(self) -> float:
        return self.dist_km / self.speed_kmh * 60.0


@dataclass(frozen=True)
class Line:
    code: str
    nodes: tuple             # station ids, Wellington first
    sections: tuple          # Section objects
    terminal: str
    chainage: Dict[str, float]


@lru_cache(maxsize=1)
def stations() -> pd.DataFrame:
    return pd.read_csv(DATA_DIR / "stations.csv")


@lru_cache(maxsize=1)
def lines() -> Dict[str, Line]:
    st = stations().set_index("station_id")
    sec = pd.read_csv(DATA_DIR / "sections.csv")
    out: Dict[str, Line] = {}
    for code, g in sec.groupby("line", sort=False):
        secs: List[Section] = []
        nodes = [g.iloc[0].from_id]
        for i, r in enumerate(g.itertuples()):
            secs.append(Section(code, i, r.from_id, r.to_id, float(r.dist_km), float(r.speed_kmh), bool(r.electrified),
                                int(r.tracks), r.block, float(r.grade_out), float(r.grade_in)))
            nodes.append(r.to_id)
        chain = {n: float(st.loc[n, "chainage_km"]) for n in nodes}
        out[code] = Line(code, tuple(nodes), tuple(secs), nodes[-1], chain)
    return out


def station_name(sid: str) -> str:
    return str(stations().set_index("station_id").loc[sid, "name"])


def dwell_min(sid: str) -> float:
    return float(stations().set_index("station_id").loc[sid, "dwell_min"])


def is_mode_change(sid: str) -> bool:
    return bool(stations().set_index("station_id").loc[sid, "mode_change"])


def total_km(code: str) -> float:
    return sum(s.dist_km for s in lines()[code].sections)


def electrified_km(code: str) -> float:
    return sum(s.dist_km for s in lines()[code].sections if s.electrified)
