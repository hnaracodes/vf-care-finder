"""Build /api/sim fixtures for the agent simulator (SPEC 3.2, 3.3) until the server's
/api/sim lands. Every number comes from the bundle through the real ranker; the rules
mirror SPEC 7.3 so the browser engine can be checked for parity against `summary`.

Run:  .venv/bin/python app/src/sim/fixtures/make_fixtures.py
Writes app/src/sim/fixtures/sim_<case>.json (the shape of SimPayload in sim/contract.ts).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "dbapp"))
import server  # noqa: E402  (puts the repo's src/vf first on the path)
from vf import rank, travel as T, why_not  # noqa: E402
from vf import services as S  # noqa: E402
from vf.geo import haversine_km  # noqa: E402

OUT = Path(__file__).resolve().parent
SIM_VILLAGE_KM = 50.0
MAX_VILLAGES = 3000
MAX_FACILITIES = 2500

CASES = {
    "lucknow_emoc": ("emoc", "226001", None),
    "jokihat_dialysis": ("dialysis", "Jokihat", None),
    "jaipur_dialysis": ("dialysis", "Jaipur", None),
    "kotputli_trauma": ("trauma", "Kotputli", None),
}


def title(s):
    return T._nice(s) if s else ""


def summary_rules(V, F, W, A, a, b, vfac, km, status, ev, opn):
    """SPEC 7.3, numpy reference. km is V x F."""
    mins = vfac[:, None] * a * np.power(km, b)
    m_ev = np.where(ev[None, :], mins, np.inf)
    near = m_ev.min(axis=1) if F else np.full(V, np.inf)
    fin = np.isfinite(near)
    within = int((near <= W).sum())
    avg = round(float(near[fin].mean()), 1) if fin.any() else None
    km_open = np.where(opn[None, :], km, np.inf)
    first = km_open.argmin(axis=1)  # ties: lowest index
    first_noev = ~ev[first]
    st = status[first]
    fc = np.flatnonzero(first_noev & fin)
    extra = None
    if len(fc):
        ev_idx = np.flatnonzero(ev)
        vals = []
        for v in fc:
            j = first[v]
            d = haversine_km(flat[j], flon[j], flat[ev_idx], flon[ev_idx])
            js = ev_idx[int(np.argmin(d))]
            leg = vfac[v] * a * float(d.min()) ** b
            vals.append(mins[v, j] + leg - near[v])
        extra = round(float(np.mean(vals)), 1)
    return {
        "within": within, "avg_minutes": avg, "n_no_evidence_in_reach": int((~fin).sum()),
        "first_no_evidence": int(first_noev.sum()),
        "first_claim_only": int((first_noev & (st == 1)).sum()),
        "first_held_back": int((first_noev & (st == 2)).sum()),
        "extra_minutes_mean": extra,
    }


def build(s, service, q, st):
    global flat, flon
    t0 = time.perf_counter()
    res = s.resolve(q, st, None)
    assert res.status == "ok", (q, res.status)
    ans = server.do_answer(s, service, res, None)
    idx = s.index
    line = service
    lat, lon = float(res.lat), float(res.lon)
    loc = rank._location_dict(res)
    w = ans.get("window") or {}
    W = float(w.get("minutes") or S.WINDOWS[service].minutes)
    A = float(w.get("allowance") if w.get("allowance") is not None else S.WINDOWS[service].allowance)
    ofac = T.origin_factor(loc, idx.state_at)
    assert not ofac.sea
    of = float(ofac.value)

    # villages
    po = idx.post_offices
    po = po[~po["placeholder"].astype(bool)]
    vc = po["lat"].value_counts()
    po = po[~po["lat"].isin(vc[vc >= 100].index)]
    po = po.dropna(subset=["lat", "lon"]).drop_duplicates(subset=["lat", "lon"])
    d = haversine_km(lat, lon, po["lat"].to_numpy(float), po["lon"].to_numpy(float))
    sel = np.flatnonzero(d <= SIM_VILLAGE_KM)
    sel = sel[np.argsort(d[sel], kind="stable")][:MAX_VILLAGES]
    vlat = po["lat"].to_numpy(float)[sel]
    vlon = po["lon"].to_numpy(float)[sel]
    vpin = po["pin"].astype(str).to_numpy()[sel]
    vfac = []
    for la, lo in zip(vlat, vlon):
        f = T.state_factor(idx.state_at(la, lo)).value
        vfac.append(of if f is None else float(f))
    vfac = np.asarray(vfac, float)

    # facilities
    dd = rank._distances(idx, lat, lon)
    g = rank.gate_masks(idx, line, dd, res)
    ld = idx.lines[line]
    cand = [int(p) for p in np.flatnonzero(g["in_reach"]) if int(p) not in idx.dup_drop]
    cand.sort(key=lambda p: (dd[p], p))
    n_listing = int(g["in_reach"].sum())
    n_merged = n_listing - len(cand)
    cand = cand[:MAX_FACILITIES]
    cards = list(ans.get("results") or [])
    card_rank = {c["facility_id"]: r for r, c in enumerate(cards, 1)}
    if not cards and ans.get("beyond_radius"):
        card_rank[ans["beyond_radius"]["facility_id"]] = 1
    strip = why_not.why_not_strip(idx, line, res, ans, mask_names=False)
    letters = {}
    for i, it in enumerate(strip.get("items") or []):
        letters[it["facility_id"]] = chr(ord("A") + i)
    fac = []
    status = []
    for i, p in enumerate(cand):
        fid = str(idx.fac["facility_id"].iat[p])
        grade = int(ld.grade[p])
        if bool(g["rec_mask"][p]):
            stt = "evidence"
        elif grade >= 2 and bool(ld.blocked[p]):
            stt = "held_back"
        else:
            stt = "claim_only"
        status.append({"evidence": 0, "claim_only": 1, "held_back": 2}[stt])
        op = idx.fac["operator_type"].iat[p] if "operator_type" in idx.fac else None
        fac.append({
            "i": i, "facility_id": fid, "name": str(idx.fac["name"].iat[p]),
            "lat": float(idx.lat[p]), "lon": float(idx.lon[p]), "status": stt,
            "strong": stt == "evidence" and grade >= 3, "grade": grade,
            "approximate": bool(idx.approx[p]) if idx.approx is not None else False,
            "operator": None if op is None or str(op) == "nan" else str(op),
            "km_from_origin": round(float(dd[p]), 1), "card_rank": card_rank.get(fid),
            "why_not_letter": letters.get(fid), "name_masked": False,
        })
    status = np.asarray(status, int)
    flat = np.asarray([f["lat"] for f in fac], float)
    flon = np.asarray([f["lon"] for f in fac], float)
    km = haversine_km(vlat[:, None], vlon[:, None], flat[None, :], flon[None, :])
    ev = status == 0
    rules = summary_rules(len(sel), len(fac), W, A, T.A_MIN, T.B_EXP, vfac, km, status, ev, np.ones(len(fac), bool))

    # context labels
    pl = s.resolver.places
    plat, plon = pl["lat"].to_numpy(float), pl["lon"].to_numpy(float)
    pd_ = haversine_km(lat, lon, plat, plon)
    kinds = pl["kind"].to_numpy()
    labels = []
    odist = (loc.get("district") or "").lower()
    for k in np.flatnonzero(np.isin(kinds, ["district", "city"]) & (pd_ <= 250)):
        nm = str(pl["name"].iat[k])
        prio = 1 if kinds[k] == "district" and nm.lower() == odist else 2
        labels.append({"name": nm.title(), "kind": str(kinds[k]), "lat": float(plat[k]), "lon": float(plon[k]),
                       "prio": prio, "_d": float(pd_[k])})
    cells = {}
    for k in np.flatnonzero((kinds == "post_office") & (pd_ <= 60)):
        cy = int(np.floor((plat[k] - lat) * 111.0 / 6))
        cx = int(np.floor((plon[k] - lon) * 111.0 * np.cos(np.radians(lat)) / 6))
        n = pl["n_offices"].iat[k]
        n = 0 if n is None or n != n else int(n)
        if (cx, cy) not in cells or n > cells[(cx, cy)][0]:
            cells[(cx, cy)] = (n, k)
    for n, k in cells.values():
        labels.append({"name": str(pl["name"].iat[k]).title(), "kind": "post_office", "lat": float(plat[k]),
                       "lon": float(plon[k]), "prio": 3, "_d": float(pd_[k])})
    labels.sort(key=lambda r: (r["prio"], r["_d"]))
    labels = [{k: v for k, v in r.items() if k != "_d"} for r in labels[:120]]

    org = ans.get("location") or {}
    label = org.get("label") or q
    basis = (w.get("basis") or "").split(". ")[0].strip()
    payload = {
        "status": "ok", "version": "sim_v1", "service": service, "service_label": S.label(service),
        "service_phrase": rank._label_lc(service),
        "origin": {"lat": lat, "lon": lon, "short_name": rank._place_name(loc), "label": label,
                   "precision_km": loc.get("precision_km"),
                   "state_display": title(idx.state_at(lat, lon) or loc.get("state")), "factor": of},
        "window": {"minutes": W, "allowance": A, "hours_text": T.fmt_hours(W),
                   "km_equivalent": round(float(T.window_km(W, factor=of)), 1),
                   "reach_km": round(float(T.window_km(S.REACH_FACTOR_V1 * W, factor=of)), 1),
                   "reach_minutes": S.REACH_FACTOR_V1 * W, "basis_short": basis or None},
        "travel": {"a": T.A_MIN, "b": T.B_EXP, "text": T.MODEL_TEXT},
        "villages": {"n": int(len(sel)), "radius_km": SIM_VILLAGE_KM,
                     "lon": [round(float(x), 6) for x in vlon], "lat": [round(float(x), 6) for x in vlat],
                     "pin": [str(x) for x in vpin], "factor": [float(x) for x in vfac]},
        "facilities": fac,
        "summary": {"n_villages": int(len(sel)), **rules, "n_facilities": len(fac),
                    "n_evidence": int((status == 0).sum()), "n_claim_only": int((status == 1).sum()),
                    "n_held_back": int((status == 2).sum()), "n_listing_records": n_listing,
                    "n_merged_duplicates": n_merged},
        "context": {"labels": labels},
        "notes": ["Straight-line paths; times from the drive-time estimate, free-flow.",
                  "Each village is a post office area; offices sharing one point are one village."],
        "timing_ms": round((time.perf_counter() - t0) * 1000, 1),
    }
    # rounding the coordinates must not change the counters: recompute on the rounded villages
    km2 = haversine_km(np.asarray(payload["villages"]["lat"])[:, None], np.asarray(payload["villages"]["lon"])[:, None],
                       flat[None, :], flon[None, :])
    payload["summary"].update(summary_rules(len(sel), len(fac), W, A, T.A_MIN, T.B_EXP, vfac, km2, status, ev,
                                            np.ones(len(fac), bool)))
    return server.to_jsonable(payload)


def main():
    s = server.state()
    for name, (svc, q, st) in CASES.items():
        p = build(s, svc, q, st)
        (OUT / f"sim_{name}.json").write_text(json.dumps(p, separators=(",", ":")))
        sm = p["summary"]
        print(f"{name:18s} villages {sm['n_villages']:5d} within {sm['within']:5d} avg {sm['avg_minutes']} "
              f"first_noev {sm['first_no_evidence']} extra {sm['extra_minutes_mean']} fac {sm['n_facilities']} "
              f"(ev {sm['n_evidence']}) labels {len(p['context']['labels'])} {p['timing_ms']} ms")


if __name__ == "__main__":
    main()
