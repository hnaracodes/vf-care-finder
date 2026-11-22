"""Prototype of the browser swarm rules, run on the real bundle, to get the what-if numbers for the mockup.
Villages = the /api/trip points (post office areas within 50 km). Facilities = every record within reach that
lists the service (grade >= 1). Evidence = grade >= 2 and no blocking warning (the trip view's 'supported').
Minutes = factor x 2.30 x km^0.842, factor = the village's state (from its PIN in the gazetteer)."""
import json, sys, time, os
import numpy as np, pandas as pd
sys.path.insert(0, "/Users/hrudaynara/Projects/CMU Datathon/src")
from vf import rank, travel
B = "/Users/hrudaynara/Projects/CMU Datathon/dbapp/bundle"
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
idx = rank.load_index(B)
PL = pd.read_parquet(f"{B}/places.parquet")
pin_state = PL[PL.kind == "post_office"].drop_duplicates("pin").set_index("pin")["state"].to_dict() if "pin" in PL else {}

def hav(la1, lo1, la2, lo2):
    la1, lo1, la2, lo2 = map(np.radians, (la1, lo1, la2, lo2))
    a = np.sin((la2 - la1) / 2) ** 2 + np.cos(la1) * np.cos(la2) * np.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371.0088 * np.arcsin(np.sqrt(a))

def setup(key, line):
    a = json.load(open(os.path.join(DATA, f"a_{key}.json"))); t = json.load(open(os.path.join(DATA, f"t_{key}.json")))
    lat0, lon0 = a["location"]["lat"], a["location"]["lon"]
    W = a["window"]["minutes"]; allow = a["window"]["allowance"]
    P = np.array(t["points"]); plon, plat = P[:, 0], P[:, 1]
    fac = np.array([travel.state_factor(pin_state.get(p)).value or 1.0 for p in t["pins"]])
    ld = idx.lines[line]
    g = np.asarray(ld.grade, int); bl = np.asarray(ld.blocked, bool)
    flat, flon = np.asarray(idx.lat, float), np.asarray(idx.lon, float)
    ok = np.isfinite(flat) & (g >= 1)
    ok &= hav(lat0, lon0, np.where(ok, flat, 0), np.where(ok, flon, 0)) <= a["window"]["reach_km"]
    pos = np.flatnonzero(ok)
    F = pd.DataFrame({"pos": pos, "fid": idx.fac.facility_id.to_numpy()[pos], "name": idx.fac.name.to_numpy()[pos],
                      "lat": flat[pos], "lon": flon[pos], "grade": g[pos], "blocked": bl[pos],
                      "op": idx.fac.operator_type.to_numpy()[pos] if "operator_type" in idx.fac else None})
    F["evidence"] = (F.grade >= 2) & ~F.blocked
    KM = hav(plat[:, None], plon[:, None], F.lat.values[None, :], F.lon.values[None, :])
    return dict(a=a, t=t, W=W, allow=allow, plat=plat, plon=plon, fac=fac, F=F, KM=KM)

def run(S, evidence=None, alive=None, extra=None):
    F, KM, fac = S["F"], S["KM"], S["fac"]
    ev = F.evidence.values.copy() if evidence is None else evidence
    al = np.ones(len(F), bool) if alive is None else alive
    grade = np.where(ev & (F.grade.values < 2), 2, F.grade.values)
    km = KM
    if extra:  # added what-if facilities: evidence, grade 2
        km = np.hstack([KM] + [hav(S["plat"], S["plon"], la, lo)[:, None] for la, lo in extra])
        ev = np.concatenate([ev, np.ones(len(extra), bool)]); al = np.concatenate([al, np.ones(len(extra), bool)])
        grade = np.concatenate([grade, np.full(len(extra), 2)])
    mins = fac[:, None] * 2.30 * np.power(km, 0.842)
    m_ev = np.where(ev & al, mins, np.inf)
    nearest_ev = m_ev.min(axis=1)
    within = nearest_ev <= S["W"]
    order = m_ev - np.where(grade == 3, S["allow"], 0)[None, :]
    order_in = np.where(m_ev <= S["W"], order, np.inf)
    choice = np.where(within, order_in.argmin(axis=1), m_ev.argmin(axis=1))
    chosen_min = mins[np.arange(len(fac)), choice]
    m_cl = np.where(al, mins, np.inf)
    first = m_cl.argmin(axis=1)
    first_noev = ~ev[first]
    return dict(n=len(fac), n_within=int(within.sum()), mean_min=float(np.mean(chosen_min[np.isfinite(chosen_min)])),
                n_first_noev=int(first_noev.sum()), choice=choice, first=first)

