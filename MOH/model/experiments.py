"""
Simulation experiments: severity x duration x 32 intervention packages, Monte Carlo replications with common random
numbers (the same seeds are used for every package, so differences between packages are due to the package).

    python -m model.experiments [reps]

Outputs (results/):
    experiment_grid.csv     one row per (severity, duration, package): mean KPIs over the replications
    recommendations.csv     cheapest feasible package meeting the service target (else best achievable)
    pareto_front.csv        non-dominated packages (service reliability up, annualised cost down)
    hourly_curves.json      hourly service-reliability curve (mean over replications) per scenario and package
    traces.json             one traced operating day (seed 0) per scenario and package, for the HTML dashboard
    baseline_day.csv        undisturbed reference day
"""
from __future__ import annotations

import json
import warnings
warnings.filterwarnings("ignore", category=RuntimeWarning)
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from . import config as C
from .rail_net import all_combos, combo_label, simulate

RESULTS_DIR = Path(__file__).resolve().parents[1] / "results"

MEAN_COLS = ["service_pct", "otp5_window_pct", "otp5_day_pct", "mean_delay_min", "p95_delay_min", "cancelled_services",
             "strandings", "min_soc_pct", "passenger_delay_h", "charger_wait_min", "charger_util_pct", "recovery_h",
             "holds", "services_in_window"]


def run_cell(severity: str, duration_h: float, ints, reps: int, keep_trace: bool = False):
    ks, trace = [], None
    for r in range(reps):
        k, sim = simulate(severity, duration_h, ints, seed=r, trace=(keep_trace and r == 0))
        ks.append(k)
        if keep_trace and r == 0:
            trace = sim.trace
    row = dict(severity=severity, duration_h=duration_h, interventions=combo_label(ints),
               cost_nzd_m_yr=ks[0]["cost_nzd_m_yr"], feasibility=ks[0]["feasibility"], reps=reps)
    for c in MEAN_COLS:
        row[c] = float(np.mean([k[c] for k in ks]))
    row["service_pct_sd"] = float(np.std([k["service_pct"] for k in ks]))
    hp = np.array([k["hour_pct"] for k in ks], dtype=float)
    with np.errstate(all="ignore"):
        hour = np.nanmean(hp, axis=0)
    return row, [None if np.isnan(x) else round(float(x), 1) for x in hour], trace


def pareto_flag(df: pd.DataFrame) -> pd.Series:
    """Non-dominated on (service_pct maximised, cost_nzd_m_yr minimised)."""
    flags = []
    for _, r in df.iterrows():
        dom = ((df.service_pct >= r.service_pct - 1e-9) & (df.cost_nzd_m_yr <= r.cost_nzd_m_yr + 1e-9)
               & ((df.service_pct > r.service_pct + 1e-9) | (df.cost_nzd_m_yr < r.cost_nzd_m_yr - 1e-9))).any()
        flags.append(not dom)
    return pd.Series(flags, index=df.index)


def recommend(df: pd.DataFrame, target=C.TARGET_SERVICE_PCT, min_feas=C.MIN_FEASIBILITY):
    """Cheapest package meeting the service target at acceptable feasibility; else best achievable."""
    ok = df[(df.service_pct >= target) & (df.feasibility >= min_feas)]
    if not ok.empty:
        return ok.sort_values(["cost_nzd_m_yr", "mean_delay_min"]).iloc[0], True
    feas = df[df.feasibility >= min_feas]
    pool = feas if not feas.empty else df
    best = pool.service_pct.max()
    near = pool[pool.service_pct >= best - 0.5]            # within half a point of the best: take the cheapest
    return near.sort_values(["cost_nzd_m_yr", "mean_delay_min"]).iloc[0], False


def run_all(reps: int = C.DEFAULT_REPS, verbose: bool = True):
    rows, curves, traces = [], {}, {}
    t0 = time.time()
    combos = all_combos()
    total = len(C.SEVERITIES) * len(C.DURATIONS_H) * len(combos)
    n = 0
    import pickle
    part_dir = RESULTS_DIR / "_parts"
    part_dir.mkdir(exist_ok=True)
    for sev in C.SEVERITIES:
        part = part_dir / f"{sev}_{reps}.pkl"            # resumable: finished severities are cached
        if part.exists():
            r_, c_, t_ = pickle.loads(part.read_bytes())
            rows += r_; curves.update(c_); traces.update(t_); n += len(r_)
            continue
        p_rows, p_curves, p_traces = [], {}, {}
        for dur in C.DURATIONS_H:
            for ints in combos:
                row, hour, tr = run_cell(sev, dur, ints, reps, keep_trace=True)
                p_rows.append(row)
                key = f"{sev}|{dur}|{row['interventions']}"
                p_curves[key] = hour
                p_traces[key] = tr
                n += 1
            if verbose:
                print(f"  {sev} {dur} h done  ({n}/{total} cells, {time.time() - t0:.0f} s)", flush=True)
        part.write_bytes(pickle.dumps((p_rows, p_curves, p_traces)))
        rows += p_rows; curves.update(p_curves); traces.update(p_traces)
    grid = pd.DataFrame(rows)
    parts, recs = [], []
    for (sev, dur), d in grid.groupby(["severity", "duration_h"], sort=False):
        d = d.copy()
        d["pareto"] = pareto_flag(d)
        parts.append(d)
        best, met = recommend(d)
        base = d[d.interventions == "None"].iloc[0]
        recs.append(dict(severity=sev, duration_h=dur, baseline_service_pct=base.service_pct, recommended=best.interventions,
                         target_met=met, service_pct=best.service_pct, cost_nzd_m_yr=best.cost_nzd_m_yr,
                         strandings=best.strandings, cancelled_services=best.cancelled_services,
                         mean_delay_min=best.mean_delay_min, feasibility=best.feasibility))
    return pd.concat(parts, ignore_index=True), pd.DataFrame(recs), curves, traces


def main():
    reps = int(sys.argv[1]) if len(sys.argv) > 1 else C.DEFAULT_REPS
    RESULTS_DIR.mkdir(exist_ok=True)
    base_rows = []
    for ints in [frozenset(), frozenset("C")]:
        row, _, _ = run_cell("BASE", 1, ints, reps)
        base_rows.append(row)
    pd.DataFrame(base_rows).to_csv(RESULTS_DIR / "baseline_day.csv", index=False)
    grid, recs, curves, traces = run_all(reps)
    grid.to_csv(RESULTS_DIR / "experiment_grid.csv", index=False)
    recs.to_csv(RESULTS_DIR / "recommendations.csv", index=False)
    grid[grid.pareto].to_csv(RESULTS_DIR / "pareto_front.csv", index=False)
    (RESULTS_DIR / "hourly_curves.json").write_text(json.dumps(curves))
    (RESULTS_DIR / "traces.json").write_text(json.dumps(traces, separators=(",", ":")))
    pd.set_option("display.width", 220)
    print(f"\n{len(grid)} cells x {reps} replications\n")
    print(recs.round(1).to_string(index=False))


if __name__ == "__main__":
    main()
