"""Static figures for the proposal pack: python -m model.make_figures  (run model.experiments first)."""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from . import config as C
from . import network as N
from .experiments import RESULTS_DIR, recommend
from .rail_net import simulate

OUT = RESULTS_DIR / "figures"
BLUE, ORANGE, RED, GREEN, GREY = "#1769aa", "#e08a00", "#d62728", "#16803c", "#9ca3af"


def fmt_t(x, _=None):
    return f"{int(x // 60) % 24:02d}:{int(x % 60):02d}"


def time_distance(ax, sim, code: str, title: str, window=None):
    line = N.lines()[code]
    li = list(N.lines()).index(code)
    chain = line.chainage
    paths = {}
    for r in sim.trace:
        if r[0] != 0 or r[1] != li:
            continue
        _, _, k, d, sec, t0, t1, s0, s1, strand = r
        a, b = line.nodes[sec], line.nodes[sec + 1]
        x0, x1 = (chain[a], chain[b]) if d > 0 else (chain[b], chain[a])
        paths.setdefault((k, d), []).append((t0, x0, t1, x1, strand))
    for (k, d), segs in paths.items():
        segs.sort()
        for (t0, x0, t1, x1, strand) in segs:
            ax.plot([t0, t1], [x0, x1], c=RED if strand else (BLUE if d > 0 else ORANGE), lw=2.4 if strand else 1.2, zorder=3)
        for (a, b) in zip(segs[:-1], segs[1:]):                     # dwell / waiting between sections
            ax.plot([a[2], b[0]], [a[3], b[1]], c=GREY, lw=0.8, zorder=2)
    for r in sim.trace:                                              # charging
        if r[0] == 1 and r[1] == li:
            node = line.nodes[r[4]]
            ax.plot([r[5], r[6]], [chain[node]] * 2, c=GREEN, lw=3.5, zorder=4, solid_capstyle="butt")
    for n in line.nodes:
        ax.axhline(chain[n], c="#e5e7eb", lw=0.6, zorder=0)
    ax.set_yticks([chain[n] for n in line.nodes])
    ax.set_yticklabels([N.station_name(n) for n in line.nodes], fontsize=7)
    if window:
        ax.axvspan(window[0], window[1], color=RED, alpha=0.08, lw=0)
    ax.xaxis.set_major_formatter(plt.FuncFormatter(fmt_t))
    ax.set_xlim(C.DAY_START_MIN + 60, C.DAY_END_MIN - 60)
    ax.set_title(title, fontsize=9)


def fig_time_distance(sev="L2", dur=4):
    win = (C.EVENT_START_MIN, C.EVENT_START_MIN + dur * 60)
    fig, axes = plt.subplots(2, 2, figsize=(13, 8), sharex=True)
    for row, combo in enumerate(["None", "C"]):
        ints = frozenset() if combo == "None" else frozenset(combo)
        _, sim = simulate(sev, dur, ints, seed=0, trace=True)
        for col, code in enumerate(["WAI", "MAN"]):
            time_distance(axes[row, col], sim, code, f"{code} - package {combo}", win)
    fig.suptitle(f"Time-distance diagrams, {C.SEVERITIES[sev]['label']}, {dur} h (red band = disruption; red lines = stranded; "
                 f"green = charging)", fontsize=10)
    fig.tight_layout()
    fig.savefig(OUT / f"time_distance_{sev}_{dur}h.png", dpi=150)
    plt.close(fig)


def fig_soc(sev="L2", dur=4):
    fig, axes = plt.subplots(1, 2, figsize=(12, 4), sharey=True)
    for ax, combo in zip(axes, ["None", "C"]):
        ints = frozenset() if combo == "None" else frozenset(combo)
        _, sim = simulate(sev, dur, ints, seed=0, trace=True)
        li = list(N.lines()).index("WAI")
        by_k = {}
        for r in sim.trace:
            if r[1] == li:
                by_k.setdefault(r[2], []).append(r)
        for k, rs in by_k.items():
            pts = sorted([(r[5], r[7]) for r in rs] + [(r[6], r[8]) for r in rs])
            ax.plot([p[0] for p in pts], [100 * p[1] for p in pts], lw=1.1, label=f"trip {k}")
        ax.axhline(100 * C.SOC_HARD, c=RED, ls="--", lw=1)
        ax.axvspan(C.EVENT_START_MIN, C.EVENT_START_MIN + dur * 60, color=RED, alpha=0.08, lw=0)
        ax.xaxis.set_major_formatter(plt.FuncFormatter(fmt_t))
        ax.set_title(f"Wairarapa units, package {combo}", fontsize=9)
        ax.set_ylabel("State of charge (%)")
    fig.suptitle(f"Battery SoC along each service, {sev} {dur} h (dashed = stranding limit)", fontsize=10)
    fig.tight_layout()
    fig.savefig(OUT / f"soc_{sev}_{dur}h.png", dpi=150)
    plt.close(fig)


