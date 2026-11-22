"""Write the front end's FIXTURE JSON files into app/src/fixtures/.

These are development fixtures, not data. Every facility in them is fictional
and named "Fixture ...". They exist so the query screen can be built and
screenshotted before dbapp/server.py answers. Shapes follow tasks/WP5_ranking.md
(Answer, Card) and tasks/WP2_geography_resolver.md (Resolution); the trip shape
is the one the front end proposes for /api/trip (see app/README.md).

Distances are computed with the same haversine as vf.geo; scores use the v0
formula in docs/04_thesis_and_plan.md so the bars add up. Post office points for
the trip fixture are real geocoded offices near Jaipur from
silver/post_offices.parquet (open government data), deduplicated by point.

Run: .venv/bin/python app/scripts/make_fixtures.py
"""

from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "app" / "src" / "fixtures"
sys.path.insert(0, str(ROOT / "src"))

R_EARTH = 6371.0088


def hav(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = p2 - p1
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    return 2 * R_EARTH * math.asin(math.sqrt(min(1.0, a)))


def write(name: str, obj) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = OUT / f".{name}.tmp"
    tmp.write_text(json.dumps(obj, indent=1, ensure_ascii=False) + "\n")
    os.replace(tmp, OUT / name)


SERVICES = [
    ("emoc", "Emergency C-section and high-risk delivery", 50,
     "A caesarean section named in the record; best with a blood bank and an operating theatre or anaesthesia."),
    ("nicu", "Newborn intensive care (NICU)", 75, "A neonatal intensive or special care unit named in the record."),
    ("dialysis", "Dialysis", 50, "Dialysis named in the record; best with a named dialysis unit, machines or a nephrologist."),
    ("icu", "Intensive care with ventilator", 75, "An intensive care unit named in the record; best with ventilators."),
    ("cardiac_surgery", "Heart surgery (bypass, valve)", 250, "Heart surgery such as bypass or valve replacement named in the record."),
    ("cath_lab", "Heart attack care (angiography, angioplasty)", 100, "A cath lab, angiography or angioplasty named in the record."),
    ("mri", "MRI scan", 100, "An MRI machine or MRI service named in the record."),
    ("ct", "CT scan", 75, "A CT scanner or CT service named in the record."),
    ("trauma", "Trauma and accident care", 50, "Trauma or accident care named in the record; best with an emergency unit open round the clock."),
    ("burns", "Burn care", 200, "A burns unit or burn care named in the record."),
    ("neurosurgery", "Head injury and brain surgery", 150, "Neurosurgery named in the record."),
    ("oncology", "Cancer treatment (chemotherapy, radiotherapy)", 250, "Chemotherapy or radiotherapy named in the record."),
]
SVC = {s[0]: {"id": s[0], "label": s[1], "radius_km": s[2], "description": s[3]} for s in SERVICES}

LIMITS = [
    "Distances are straight-line kilometres, not travel time.",
    "This dataset mostly covers private facilities with a web presence. Public hospitals are under-covered.",
    "Evidence describes what web pages say, not what a facility can do today. Pages can be stale.",
    "Quotes name the fact and its field, not the web page it came from: the data has no per-fact source.",
    "The test set for the fact tagger was labeled by an AI agent, not a clinician.",
    "Weights and radii are stated defaults, not fitted or clinically validated.",
]

GRADE_PART = {"supported": 0.7, "corroborated": 1.0}
STRENGTH_ADJ = {"thin": -0.2, "moderate": 0.0, "strong": 0.1}


def resolution(status, label, how, lat, lon, precision_km, kind, candidates=None, place_id=None):
    return {
        "status": status, "label": label, "how": how, "lat": lat, "lon": lon,
        "precision_km": precision_km, "kind": kind, "place_id": place_id,
        "candidates": candidates or [],
    }


def card(f, loc, radius, quotes_ok=True):
    d = hav(loc["lat"], loc["lon"], f["lat"], f["lon"])
    ev = None
    if f["grade_label"] in GRADE_PART:
        ev = min(1.0, GRADE_PART[f["grade_label"]] + STRENGTH_ADJ[f["strength"]])
    dist = math.exp(-d / radius)
    pens = f.get("penalties", [])
    score = None
    if ev is not None:
        score = round(0.5 * ev + 0.3 * dist + 0.2 * f["trust"] - sum(p["value"] for p in pens), 3)
    return {
        "facility_id": f["id"], "name": f["name"], "type": f["type"], "operator": f["operator"],
        "city": f["city"], "state": f["state"], "lat": f["lat"], "lon": f["lon"],
        "distance_km": round(d, 2), "location_precision_km": f.get("prec", 2.0),
        "location_note": f.get("location_note"),
        "grade": f["grade"], "grade_label": f["grade_label"],
        "confidence": f.get("confidence"), "confidence_reason": f.get("confidence_reason"),
        "score": score,
        "parts": None if score is None else {
            "evidence": round(ev, 3), "distance": round(dist, 3), "trust": f["trust"], "penalties": pens,
        },
        "evidence": f["evidence"], "missing": f["missing"],
        "sources": f["sources"], "why_here": f.get("why_here"), "could_be_wrong": f.get("could_be_wrong"),
        "also_listed_as": f.get("also_listed_as", []),
        "reason": f.get("reason"),
    }


def how(service, radius, gates, recipe, method="recipe"):
    return {
        "method": method,
        "recipe": recipe,
        "gates": gates,
        "formula": "score = 0.5 x evidence + 0.3 x distance + 0.2 x source trust - penalties",
        "weights": {"evidence": 0.5, "distance": 0.3, "trust": 0.2},
        "parts_explained": {
            "evidence": "How completely the record names the service: supported 0.7, corroborated 1.0; thin evidence -0.2, strong +0.1.",
            "distance": "exp(-km / radius): 1.0 at the searched point, 0.37 at the radius.",
            "trust": "Independent web domains behind the record, an official website, and how recent the pages are.",
        },
        "radius_km": radius,
        "radius_reason": "A straight-line product default for this service, not clinical guidance.",
        "score_status": "provisional",
        "score_status_note": "The weights are judgment calls, not fitted. They will be replaced by a calibrated probability when outside labels are linked.",
        "limits": LIMITS,
    }


def main() -> None:
    write("services.json", {"services": list(SVC.values()), "fixture": True})

    # ------------------------------------------------------------- Jaipur
    jaipur = resolution("ok", "Jaipur, Rajasthan", "Matched the city name Jaipur; the point is the centre of its post offices.",
                        26.9124, 75.7873, 6.0, "city", place_id="city:jaipur|rajasthan")
    write("resolve_jaipur.json", jaipur)
    svc = SVC["dialysis"]
    R = svc["radius_km"]
    one_src = {"n_domains": 1, "domains": ["fixture-listing.example"], "official_website": None}
    results = [
        dict(id="FX-001", name="Fixture Kidney Care Hospital", type="hospital", operator="private",
             city="Jaipur", state="Rajasthan", lat=26.8612, lon=75.8044, grade=3, grade_label="corroborated",
             strength="strong", trust=0.82, confidence="high",
             confidence_reason="Dialysis and a named dialysis unit both appear, from 3 independent web sources.",
             evidence=[
                 {"requirement": "dialysis", "quote": "Fixture text: 24 hour haemodialysis for patients with kidney failure.", "field": "capability", "fact_id": "fx-f-0011"},
                 {"requirement": "named dialysis unit, machines or nephrologist", "quote": "Fixture text: 12-bed dialysis unit with RO water plant.", "field": "equipment", "fact_id": "fx-f-0012"},
             ],
             missing=[], sources={"n_domains": 3, "domains": ["fixture-kidney.example", "fixture-listing.example", "fixture-reviews.example"], "official_website": "fixture-kidney.example"},
             why_here="Ranked first: strongest evidence of the three and the closest.",
             could_be_wrong="The pages may be out of date; call before travelling."),
        dict(id="FX-002", name="Fixture Multispeciality Hospital", type="hospital", operator="private",
             city="Jaipur", state="Rajasthan", lat=26.9541, lon=75.7172, grade=2, grade_label="supported",
             strength="moderate", trust=0.71, confidence="medium",
             confidence_reason="Dialysis is named, but no dialysis unit, machines or nephrologist.",
             penalties=[{"name": "possible merged record", "value": 0.15,
                         "reason": "The record's name may join two facilities.",
                         "quote": "Fixture Multispeciality Hospital and Fixture Eye Centre"}],
             evidence=[{"requirement": "dialysis", "quote": "Fixture text: Dialysis services available for admitted patients.", "field": "procedure", "fact_id": "fx-f-0021"}],
             missing=["no fact names a dialysis unit, dialysis machines or a nephrologist"],
             sources={"n_domains": 2, "domains": ["fixture-multi.example", "fixture-listing.example"], "official_website": "fixture-multi.example"},
             why_here="Ranked below the first: less complete evidence and farther away.",
             could_be_wrong="This record may combine two facilities, so the dialysis service may belong to the other one."),
        dict(id="FX-003", name="Fixture Charitable Hospital", type="hospital", operator="public",
             city="Jaipur", state="Rajasthan", lat=26.7905, lon=75.8710, grade=2, grade_label="supported",
             strength="thin", trust=0.55, confidence="low", prec=5.0,
             location_note="Placed at its PIN code's area: the scraped point disagreed with the PIN.",
             confidence_reason="One fact from one web source.",
             penalties=[{"name": "minor location conflict", "value": 0.10,
                         "reason": "The scraped location was 62 km from its PIN code's area, so it was moved to the PIN.",
                         "quote": None}],
             evidence=[{"requirement": "dialysis", "quote": "Fixture text: dialysis", "field": "capability", "fact_id": "fx-f-0031"}],
             missing=["no fact names a dialysis unit, dialysis machines or a nephrologist"],
             sources=one_src,
             why_here="Ranked third: thin evidence and the location is only known to its PIN area.",
             could_be_wrong="Only one short fact supports dialysis, and the location is approximate."),
    ]
    unverified = [
        dict(id="FX-101", name="Fixture Nephrology Clinic", type="clinic", operator="private", city="Jaipur", state="Rajasthan",
             lat=26.9200, lon=75.7950, grade=1, grade_label="claimed", strength="thin", trust=0.4,
             evidence=[], missing=["no fact mentions dialysis"], sources=one_src,
             reason="Lists nephrology as a specialty, but no fact in its record mentions dialysis."),
        dict(id="FX-102", name="Fixture City Hospital", type="hospital", operator="private", city="Jaipur", state="Rajasthan",
             lat=27.0310, lon=75.8890, grade=1, grade_label="claimed", strength="thin", trust=0.5,
             evidence=[], missing=["no fact mentions dialysis"], sources=one_src,
             reason="Lists nephrology as a specialty, but no fact in its record mentions dialysis."),
        dict(id="FX-103", name="Fixture Diagnostic Centre", type="clinic", operator="private", city="Jaipur", state="Rajasthan",
             lat=26.7600, lon=75.6600, grade=1, grade_label="claimed", strength="thin", trust=0.3,
             evidence=[], missing=["no fact mentions dialysis"], sources=one_src,
             reason="Lists nephrology as a specialty, but no fact in its record mentions dialysis."),
    ]
    cards = [card(f, jaipur, R) for f in results]
    cards.sort(key=lambda c: -c["score"])
    unv = [card(f, jaipur, R) for f in unverified]
    gates = [
        {"name": "Location usable", "description": "Facilities whose location could be used", "count": 9656},
        {"name": "Within reach", "description": f"Within {3 * R} km straight line (at most 50 kept)", "count": 50},
        {"name": "Reliable evidence", "description": "The record names the service, and no blocking warning", "count": 7},
        {"name": "Shown", "description": "Top three by score", "count": 3},
    ]
    recipe = ["Lowest level: nephrology listed as a specialty. This alone is never recommended.",
              "Supported: a fact names dialysis.",
              "Corroborated: dialysis and a named dialysis unit, dialysis machines or a nephrologist."]
    write("answer_dialysis_jaipur.json", {
        "status": "ok", "service": "dialysis", "service_label": svc["label"], "radius_km": R, "method": "recipe",
        "location": jaipur, "results": cards, "beyond_radius": None, "unverified_nearby": unv,
        "how_we_decided": how("dialysis", R, gates, recipe), "fixture": True,
    })

    # ---------------------------------------------- fallback (keyword) Jaipur
    kw_fac = [
        dict(id="FX-201", name="Fixture Eye Hospital", type="hospital", operator="private", city="Jaipur", state="Rajasthan",
             lat=26.8890, lon=75.7600, grade=0, grade_label="none", strength="thin", trust=0.66, confidence="low",
             confidence_reason="Keyword match only: there is no service recipe for this search.",
             evidence=[{"requirement": "matches \"cataract surgery\"", "quote": "Fixture text: Phaco cataract surgery with foldable lens implant.", "field": "procedure", "fact_id": "fx-f-0201"}],
             missing=[], sources={"n_domains": 2, "domains": ["fixture-eye.example", "fixture-listing.example"], "official_website": "fixture-eye.example"},
             why_here="Best keyword match within reach.", could_be_wrong="A keyword match is not checked against any rule."),
        dict(id="FX-202", name="Fixture Vision Clinic", type="clinic", operator="private", city="Jaipur", state="Rajasthan",
             lat=26.9400, lon=75.8300, grade=0, grade_label="none", strength="thin", trust=0.4, confidence="low",
             confidence_reason="Keyword match only: there is no service recipe for this search.",
             evidence=[{"requirement": "matches \"cataract surgery\"", "quote": "Fixture text: cataract and glaucoma consultations", "field": "capability", "fact_id": "fx-f-0202"}],
             missing=[], sources=one_src,
             why_here="Second best keyword match.", could_be_wrong="The match is to a consultation, which may not mean surgery is done here."),
    ]
    kw_cards = []
    for f in kw_fac:
        c = card(f, jaipur, 50)
        c["score"] = None
        c["parts"] = None
        kw_cards.append(c)
    write("answer_keyword_jaipur.json", {
        "status": "ok", "service": "other", "service_label": "cataract surgery", "radius_km": 50, "method": "keyword",
        "location": jaipur, "results": kw_cards, "beyond_radius": None, "unverified_nearby": [],
        "how_we_decided": {**how("other", 50, [
            {"name": "Location usable", "description": "Facilities whose location could be used", "count": 9656},
            {"name": "Within reach", "description": "Within 50 km straight line", "count": 41},
            {"name": "Keyword match", "description": "A fact or specialty matches the words searched", "count": 6},
            {"name": "Shown", "description": "Best matches", "count": 2},
        ], ["No service recipe covers this search. Facilities are matched on the words in their facts and specialties.",
            "Confidence is never above low for a keyword match."], method="keyword"),
            "formula": "Keyword similarity between the search and each fact (TF-IDF), best fact per facility",
            "weights": None, "parts_explained": {}, "score_status": None, "score_status_note": None},
        "fixture": True,
    })

    # ---------------------------------------------- Bihar abstention
    vil = resolution("ok", "Fixturepur (fixture village), Darbhanga, Bihar",
                     "Matched a post office name; the point is that post office.", 25.9500, 86.2000, 4.0, "post_office",
                     place_id="post_office:fixture")
    write("resolve_fixturepur.json", vil)
    beyond = card(dict(id="FX-301", name="Fixture District Kidney Centre", type="hospital", operator="private",
                       city="Darbhanga", state="Bihar", lat=26.3700, lon=85.6500, grade=2, grade_label="supported",
                       strength="moderate", trust=0.64, confidence="medium",
                       confidence_reason="Dialysis is named, but no dialysis unit.",
                       evidence=[{"requirement": "dialysis", "quote": "Fixture text: Haemodialysis available on all days.", "field": "procedure", "fact_id": "fx-f-0301"}],
                       missing=["no fact names a dialysis unit, dialysis machines or a nephrologist"],
                       sources={"n_domains": 2, "domains": ["fixture-dkc.example", "fixture-listing.example"], "official_website": None},
                       why_here="The nearest facility with reliable evidence, beyond the search radius.",
                       could_be_wrong="It is outside the radius; the trip is long."), vil, R)
    b_unv = [card(dict(id="FX-302", name="Fixture Nursing Home", type="nursing home", operator="private", city="Kusheshwar", state="Bihar",
                       lat=25.9900, lon=86.1300, grade=1, grade_label="claimed", strength="thin", trust=0.3,
                       evidence=[], missing=["no fact mentions dialysis"], sources=one_src,
                       reason="Lists nephrology as a specialty, but no fact in its record mentions dialysis."), vil, R)]
    write("answer_dialysis_fixturepur.json", {
        "status": "no_reliable_option", "service": "dialysis", "service_label": svc["label"], "radius_km": R,
        "method": "recipe", "location": vil, "results": [], "beyond_radius": beyond, "unverified_nearby": b_unv,
        "how_we_decided": how("dialysis", R, [
            {"name": "Location usable", "description": "Facilities whose location could be used", "count": 9656},
            {"name": "Within reach", "description": f"Within {3 * R} km straight line (at most 50 kept)", "count": 14},
            {"name": "Reliable evidence", "description": "The record names the service, and no blocking warning", "count": 1},
            {"name": "Within the radius", "description": f"Reliable and within {R} km", "count": 0},
        ], recipe),
        "fixture": True,
    })

    # ---------------------------------------------- resolver edge cases
    write("resolve_aurangabad.json", resolution(
        "ambiguous", "Aurangabad", "Two districts share this name.", None, None, None, "district",
        candidates=[
            {"label": "Aurangabad, Maharashtra", "place_id": "district:aurangabad|maharashtra", "kind": "district", "lat": 19.8762, "lon": 75.3433, "precision_km": 25.0},
            {"label": "Aurangabad, Bihar", "place_id": "district:aurangabad|bihar", "kind": "district", "lat": 24.7520, "lon": 84.3742, "precision_km": 20.0},
        ]))
    write("resolve_not_found.json", resolution("not_found", "", "No PIN, district, city or post office matches this text.", None, None, None, None))
    write("resolve_outside.json", resolution("outside_india", "51.5074, -0.1278", "These coordinates are outside India.", 51.5074, -0.1278, None, "coords"))

    # ---------------------------------------------- trip fixture (Jaipur)
    import pandas as pd
    po = pd.read_parquet(ROOT / "data" / "silver" / "post_offices.parquet", columns=["lat", "lon", "coord_valid"])
    po = po[po.coord_valid].copy()
    po["d"] = [hav(jaipur["lat"], jaipur["lon"], a, b) for a, b in zip(po.lat, po.lon)]
    po = po[po.d <= R * 1.6].drop_duplicates(["lat", "lon"]).sort_values("d").head(3000)
    facs = [{"facility_id": c["facility_id"], "name": c["name"], "lat": c["lat"], "lon": c["lon"], "kind": "recommended", "rank": i + 1}
            for i, c in enumerate(cards)]
    facs += [{"facility_id": c["facility_id"], "name": c["name"], "lat": c["lat"], "lon": c["lon"], "kind": "claim_only", "rank": None}
             for c in unv]
    rec_idx = [i for i, f in enumerate(facs) if f["kind"] == "recommended"]
    pts, near_rec, near_claim, extra = [], [], [], []
    for a, b in zip(po.lat, po.lon):
        drec = [(hav(a, b, facs[i]["lat"], facs[i]["lon"]), i) for i in rec_idx]
        dall = [(hav(a, b, f["lat"], f["lon"]), i) for i, f in enumerate(facs)]
        r = min(drec)
        c = min(dall)
        pts.append([round(float(b), 5), round(float(a), 5)])
        near_rec.append(r[1])
        near_claim.append(c[1])
        if facs[c[1]]["kind"] != "recommended":
            via = c[0] + min(hav(facs[c[1]]["lat"], facs[c[1]]["lon"], facs[i]["lat"], facs[i]["lon"]) for i in rec_idx)
            extra.append(via - r[0])
    n_bad = len(extra)
    write("trip_dialysis_jaipur.json", {
        "status": "ok", "service": "dialysis", "service_label": svc["label"], "radius_km": R,
        "center": {"lat": jaipur["lat"], "lon": jaipur["lon"]},
        "facilities": facs, "points": pts,
        "nearest_recommended": near_rec, "nearest_claim": near_claim,
        "summary": {
            "n_points": len(pts), "n_first_unsupported": n_bad,
            "extra_km_mean": round(sum(extra) / n_bad, 1) if n_bad else 0.0,
        },
        "notes": ["Each dot is one post office area, not a person.",
                  "Straight-line km. Post offices that share one placeholder point are drawn once."],
        "fixture": True,
    })
    # ---------------------------------------------- simulate fixture (dev only, ?fixture_sim)
    # p_offers here are FIXTURE values chosen by hand to exercise the panel. They are
    # not estimates. The real app hides simulation until WP13 ships p_offers.
    p_fx = {"FX-001": 0.9, "FX-002": 0.7, "FX-003": 0.5, "FX-101": 0.2, "FX-102": 0.2, "FX-103": 0.2}
    sfac = [{"facility_id": f["facility_id"], "lat": f["lat"], "lon": f["lon"], "p_offers": p_fx[f["facility_id"]]} for f in facs]
    by_rank = sorted(rec_idx, key=lambda i: facs[i]["rank"])

    def dd(i, j):
        return hav(sfac[i]["lat"], sfac[i]["lon"], sfac[j]["lat"], sfac[j]["lon"])

    orders = {"nearest_claim": [], "nearest_evidence": [], "ranked": []}
    stats = {k: [0.0, 0.0] for k in orders}
    for lon, lat in pts:
        dist = {i: hav(lat, lon, f["lat"], f["lon"]) for i, f in enumerate(sfac)}
        o_claim = sorted(range(len(sfac)), key=lambda i: dist[i])[:4]
        o_ev = sorted(rec_idx, key=lambda i: dist[i])
        for k, o in (("nearest_claim", o_claim), ("nearest_evidence", o_ev), ("ranked", by_rank)):
            orders[k].append(o)
            e, stay, prev = 0.0, 1.0, None
            for i in o:
                e += stay * (dist[i] if prev is None else dd(prev, i))
                stay *= 1 - sfac[i]["p_offers"]
                prev = i
            stats[k][0] += e
            stats[k][1] += 1 - sfac[o[0]]["p_offers"]
    n = len(pts)
    labels = {"nearest_claim": "Nearest facility that claims it", "nearest_evidence": "Nearest with evidence", "ranked": "Our ranking"}
    write("simulate_dialysis_jaipur.json", {
        "status": "ok", "service": "dialysis", "n_origins": n,
        "policies": [{"policy": k, "label": labels[k], "expected_km": round(stats[k][0] / n, 1),
                      "share_second_trip": round(stats[k][1] / n, 3)} for k in orders],
        "facilities": sfac, "origins": pts, "orders": orders,
        "notes": ["FIXTURE: these chances are hand-set test values, not estimates."],
        "fixture": True,
    })
    print(f"wrote fixtures to {OUT}: {len(cards)} cards, trip points {len(pts)}, first-unsupported {n_bad}")


if __name__ == "__main__":
    main()
