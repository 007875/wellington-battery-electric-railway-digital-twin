# Wellington LNIRIM - Digital Twin / Petri Net resilience prototype

Proof-of-concept for the proposal *"A Digital Twin (DT)-Petri Net (PN) Framework for Resilience Analysis of Hybrid Railway
Systems: The Wellington LNIRIM Case"*. It models battery-electric trains on the Wairarapa and Manawatu lines (partial
1500 V DC electrification, battery-only sections, terminal fast chargers, single-track blocks) as a **timed coloured Petri net**,
driven by a **Digital Twin** of the asset state, and compares intervention packages under disruption scenarios.

**Everything numeric is illustrative** (`model/config.py`, `data/*.csv`): distances, speeds, battery size, charger power,
headways, costs. They make the structure runnable; they are not Metlink/KiwiRail/Alstom data and the results must not be quoted as findings.

## Open the results (no Python needed)
`results/dashboard.html` - double-click. Choose severity/duration, target and feasibility, tick packages to compare.
Also `results/figures/*.png`, `results/experiment_grid.csv`, `results/recommendations.csv`.

## Run
```
pip install -r requirements.txt
python -m tests.test_model            # validation tests (or: python tests/test_model.py)
streamlit run dashboard.py            # live dashboard (re-simulates; plotly + OpenStreetMap map)
./run_all.sh   (Windows: run_all.bat) # regenerate experiments, figures and the HTML dashboard (~10 min)
```
The Streamlit dashboard was syntax-checked but not executed in the build environment; the HTML dashboard was exercised with a scripted DOM test.

## Architecture (the three layers of the proposal)
| Layer | File | Role |
|---|---|---|
| DT layer | `model/digital_twin.py`, `model/network.py`, `data/` | asset registry, event feed (overhead-line outage, speed restriction, charger derate, cold weather), unit-state observation |
| PN layer | `model/petri.py`, `model/rail_net.py`, `model/concept_net.py` | generic timed coloured PN engine; the full railway net; the Figure-2 conceptual net re-implemented 1:1 |
| Integration layer | `model/rail_net.py` (`Sim`), `model/scenarios.py` | PN queries DT status at each firing and pushes unit states back; scenarios are DT events |

Figure 2 -> full model: P0 origin = `S|L|0|+1`; T0 Depart = `DEP` (needs a free block token); P1/P2 battery low/normal = SoC of the unit token
and the dispatch decision; T4 pantograph connection = battery gain inside `DEP` on healthy overhead line; T5/P6 = `ARR`, terminal place `T|L`;
T1/P3 charging = `CHARGE_T/CHARGE_W` consuming a charger token; T3/P4 = unit re-appears after the charge duration.
Added: multiple units, single-track blocks and loops, charger queues, stochastic run/dwell/consumption, stranding + rescue, DT-informed dispatch.

## Experiments
Severity (L1 localised, L2 overhead-line outage + cold, L3 compound on both lines) x duration (1,2,4,6 h) x 32 packages
(A second charger, B 3 MW charging, C DT-informed SoC-aware dispatch, D +8 km overhead line, E rescue locomotive), 8 Monte Carlo
days per cell with common random numbers. KPIs: services within 15 min, mean/P95 delay, strandings, cancellations, minimum SoC,
passenger delay, charger wait/utilisation, recovery time, hourly performance curve. Recommendation = cheapest package with
feasibility >= 3 meeting the service target (default 90 %), otherwise the best achievable.

## Known limitations (be explicit about these)
* Illustrative data; headway 90 min (not the real timetable); terminal layover is tuned to make the timetable conflict-free.
* Not linked to OpenTrack/RailML; no signalling beyond block occupation; no real DT data feed (events are synthetic); suburban traffic is a noise term.
* The DT dispatcher knows the true outage end time (optimistic); energy model is linear (no temperature/ageing curves).
* Intervention costs and feasibility scores are placeholders for stakeholder elicitation.
* Charger count/power (A, B) barely matter at this service frequency; speed restrictions (L1) are not mitigated by any package - findings of the toy parameters, not of Wellington.