def fig_pareto(grid, sev="L2", dur=4):
    d = grid[(grid.severity == sev) & (grid.duration_h == dur)]
    fig, ax = plt.subplots(figsize=(7.5, 4.8))
    ax.scatter(d.cost_nzd_m_yr, d.service_pct, c=[BLUE if p else "#b0b5bb" for p in d.pareto], s=42)
    for _, r in d[d.pareto].iterrows():
        ax.annotate(r.interventions, (r.cost_nzd_m_yr, r.service_pct), xytext=(4, 4), textcoords="offset points", fontsize=8)
    ax.axhline(C.TARGET_SERVICE_PCT, ls="--", c="k", lw=1)
    ax.set_xlabel("Annualised intervention cost (NZ$ million / yr, illustrative)")
    ax.set_ylabel("Services delivered within 15 min (%)")
    ax.set_title(f"{sev}, {dur} h: service vs cost (blue = Pareto-efficient)", fontsize=10)
    fig.tight_layout()
    fig.savefig(OUT / f"pareto_{sev}_{dur}h.png", dpi=160)
    plt.close(fig)


def fig_hourly(sev="L2", dur=4):
    curves = json.loads((RESULTS_DIR / "hourly_curves.json").read_text())
    recs = pd.read_csv(RESULTS_DIR / "recommendations.csv", keep_default_na=False)
    rec = recs[(recs.severity == sev) & (recs.duration_h == dur)].iloc[0].recommended
    hours = list(range(C.DAY_START_MIN // 60, 24))
    fig, ax = plt.subplots(figsize=(8, 4.2))
    for combo, col in [("None", RED), ("C", ORANGE), (rec, BLUE)]:
        y = curves[f"{sev}|{dur}|{combo}"]
        ax.plot(hours, [np.nan if v is None else v for v in y], marker="o", ms=3, c=col, lw=2 if combo == rec else 1.4,
                label=f"{combo}" + (" (recommended)" if combo == rec else ""))
    ax.axvspan(C.EVENT_START_MIN / 60, C.EVENT_START_MIN / 60 + dur, color=RED, alpha=0.08, lw=0)
    ax.axhline(90, ls="--", c="k", lw=1)
    ax.set_xlabel("Hour of day (scheduled departure)")
    ax.set_ylabel("Services delivered within 15 min (%)")
    ax.set_ylim(-3, 105)
    ax.legend(title="Package")
    ax.set_title(f"System performance curve, {sev} {dur} h (resilience = shape of this curve)", fontsize=10)
    fig.tight_layout()
    fig.savefig(OUT / f"performance_curve_{sev}_{dur}h.png", dpi=160)
    plt.close(fig)


def fig_network():
    fig, ax = plt.subplots(figsize=(12, 4.6))
    ypos = {"WAI": 1.0, "MAN": -1.0}
    for code, line in N.lines().items():
        y = ypos[code]
        xs = [line.chainage[n] for n in line.nodes]
        for sec in line.sections:
            xa, xb = line.chainage[sec.from_id], line.chainage[sec.to_id]
            ax.plot([xa, xb], [y, y], c=BLUE if sec.electrified else ORANGE, lw=6 if sec.electrified else 3,
                    ls="-" if sec.electrified else (0, (3, 1.2)), solid_capstyle="butt")
        for n in line.nodes:
            ax.plot(line.chainage[n], y, "o", c="white", mec="k", ms=7, zorder=3)
            ax.annotate(N.station_name(n), (line.chainage[n], y), xytext=(0, 12 if y > 0 else -18), textcoords="offset points",
                        ha="center", fontsize=7, rotation=0)
        ax.annotate(C.LINES[code]["name"], (0, y), xytext=(0, -34 if y > 0 else 26), textcoords="offset points", fontsize=8, weight="bold")
        ax.plot(line.chainage[line.terminal], y, marker="s", c=GREEN, ms=11, zorder=2)
    ax.plot([], [], c=BLUE, lw=6, label="1500 V DC overhead line (charges battery while running)")
    ax.plot([], [], c=ORANGE, lw=3, ls=(0, (3, 1.2)), label="non-electrified: battery-only")
    ax.plot([], [], "s", c=GREEN, ms=9, label="terminal fast charger")
    ax.legend(loc="upper right", fontsize=8)
    ax.set_xlabel("Distance from Wellington (km, approximate)")
    ax.set_yticks([])
    ax.set_ylim(-2.4, 2.4)
    ax.set_title("Wellington LNIRIM network as modelled (illustrative geometry)", fontsize=10)
    fig.tight_layout()
    fig.savefig(OUT / "network_schematic.png", dpi=150)
    plt.close(fig)


def fig_matrix(recs):
    piv = recs.pivot(index="severity", columns="duration_h", values="recommended")
    piv.to_csv(RESULTS_DIR / "recommended_package_matrix.csv")
    return piv


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    grid = pd.read_csv(RESULTS_DIR / "experiment_grid.csv", keep_default_na=False)
    recs = pd.read_csv(RESULTS_DIR / "recommendations.csv", keep_default_na=False)
    fig_network()
    fig_time_distance("L2", 4)
    fig_soc("L2", 4)
    fig_pareto(grid, "L2", 4)
    fig_hourly("L2", 4)
    print(fig_matrix(recs))


if __name__ == "__main__":
    main()
