"""
The CONCEPTUAL net of the proposal (Figure 2, built in CPN IDE), re-implemented 1:1 in Python with the same engine
that runs the full model. It is a validation step: the full model (rail_net.py) is a spatial/resource-constrained
generalisation of exactly these places and transitions.

Places:      P0 Train at origin | P1 Battery low | P2 Battery normal | P3 Charging | P4 Battery full
             P5 Train on route  | P6 Train at terminal
Transitions: T0 Depart | T1 Start charging | T2 Start moving | T3 Finish charging
             T4 Pantograph connection to charging | T5 Arrive
Arcs:        P0,P4 -> T0 -> P1,P2 ; P1,P6 -> T1 -> P3 ; P2 -> T2 -> P5 ; P5 -> T4 -> P3 ; P3 -> T3 -> P4 ; P5 -> T5 -> P6
"""
from __future__ import annotations

import random
from collections import Counter

from .petri import Net


def build(n_trains: int = 5, seed: int = 1, p_low: float = 0.35, p_catenary: float = 0.30):
    rng = random.Random(seed)
    net = Net()
    for p in ["P0", "P1", "P2", "P3", "P4", "P5", "P6"]:
        net.add_place(p)

    def t0(b, now):                                         # Depart: battery low or normal
        tr = b[0]
        low = rng.random() < p_low
        return [("P1" if low else "P2", tr, rng.uniform(1, 3))]

    def t1(b, now):                                         # Start charging
        return [("P3", b[0], 0.5)]

    def t2(b, now):                                         # Start moving
        return [("P5", b[0], rng.uniform(1, 2))]

    def t3(b, now):                                         # Finish charging (20-27 min as in the proposal)
        return [("P4", b[0], rng.uniform(20, 27))]

    def t4(b, now):                                         # Pantograph connection to charging while on route
        return [("P3", b[0], 1.0)]

    def t5(b, now):                                         # Arrive
        return [("P6", b[0], rng.uniform(40, 60))]

    # P5 is in conflict between T4 and T5: the colour carries the (random) electrification flag for this leg
    def mark_leg(tr):
        tr["cat"] = rng.random() < p_catenary
        return tr

    net.add_transition("T0", [("P0", None)], t0)
    net.add_transition("T0b", [("P4", None)], t0)           # P4 -> T0 (second input arc of T0 in Figure 2)
    net.add_transition("T1", [("P1", None)], t1)
    net.add_transition("T1b", [("P6", None)], t1)           # P6 -> T1
    net.add_transition("T2", [("P2", None)], lambda b, now: t2([mark_leg(b[0])], now))
    net.add_transition("T4", [("P5", lambda c: c["cat"])], t4)
    net.add_transition("T5", [("P5", lambda c: not c["cat"])], t5)
    net.add_transition("T3", [("P3", None)], t3)
    for i in range(n_trains):
        net.put("P0", {"id": i, "cat": False}, t=0.0)
    return net


def run(n_trains: int = 5, horizon: float = 600.0, seed: int = 1) -> dict:
    net = build(n_trains, seed)
    fired = Counter()
    net.on_fire = lambda tr, col, now: fired.update([tr.name.rstrip("b")])
    net.run(until=horizon)
    return dict(firings=dict(fired), marking=net.marking(), tokens=sum(net.marking().values()))


if __name__ == "__main__":                                  # pragma: no cover
    print(run())
