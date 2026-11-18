"""Standalone, offline HTML dashboard (no Python/Streamlit/internet needed to open it):
    python -m model.build_html_dashboard      ->  results/dashboard.html
It embeds the experiment grid, hourly curves and one traced day per scenario/package (vanilla JS + SVG)."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from . import config as C
from . import network as N
from .experiments import RESULTS_DIR

TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Wellington LNIRIM hybrid-train DT-PN dashboard</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--ink:#111827;--mute:#6b7280;--line:#e5e7eb;--blue:#1769aa;--orange:#e08a00;--red:#d62728;--green:#16803c}
@media(prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#181b21;--ink:#e5e7eb;--mute:#9ca3af;--line:#2a2f38}}
*{box-sizing:border-box}body{margin:0;font:14px/1.45 system-ui,Segoe UI,Arial,sans-serif;background:var(--bg);color:var(--ink)}
header{padding:16px 22px;border-bottom:1px solid var(--line);background:var(--card)}
h1{margin:0;font-size:20px}h2{margin:0 0 8px;font-size:15px}.sub{color:var(--mute);font-size:12px;margin-top:2px}
main{display:grid;grid-template-columns:290px 1fr;gap:16px;padding:16px 22px;max-width:1500px;margin:auto}
@media(max-width:900px){main{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px;margin-bottom:14px}
label{display:block;margin:10px 0 3px;font-weight:600;font-size:12px}select,input[type=range]{width:100%}
.pk{display:block;font-weight:400;margin:4px 0}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}
@media(max-width:1100px){.grid{grid-template-columns:repeat(2,1fr)}}
.kpi{border:1px solid var(--line);border-radius:8px;padding:8px 10px}.kpi b{display:block;font-size:20px}.kpi span{color:var(--mute);font-size:11px}
.two{display:grid;grid-template-columns:1fr 1fr;gap:14px}@media(max-width:1100px){.two{grid-template-columns:1fr}}
svg{width:100%;height:auto;display:block}svg text{fill:var(--ink);font-size:11px}.ax{stroke:var(--line)}
table{border-collapse:collapse;width:100%;font-size:12px}th,td{padding:4px 6px;border-bottom:1px solid var(--line);text-align:right}
th:first-child,td:first-child{text-align:left}tr.rec td{font-weight:700;background:rgba(23,105,170,.12)}
.ok{color:var(--green);font-weight:700}.warn{color:var(--orange);font-weight:700}.note{color:var(--mute);font-size:12px}
.scroll{max-height:360px;overflow:auto}
</style></head><body>
<header><h1>Hybrid battery-electric trains - Digital Twin / Petri Net resilience dashboard</h1>
<div class="sub">Wellington LNIRIM case (Wairarapa + Manawatu lines). Proof of concept: ALL parameters are illustrative (model/config.py, data/*.csv). Not for operational use.</div></header>
<main>
<aside><div class="card">
<h2>Scenario</h2>
<label>Disruption severity</label><select id="sev"></select>
<label>Duration (h)</label><select id="dur"></select>
<h2 style="margin-top:14px">Decision rule</h2>
<label>Target: services within 15 min: <span id="tv"></span>%</label><input type="range" id="target" min="50" max="100" step="1">
<label>Minimum feasibility (1-5): <span id="fv"></span></label><input type="range" id="feas" min="1" max="5" step="1">
<h2 style="margin-top:14px">Compare a package</h2><div id="pk"></div>
<div class="note" style="margin-top:8px">Packages: A second charger, B 3 MW charging, C DT-informed dispatch, D +8 km overhead line, E rescue loco.</div>
</div></aside>
<section>
<div class="card" id="rec"></div>
<div class="card"><h2>Network and disruption</h2><div id="net"></div><div class="note" id="sevtxt"></div></div>
<div class="two">
<div class="card"><h2>Service vs cost (32 packages)</h2><div id="pareto"></div></div>
<div class="card"><h2>System performance curve (hourly)</h2><div id="curve"></div></div>
</div>
<div class="card"><h2>Time-distance diagram <select id="lineSel" style="width:auto"><option value="0">Wairarapa</option><option value="1">Manawatu</option></select></h2>
<div class="two"><div id="td0"></div><div id="td1"></div></div>
<div class="note">Left: no action. Right: selected package (one replication, seed 0). Red = stranded, green = charging, blue = outbound, orange = inbound, shaded = disruption.</div></div>
<div class="card"><h2>All packages</h2><div class="scroll" id="tbl"></div></div>
<div class="card"><h2>Recommended package across severity and duration</h2><div id="mat"></div></div>
</section></main>
<script>
const D = __DATA__;
const $ = id => document.getElementById(id);
const BLUE="#1769aa",ORANGE="#e08a00",RED="#d62728",GREEN="#16803c",GREY="#9ca3af";
const st = {sev:D.severities[0].id, dur:D.durations[2], target:D.target, feas:D.minFeas, pk:new Set()};
const lab = s => s.size? [...s].sort().join("+") : "None";
const rows = (sev,dur) => D.grid.filter(r=>r.severity===sev && r.duration_h===dur);
function recommend(rs,target,mf){
  const ok=rs.filter(r=>r.service_pct>=target && r.feasibility>=mf);
  if(ok.length){ok.sort((a,b)=>a.cost-b.cost||a.mean_delay_min-b.mean_delay_min);return [ok[0],true];}
  let pool=rs.filter(r=>r.feasibility>=mf); if(!pool.length)pool=rs;
  const best=Math.max(...pool.map(r=>r.service_pct));
  const near=pool.filter(r=>r.service_pct>=best-0.5); near.sort((a,b)=>a.cost-b.cost||a.mean_delay_min-b.mean_delay_min);
  return [near[0],false];
}
function pareto(rs){return rs.map(r=>!rs.some(o=>o.service_pct>=r.service_pct-1e-9&&o.cost<=r.cost+1e-9&&(o.service_pct>r.service_pct+1e-9||o.cost<r.cost-1e-9)));}
const f1=x=>(+x).toFixed(1);
function fmtT(m){m=Math.round(m);return String(Math.floor(m/60)%24).padStart(2,"0")+":"+String(m%60).padStart(2,"0");}
function svg(w,h,inner){return `<svg viewBox="0 0 ${w} ${h}" xmlns="http://www.w3.org/2000/svg">${inner}</svg>`;}

function init(){
  $("sev").innerHTML=D.severities.map(s=>`<option value="${s.id}">${s.label}</option>`).join("");
  $("dur").innerHTML=D.durations.map(d=>`<option value="${d}">${d} h</option>`).join("");
  $("dur").value=st.dur; $("target").value=st.target; $("feas").value=st.feas;
  $("pk").innerHTML=Object.entries(D.interventions).map(([k,v])=>`<label class="pk"><input type="checkbox" value="${k}"> <b>${k}</b> ${v.name}</label>`).join("");
  $("sev").onchange=e=>{st.sev=e.target.value;draw()};
  $("dur").onchange=e=>{st.dur=+e.target.value;draw()};
  $("target").oninput=e=>{st.target=+e.target.value;draw()};
  $("feas").oninput=e=>{st.feas=+e.target.value;draw()};
  $("lineSel").onchange=draw;
  $("pk").querySelectorAll("input").forEach(c=>c.onchange=()=>{c.checked?st.pk.add(c.value):st.pk.delete(c.value);draw()});
  draw();
}
function drawNet(){
  const W=900,H=230; let s=""; const maxkm=140, x=km=>40+km/maxkm*(W-80);
  D.lines.forEach((ln,i)=>{
    const y=i===0?70:160; s+=`<text x="40" y="${y-34}" style="font-weight:700">${ln.name}</text>`;
    ln.sections.forEach(sec=>{
      const hz=hazard(st.sev,ln.code,sec.idx);
      s+=`<line x1="${x(sec.a)}" x2="${x(sec.b)}" y1="${y}" y2="${y}" stroke="${hz?RED:(sec.elec?BLUE:ORANGE)}" stroke-width="${sec.elec?7:4}" ${sec.elec?"":'stroke-dasharray="7 3"'}/>`;
    });
    ln.nodes.forEach((n,j)=>{s+=`<circle cx="${x(n.km)}" cy="${y}" r="5" fill="#fff" stroke="#111"/><text x="${x(n.km)}" y="${y+(j%2?24:-12)}" text-anchor="middle">${n.name}</text>`;});
    const t=ln.nodes[ln.nodes.length-1]; s+=`<rect x="${x(t.km)-7}" y="${y-7}" width="14" height="14" fill="${GREEN}"/>`;
  });
  s+=`<text x="40" y="215" style="fill:${BLUE}">&#9632; overhead line (charges)</text><text x="260" y="215" style="fill:${ORANGE}">&#9632; battery-only</text><text x="400" y="215" style="fill:${RED}">&#9632; overhead line out (disruption)</text><text x="640" y="215" style="fill:${GREEN}">&#9632; terminal charger</text>`;
  $("net").innerHTML=svg(W,H,s);
  $("sevtxt").textContent=D.severities.find(s=>s.id===st.sev).label;
}
function hazard(sev,line,idx){
  if(sev==="L2") return line==="WAI"&&idx===0;
  if(sev==="L3") return (line==="WAI"&&idx===0)||(line==="MAN"&&idx<=2);
  return false;
}
function drawPareto(rs,flags,target,sel,rec){
  const W=520,H=340,m={l:46,r:12,t:12,b:40};
  const xs=rs.map(r=>r.cost),ys=rs.map(r=>r.service_pct);
  const x0=0,x1=Math.max(...xs)*1.05||1,y0=Math.max(0,Math.min(...ys,target)-5),y1=Math.min(100,Math.max(...ys,target)+4);
  const X=v=>m.l+(v-x0)/(x1-x0)*(W-m.l-m.r),Y=v=>H-m.b-(v-y0)/(y1-y0||1)*(H-m.t-m.b);
  let s=`<line class="ax" x1="${m.l}" x2="${W-m.r}" y1="${H-m.b}" y2="${H-m.b}"/><line class="ax" x1="${m.l}" x2="${m.l}" y1="${m.t}" y2="${H-m.b}"/>`;
  s+=`<line x1="${m.l}" x2="${W-m.r}" y1="${Y(target)}" y2="${Y(target)}" stroke="#000" stroke-dasharray="5 4"/><text x="${W-m.r-4}" y="${Y(target)-4}" text-anchor="end">target</text>`;
  rs.forEach((r,i)=>{const c=flags[i]?BLUE:GREY,isSel=r.interventions===sel,isRec=r.interventions===rec;
    s+=`<circle cx="${X(r.cost)}" cy="${Y(r.service_pct)}" r="${isSel||isRec?7:5}" fill="${c}" ${isRec?`stroke="${GREEN}" stroke-width="3"`:isSel?'stroke="#111" stroke-width="2"':""}><title>${r.interventions}: ${f1(r.service_pct)}% , NZ$ ${r.cost.toFixed(2)} M/yr</title></circle>`;
    if(flags[i])s+=`<text x="${X(r.cost)+6}" y="${Y(r.service_pct)-6}">${r.interventions}</text>`;});
  for(let k=0;k<=4;k++){const v=y0+(y1-y0)*k/4;s+=`<text x="${m.l-6}" y="${Y(v)+4}" text-anchor="end">${v.toFixed(0)}</text>`;}
  for(let k=0;k<=4;k++){const v=x0+(x1-x0)*k/4;s+=`<text x="${X(v)}" y="${H-m.b+16}" text-anchor="middle">${v.toFixed(1)}</text>`;}
  s+=`<text x="${(W+m.l)/2}" y="${H-6}" text-anchor="middle">annualised cost (NZ$ M / yr)</text><text transform="translate(12 ${H/2}) rotate(-90)" text-anchor="middle">services within 15 min (%)</text>`;
  $("pareto").innerHTML=svg(W,H,s);
}
function drawCurve(sel,rec){
  const W=520,H=340,m={l:46,r:12,t:12,b:40}, X=h=>m.l+(h-D.hours[0])/(D.hours[D.hours.length-1]-D.hours[0])*(W-m.l-m.r), Y=v=>H-m.b-v/100*(H-m.t-m.b);
  let s=`<rect x="${X(D.eventStart/60)}" y="${m.t}" width="${X(D.eventStart/60+st.dur)-X(D.eventStart/60)}" height="${H-m.t-m.b}" fill="${RED}" opacity=".08"/>`;
  s+=`<line class="ax" x1="${m.l}" x2="${W-m.r}" y1="${H-m.b}" y2="${H-m.b}"/><line class="ax" x1="${m.l}" x2="${m.l}" y1="${m.t}" y2="${H-m.b}"/><line x1="${m.l}" x2="${W-m.r}" y1="${Y(90)}" y2="${Y(90)}" stroke="#000" stroke-dasharray="5 4"/>`;
  const series=[["None",RED],[sel,BLUE],[rec,GREEN]].filter((a,i,arr)=>arr.findIndex(b=>b[0]===a[0])===i);
  series.forEach(([lb,col],i)=>{const y=D.curves[`${st.sev}|${st.dur}|${lb}`]||[];let p="",pen=false;
    y.forEach((v,j)=>{if(v===null){pen=false;return;}p+=(pen?"L":"M")+X(D.hours[j])+" "+Y(v)+" ";pen=true;});
    s+=`<path d="${p}" fill="none" stroke="${col}" stroke-width="2.2"/><text x="${m.l+8}" y="${m.t+14+i*14}" style="fill:${col}">${lb}${lb===rec?" (recommended)":""}</text>`;});
  for(let k=0;k<=4;k++)s+=`<text x="${m.l-6}" y="${Y(k*25)+4}" text-anchor="end">${k*25}</text>`;
  D.hours.forEach((h,j)=>{if(j%2===0)s+=`<text x="${X(h)}" y="${H-m.b+16}" text-anchor="middle">${h}h</text>`});
  s+=`<text x="${(W+m.l)/2}" y="${H-6}" text-anchor="middle">scheduled departure hour</text><text transform="translate(12 ${H/2}) rotate(-90)" text-anchor="middle">within 15 min (%)</text>`;
  $("curve").innerHTML=svg(W,H,s);
}
function drawTD(el,combo,li){
  const W=520,H=330,m={l:92,r:10,t:10,b:30}, ln=D.lines[li];
  const t0=D.dayStart+60,t1=D.dayEnd-60,maxkm=ln.nodes[ln.nodes.length-1].km;
  const X=t=>m.l+(t-t0)/(t1-t0)*(W-m.l-m.r),Y=km=>H-m.b-km/maxkm*(H-m.t-m.b);
  let s=`<rect x="${X(D.eventStart)}" y="${m.t}" width="${X(D.eventStart+st.dur*60)-X(D.eventStart)}" height="${H-m.t-m.b}" fill="${RED}" opacity=".08"/>`;
  ln.nodes.forEach(n=>{s+=`<line class="ax" x1="${m.l}" x2="${W-m.r}" y1="${Y(n.km)}" y2="${Y(n.km)}"/><text x="${m.l-4}" y="${Y(n.km)+4}" text-anchor="end">${n.name}</text>`;});
  for(let h=Math.ceil(t0/60);h*60<=t1;h+=2)s+=`<text x="${X(h*60)}" y="${H-m.b+14}" text-anchor="middle">${String(h%24).padStart(2,"0")}</text>`;
  const tr=(D.traces[`${st.sev}|${st.dur}|${combo}`]||[]).filter(r=>r[1]===li);
  const byTrain={};
  tr.filter(r=>r[0]===0).forEach(r=>{const key=r[2]+"|"+r[3];(byTrain[key]=byTrain[key]||[]).push(r);});
  Object.values(byTrain).forEach(segs=>{
    segs.sort((a,b)=>a[5]-b[5]);
    segs.forEach((r,i)=>{const d=r[3],sec=ln.sections[r[4]],xa=d>0?sec.a:sec.b,xb=d>0?sec.b:sec.a;
      s+=`<line x1="${X(r[5])}" y1="${Y(xa)}" x2="${X(r[6])}" y2="${Y(xb)}" stroke="${r[9]?RED:(d>0?BLUE:ORANGE)}" stroke-width="${r[9]?3:1.4}"><title>trip ${r[2]} SoC ${(r[7]*100).toFixed(0)}% -> ${(r[8]*100).toFixed(0)}%</title></line>`;
      if(i>0){const p=segs[i-1],ps=ln.sections[p[4]],pb=p[3]>0?ps.b:ps.a;s+=`<line x1="${X(p[6])}" y1="${Y(pb)}" x2="${X(r[5])}" y2="${Y(xa)}" stroke="${GREY}" stroke-width=".8"/>`;}});
  });
  tr.filter(r=>r[0]===1).forEach(r=>{const km=ln.nodes[r[4]].km;s+=`<line x1="${X(r[5])}" x2="${X(r[6])}" y1="${Y(km)}" y2="${Y(km)}" stroke="${GREEN}" stroke-width="4"/>`;});
  $(el).innerHTML=svg(W,H,s);
}
function draw(){
  const rs=rows(st.sev,st.dur), flags=pareto(rs); rs.forEach((r,i)=>r.pareto=flags[i]);
  const [best,met]=recommend(rs,st.target,st.feas), base=rs.find(r=>r.interventions==="None"), L=lab(st.pk), sel=rs.find(r=>r.interventions===L);
  $("tv").textContent=st.target; $("fv").textContent=st.feas;
  const kp=(v,l)=>`<div class="kpi"><b>${v}</b><span>${l}</span></div>`;
  $("rec").innerHTML=`<h2>Recommendation <span class="${met?"ok":"warn"}">${met?"target met":"target NOT reachable at this feasibility - best achievable shown"}</span>: <b>${best.interventions}</b></h2>`+
   `<div class="note">${best.interventions==="None"?"No intervention":best.interventions.split("+").map(k=>k+" "+D.interventions[k].name).join("; ")}</div>`+
   `<div class="grid" style="margin-top:10px">${kp(f1(best.service_pct)+"%","services within 15 min ("+(best.service_pct-base.service_pct>=0?"+":"")+f1(best.service_pct-base.service_pct)+" pts vs no action)")}${kp("NZ$ "+best.cost.toFixed(2)+" M/yr","annualised cost")}${kp(f1(best.strandings),"strandings / day")}${kp(f1(best.cancelled_services),"cancelled services")}`+
   `${kp(f1(best.mean_delay_min)+" min","mean delay in window")}${kp(f1(best.recovery_h)+" h","recovery time")}${kp(f1(best.min_soc_pct)+"%","minimum SoC reached")}${kp(best.feasibility+" / 5","operational feasibility")}</div>`+
   `<div class="note" style="margin-top:8px">Selected package <b>${L}</b>: ${f1(sel.service_pct)}% within 15 min, ${f1(sel.strandings)} strandings, ${f1(sel.cancelled_services)} cancellations, mean delay ${f1(sel.mean_delay_min)} min. Means over ${base.reps} Monte Carlo replications (service % sd ${f1(sel.service_pct_sd)}).</div>`;
  drawNet(); drawPareto(rs,flags,st.target,L,best.interventions); drawCurve(L,best.interventions);
  const li=+$("lineSel").value; drawTD("td0","None",li); drawTD("td1",L,li);
  const sorted=[...rs].sort((a,b)=>b.service_pct-a.service_pct||a.cost-b.cost);
  $("tbl").innerHTML=`<table><tr><th>Package</th><th>Within 15 min %</th><th>Mean delay min</th><th>Strandings</th><th>Cancelled</th><th>Cost NZ$M/yr</th><th>Feas.</th><th>Pareto</th></tr>`+
   sorted.map(r=>`<tr class="${r.interventions===best.interventions?"rec":""}"><td>${r.interventions}</td><td>${f1(r.service_pct)}</td><td>${f1(r.mean_delay_min)}</td><td>${f1(r.strandings)}</td><td>${f1(r.cancelled_services)}</td><td>${r.cost.toFixed(2)}</td><td>${r.feasibility}</td><td>${r.pareto?"yes":""}</td></tr>`).join("")+`</table>`;
  let t=`<table><tr><th>Severity \\ duration</th>${D.durations.map(d=>`<th>${d} h</th>`).join("")}</tr>`;
  D.severities.forEach(sv=>{t+=`<tr><td>${sv.id}</td>`+D.durations.map(d=>{const r2=rows(sv.id,d),[b2,m2]=recommend(r2,st.target,st.feas);return `<td class="${m2?"ok":"warn"}">${b2.interventions}</td>`}).join("")+`</tr>`;});
  $("mat").innerHTML=t+`</table><div class="note">Green = target met; orange = best achievable (target not reachable). Recomputed with the sliders.</div>`;
}
init();
</script></body></html>
"""


