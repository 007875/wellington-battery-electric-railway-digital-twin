"""
Central assumptions for the Wellington LNIRIM Digital Twin - Petri Net (DT-PN) prototype.

EVERY number in this file is an ILLUSTRATIVE modelling assumption.
They exist to make the model structure runnable and transparent. In the PhD they would be replaced by
validated data (Metlink/GTFS timetables, KiwiRail track data, OpenTrack/RailML export, rolling-stock
specifications, charger specifications, historical disruption records).

Network geometry (stations, sections) is NOT here: it is read from data/stations.csv and data/sections.csv.
Time is in MINUTES since midnight everywhere in the model.
"""
from __future__ import annotations

# ----------------------------------------------------------------------------
# Operating day and disruption window
# ----------------------------------------------------------------------------
DAY_START_MIN = 5 * 60            # simulated day starts 05:00
DAY_END_MIN = 24 * 60             # events after 24:00 are not simulated (all services finish before)
FIRST_DEP_MIN = 5 * 60 + 30       # first Wellington departure per line
LAST_DEP_MIN = 18 * 60 + 30       # last Wellington departure per line (all units are back by ~23:00)
EVENT_START_MIN = 7 * 60          # disruption begins 07:00 (morning peak)
RECOVERY_WINDOW_H = 3.0           # KPIs are measured from event start to event end + this
ON_TIME_MIN = 5.0                 # "on time" threshold (minutes late at final arrival)
SERVICE_OK_MIN = 15.0             # "service delivered" threshold used for the resilience KPI

# ----------------------------------------------------------------------------
# Lines: headway, fleet, timetable supplements
# ----------------------------------------------------------------------------
LINES = {
    "WAI": dict(name="Wairarapa Line (Wellington-Masterton)", headway_min=90, units=3, first_dep_offset_min=0),
    "MAN": dict(name="Manawatu Line (Wellington-Palmerston North)", headway_min=90, units=3, first_dep_offset_min=15),
}
TERMINAL_LAYOVER_RANGE = (26.0, 44.0)   # the scheduled terminal turnaround (incl. fast charging) is tuned inside this
                                        # range so that the single-track timetable is conflict-free (crossings at loops)
TIMETABLE_PAD = 0.07              # run-time supplement in the published timetable (recovery margin)
MODE_CHANGE_MIN = 1.5             # pantograph up/down at Upper Hutt / Waikanae
CLEARANCE_MIN = 1.0               # block released this long after the train arrives at the next station
MIN_TURN_MIN = 6.0                # minimum dwell at an end of line
WELLINGTON_LAYOVER_MIN = 15.0     # planning layover used only when generating the timetable

# ----------------------------------------------------------------------------
# Rolling stock (battery-electric multiple unit, illustrative)
# ----------------------------------------------------------------------------
BATTERY_KWH = 1000.0              # usable battery energy window at 100 % state of charge (SoC)
SOC_MAX = 0.95                    # charge limit
SOC_RESERVE = 0.15                # planning reserve the dispatcher wants to keep
SOC_HARD = 0.05                   # below this the unit cannot move (stranded)
CONS_KWH_KM = 8.0                 # traction + auxiliary consumption on battery
PAN_CHARGE_KW = 500.0             # battery charging power while running under the overhead line
CHARGER_KW = 2000.0               # fast charger power at terminals / Wellington (up to TAPER_SOC)
TAPER_SOC = 0.80                  # above this SoC the charging power tapers
TAPER_FACTOR = 0.35               # charging power fraction above TAPER_SOC
BASE_CHARGE_TARGET = 0.90         # fixed charge target used by the baseline (no DT) dispatch rule
BASE_MIN_DEPART_SOC = 0.80        # baseline static rule: never leave a charging point below this SoC if a charger works
PAX_CAPACITY = 475
LOAD_FACTOR = 0.40                # average load factor for passenger-delay estimates

# ----------------------------------------------------------------------------
# Stochastic terms (all multiplicative noise is lognormal with these sigmas)
# ----------------------------------------------------------------------------
RUNTIME_SIGMA = 0.04              # section run-time variability
CONSUMPTION_SIGMA = 0.05          # section energy variability
DWELL_EXTRA_MEAN_MIN = 0.4        # exponential extra dwell at intermediate stations
START_DELAY_MEAN_MIN = 1.0        # exponential departure delay at Wellington
BACKGROUND_DELAY_MEAN_MIN = 1.2   # suburban-traffic interference on electrified double track (per section)

# ----------------------------------------------------------------------------
# Failure handling
# ----------------------------------------------------------------------------
TOW_SPEED_KMH = 40.0              # speed once a stranded unit is towed on
RESCUE_MIN = 90.0                 # time to recover a stranded unit (blocks the track meanwhile)
DT_HOLD_POLL_MIN = 5.0            # a DT-informed dispatcher re-checks a held train every 5 min
DT_HOLD_MIN_SOC = 0.08            # DT dispatcher holds a unit only if its projected minimum SoC would fall below this
DT_CANCEL_AFTER_MIN = 60.0        # outbound service cancelled if still unsafe after this hold
DT_MAX_HOLD_MIN = 240.0           # safety valve: stop holding after this long
DT_CHARGE_MARGIN = 0.05           # extra SoC the DT dispatcher adds above the computed need
DT_MIN_CHARGE_TARGET = 0.55

# ----------------------------------------------------------------------------
# Interventions (what the decision-maker can switch on)
# cost = annualised cost in NZ$ million per year (ILLUSTRATIVE; capex annualised + opex)
# feasibility: 1 (hard) .. 5 (easy). PLACEHOLDER for stakeholder elicitation.
# ----------------------------------------------------------------------------
INTERVENTIONS = {
    "A": dict(name="Second fast charger at each terminal", cost_nzd_m_yr=0.8, feasibility=4),
    "B": dict(name="Higher-power charging (3 MW vs 2 MW)", cost_nzd_m_yr=1.2, feasibility=3),
    "C": dict(name="DT-informed SoC-aware dispatch", cost_nzd_m_yr=0.15, feasibility=4),
    "D": dict(name="Extend overhead line +8 km beyond Upper Hutt / Waikanae", cost_nzd_m_yr=2.5, feasibility=2),
    "E": dict(name="Rescue locomotive on standby (recovery 90 -> 35 min)", cost_nzd_m_yr=0.7, feasibility=4),
}
A_TERMINAL_CHARGERS = 2           # chargers per terminal with intervention A (baseline 1)
B_CHARGER_KW = 3000.0
D_EXTRA_ELEC_KM = 8.0
E_RESCUE_MIN = 35.0

# ----------------------------------------------------------------------------
# Disruption severity levels (the "scenarios"); all are DT events, see scenarios.py
# ----------------------------------------------------------------------------
SEVERITIES = {
    "L1": dict(label="L1 Localised: Rimutaka speed restriction + Masterton charger at 35 %"),
    "L2": dict(label="L2 Energy-system: Hutt Valley overhead-line outage + cold weather"),
    "L3": dict(label="L3 Compound: overhead-line outage on both lines + Palmerston North charger derated + cold"),
}
DURATIONS_H = [1, 2, 4, 6]

# Decision rule used for the recommendation
TARGET_SERVICE_PCT = 90.0         # share of services in the window delivered within SERVICE_OK_MIN
MIN_FEASIBILITY = 3

# Monte Carlo
DEFAULT_REPS = 10                 # replications per scenario x package (common random numbers across packages)
