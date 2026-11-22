"""Reference model of the what-if agent swarm for the "map story" mockup.

This is the same rule set the browser runs (app/src/swarm.ts), written in Python so the
mockup's numbers are computed, not drawn by hand. Inputs are the real /api/trip payload
(post office points, every facility that lists the service within reach, and whether its
record shows evidence). Nothing here is random: agent start delays come from a hash of
(seed, village, agent index), so a recording repeats exactly.

Rules
  evidence  ("Trust the record's evidence"): go to the facility with evidence that has the
            fewest estimated minutes. Decided again from the current position whenever the
            world changes (verify, add, close).
  claims    ("Trust specialty claims alone"): go to the nearest facility that lists the
            service. On arrival, if its record shows no evidence, the agent goes on to its
            next choice: the nearest facility whose record shows it, decided from where it
            stands. Worded as a counterfactual: "if that place cannot help". The no-evidence
            assumption is applied once; chaining it (try the next listing place, and the
            next) averaged 9.1 stops per rerouted village in Lucknow, which reads as a claim.
Minutes   = state factor x 2.30 x km^0.842 (origin village's state; D048), free-flow.
Movement  = straight lines.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field

import numpy as np

A_MIN, B_EXP = 2.30, 0.842


def hav(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371.0088 * np.arcsin(np.sqrt(a))


def minutes(km, f):
    return f * A_MIN * np.power(np.maximum(km, 0.0), B_EXP)


def h01(*parts) -> float:
    d = hashlib.blake2b("|".join(map(str, parts)).encode(), digest_size=8).digest()
    return int.from_bytes(d, "big") / 2 ** 64


@dataclass
class World:
    """Facilities as parallel arrays. evidence: the record shows the service (grade 2+, no
    blocking warning). open: not closed by a what-if. whatif: the edit label, if any."""
    lon: np.ndarray
    lat: np.ndarray
    evidence: np.ndarray
    open: np.ndarray
    name: list
    whatif: list = field(default_factory=list)

    @classmethod
    def from_trip(cls, t):
        F = t["facilities"]
        return cls(np.array([f["lon"] for f in F]), np.array([f["lat"] for f in F]),
                   np.array([bool(f["supported"]) for f in F]), np.ones(len(F), bool),
                   [f["name"] for f in F], [None] * len(F))

    def copy(self):
        return World(self.lon.copy(), self.lat.copy(), self.evidence.copy(), self.open.copy(), list(self.name),
                     list(self.whatif))

    def verify(self, i):
        w = self.copy()
        w.evidence[i] = True
        w.whatif[i] = "verified"
        return w

    def close(self, i):
        w = self.copy()
        w.open[i] = False
        w.whatif[i] = "closed"
        return w

    def add(self, lon, lat, name):
        w = self.copy()
        w.lon = np.append(w.lon, lon)
        w.lat = np.append(w.lat, lat)
        w.evidence = np.append(w.evidence, True)
        w.open = np.append(w.open, True)
        w.name.append(name)
        w.whatif.append("added")
        return w


def counters(pts, fac_state, world: World, window_min):
    """The three before/after counters. pts: (n,2) lon,lat; fac_state: per-village factor."""
    plon, plat = pts[:, 0], pts[:, 1]
    km = hav(plat[:, None], plon[:, None], world.lat[None, :], world.lon[None, :])
    mins = minutes(km, fac_state[:, None])
    ev = world.evidence & world.open
    m_ev = np.where(ev[None, :], mins, np.inf).min(axis=1) if ev.any() else np.full(len(pts), np.inf)
    within = int((m_ev <= window_min).sum())
    finite = np.isfinite(m_ev)
    avg = float(m_ev[finite].mean()) if finite.any() else None
    lst = world.open
    first = np.where(lst[None, :], km, np.inf).argmin(axis=1)
    first_no_ev = int((~world.evidence[first]).sum())
    return {"n": len(pts), "within": within, "avg_min": avg, "first_no_evidence": first_no_ev}


def verify_priority(pts, fac_state, world: World, window_min, top=3):
    """Try verifying each open facility whose record shows no evidence. Rank by villages
    brought within the window, then by fewer first stops without evidence, then by minutes."""
    base = counters(pts, fac_state, world, window_min)
    rows = []
    for i in np.flatnonzero(world.open & ~world.evidence):
        c = counters(pts, fac_state, world.verify(i), window_min)
        rows.append({"i": int(i), "name": world.name[i], "after": c,
                     "gain_within": c["within"] - base["within"],
                     "gain_first": base["first_no_evidence"] - c["first_no_evidence"],
                     "gain_min": (base["avg_min"] or 0) - (c["avg_min"] or 0)})
    rows.sort(key=lambda r: (-r["gain_within"], -r["gain_first"], -r["gain_min"]))
    return base, rows[:top], len(rows)


@dataclass(eq=False)
class Agent:
    village: int
    k: int
    depart: float
    legs: list = field(default_factory=list)   # [(x0,y0,x1,y1,t0,t1,target, kind)]
    stops_without_evidence: int = 0


def run(pts, fac_state, world: World, policy, t_end, agents_per_village=3, seed=7, stagger=18.0,
        events=(), max_hops=6, hold=4.0):
    """Step the swarm to model minute t_end. events: [(t, World)] world changes; agents
    re-decide when one happens. Returns agents with their leg history and the live legs."""
    agents = []
    for v in range(len(pts)):
        for k in range(agents_per_village):
            agents.append(Agent(v, k, h01(seed, v, k) * stagger))
    worlds = [(0.0, world)] + sorted(events, key=lambda e: e[0])

    def world_at(t):
        w = worlds[0][1]
        for te, we in worlds:
            if te <= t:
                w = we
        return w

    def choose(a, lon, lat, w, visited, referred=False):
        km = hav(lat, lon, w.lat, w.lon)
        if policy == "evidence" or referred:
            ok = w.evidence & w.open
        else:
            ok = w.open.copy()
            if visited:
                ok[list(visited)] = False
        if not ok.any():
            return None, None
        j = int(np.where(ok, km, np.inf).argmin())
        return j, float(km[j])

    for a in agents:
        lon, lat = pts[a.village]
        t = a.depart
        f = fac_state[a.village]
        visited = set()
        hops = 0
        referred = False
        while t < t_end and hops <= max_hops:
            w = world_at(t)
            j, km = choose(a, lon, lat, w, visited, referred)
            if j is None:
                break
            dur = float(minutes(km, f))
            t1 = t + dur
            # a world change while travelling: cut the leg there and re-decide
            nxt = [te for te, _ in worlds if t < te < min(t1, t_end)]
            if nxt:
                te = nxt[0]
                fr = (te - t) / dur if dur > 0 else 1
                nlon, nlat = lon + (w.lon[j] - lon) * fr, lat + (w.lat[j] - lat) * fr
                a.legs.append((lon, lat, nlon, nlat, t, te, j, "cut"))
                lon, lat, t = nlon, nlat, te
                continue
            a.legs.append((lon, lat, float(w.lon[j]), float(w.lat[j]), t, t1, j, "leg"))
            if t1 > t_end:
                break
            lon, lat, t = float(w.lon[j]), float(w.lat[j]), t1
            ww = world_at(t)
            if ww.evidence[j] and ww.open[j]:
                break  # arrived at a facility whose record shows it
            if policy == "evidence":
                continue  # it was closed or changed while travelling; re-decide
            a.stops_without_evidence += 1
            # hold at the stop for a moment (drawn as a flash), then go on
            a.legs.append((lon, lat, lon, lat, t, t + hold, j, "hold"))
            t += hold
            visited.add(j)
            hops += 1
            referred = True
    return agents


def position(a: Agent, t):
    """Where the agent is at minute t, and the index of its current leg (or None)."""
    for n, (x0, y0, x1, y1, t0, t1, j, kind) in enumerate(a.legs):
        if t0 <= t <= t1:
            fr = (t - t0) / (t1 - t0) if t1 > t0 else 1
            return x0 + (x1 - x0) * fr, y0 + (y1 - y0) * fr, n
    if a.legs and t > a.legs[-1][5]:
        return a.legs[-1][2], a.legs[-1][3], None
    return None


def claims_outcomes(pts, fac_state, world: World):
    """Per village under 'claims': stops at places without evidence before one with it, and the
    extra minutes against going straight to the nearest facility with evidence."""
    out = []
    ev = world.evidence & world.open
    for v in range(len(pts)):
        lon, lat = pts[v]
        f = fac_state[v]
        total, hops = 0.0, 0
        km = hav(lat, lon, world.lat, world.lon)
        j = int(np.where(world.open, km, np.inf).argmin())
        total += float(minutes(km[j], f))
        if not ev[j] and ev.any():
            hops = 1
            km2 = hav(world.lat[j], world.lon[j], world.lat, world.lon)
            total += float(minutes(np.where(ev, km2, np.inf).min(), f))
        km0 = hav(lat, lon, world.lat, world.lon)
        direct = float(minutes(np.where(ev, km0, np.inf).min(), f)) if ev.any() else math.inf
        out.append((hops, total - direct))
    return out
