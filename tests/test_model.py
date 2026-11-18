"""Sanity / validation tests:  python -m pytest tests   (or: python tests/test_model.py)"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from model import concept_net, config as C, rail_net as R
from model.petri import Net


def test_engine_timing_and_conservation():
    net = Net()
    for p in ("A", "B"):
        net.add_place(p)
    net.add_transition("t", [("A", None)], lambda b, now: [("B", b[0], 5.0)])
    for i in range(3):
        net.put("A", i, t=0.0)
    net.run(100)
    assert net.count("B") == 3 and net.count("A") == 0


def test_concept_net_conserves_trains():
    for seed in range(3):
        assert concept_net.run(5, 600, seed)["tokens"] == 5


def test_charging_inverse():
    for s0 in (0.2, 0.5, 0.85):
        m = R.charge_minutes(s0, 0.9, C.CHARGER_KW)
        assert abs(R.soc_after(s0, m, C.CHARGER_KW) - 0.9) < 1e-6


def test_baseline_day_is_healthy():
    k, sim = R.simulate("BASE", 1, frozenset(), seed=0)
    assert k["strandings"] == 0 and k["cancelled_services"] == 0 and k["service_pct"] >= 95
    assert all(sim.net.count(f"DONE|{c}") == len(sim.units[c]) for c in sim.lines)   # every unit finished its day


def test_blocks_conserved():
    _, sim = R.simulate("L2", 2, frozenset("C"), seed=1)
    for c, line in sim.lines.items():
        for sec in line.sections:
            for key in sim._block_keys(sec):
                assert sim.net.count(f"B|{c}|{key}") == 1          # every track block token is back


def test_dt_policy_reduces_strandings_in_ohl_outage():
    a = sum(R.simulate("L2", 4, frozenset(), seed=s)[0]["strandings"] for s in range(3))
    b = sum(R.simulate("L2", 4, frozenset("C"), seed=s)[0]["strandings"] for s in range(3))
    assert b < a


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f(); print("ok", n)
