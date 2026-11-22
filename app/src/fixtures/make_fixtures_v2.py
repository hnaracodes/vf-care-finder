"""Dev-only fixtures in the SPEC section 3 contract shape (app/design/SPEC.md), for building the
results UI before the server's enrich_answer and /api/sim land (engineer C).

Writes app/src/fixtures/real/*.json from the real bundle, so the UI can be checked against real
records. The folder is git-ignored (it holds facility names and quotes from the bundle, which is
not versioned). The UI marks every fixture answer with a visible FIXTURE badge, and fixtures are
only reachable in a dev build with ?fixtures=1.

This is a stand-in for C's code, written from the same contract text. When the real endpoint
exists the UI uses it and these files are not read.

Run:  .venv/bin/python app/src/fixtures/make_fixtures_v2.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "dbapp"))
import server  # noqa: E402  (puts the repo src/vf first on sys.path)
from vf import public_list, questions, rank, travel, why_not  # noqa: E402
from vf import services as S  # noqa: E402
from vf.geo import haversine_km  # noqa: E402

OUT = Path(__file__).resolve().parent / "real"
CASES = [
    ("emoc", "226001", "lucknow"),
    ("dialysis", "Jokihat, Bihar", "jokihat"),
    ("dialysis", "Jaipur", "jaipur"),
    ("trauma", "Kotputli", "kotputli"),
    ("emoc", "Lucknow", "lucknow_city"),
]
SHORTENERS = ("goo.gl", "maps.app.goo.gl", "bit.ly", "tinyurl.com")
MAIN_KEY = {"emoc": "caesarean section", "dialysis": "dialysis unit, machines or nephrologist"}


def title(s):
    return " ".join(w.capitalize() for w in str(s).split()) if s else s


def req_key(req: str):
    for k in sorted(questions.REQUIREMENT_PHRASE, key=len, reverse=True):
        if req.startswith(k):
            return k
    return req.split(" (")[0]


def official_domain(url):
    if not url:
        return None
    u = url if "://" in url else "http://" + url
    p = urlparse(u)
    host = (p.hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    full = host + p.path
    if any(host == s or host.endswith("." + s) for s in SHORTENERS) or host.startswith("maps.google") \
            or full.startswith("google.com/maps"):
        return None
    return host or None


def marks_for(text, rx):
    if rx is None:
        return []
    from vf import recipes
    norm = recipes.normalize_text(text)
    # normalize_text may change length; only keep marks when lengths agree
    if len(norm) != len(text):
        low = text.lower()
        return [[m.start(), m.end()] for m in rx.finditer(low)]
    return [[m.start(), m.end()] for m in rx.finditer(norm)]


def enrich_card(s, c, line, origin_state, loose, infos, book, rx, plist):
    name = c.get("name") or ""
    a, _, b = name.partition(",")
    c["name_short"], c["name_rest"] = a.strip(), (b.strip() or None)
    if c.get("minutes") is not None:
        rng = f"{travel.fmt_minutes(c['minutes_low'])} to {travel.fmt_minutes(c['minutes_high'])}"
        c["time"] = {"short": rng if loose else "about " + travel.fmt_minutes(c["minutes"]), "range": rng,
                     "loose": bool(loose)}
    else:
        c["time"] = None
    st = s.index.state_at(c["lat"], c["lon"]) if c.get("lat") is not None else None
    c["state_display"] = title(st) if st else c.get("state")
    c["crosses_state"] = bool(c["state_display"] and origin_state and c["state_display"].lower() != origin_state.lower())
    merged, by = [], {}
    for e in c.get("evidence") or []:
        t = questions.mask_numbers(e["quote"])
        k = " ".join(t.lower().split()).rstrip(" .")
        keys = [req_key(r) for r in (e.get("requirements") or [e["requirement"]])]
        if k in by:
            for kk in keys:
                if kk not in by[k]["requirement_keys"]:
                    by[k]["requirement_keys"].append(kk)
            continue
        q = {"text": t, "field": e["field"], "requirement_keys": keys, "marks": marks_for(t, rx)}
        by[k] = q
        merged.append(q)
    c["quotes"] = merged
    miss = [req_key(m.replace("no fact in the record mentions ", "")) for m in c.get("missing") or []]
    c["missing_phrases"] = [questions.REQUIREMENT_PHRASE.get(k, k) for k in miss]
    c["questions"] = [{"question": q["question"], "why": q["why"]}
                      for q in questions.questions_for(c, line, infos.get(c["facility_id"]), max_questions=3)]
    ci = questions.call_info(c["facility_id"], book, reveal=False)
    c["call"] = {"has_phone": ci["has_phone"], "phone": ci["phone"], "tel": None, "note": ci["note"]}
    c["official_domain"] = official_domain((c.get("sources") or {}).get("official_website"))
    c["public_list"] = plist.line(c["facility_id"]) if line == "emoc" else None
    c.setdefault("outside_lines", [])
    return c


def build(s, service, q, infos, book, plist):
    res = s.resolve(q, None, None)
    ans = server.do_answer(s, service, res, None)
    idx = s.index
    w = ans.get("window") or {}
    loc = ans["location"]
    o_state = title(w.get("state") or loc.get("state") or idx.state_at(res.lat, res.lon))
    prec = loc.get("precision_km")
    ans["origin"] = {"short_name": rank._place_name(loc), "label": loc.get("label"), "precision_km": prec,
                     "loose": bool(w.get("origin_loose")),
                     "too_coarse": bool(prec is not None and w.get("km_equivalent") and prec > w["km_equivalent"]),
                     "state_display": o_state}
    w["hours_text"] = travel.fmt_hours(w["minutes"])
    w["reach_text"] = travel.fmt_minutes(w["reach_minutes"])
    rx = why_not.literal_pattern(service)
    loose = bool(w.get("origin_loose"))
    bid = (ans.get("beyond_radius") or {}).get("facility_id")
    ans["further_away"] = [c for c in ans.get("further_away") or [] if c.get("facility_id") != bid]
    cards = list(ans["results"]) + ([ans["beyond_radius"]] if ans.get("beyond_radius") else []) + ans["further_away"]
    for c in cards:
        enrich_card(s, c, service, o_state, loose, infos, book, rx, plist)

    # sim payload (SPEC 3.2, rules of 7.3)
    sim = build_sim(s, service, res, ans)
    fac_i = {f["facility_id"]: f["i"] for f in sim["facilities"]} if sim["status"] == "ok" else {}
    for c in cards:
        c["sim_index"] = fac_i.get(c["facility_id"])
    wn = why_not.why_not_strip(idx, service, res, ans, mask_names=False)
    st_of = {f["facility_id"]: f["status"] for f in sim.get("facilities", [])}
    for i, it in enumerate(wn["items"]):
        it["letter"] = chr(65 + i)
        it["in_sim"] = st_of.get(it["facility_id"]) in ("claim_only", "held_back")
        for f in sim.get("facilities", []):
            if f["facility_id"] == it["facility_id"]:
                f["why_not_letter"] = it["letter"]
    ans["why_not"] = wn
    fn = why_not.funnel(idx, service, res, answer_dict=ans, max_ids=0)
    ans["funnel"] = {"steps": [{"step": x["step"], "label": x["label"], "n": x["n"]} for x in fn["steps"]],
                     "consistency": fn.get("consistency")}
    ans["demo_masked"] = False
    if hasattr(public_list, "annotate_answer"):
        try:
            public_list.annotate_answer(ans, service, res.lat, res.lon, res.state)
        except Exception as e:  # noqa: BLE001
            print("annotate_answer failed:", e)
    for c in cards:
        if service != "emoc":
            c["public_list"] = None
    ans["fixture"] = True
    sim["fixture"] = True
    return server.to_jsonable(res), server.to_jsonable(ans), server.to_jsonable(sim)


def build_sim(s, service, res, ans):
    idx = s.index
    w = ans["window"]
    W, A = float(w["minutes"]), float(w["allowance"])
    a, b = travel.A_MIN, travel.B_EXP
    o_lat, o_lon = float(res.lat), float(res.lon)
    ofac = w.get("factor")
    if ofac is None:
        return {"status": "unavailable", "reason": "Drive times are not modelled for island origins."}
    po = idx.post_offices
    po = po[~po["placeholder"].astype(bool)].dropna(subset=["lat", "lon"])
    vc = po["lat"].value_counts()
    po = po[~po["lat"].isin(vc[vc >= 100].index)]
    po = po.drop_duplicates(subset=["lat", "lon"])
    dv = haversine_km(o_lat, o_lon, po["lat"].to_numpy(float), po["lon"].to_numpy(float))
    sel = np.flatnonzero(dv <= 50)
    sel = sel[np.argsort(dv[sel], kind="stable")][:3000]
    vlat, vlon = po["lat"].to_numpy(float)[sel], po["lon"].to_numpy(float)[sel]
    vpin = po["pin"].astype(str).to_numpy()[sel]
    vf = []
    for la, lo in zip(vlat, vlon):
        f = travel.state_factor(idx.state_at(la, lo)).value
        vf.append(f if f is not None else ofac)
    vf = np.asarray(vf, float)

    d = rank._distances(idx, o_lat, o_lon)
    g = rank.gate_masks(idx, service, d, res)
    ld = idx.lines[service]
    pos = [int(p) for p in np.flatnonzero(g["in_reach"]) if int(p) not in idx.dup_drop]
    pos.sort(key=lambda p: d[p])
    pos = pos[:2500]
    card_rank = {c["facility_id"]: i + 1 for i, c in enumerate(ans["results"])}
    if not ans["results"] and ans.get("beyond_radius"):
        card_rank[ans["beyond_radius"]["facility_id"]] = 1
    facs = []
    for i, p in enumerate(pos):
        r = idx.fac.iloc[p]
        gr = int(ld.grade[p])
        stt = "evidence" if g["rec_mask"][p] else ("held_back" if gr >= 2 and bool(ld.blocked[p]) else "claim_only")
        facs.append({"i": i, "facility_id": r["facility_id"], "name": r["name"], "lat": float(idx.lat[p]),
                     "lon": float(idx.lon[p]), "status": stt, "strong": stt == "evidence" and gr >= 3, "grade": gr,
                     "approximate": bool(idx.approx[p]) if idx.approx is not None else False,
                     "operator": rank._str_or_none(r.get("operator_type")), "km_from_origin": round(float(d[p]), 1),
                     "card_rank": card_rank.get(r["facility_id"]), "why_not_letter": None, "name_masked": False})
    flat = np.array([f["lat"] for f in facs]); flon = np.array([f["lon"] for f in facs])
    ev = np.array([f["status"] == "evidence" for f in facs], bool)
    V, F = len(vlat), len(facs)
    KM = np.stack([haversine_km(vlat[v], vlon[v], flat, flon) for v in range(V)]) if V and F else np.zeros((V, F))
    MIN = vf[:, None] * a * np.power(KM, b)
    nearest = np.where(ev[None, :], MIN, np.inf).min(axis=1) if F else np.full(V, np.inf)
    within = int((nearest <= W).sum())
    fin = np.isfinite(nearest)
    avg = round(float(nearest[fin].mean()), 1) if fin.any() else None
    first = KM.argmin(axis=1) if F else np.full(V, -1)
    noev = ~ev[first] if F else np.zeros(V, bool)
    st_arr = np.array([f["status"] for f in facs])
    extra = []
    for v in np.flatnonzero(noev & fin):
        j = first[v]
        dj = haversine_km(flat[j], flon[j], flat[ev], flon[ev])
        k = np.flatnonzero(ev)[int(np.argmin(dj))]
        extra.append(MIN[v, j] + vf[v] * a * float(haversine_km(flat[j], flon[j], flat[k], flon[k])) ** b - nearest[v])
    summary = {"n_villages": V, "within": within, "avg_minutes": avg, "n_no_evidence_in_reach": int((~fin).sum()),
               "first_no_evidence": int(noev.sum()),
               "first_claim_only": int((noev & (st_arr[first] == "claim_only")).sum()) if F else 0,
               "first_held_back": int((noev & (st_arr[first] == "held_back")).sum()) if F else 0,
               "extra_minutes_mean": round(float(np.mean(extra)), 1) if extra else None,
               "n_facilities": F, "n_evidence": int(ev.sum()), "n_claim_only": int((st_arr == "claim_only").sum()),
               "n_held_back": int((st_arr == "held_back").sum()),
               "n_listing_records": int(g["in_reach"].sum()),
               "n_merged_duplicates": int(g["in_reach"].sum()) - len(pos)}
    pl = s.resolver.places
    dl = haversine_km(o_lat, o_lon, pl["lat"].to_numpy(float), pl["lon"].to_numpy(float))
    labels = []
    o_dist = str(res.district or "").lower() if getattr(res, "district", None) else ""
    for k in np.flatnonzero((dl <= 250) & pl["kind"].isin(["district", "city"]).to_numpy()):
        r = pl.iloc[int(k)]
        labels.append({"name": title(r["name"]), "kind": r["kind"], "lat": float(r["lat"]), "lon": float(r["lon"]),
                       "prio": 1 if o_dist and str(r["name"]).lower() == o_dist else 2})
    cells = {}
    for k in np.flatnonzero((dl <= 60) & (pl["kind"] == "post_office").to_numpy()):
        r = pl.iloc[int(k)]
        if not np.isfinite(r["lat"]):
            continue
        key = (int(r["lat"] / 0.054), int(r["lon"] / 0.06))
        if key not in cells or (r["n_offices"] or 0) > (cells[key]["n_offices"] or 0):
            cells[key] = r
    for r in cells.values():
        labels.append({"name": title(r["name"]), "kind": "post_office", "lat": float(r["lat"]), "lon": float(r["lon"]),
                       "prio": 3})
    labels = sorted(labels, key=lambda x: x["prio"])[:120]
    basis = (w.get("basis") or "").split(". ")[0].rstrip(".") + "."
    return {"status": "ok", "version": "sim_v1", "service": service, "service_label": ans["service_label"],
            "service_phrase": why_not.label_lc(service),
            "origin": {"lat": o_lat, "lon": o_lon, "short_name": ans["origin"]["short_name"],
                       "label": ans["origin"]["label"], "precision_km": ans["origin"]["precision_km"],
                       "state_display": ans["origin"]["state_display"], "factor": ofac},
            "window": {"minutes": W, "allowance": A, "hours_text": w["hours_text"], "km_equivalent": w["km_equivalent"],
                       "reach_km": w["reach_km"], "reach_minutes": w["reach_minutes"], "basis_short": basis},
            "travel": {"a": a, "b": b, "text": travel.MODEL_TEXT},
            "villages": {"n": V, "radius_km": 50, "lon": [round(x, 5) for x in vlon], "lat": [round(x, 5) for x in vlat],
                         "pin": list(vpin), "factor": [round(x, 3) for x in vf]},
            "facilities": facs, "summary": summary, "context": {"labels": labels},
            "notes": ["Straight-line paths; times from the drive-time estimate, free-flow.",
                      "Each village is a post office area; offices sharing one point are one village."]}


def main():
    s = server.state()
    infos = questions.load_record_info(s.bundle)
    book = questions.load_phone_book()
    plist = public_list.load(s.bundle)
    OUT.mkdir(exist_ok=True)
    (OUT / ".gitignore").write_text("*\n")
    index = {}
    for service, q, key in CASES:
        res, ans, sim = build(s, service, q, infos, book, plist)
        for kind, obj in (("resolve", res), ("answer", ans), ("sim", sim)):
            (OUT / f"{kind}_{key}.json").write_text(json.dumps(obj))
        index[key] = {"service": service, "q": q}
        sm = sim.get("summary", {})
        print(f"{key:13s} {ans['status']:20s} cards={len(ans['results'])} villages={sm.get('n_villages')} "
              f"within={sm.get('within')} avg={sm.get('avg_minutes')} first_noev={sm.get('first_no_evidence')}")
    amb = s.resolve("Aurangabad", None, None)
    (OUT / "resolve_aurangabad.json").write_text(json.dumps(server.to_jsonable(amb)))
    (OUT / "cases.json").write_text(json.dumps(index))
    (OUT / "services.json").write_text(json.dumps(server.to_jsonable(
        {"services": s.services, "fallback": {"id": "other", "label": "Something else", "needs_text": True},
         "simulate_available": False})))


if __name__ == "__main__":
    main()
