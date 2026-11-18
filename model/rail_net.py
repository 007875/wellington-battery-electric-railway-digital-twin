"""
The full Petri-net model of hybrid battery-electric train operations on the Wellington LNIRIM network.

It is the spatial, resource-constrained generalisation of the conceptual net of the proposal (Figure 2,
see concept_net.py). Mapping of the conceptual places/transitions onto this model:

  Figure 2                              This model (per line L)
  ------------------------------------  ----------------------------------------------------------------------
  P0 Train at origin                    S|L|0|+1   unit ready at Wellington (timetable + start-delay noise)
  T0 Depart                             DEP|L|i|d  needs a free track block (single track = one shared block)
  P1/P2 Battery low / normal            decided by the SoC of the unit token and the DT-informed safety check
  T2 Start moving / P5 Train on route   R|L|i|d     unit running a section (time = distance/speed x noise)
  T4 Pantograph connection to charging  inside DEP: battery gains PAN_CHARGE_KW while the section is electrified
                                        AND the overhead line is healthy (DT status); otherwise battery-only
  T5 Arrive / P6 Train at terminal      ARR|L|i|d, T|L  (terminal) and W|L (back at Wellington)
  T1 Start charging / P3 Charging       CHARGE_T / CHARGE_W need a free charger token CH|L|node (finite resource)
  T3 Finish charging / P4 Battery full  the unit re-appears in S|L|node|dir after the charge duration

Added to the conceptual net (as announced in the proposal): multiple units, stations and loops, single-track blocks,
charger capacity and queues, stochastic run/dwell/consumption variability, disruption events from the Digital Twin,
stranding with rescue, and a DT-informed dispatch policy (intervention C).

Time unit: minutes since midnight.
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from typing import Dict, FrozenSet, List, Optional, Tuple

import numpy as np

from . import config as C
from . import network as N
from . import scenarios as S
from .digital_twin import DigitalTwin
from .petri import Net

INF = float("inf")


# ----------------------------------------------------------------------------------------------- charging maths
def charge_minutes(soc0: float, soc1: float, kw: float) -> float:
    """Minutes to charge from soc0 to soc1 at nominal power kw (taper above TAPER_SOC)."""
    if soc1 <= soc0:
        return 0.0
    if kw <= 0:
        return INF
    e1 = max(0.0, min(soc1, C.TAPER_SOC) - soc0) * C.BATTERY_KWH
    e2 = max(0.0, soc1 - max(soc0, C.TAPER_SOC)) * C.BATTERY_KWH
    return 60.0 * (e1 / kw + e2 / (kw * C.TAPER_FACTOR))


def soc_after(soc0: float, minutes: float, kw: float) -> float:
    """State of charge after charging for `minutes` at nominal power kw (inverse of charge_minutes)."""
    if kw <= 0 or minutes <= 0:
        return soc0
    if soc0 < C.TAPER_SOC:
        t1 = (C.TAPER_SOC - soc0) * C.BATTERY_KWH / kw * 60.0
        if minutes <= t1:
            return soc0 + kw * minutes / 60.0 / C.BATTERY_KWH
        return min(C.SOC_MAX, C.TAPER_SOC + kw * C.TAPER_FACTOR * (minutes - t1) / 60.0 / C.BATTERY_KWH)
    return min(C.SOC_MAX, soc0 + kw * C.TAPER_FACTOR * minutes / 60.0 / C.BATTERY_KWH)


# ----------------------------------------------------------------------------------------------- timetable
def _dwell(node_id: str) -> float:
    return N.dwell_min(node_id) + (C.MODE_CHANGE_MIN if N.is_mode_change(node_id) else 0.0)


def build_timetable(code: str, k: int, t0: float, layover: float) -> "Trip":
    line = N.lines()[code]
    n = len(line.nodes)
    dep_out, arr_out = [0.0] * n, [0.0] * n
    dep_in, arr_in = [0.0] * n, [0.0] * n
    t = t0
    dep_out[0] = t
    for i, sec in enumerate(line.sections):
        t += sec.run_min * (1 + C.TIMETABLE_PAD)
        arr_out[i + 1] = t
        t += layover if i + 1 == n - 1 else _dwell(line.nodes[i + 1])
        dep_out[i + 1] = t
    dep_in[n - 1] = dep_out[n - 1]
    for i in range(n - 2, -1, -1):
        t += line.sections[i].run_min * (1 + C.TIMETABLE_PAD)
        arr_in[i] = t
        if i > 0:
            t += _dwell(line.nodes[i])
            dep_in[i] = t
    return Trip(k, dep_out, arr_out, dep_in, arr_in)


def _trip_starts(code: str) -> List[float]:
    cfg = C.LINES[code]
    n = int((C.LAST_DEP_MIN - C.FIRST_DEP_MIN) // cfg["headway_min"]) + 1
    return [C.FIRST_DEP_MIN + cfg["first_dep_offset_min"] + k * cfg["headway_min"] for k in range(n)]


def timetable_conflicts(code: str, layover: float) -> float:
    """Minutes of overlapping outbound/inbound occupation of single-track blocks in the published timetable."""
    line = N.lines()[code]
    tts = [build_timetable(code, k, t0, layover) for k, t0 in enumerate(_trip_starts(code))]
    tot = 0.0
    for sec in line.sections:
        if sec.tracks != 1:
            continue
        i = sec.idx
        for a_t in tts:
            a = (a_t.dep_out[i], a_t.arr_out[i + 1] + C.CLEARANCE_MIN)
            for b_t in tts:
                b = (b_t.dep_in[i + 1], b_t.arr_in[i] + C.CLEARANCE_MIN)
                tot += max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    return tot


_LAYOVER_CACHE: Dict[str, float] = {}


def tuned_layover(code: str) -> float:
    """Terminal layover (inside C.TERMINAL_LAYOVER_RANGE) that gives a conflict-free single-track timetable."""
    if code not in _LAYOVER_CACHE:
        lo, hi = C.TERMINAL_LAYOVER_RANGE
        cands = [lo + 0.25 * i for i in range(int((hi - lo) / 0.25) + 1)]
        best = min(cands, key=lambda L: (round(timetable_conflicts(code, L), 3), abs(L - 30.0)))
        _LAYOVER_CACHE[code] = best
    return _LAYOVER_CACHE[code]


# ----------------------------------------------------------------------------------------------- tokens
class Unit:
    """Colour of a train-unit token."""
    __slots__ = ("uid", "line", "trips", "ti", "k", "dir", "soc", "held", "t_arr", "chk")

    def __init__(self, uid: str, line: str, trips: List[int]):
        self.uid, self.line, self.trips = uid, line, trips
        self.ti = 0
        self.k = trips[0]
        self.dir = 1
        self.soc = C.SOC_MAX
        self.held = 0.0
        self.t_arr = 0.0
        self.chk = (None, None)


@dataclass
class Trip:
    k: int
    dep_out: List[float]
    arr_out: List[float]
    dep_in: List[float]
    arr_in: List[float]
    act_arr_out: Optional[float] = None
    act_arr_in: Optional[float] = None
    cancelled: bool = False
    stranded_out: bool = False
    stranded_in: bool = False


def all_combos() -> List[FrozenSet[str]]:
    keys = sorted(C.INTERVENTIONS)
    out = []
    for r in range(len(keys) + 1):
        out += [frozenset(c) for c in itertools.combinations(keys, r)]
    return out


def combo_label(ints) -> str:
    return "+".join(sorted(ints)) if ints else "None"


def combo_cost(ints) -> float:
    return sum(C.INTERVENTIONS[i]["cost_nzd_m_yr"] for i in ints)


def combo_feasibility(ints) -> int:
    return min([C.INTERVENTIONS[i]["feasibility"] for i in ints], default=5)


# ----------------------------------------------------------------------------------------------- simulation
class Sim:
    def __init__(self, dt: DigitalTwin, ints=frozenset(), seed: int = 0, trace: bool = False):
        self.dt = dt
        self.ints = frozenset(ints)
        self.policy_c = "C" in self.ints
        self.kw_nom = C.B_CHARGER_KW if "B" in self.ints else C.CHARGER_KW
        self.rescue_min = C.E_RESCUE_MIN if "E" in self.ints else C.RESCUE_MIN
        self.term_chargers = C.A_TERMINAL_CHARGERS if "A" in self.ints else 1
        self.ext_km = C.D_EXTRA_ELEC_KM if "D" in self.ints else 0.0
        self.trace_on = trace
        self.trace: List[tuple] = []
        self.lines = N.lines()
        self.net = Net()
        self.tt: Dict[str, List[Trip]] = {}
        self.layover: Dict[str, float] = {}
        self.noise: Dict[str, dict] = {}
        self.units: Dict[str, List[Unit]] = {}
        self.strands = 0
        self.charge_stats = {"queue_wait": [], "busy_min": 0.0, "n_charges": 0}
        self.min_soc = 1.0
        self.soc_obs: List[Tuple[float, float]] = []
        self.hold_events = 0
        for li, code in enumerate(self.lines):
            self._build_line(code, li, seed)

    # ------------------------------------------------------------------ helpers
    def _efrac(self, sec: N.Section) -> float:
        if sec.electrified:
            return 1.0
        if self.ext_km > 0 and sec.idx > 0 and self.lines[sec.line].sections[sec.idx - 1].electrified:
            return min(1.0, self.ext_km / sec.dist_km)
        return 0.0

    @staticmethod
    def _ohl_idx(sec: N.Section) -> int:
        return sec.idx if sec.electrified else sec.idx - 1

    def _dwell(self, node_id: str) -> float:
        return _dwell(node_id)

    # ------------------------------------------------------------------ DT-based route energy projection
    def _route_min(self, code: str, node_idx: int, d: int, soc: float, t: float, assume_ok: bool) -> float:
        line = self.lines[code]
        secs = line.sections
        seq = secs[node_idx:] if d > 0 else list(reversed(secs[:node_idx]))
        dt = self.dt
        m = soc
        for sec in seq:
            sf = 1.0 if assume_ok else dt.speed_factor(code, sec.idx, t)
            run = sec.run_min / sf
            ef = self._efrac(sec)
            ohl = True if (assume_ok or ef == 0.0) else dt.ohl_ok(code, self._ohl_idx(sec), t)
            ef_eff = ef if ohl else 0.0
            grade = sec.grade_out if d > 0 else sec.grade_in
            drain = C.CONS_KWH_KM * sec.dist_km * (1 - ef_eff) * grade * dt.consumption_factor(t) / C.BATTERY_KWH
            gain = C.PAN_CHARGE_KW * run * ef_eff / 60.0 / C.BATTERY_KWH
            if d > 0:
                soc = min(C.SOC_MAX, soc + gain) - drain
            else:
                soc = min(C.SOC_MAX, soc - drain + gain) if soc - drain > 0 else soc - drain
            m = min(m, soc)
            t += run + 1.0
        return m

    def _decide(self, u: Unit, node_idx: int, d: int, now: float) -> int:
        """DT-informed dispatch decision (policy C) for a unit that is ready to depart:
        0 = depart, 1 = hold (conditions will improve by waiting), 2 = recharge first (a working charger fixes it)."""
        if not self.policy_c or u.held >= C.DT_MAX_HOLD_MIN:
            return 0
        key = (now, node_idx, d)
        if u.chk[0] == key:
            return u.chk[1]
        code, thr = u.line, C.DT_HOLD_MIN_SOC
        line = self.lines[code]
        last = len(line.nodes) - 1
        if self._route_min(code, node_idx, d, u.soc, now, False) >= thr:
            res = 0
        else:
            has_charger = (node_idx == 0 and d == 1) or (node_idx == last and d == -1)
            if has_charger:
                kw = self._charge_kw(line.nodes[node_idx], code, now)
                full_ok = self._route_min(code, node_idx, d, C.SOC_MAX, now, False) >= thr
                free_ok = self._route_min(code, node_idx, d, C.SOC_MAX, now, True) >= thr
                if kw > 0 and full_ok and u.soc < C.SOC_MAX - 0.02:
                    res = 2
                elif free_ok:
                    res = 1
                else:
                    res = 0
            else:
                res = 1 if self._route_min(code, node_idx, d, u.soc, now, True) >= thr else 0
        u.chk = (key, res)
        return res

    def _need(self, code: str, node_idx: int, d: int, t: float) -> float:
        """Smallest starting SoC with which the remaining route keeps SOC_RESERVE (bisection on the DT projection)."""
        if self._route_min(code, node_idx, d, C.SOC_MAX, t, False) < C.SOC_RESERVE:
            return C.SOC_MAX
        lo, hi = 0.15, C.SOC_MAX
        for _ in range(9):
            mid = 0.5 * (lo + hi)
            if self._route_min(code, node_idx, d, mid, t, False) >= C.SOC_RESERVE:
                hi = mid
            else:
                lo = mid
        return hi

    # ------------------------------------------------------------------ net construction
    def _build_line(self, code: str, li: int, seed: int) -> None:
        net, line, cfg = self.net, self.lines[code], C.LINES[code]
        n_nodes, n_sec = len(line.nodes), len(line.sections)
        last = n_nodes - 1
        t0s = _trip_starts(code)
        n_trips = len(t0s)
        self.layover[code] = tuned_layover(code)
        self.tt[code] = [build_timetable(code, k, t0s[k], self.layover[code]) for k in range(n_trips)]
        rng = np.random.default_rng([seed, li])
        self.noise[code] = dict(
            rt=rng.lognormal(0.0, C.RUNTIME_SIGMA, (n_trips, n_sec, 2)).tolist(),
            en=rng.lognormal(0.0, C.CONSUMPTION_SIGMA, (n_trips, n_sec, 2)).tolist(),
            dw=rng.exponential(C.DWELL_EXTRA_MEAN_MIN, (n_trips, n_nodes, 2)).tolist(),
            bg=rng.exponential(C.BACKGROUND_DELAY_MEAN_MIN, (n_trips, n_sec, 2)).tolist(),
            sd=rng.exponential(C.START_DELAY_MEAN_MIN, n_trips).tolist(),
        )
        # ---- places
        for i in range(n_nodes):
            for d in (1, -1):
                net.add_place(f"S|{code}|{i}|{d}")
        for i in range(n_sec):
            for d in (1, -1):
                net.add_place(f"R|{code}|{i}|{d}")
        for sec in line.sections:
            for key in self._block_keys(sec):
                net.add_place(f"B|{code}|{key}")
        for nm in ("T", "W", "DONE"):
            net.add_place(f"{nm}|{code}")
        net.add_place(f"CH|{code}|W")
        net.add_place(f"CH|{code}|T")
        # ---- initial marking: free blocks, chargers, units
        for sec in line.sections:
            for key in self._block_keys(sec):
                net.put(f"B|{code}|{key}", None, t=0.0)
        for _ in range(int(N.stations().set_index("station_id").loc["WEL", "charger_count"])):
            net.put(f"CH|{code}|W", None, t=0.0)
        for _ in range(self.term_chargers):
            net.put(f"CH|{code}|T", None, t=0.0)
        units = []
        for u in range(cfg["units"]):
            trips = list(range(u, n_trips, cfg["units"]))
            if not trips:
                continue
            un = Unit(f"{code}-{u + 1}", code, trips)
            units.append(un)
            ready = self.tt[code][trips[0]].dep_out[0] + self.noise[code]["sd"][trips[0]]
            net.put(f"S|{code}|0|1", un, t=ready)
        self.units[code] = units
        # ---- transitions: hold / cancel / depart / arrive
        for i in range(n_nodes):
            for d in (1, -1):
                if (d == 1 and i == last) or (d == -1 and i == 0):
                    continue
                self._add_dep_hold(code, line, i, d, last)
        for sec in line.sections:
            for d in (1, -1):
                net.add_transition(f"ARR|{code}|{sec.idx}|{d}", [(f"R|{code}|{sec.idx}|{d}", None)],
                                   lambda b, now, sec=sec, d=d: self._arr(code, sec, d, b, now))
        net.add_transition(f"CHARGE_T|{code}", [(f"T|{code}", None), (f"CH|{code}|T", None)],
                           lambda b, now: self._charge_t(code, b, now))
        net.add_transition(f"CHARGE_W|{code}", [(f"W|{code}", None), (f"CH|{code}|W", None)],
                           lambda b, now: self._charge_w(code, b, now))

    @staticmethod
    def _block_keys(sec: N.Section) -> List[str]:
        return [sec.block] if sec.tracks == 1 else [f"{sec.block}|1", f"{sec.block}|-1"]

    @staticmethod
    def _block_key(sec: N.Section, d: int) -> str:
        return sec.block if sec.tracks == 1 else f"{sec.block}|{d}"

    def _add_dep_hold(self, code: str, line: N.Line, i: int, d: int, last: int) -> None:
        net = self.net
        sec = line.sections[i] if d == 1 else line.sections[i - 1]
        place = f"S|{code}|{i}|{d}"
        bplace = f"B|{code}|{self._block_key(sec, d)}"
        is_origin_out = (i == 0 and d == 1)
        has_charger = is_origin_out or (i == last and d == -1)
        net.add_transition(f"HOLD|{code}|{i}|{d}", [(place, None)],
                           lambda b, now: [(place, self._hold(b[0]), C.DT_HOLD_POLL_MIN)],
                           guard=lambda b, now: self._decide(b[0], i, d, now) == 1
                           and not (is_origin_out and b[0].held >= C.DT_CANCEL_AFTER_MIN))
        if is_origin_out:
            net.add_transition(f"CANCEL|{code}", [(place, None)], lambda b, now: self._cancel(code, b, now),
                               guard=lambda b, now: b[0].held >= C.DT_CANCEL_AFTER_MIN and self._decide(b[0], i, d, now) == 1)
        if has_charger:
            chp = f"CH|{code}|{'W' if i == 0 else 'T'}"
            net.add_transition(f"RECHARGE|{code}|{i}|{d}", [(place, None), (chp, None)],
                               lambda b, now: self._recharge(code, i, d, place, chp, b, now),
                               guard=lambda b, now: self._decide(b[0], i, d, now) == 2)
        net.add_transition(f"DEP|{code}|{sec.idx}|{d}", [(place, None), (bplace, None)],
                           lambda b, now: self._dep(code, sec, d, b, now),
                           guard=lambda b, now: self._decide(b[0], i, d, now) == 0)

    # ------------------------------------------------------------------ transition actions
    def _hold(self, u: Unit) -> Unit:
        u.held += C.DT_HOLD_POLL_MIN
        self.hold_events += 1
        return u

    def _cancel(self, code: str, b, now: float):
        u = b[0]
        self.tt[code][u.k].cancelled = True
        u.held = 0.0
        u.t_arr = now
        return [(f"W|{code}", u, 0.0)]

    def _recharge(self, code: str, node_idx: int, d: int, place: str, chp: str, b, now: float):
        """DT dispatcher sends a unit that cannot safely depart back to a (now working) charger."""
        u: Unit = b[0]
        node_id = self.lines[code].nodes[node_idx]
        kw = self._charge_kw(node_id, code, now)
        dur, soc1 = self._plan_charge(code, u, kw, now, now, node_idx, d)
        dur = max(dur, C.DT_HOLD_POLL_MIN)
        if self.trace_on:
            self.trace.append((1, list(self.lines).index(code), u.k, 0 if node_idx == 0 else 1, node_idx, round(now, 1),
                               round(now + dur, 1), round(u.soc, 3), round(soc1, 3), 0))
        if node_idx != 0:
            self.charge_stats["busy_min"] += dur
        u.soc = soc1
        return [(place, u, dur), (chp, None, dur)]

    def _dep(self, code: str, sec: N.Section, d: int, b, now: float):
        u: Unit = b[0]
        dt, k, di = self.dt, u.k, (0 if d > 0 else 1)
        nz = self.noise[code]
        sf = dt.speed_factor(code, sec.idx, now)
        run = sec.run_min / sf * nz["rt"][k][sec.idx][di]
        ef = self._efrac(sec)
        ohl = True if ef == 0.0 else dt.ohl_ok(code, self._ohl_idx(sec), now)
        if sec.electrified and ohl and sec.tracks == 2:
            run += nz["bg"][k][sec.idx][di]                  # interference from suburban traffic
        ef_eff = ef if ohl else 0.0
        grade = sec.grade_out if d > 0 else sec.grade_in
        drain = (C.CONS_KWH_KM * sec.dist_km * (1 - ef_eff) * grade * dt.consumption_factor(now)
                 * nz["en"][k][sec.idx][di] / C.BATTERY_KWH)
        gain = C.PAN_CHARGE_KW * run * ef_eff / 60.0 / C.BATTERY_KWH
        soc0 = u.soc
        if d > 0:
            start = min(C.SOC_MAX, soc0 + gain)
        else:
            start = soc0
        stranded = drain > 0 and (start - drain) < C.SOC_HARD
        if stranded:
            frac = max(0.0, (start - C.SOC_HARD) / drain)
            total = run * frac + self.rescue_min + (1 - frac) * sec.dist_km / C.TOW_SPEED_KMH * 60.0
            soc1 = C.SOC_HARD
            self.strands += 1
            tr = self.tt[code][k]
            if d > 0:
                tr.stranded_out = True
            else:
                tr.stranded_in = True
        else:
            total = run
            soc1 = start - drain
            if d < 0:
                soc1 = min(C.SOC_MAX, soc1 + gain)
        u.soc = soc1
        u.held = 0.0
        if self.trace_on:
            li = list(self.lines).index(code)
            self.trace.append((0, li, k, d, sec.idx, round(now, 1), round(now + total, 1), round(soc0, 3), round(soc1, 3),
                               int(stranded)))
        return [(f"R|{code}|{sec.idx}|{d}", u, total)]

    def _arr(self, code: str, sec: N.Section, d: int, b, now: float):
        u: Unit = b[0]
        line, k = self.lines[code], u.k
        di = 0 if d > 0 else 1
        node_idx = sec.idx + 1 if d > 0 else sec.idx
        node_id = line.nodes[node_idx]
        tr = self.tt[code][k]
        self.dt.observe(u.uid, now, node_id, u.soc)
        self.min_soc = min(self.min_soc, u.soc)
        outs = [(f"B|{code}|{self._block_key(sec, d)}", None, C.CLEARANCE_MIN)]
        last = len(line.nodes) - 1
        u.t_arr = now
        if d > 0 and node_idx == last:
            tr.act_arr_out = now
            outs.append((f"T|{code}", u, 0.0))
        elif d < 0 and node_idx == 0:
            tr.act_arr_in = now
            outs.append((f"W|{code}", u, 0.0))
        else:
            extra = self.noise[code]["dw"][k][node_idx][di]
            sched = tr.dep_out[node_idx] if d > 0 else tr.dep_in[node_idx]
            ready = max(now + self._dwell(node_id) + extra, sched)
            outs.append((f"S|{code}|{node_idx}|{d}", u, ready - now))
        return outs

    def _charge_kw(self, node_id: str, code: str, now: float) -> float:
        elec_idx = 0 if node_id == "WEL" else None
        return self.kw_nom * self.dt.charger_factor(node_id, code, now, elec_idx)

    def _charge_t(self, code: str, b, now: float):
        u: Unit = b[0]
        line = self.lines[code]
        last = len(line.nodes) - 1
        node_id = line.nodes[last]
        tr = self.tt[code][u.k]
        kw = self._charge_kw(node_id, code, now)
        sched = tr.dep_in[last]
        dur, soc1 = self._plan_charge(code, u, kw, now, sched, last, -1)
        self.charge_stats["queue_wait"].append((now, now - u.t_arr))
        self.charge_stats["busy_min"] += dur
        self.charge_stats["n_charges"] += 1
        ready = max(now + dur, now + C.MIN_TURN_MIN, sched)
        if self.trace_on:
            self.trace.append((1, list(self.lines).index(code), u.k, 1, last, round(now, 1), round(now + dur, 1),
                               round(u.soc, 3), round(soc1, 3), 0))
        u.soc = soc1
        self.min_soc = min(self.min_soc, soc1)
        return [(f"S|{code}|{last}|-1", u, ready - now), (f"CH|{code}|T", None, dur)]

    def _charge_w(self, code: str, b, now: float):
        u: Unit = b[0]
        u.ti += 1
        if u.ti >= len(u.trips):
            return [(f"DONE|{code}", u, 0.0), (f"CH|{code}|W", None, 0.0)]
        u.k = u.trips[u.ti]
        u.dir = 1
        u.held = 0.0
        tr = self.tt[code][u.k]
        kw = self._charge_kw("WEL", code, now)
        sched = tr.dep_out[0]
        dur, soc1 = self._plan_charge(code, u, kw, now, sched, 0, 1)
        if self.trace_on:
            self.trace.append((1, list(self.lines).index(code), u.k, 0, 0, round(now, 1), round(now + dur, 1),
                               round(u.soc, 3), round(soc1, 3), 0))
        u.soc = soc1
        ready = max(now + dur, now + C.MIN_TURN_MIN, sched) + self.noise[code]["sd"][u.k]
        return [(f"S|{code}|0|1", u, ready - now), (f"CH|{code}|W", None, dur)]

    def _plan_charge(self, code: str, u: Unit, kw: float, now: float, sched: float, node_idx: int, d: int):
        """Charging duration and resulting SoC. Baseline: fixed target, limited to the scheduled layover.
        Policy C (DT-informed): target = what the DT projects the next leg needs (+ margin), not limited by the timetable."""
        if kw <= 0:
            return C.MIN_TURN_MIN, u.soc
        if self.policy_c:
            need = self._need(code, node_idx, d, max(now + C.MIN_TURN_MIN, sched))
            target = min(C.SOC_MAX, max(C.DT_MIN_CHARGE_TARGET, need + C.DT_CHARGE_MARGIN))
            dur = charge_minutes(u.soc, target, kw)
            return dur, max(u.soc, target) if dur > 0 else u.soc
        window = max(C.MIN_TURN_MIN, sched - now)
        t_target = charge_minutes(u.soc, C.BASE_CHARGE_TARGET, kw)
        t_floor = charge_minutes(u.soc, C.BASE_MIN_DEPART_SOC, kw)      # static rule: do not leave below 80 %
        dur = max(min(window, t_target), t_floor)
        return dur, max(u.soc, soc_after(u.soc, dur, kw))

    # ------------------------------------------------------------------ run + KPIs
    def run(self) -> None:
        self.net.run(until=C.DAY_END_MIN)

    def services(self):
        """Yield (line, k, direction, sched_dep, sched_arr, actual_arr or None, cancelled)."""
        for code, trips in self.tt.items():
            last = len(self.lines[code].nodes) - 1
            for tr in trips:
                yield (code, tr.k, "out", tr.dep_out[0], tr.arr_out[last], tr.act_arr_out, tr.cancelled)
                yield (code, tr.k, "in", tr.dep_in[last], tr.arr_in[0], tr.act_arr_in, tr.cancelled)

    def kpis(self, duration_h: float) -> dict:
        w0, w1 = S.window(duration_h)
        pax = C.PAX_CAPACITY * C.LOAD_FACTOR
        n_w = ok15_w = ok5_w = n_day = ok5_day = cancelled = 0
        delays_w: List[float] = []
        pax_delay = 0.0
        hour_n, hour_ok = {}, {}
        for code, k, dr, sd, sa, aa, canc in self.services():
            n_day += 1
            if canc or aa is None:
                delay, completed = None, False
            else:
                delay, completed = aa - sa, True
            cancelled += 1 if canc else 0
            ok15 = completed and delay <= C.SERVICE_OK_MIN
            ok5 = completed and delay <= C.ON_TIME_MIN
            ok5_day += ok5
            pax_delay += pax * (max(0.0, delay) if completed else 60.0)
            h = int(sd // 60)
            hour_n[h] = hour_n.get(h, 0) + 1
            hour_ok[h] = hour_ok.get(h, 0) + (1 if ok15 else 0)
            if w0 <= sd < w1:
                n_w += 1
                ok15_w += ok15
                ok5_w += ok5
                if completed:
                    delays_w.append(delay)
        hours = list(range(C.DAY_START_MIN // 60, 24))
        hour_pct = [100.0 * hour_ok[h] / hour_n[h] if hour_n.get(h) else float("nan") for h in hours]
        ev_end = C.EVENT_START_MIN + duration_h * 60.0
        rec_h = self._recovery_hours(hours, hour_pct, ev_end)
        qw = [w for (t, w) in self.charge_stats["queue_wait"] if w0 <= t < w1]
        span_h = (C.DAY_END_MIN - C.DAY_START_MIN) / 60.0
        n_term = self.term_chargers * len(self.lines)
        return dict(
            service_pct=100.0 * ok15_w / n_w if n_w else 100.0,
            otp5_window_pct=100.0 * ok5_w / n_w if n_w else 100.0,
            otp5_day_pct=100.0 * ok5_day / n_day if n_day else 100.0,
            services_in_window=n_w,
            mean_delay_min=float(np.mean(delays_w)) if delays_w else 0.0,
            p95_delay_min=float(np.percentile(delays_w, 95)) if delays_w else 0.0,
            cancelled_services=cancelled,
            strandings=self.strands,
            min_soc_pct=100.0 * self.min_soc,
            passenger_delay_h=pax_delay / 60.0,
            charger_wait_min=float(np.mean(qw)) if qw else 0.0,
            charger_util_pct=100.0 * self.charge_stats["busy_min"] / 60.0 / (n_term * span_h),
            recovery_h=rec_h,
            holds=self.hold_events,
            hour_pct=hour_pct,
        )

    @staticmethod
    def _recovery_hours(hours, pct, ev_end_min: float, thr: float = 90.0) -> float:
        """Hours after the event end until the hourly service reliability is back above thr for good."""
        start_h = int(ev_end_min // 60)
        rec = None
        for h in range(len(hours) - 1, -1, -1):
            v = pct[h]
            if hours[h] < start_h:
                break
            if not math.isnan(v) and v < thr:
                break
            rec = hours[h]
        if rec is None:
            return float(hours[-1] + 1) - ev_end_min / 60.0           # not recovered before the end of service
        return max(0.0, rec - ev_end_min / 60.0)


# ----------------------------------------------------------------------------------------------- public API
def simulate(severity: str, duration_h: float, ints=frozenset(), seed: int = 0, trace: bool = False,
             dt: Optional[DigitalTwin] = None):
    """Run one operating day. Returns (kpi dict, Sim) - the Sim holds the trace if trace=True."""
    dt = dt if dt is not None else S.twin_for(severity, duration_h)
    sim = Sim(dt, ints, seed=seed, trace=trace)
    sim.run()
    k = sim.kpis(duration_h)
    k.update(severity=severity, duration_h=duration_h, interventions=combo_label(ints),
             cost_nzd_m_yr=combo_cost(ints), feasibility=combo_feasibility(ints), seed=seed)
    return k, sim
