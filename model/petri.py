"""
A small, dependency-free TIMED COLOURED PETRI NET (TCPN) engine.

It follows the same semantics as CPN Tools / CPN IDE (the tool used in the proposal, Figure 2):

* a *place* holds *tokens*; every token has a *colour* (here: any Python object, e.g. a train record or None for
  an anonymous resource token) and a *timestamp* (the earliest model time at which it can be consumed);
* a *transition* is enabled at time t when every input place offers a token with timestamp <= t that satisfies the
  arc filter, and the guard on the whole binding is true;
* firing consumes the bound tokens and produces output tokens whose timestamps are t + delay (timed net).

The engine is event driven: it only re-examines transitions whose input places changed or whose tokens became
available, so a full operating day of a regional rail network runs in a fraction of a second.

Usage
-----
    net = Net()
    net.add_place("P0"); net.add_place("P1")
    net.add_transition("T0", inputs=[("P0", None)], action=lambda b, now: [("P1", b[0], 5.0)])
    net.put("P0", {"id": 1}, t=0)
    net.run(until=100)
"""
from __future__ import annotations

import bisect
import heapq
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

EPS = 1e-9

Arc = Tuple[str, Optional[Callable[[Any], bool]]]          # (place name, optional colour filter)
Output = Tuple[str, Any, float]                             # (place name, colour, delay)


class Token:
    __slots__ = ("c", "t")

    def __init__(self, c: Any, t: float):
        self.c = c
        self.t = t

    def __repr__(self) -> str:                              # pragma: no cover
        return f"Token({self.c!r}@{self.t:.2f})"


class Transition:
    __slots__ = ("name", "inputs", "action", "guard", "idx", "fired")

    def __init__(self, name, inputs, action, guard, idx):
        self.name = name
        self.inputs: List[Arc] = list(inputs)
        self.action = action
        self.guard = guard
        self.idx = idx
        self.fired = 0


class Net:
    """Timed coloured Petri net with a global clock."""

    def __init__(self) -> None:
        self.places: Dict[str, List[Token]] = {}
        self.transitions: List[Transition] = []
        self._by_place: Dict[str, List[Transition]] = {}
        self._heap: List[Tuple[float, int, str]] = []
        self._seq = 0
        self._dirty: Dict[int, bool] = {}
        self.now = 0.0
        self.n_fired = 0
        self.on_fire: Optional[Callable[[Transition, Sequence[Any], float], None]] = None

    # ------------------------------------------------------------------ construction
    def add_place(self, name: str) -> None:
        if name not in self.places:
            self.places[name] = []
            self._by_place[name] = []

    def add_transition(self, name: str, inputs: Sequence[Arc], action: Callable[[Sequence[Any], float], Sequence[Output]],
                       guard: Optional[Callable[[Sequence[Any], float], bool]] = None) -> Transition:
        tr = Transition(name, inputs, action, guard, len(self.transitions))
        self.transitions.append(tr)
        for pl, _ in tr.inputs:
            if pl not in self.places:
                raise KeyError(f"unknown place {pl!r} in transition {name!r}")
            self._by_place[pl].append(tr)
        return tr

    # ------------------------------------------------------------------ marking
    def put(self, place: str, colour: Any = None, t: Optional[float] = None, delay: float = 0.0) -> None:
        """Add a token. t = absolute availability time (default: now); delay is added to the base time."""
        base = self.now if t is None else t
        tok = Token(colour, base + delay)
        bisect.insort(self.places[place], tok, key=lambda k: k.t)
        if tok.t <= self.now + EPS:
            self._mark(place)
        else:
            self._seq += 1
            heapq.heappush(self._heap, (tok.t, self._seq, place))

    def count(self, place: str, available_only: bool = False) -> int:
        if not available_only:
            return len(self.places[place])
        return sum(1 for k in self.places[place] if k.t <= self.now + EPS)

    def marking(self) -> Dict[str, int]:
        return {p: len(v) for p, v in self.places.items() if v}

    # ------------------------------------------------------------------ execution
    def _mark(self, place: str) -> None:
        for tr in self._by_place[place]:
            self._dirty[tr.idx] = True

    def _find_binding(self, tr: Transition) -> Optional[List[Token]]:
        now = self.now + EPS
        inputs = tr.inputs
        n = len(inputs)
        chosen: List[Token] = []

        def rec(i: int) -> Optional[List[Token]]:
            if i == n:
                if tr.guard is None or tr.guard([k.c for k in chosen], self.now):
                    return list(chosen)
                return None
            pl, pred = inputs[i]
            for tok in self.places[pl]:
                if tok.t > now:
                    break                                   # list is sorted by time
                if any(tok is k for k in chosen):
                    continue
                if pred is not None and not pred(tok.c):
                    continue
                chosen.append(tok)
                r = rec(i + 1)
                if r is not None:
                    return r
                chosen.pop()
            return None

        return rec(0)

    def _fire(self, tr: Transition, binding: List[Token]) -> None:
        for (pl, _), tok in zip(tr.inputs, binding):
            self.places[pl].remove(tok)
        colours = [k.c for k in binding]
        outs = tr.action(colours, self.now)
        for pl, col, delay in outs:
            self.put(pl, col, delay=max(0.0, delay))
        tr.fired += 1
        self.n_fired += 1
        if self.on_fire is not None:
            self.on_fire(tr, colours, self.now)

    def _settle(self) -> None:
        guard_loop = 0
        while self._dirty:
            idx = min(self._dirty)
            del self._dirty[idx]
            tr = self.transitions[idx]
            b = self._find_binding(tr)
            if b is not None:
                self._fire(tr, b)
                self._dirty[idx] = True                      # may be enabled again with another binding
                guard_loop += 1
                if guard_loop > 5_000_000:                   # pragma: no cover
                    raise RuntimeError("Petri net does not settle (infinite zero-delay loop?)")

    def run(self, until: float = float("inf")) -> float:
        """Run until no token will become available before `until`. Returns the final model time."""
        for tr in self.transitions:                          # initial scan (tokens put at t <= now before run)
            self._dirty[tr.idx] = True
        self._settle()
        while self._heap and self._heap[0][0] <= until:
            t = self._heap[0][0]
            self.now = t
            while self._heap and self._heap[0][0] <= t + EPS:
                _, _, pl = heapq.heappop(self._heap)
                self._mark(pl)
            self._settle()
        if until != float("inf"):
            self.now = max(self.now, min(until, self.now))
        return self.now

    # ------------------------------------------------------------------ export
    def to_dot(self) -> str:
        """Graphviz DOT description of the net structure (places = circles, transitions = boxes; input arcs only
        are stored in the engine, output arcs are declared by the optional `outputs` documentation if provided)."""
        lines = ["digraph PN {", "  rankdir=LR;"]
        for p in self.places:
            lines.append(f'  "{p}" [shape=circle];')
        for tr in self.transitions:
            lines.append(f'  "{tr.name}" [shape=box];')
            for pl, _ in tr.inputs:
                lines.append(f'  "{pl}" -> "{tr.name}";')
        lines.append("}")
        return "\n".join(lines)
