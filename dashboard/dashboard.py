"""Streamlit dashboard - Wellington LNIRIM hybrid-train DT-PN resilience prototype (illustrative)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from model import config as C
from model import network as N
from model.experiments import pareto_flag, recommend, run_cell
from model.rail_net import all_combos, combo_label, simulate

st.set_page_config(page_title="Wellington hybrid-train DT-PN", page_icon="🚆", layout="wide")
NAMES = {k: v["name"] for k, v in C.INTERVENTIONS.items()}
REPS_LIVE = 4


@st.cache_data(show_spinner="Running 32 packages x replications ...")
def combos_for(sev: str, dur: float) -> pd.DataFrame:
    rows = [run_cell(sev, dur, c, REPS_LIVE)[0] for c in all_combos()]
    df = pd.DataFrame(rows)
    df["pareto"] = pareto_flag(df)
    return df


@st.cache_data(show_spinner=False)
def trace_for(sev: str, dur: float, combo: str):
    ints = frozenset() if combo == "None" else frozenset(combo.split("+"))
    k, sim = simulate(sev, dur, ints, seed=0, trace=True)
    return sim.trace


with st.sidebar:
    st.header("Disruption scenario")
    sev = st.selectbox("Severity", list(C.SEVERITIES), format_func=lambda k: C.SEVERITIES[k]["label"])
    dur = st.select_slider("Duration (h)", options=C.DURATIONS_H, value=4)
    st.header("Decision rule")
    target = st.slider("Target: services within 15 min (%)", 50, 100, int(C.TARGET_SERVICE_PCT))
    min_feas = st.slider("Minimum feasibility (1-5)", 1, 5, C.MIN_FEASIBILITY)
    st.header("Compare a package")
    chosen = st.multiselect("Interventions", list(NAMES), format_func=lambda k: f"{k} - {NAMES[k]}")
    st.caption("All parameters are illustrative (model/config.py, data/*.csv). Not for operational use.")

df = combos_for(sev, float(dur))
best, met = recommend(df, target, min_feas)
base = df[df.interventions == "None"].iloc[0]
label = "+".join(sorted(chosen)) if chosen else "None"
sel = df[df.interventions == label].iloc[0]

st.title("Hybrid battery-electric trains: DT-PN resilience analysis")
st.caption("Wellington LNIRIM case | Digital Twin events -> timed coloured Petri net -> KPIs | Wairarapa + Manawatu lines")

c1, c2, c3 = st.columns([1.1, 1.4, 1.1], gap="large")
with c1:
    st.subheader("Recommendation")
    (st.success if met else st.warning)(f"{'Target met' if met else 'Target NOT reachable at this feasibility'}: **{best.interventions}**")
    if best.interventions != "None":
        for k in best.interventions.split("+"):
            st.markdown(f"- **{k}** {NAMES[k]}")
    st.metric("Services within 15 min", f"{best.service_pct:.1f}%", f"{best.service_pct - base.service_pct:+.1f} pts vs no action")
    st.metric("Annualised cost", f"NZ$ {best.cost_nzd_m_yr:.2f} M / yr")
    st.metric("Strandings (day)", f"{best.strandings:.1f}")
    st.metric("Cancelled services", f"{best.cancelled_services:.1f}")
with c2:
    st.subheader("Network and disruption")
    fig = go.Figure()
    st_df = N.stations().set_index("station_id")
    for code, line in N.lines().items():
        for sec in line.sections:
            a, b = st_df.loc[sec.from_id], st_df.loc[sec.to_id]
            fig.add_trace(go.Scattermap(lon=[a.longitude, b.longitude], lat=[a.latitude, b.latitude], mode="lines",
                                        line=dict(width=5, color="#1769aa" if sec.electrified else "#e08a00"),
                                        name="overhead line" if sec.electrified else "battery-only", showlegend=False))
    fig.add_trace(go.Scattermap(lon=st_df.longitude, lat=st_df.latitude, mode="markers+text", text=st_df.name,
                                textposition="top right", marker=dict(size=9, color="#111"), name="Stations"))
    fig.update_layout(map=dict(style="open-street-map", center=dict(lat=-40.95, lon=175.2), zoom=7.2), height=450,
                      margin=dict(l=0, r=0, t=0, b=0))
    st.plotly_chart(fig, use_container_width=True)
    st.caption("Blue = overhead line (charges battery), orange = battery-only. " + C.SEVERITIES[sev]["label"])
with c3:
    st.subheader("Selected package")
    st.markdown(f"**{label}**")
    st.metric("Services within 15 min", f"{sel.service_pct:.1f}%")
    st.metric("Mean delay (window)", f"{sel.mean_delay_min:.1f} min")
    st.metric("Min SoC reached", f"{sel.min_soc_pct:.0f}%")
    st.metric("Recovery time", f"{sel.recovery_h:.1f} h")

st.divider()
a, b = st.columns(2, gap="large")
with a:
    st.subheader("Service-cost trade-off (32 packages)")
    f2 = go.Figure()
    for flag, col, nm in [(False, "#9ca3af", "Dominated"), (True, "#1769aa", "Pareto-efficient")]:
        s = df[df.pareto == flag]
        f2.add_trace(go.Scatter(x=s.cost_nzd_m_yr, y=s.service_pct, mode="markers+text" if flag else "markers",
                                text=s.interventions if flag else None, textposition="top center",
                                marker=dict(size=10, color=col), name=nm))
    f2.add_hline(y=target, line_dash="dash", annotation_text="Target")
    f2.update_layout(xaxis_title="Annualised cost (NZ$ M/yr)", yaxis_title="Services within 15 min (%)", height=400)
    st.plotly_chart(f2, use_container_width=True)
with b:
    st.subheader("Time-distance diagram (Wairarapa, one replication)")
    f3 = go.Figure()
    line = N.lines()["WAI"]
    for combo, dash in [("None", "dot"), (label, "solid")]:
        for r in trace_for(sev, float(dur), combo):
            if r[0] == 0 and r[1] == 0:
                _, _, k, d, sec, t0, t1, s0, s1, strand = r
                a_, b_ = line.nodes[sec], line.nodes[sec + 1]
                x0, x1 = (line.chainage[a_], line.chainage[b_]) if d > 0 else (line.chainage[b_], line.chainage[a_])
                f3.add_trace(go.Scatter(x=[t0 / 60, t1 / 60], y=[x0, x1], mode="lines", showlegend=False,
                                        line=dict(width=1.5, dash=dash, color="#d62728" if combo == "None" else "#1769aa")))
    f3.add_vrect(x0=C.EVENT_START_MIN / 60, x1=C.EVENT_START_MIN / 60 + dur, fillcolor="red", opacity=0.08, line_width=0)
    f3.update_layout(xaxis_title="Hour", yaxis_title="km from Wellington", height=400)
    st.plotly_chart(f3, use_container_width=True)
    st.caption("Red dotted = no action, blue = selected package.")

st.subheader("All packages")
show = df.sort_values(["service_pct", "cost_nzd_m_yr"], ascending=[False, True])[
    ["interventions", "service_pct", "mean_delay_min", "strandings", "cancelled_services", "cost_nzd_m_yr", "feasibility", "pareto"]]
st.dataframe(show.round(1), use_container_width=True, hide_index=True)

rec_path = ROOT / "results" / "recommendations.csv"
if rec_path.exists():
    st.subheader("Recommended package across severity and duration (precomputed)")
    st.dataframe(pd.read_csv(rec_path, keep_default_na=False).pivot(index="severity", columns="duration_h", values="recommended"), use_container_width=True)