def main():
  out = {}
  for key, line in [("lko", "emoc"), ("jok", "dialysis"), ("jpr", "dialysis")]:
      S = setup(key, line); F = S["F"]
      base = run(S)
      t0 = time.perf_counter()
      rows = []
      for j in np.flatnonzero(~F.evidence.values & ~F.blocked.values):
          ev = F.evidence.values.copy(); ev[j] = True
          r = run(S, evidence=ev)
          rows.append(dict(fid=F.fid.iloc[j], name=F.name.iloc[j], grade=int(F.grade.iloc[j]), blocked=bool(F.blocked.iloc[j]),
                           op=F.op.iloc[j], lat=F.lat.iloc[j], lon=F.lon.iloc[j],
                           n_within=r["n_within"], d_within=r["n_within"] - base["n_within"],
                           mean_min=r["mean_min"], d_min=r["mean_min"] - base["mean_min"],
                           n_first_noev=r["n_first_noev"], d_first=r["n_first_noev"] - base["n_first_noev"]))
      dt = (time.perf_counter() - t0) * 1000
      R = pd.DataFrame(rows).sort_values(["d_within", "d_min", "d_first"], ascending=[False, True, True])
      print(f"== {key} {line}: villages {base['n']}, window {S['W']} min, facilities in reach {len(F)} "
            f"(evidence {int(F.evidence.sum())}, claim-only/held back {int((~F.evidence).sum())}); verify-all scan {dt:.0f} ms")
      print("  BEFORE within", base["n_within"], "mean min %.1f" % base["mean_min"], "first stop no evidence", base["n_first_noev"],
            "| API trip n_first_unsupported", S["t"]["summary"]["n_first_unsupported"])
      print(R.head(6)[["name", "grade", "blocked", "op", "n_within", "d_within", "mean_min", "d_min", "n_first_noev", "d_first"]].to_string())
      # closing the first card, and adding a facility at the searched place
      a = S["a"]; first_card = (a["results"] or [a.get("beyond_radius")])[0]
      j = np.flatnonzero(F.fid.values == first_card["facility_id"])
      if len(j):
          al = np.ones(len(F), bool); al[j[0]] = False
          rc = run(S, alive=al)
          print("  CLOSE", first_card["name"], rc["n_within"], "%.1f" % rc["mean_min"], rc["n_first_noev"])
      else:
          rc = None
      ra = run(S, extra=[(a["location"]["lat"], a["location"]["lon"])])
      print("  ADD at searched place", ra["n_within"], "%.1f" % ra["mean_min"], ra["n_first_noev"])
      out[key] = dict(base={k: v for k, v in base.items() if k not in ("choice", "first")}, window=S["W"], n_fac=len(F),
                      n_ev=int(F.evidence.sum()), verify=R.head(5).to_dict("records"),
                      close={"name": first_card["name"], **({k: v for k, v in rc.items() if k not in ("choice", "first")} if rc else {})},
                      add_here={k: v for k, v in ra.items() if k not in ("choice", "first")}, scan_ms=dt)
  json.dump(out, open(os.path.join(DATA, "sim.json"), "w"), indent=1, default=str)

if __name__ == "__main__":
    main()