def build() -> Path:
    grid = pd.read_csv(RESULTS_DIR / "experiment_grid.csv", keep_default_na=False)
    curves = json.loads((RESULTS_DIR / "hourly_curves.json").read_text())
    traces = json.loads((RESULTS_DIR / "traces.json").read_text())
    st = N.stations().set_index("station_id")
    lines = []
    for code, ln in N.lines().items():
        lines.append(dict(
            code=code, name=C.LINES[code]["name"],
            nodes=[dict(id=n, name=str(st.loc[n, "name"]), km=ln.chainage[n]) for n in ln.nodes],
            sections=[dict(idx=s.idx, a=ln.chainage[s.from_id], b=ln.chainage[s.to_id], elec=bool(s.electrified)) for s in ln.sections]))
    cols = ["severity", "duration_h", "interventions", "cost_nzd_m_yr", "feasibility", "reps", "service_pct", "service_pct_sd",
            "mean_delay_min", "strandings", "cancelled_services", "min_soc_pct", "recovery_h"]
    g = grid[cols].round(2).rename(columns={"cost_nzd_m_yr": "cost"})
    data = dict(
        grid=g.to_dict("records"), curves=curves, traces=traces, lines=lines,
        severities=[dict(id=k, label=v["label"]) for k, v in C.SEVERITIES.items()], durations=C.DURATIONS_H,
        interventions={k: dict(name=v["name"]) for k, v in C.INTERVENTIONS.items()},
        hours=list(range(C.DAY_START_MIN // 60, 24)), eventStart=C.EVENT_START_MIN, dayStart=C.DAY_START_MIN, dayEnd=C.DAY_END_MIN,
        target=C.TARGET_SERVICE_PCT, minFeas=C.MIN_FEASIBILITY)
    html = TEMPLATE.replace("__DATA__", json.dumps(data, separators=(",", ":")))
    out = RESULTS_DIR / "dashboard.html"
    out.write_text(html, encoding="utf-8")
    return out


if __name__ == "__main__":
    p = build()
    print(f"wrote {p} ({p.stat().st_size / 1e6:.1f} MB)")
