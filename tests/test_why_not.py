"""'Why not the nearer ones?' and the gate funnel (vf.why_not, D044 item 2).

A small synthetic world around one point, in both scoring modes, plus a
real-data check on the brief's queries that skips when the bundle is absent.
"""
from __future__ import annotations

import math
import re

import numpy as np
import pandas as pd
import pytest

from vf import paths, rank, why_not
from vf import services as S

LAT0, LON0 = 20.0, 78.0
KM_LAT = 1 / 111.195
LINE = "dialysis"
ORIGIN = {"status": "ok", "lat": LAT0, "lon": LON0, "precision_km": 2.0, "label": "Test point", "how": "typed"}
FUZZY_ORIGIN = {**ORIGIN, "precision_km": 60.0}


def north(km: float) -> float:
    return LAT0 + km * KM_LAT


# id, km north (None: no location), grade, strength, operator, type, coord_check, specialties, extra facts
FAC = [
    ("A", 6.0, 3, "strong", "private", "hospital", "ok", ["nephrology"], []),   # first card
    ("H", 5.9, 3, "strong", "private", "hospital", "ok", ["nephrology"], []),   # duplicate of A, keep A
    ("T", 2.5, 2, "thin", "private", "hospital", "ok", ["nephrology"], []),     # recommendable, scores lower
    ("B", 1.0, 1, "thin", "private", "hospital", "ok", ["nephrology", "cardiology"], ["OPD services"]),
    ("C", 0.5, 3, "strong", "private", "hospital", "ok", ["nephrology"], []),   # blocked: location conflict
    ("P", 2.0, 0, "none", "public", "hospital", "ok", [], []),                  # public, record silent
    ("Q", 1.5, 0, "none", "private", "clinic", "ok", [], []),                   # private, unrelated
    ("S1", 3.0, 1, "thin", "private", "hospital", "ok", ["nephrology"], []),    # shares a point with S2
    ("S2", 3.0, 1, "thin", "private", "hospital", "ok", ["nephrology"], []),
    ("MV", 3.5, 1, "thin", "private", "hospital", "moved_to_pin", ["nephrology"], []),
    ("MG", 4.0, 1, "thin", "private", "hospital", "ok", ["nephrology"],
     ["Located in Noida Sector 18", "Located in Bareilly, Uttar Pradesh, India", "Located in Testpur"]),
    ("G", 120.0, 3, "strong", "private", "hospital", "ok", ["nephrology"], []),  # beyond the radius
    ("X", 600.0, 3, "strong", "private", "hospital", "ok", ["nephrology"], []),  # beyond reach
    ("N", None, 3, "strong", "private", "hospital", "ok", ["nephrology"], []),   # no usable location
]


def _frames():
    fac, grades, facts = [], [], []
    for fid, km, g, st, op, ft, cc, spec, extra in FAC:
        fac.append(dict(facility_id=fid, name=f"Hospital {fid}", facility_type=ft, operator_type=op,
                        lat=np.nan if km is None else north(km), lon=np.nan if km is None else LON0,
                        coord_check=cc, pin="000000", city_raw="Testpur", state_raw="Teststate",
                        specialties=spec, official_website=None, source_domains=["d0.in", "d1.in"],
                        n_source_domains=2, source_confidence=0.6))
        items = []
        if g >= 2:
            t1 = f"{fid}: haemodialysis available"
            facts.append(dict(fact_id=f"{fid}1", facility_id=fid, field="procedure", text=t1))
            items.append(dict(requirement="dialysis", tag="dialysis", fact_id=f"{fid}1", field="procedure", text=t1))
        if g >= 3:
            t2 = f"{fid}: 12-bed dialysis unit"
            facts.append(dict(fact_id=f"{fid}2", facility_id=fid, field="equipment", text=t2))
            items.append(dict(requirement="dialysis unit, machines or nephrologist", tag="dialysis_unit",
                              fact_id=f"{fid}2", field="equipment", text=t2))
        facts.append(dict(fact_id=f"{fid}9", facility_id=fid, field="capability", text=f"{fid}: general wards"))
        for j, t in enumerate(extra):
            facts.append(dict(fact_id=f"{fid}x{j}", facility_id=fid, field="capability", text=t))
        missing = {0: ["a related specialty listed"], 1: ["dialysis"], 2: ["dialysis unit, machines or nephrologist"],
                   3: []}[g]
        grades.append(dict(facility_id=fid, service_line=LINE, grade=g, grade_label=S.GRADE_LABELS[g],
                           tags_present=[], evidence=[i["text"] for i in items], evidence_items=items,
                           missing_for_next=missing, n_supporting_facts=len(items), evidence_strength=st,
                           method="regex", recipe_version="test"))
    flags = pd.DataFrame([
        dict(facility_id="C", flag="location_conflict", service_line=None, severity="major", signal="coord_unresolved",
             reason="The record's location is 140 km from its PIN code's area.", quote="Faraway", check_version="t"),
        dict(facility_id="MG", flag="possible_merge", service_line=None, severity="major",
             signal="text_other_address+shared_website",
             reason="The record's own text places it in Bareilly, but its PIN code puts it in Testpur.",
             quote="Located in Bareilly, Uttar Pradesh, India", check_version="t"),
    ])
    dups = pd.DataFrame([dict(facility_id_a="A", facility_id_b="H", distance_km=0.1, name_score=95.0,
                              signal="near_same_name", keep="A")])
    return pd.DataFrame(fac), pd.DataFrame(grades), pd.DataFrame(facts), flags, dups


def _prob():
    p = {"A": 0.9, "H": 0.9, "T": 0.3, "B": 0.2, "C": 0.9, "S1": 0.2, "S2": 0.2, "MV": 0.2, "MG": 0.2,
         "G": 0.9, "X": 0.9, "N": 0.9}
    return pd.DataFrame([dict(facility_id=f, service_line=LINE, p_offers=v, p_low=v - 0.1, p_high=v + 0.05,
                              top_factors=[], model_version="synthetic", n_labels_line=100) for f, v in p.items()])


@pytest.fixture(params=["v0", "probability"])
def idx(request):
    fac, grades, facts, flags, dups = _frames()
    sp = _prob() if request.param == "probability" else None
    return rank.build_index(fac, grades, facts=facts, flags=flags, duplicates=dups, service_probability=sp)


@pytest.fixture
def v0():
    fac, grades, facts, flags, dups = _frames()
    return rank.build_index(fac, grades, facts=facts, flags=flags, duplicates=dups)


def _answer(i, k=1, origin=ORIGIN):
    return rank.rank(i, LINE, origin, k=k).to_dict()


def _by_id(items):
    return {x["facility_id"]: x for x in items}


BANNED = re.compile(r"\bcannot\b|\bcan't\b|does not have|doesn't have|\bno such\b|—|–")


def _texts(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _texts(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _texts(v)


# ---------------------------------------------------------------------------
# why_not_nearer
# ---------------------------------------------------------------------------

def test_items_are_closer_than_first_card_and_not_shown(idx):
    ans = _answer(idx)
    first = ans["results"][0]
    assert first["facility_id"] == "A"
    items = why_not.why_not_nearer(idx, LINE, ORIGIN, ans, k=50)
    shown = {c["facility_id"] for c in ans["results"]}
    assert items, "the synthetic world has nearer records that were not recommended"
    d_first = 6.0
    for it in items:
        assert it["facility_id"] not in shown
        km = it["distance_km"] if it["distance_km"] is not None else it["distance_km_to_point"]
        assert km < d_first + 1e-6
    kms = [it["distance_km"] if it["distance_km"] is not None else it["distance_km_to_point"] for it in items]
    assert kms == sorted(kms)
    ids = set(_by_id(items))
    assert "Q" not in ids  # a private record that never mentions the service is not explained
    assert not ids & {"G", "X", "N"}


def test_required_keys(idx):
    for it in why_not.why_not_nearer(idx, LINE, ORIGIN, _answer(idx), k=50):
        for key in ("facility_id", "name", "distance_km", "distance_label", "reason_plain", "reason_code",
                    "quote_or_absence", "tone", "also"):
            assert key in it
        assert it["tone"] in ("grey", "neutral")


def test_k_caps_items_but_not_the_count(idx):
    s = why_not.why_not_strip(idx, LINE, ORIGIN, _answer(idx), k=2)
    assert len(s["items"]) == 2
    assert s["n_nearer"] >= 7
    assert s["first_card"]["facility_id"] == "A"
    assert str(s["n_nearer"]) in s["heading"]


def test_claim_only_in_the_records_terms(v0):
    b = _by_id(why_not.why_not_nearer(v0, LINE, ORIGIN, _answer(v0), k=50))["B"]
    assert b["reason_code"] == "claim_only"
    assert b["reason_plain"] == "Lists nephrology as a specialty, but none of its 2 facts names dialysis."
    assert b["quote_or_absence"]["kind"] == "absence"
    assert b["claim_quote"] == {"kind": "quote", "text": "nephrology", "field": "specialties", "fact_id": None}
    assert b["tone"] == "grey"
    assert b["distance_label"] == "1.0 km"


def test_blocked_record_quotes_its_flag(idx):
    c = _by_id(why_not.why_not_nearer(idx, LINE, ORIGIN, _answer(idx), k=50))["C"]
    assert c["reason_code"] == "flag_location_conflict"
    assert c["reason_plain"] == "Record flagged: its location is 140 km from its PIN code's area."
    assert c["quote_or_absence"]["text"] == "Faraway"


def test_merge_names_the_places_its_facts_give(idx):
    mg = _by_id(why_not.why_not_nearer(idx, LINE, ORIGIN, _answer(idx), k=50))["MG"]
    reasons = [mg] + mg["also"]
    merge = [r for r in reasons if r["reason_code"] == "flag_possible_merge"]
    assert merge
    txt = merge[0]["reason_plain"]
    assert "Noida Sector 18" in txt and "Bareilly" in txt
    assert "Testpur" not in txt and "Uttar Pradesh" not in txt  # own city and states are dropped
    assert merge[0]["quote_or_absence"]["text"] == "Located in Bareilly, Uttar Pradesh, India"
    assert why_not.merge_places(idx, idx.id_to_pos["MG"]) == ["Noida Sector 18", "Bareilly"]


def test_public_silent_record_gets_a_neutral_line(idx):
    p = _by_id(why_not.why_not_nearer(idx, LINE, ORIGIN, _answer(idx), k=50))["P"]
    assert p["reason_code"] == "public_record_silent"
    assert p["tone"] == "neutral"
    assert "not evidence either way" in p["reason_plain"]


def test_shared_and_moved_points_are_position_approximate(idx):
    items = _by_id(why_not.why_not_nearer(idx, LINE, ORIGIN, _answer(idx), k=50))
    for fid in ("S1", "S2", "MV"):
        assert items[fid]["distance_label"] == "position approximate"
        assert items[fid]["distance_km"] is None
        assert items[fid]["position_approximate"] is True
    assert items["B"]["position_approximate"] is False


def test_fuzzy_origin_rounds_to_whole_km(v0):
    ans = _answer(v0, origin=FUZZY_ORIGIN)
    items = _by_id(why_not.why_not_nearer(v0, LINE, FUZZY_ORIGIN, ans, k=50))
    assert items["B"]["distance_label"] == "about 1 km"
    assert items["C"]["distance_label"] == "under 1 km"
    assert all("0.0 km" not in it["distance_label"] for it in items.values())


def test_duplicate_and_ranked_lower(v0):
    items = _by_id(why_not.why_not_nearer(v0, LINE, ORIGIN, _answer(v0, k=1), k=50))
    assert items["H"]["reason_code"] == "duplicate_listing"
    assert "Hospital A" in items["H"]["reason_plain"]
    assert items["T"]["reason_code"] == "ranked_lower"
    assert items["T"]["quote_or_absence"]["text"] == "T: haemodialysis available"


def test_probability_mode_below_threshold():
    fac, grades, facts, flags, dups = _frames()
    i = rank.build_index(fac, grades, facts=facts, flags=flags, duplicates=dups, service_probability=_prob())
    t = _by_id(why_not.why_not_nearer(i, LINE, ORIGIN, _answer(i), k=50))["T"]
    assert t["reason_code"] == "below_threshold"
    assert "30%" in t["reason_plain"]


def test_wording_rules(idx):
    s = why_not.why_not_strip(idx, LINE, ORIGIN, _answer(idx), k=50)
    f = why_not.funnel(idx, LINE, ORIGIN)
    for t in list(_texts(s)) + list(_texts(f)):
        assert not BANNED.search(t), t


def test_no_card_uses_the_radius():
    fac, grades, facts, flags, dups = _frames()
    keep = ~fac["facility_id"].isin(["A", "H", "T"])
    i = rank.build_index(fac[keep], grades[grades.facility_id.isin(fac[keep].facility_id)], facts=facts,
                         flags=flags, duplicates=dups.iloc[0:0])
    ans = rank.rank(i, LINE, ORIGIN).to_dict()
    assert ans["status"] == "no_reliable_option"
    s = why_not.why_not_strip(i, LINE, ORIGIN, ans, k=50)
    assert s["first_card"]["facility_id"] == "G"  # the beyond-radius card
    assert "B" in _by_id(s["items"])


def test_unresolved_place_gives_nothing(idx):
    nf = {"status": "not_found", "lat": None, "lon": None}
    assert why_not.why_not_nearer(idx, LINE, nf, None) == []
    f = why_not.funnel(idx, LINE, nf)
    assert f["status"] == "no_location" and len(f["steps"]) == 1
    with pytest.raises(ValueError):
        why_not.funnel(idx, "not_a_line", ORIGIN)


# ---------------------------------------------------------------------------
# funnel
# ---------------------------------------------------------------------------

STEPS = ["all_records", "usable_location", "claim_within_reach", "recommendable", "within_radius", "shown"]


def _drop_ids(step, code):
    for g in step["dropped"]:
        if g["reason_code"] == code:
            return set(g["ids"])
    return set()


def test_funnel_steps_and_counts(idx):
    ans = _answer(idx, k=3)
    f = why_not.funnel(idx, LINE, ORIGIN, answer_dict=ans)
    assert [s["step"] for s in f["steps"]] == STEPS
    assert f["consistency"]["matches_ranker"], f["consistency"]
    n = why_not.funnel_counts(f)
    assert n[0] == len(FAC)
    assert n == sorted(n, reverse=True)
    for a, b in zip(f["steps"][:-1], f["steps"][1:-1]):
        assert a["n"] - b["n"] == b["n_dropped"]
    for s in f["steps"]:
        assert sum(g["n"] for g in s["dropped"]) == s["n_dropped"]
    assert f["steps"][-1]["n"] == len(ans["results"])
    assert f["steps"][-1]["ids"][: len(ans["results"])] == [c["facility_id"] for c in ans["results"]]


def test_funnel_says_who_dropped_where(v0):
    f = why_not.funnel(v0, LINE, ORIGIN, answer_dict=_answer(v0, k=1))
    st = {s["step"]: s for s in f["steps"]}
    assert _drop_ids(st["usable_location"], "no_usable_location") == {"N"}
    assert {"Q", "P"} <= _drop_ids(st["claim_within_reach"], "no_mention")
    assert _drop_ids(st["claim_within_reach"], "beyond_reach") == {"X"}
    assert {"B", "S1", "S2", "MV", "MG"} <= _drop_ids(st["recommendable"], "claim_only")
    assert _drop_ids(st["recommendable"], "flag_location_conflict") == {"C"}
    assert _drop_ids(st["within_radius"], "beyond_radius") == {"G"}
    assert _drop_ids(st["shown"], "duplicate_listing") == {"H"}
    assert _drop_ids(st["shown"], "ranked_lower") == {"T"}
    rec = st["recommendable"]
    claim_group = [g for g in rec["dropped"] if g["reason_code"] == "claim_only"][0]
    assert claim_group["n_also_blocked"] == 1  # MG is claim-only and also flagged
    items = {x["facility_id"]: x for x in claim_group["items"]}
    assert items["MV"]["distance_label"] == "position approximate"


def test_funnel_caps_ids_not_counts(v0):
    f = why_not.funnel(v0, LINE, ORIGIN, max_ids=1)
    g = [g for g in f["steps"][3]["dropped"] if g["reason_code"] == "claim_only"][0]
    assert len(g["ids"]) == 1 and g["n"] == len(g["ids"]) + g["n_more"]
    assert g["ids"] == ["B"]  # nearest first


def test_funnel_without_answer_calls_rank(idx):
    a = why_not.funnel(idx, LINE, ORIGIN)
    b = why_not.funnel(idx, LINE, ORIGIN, answer_dict=_answer(idx, k=3))
    assert why_not.funnel_counts(a) == why_not.funnel_counts(b)


def test_specialty_plain():
    assert why_not.specialty_plain("gynecologyAndObstetrics") == "obstetrics and gynaecology"
    assert why_not.specialty_plain("criticalCareMedicine") == "critical care medicine"
    assert why_not.label_lc("emoc").startswith("emergency C-section")
    assert why_not.label_lc("mri") == "MRI scan"


# ---------------------------------------------------------------------------
# real data: the brief's queries (skips without the bundle)
# ---------------------------------------------------------------------------

BUNDLE = paths.code_root() / "dbapp" / "bundle"


@pytest.fixture(scope="module")
def real():
    if not (BUNDLE / "manifest.json").exists():
        pytest.skip("no app bundle")
    from vf import locate
    idx = rank.load_index(BUNDLE)
    res = locate.Resolver(path=BUNDLE / "places.parquet")
    return idx, res


@pytest.mark.parametrize("line,query", [("emoc", "226001"), ("dialysis", "Jaipur"), ("emoc", "422002"),
                                        ("emoc", "422001"), ("dialysis", "Jokihat")])
def test_real_queries(real, line, query):
    idx, resolver = real
    res = resolver.resolve(query)
    ans = rank.rank(idx, line, res).to_dict()
    s = why_not.why_not_strip(idx, line, res, ans)
    f = why_not.funnel(idx, line, res, answer_dict=ans)
    assert f["consistency"]["matches_ranker"], f["consistency"]
    shown = {c["facility_id"] for c in ans["results"]}
    for it in s["items"]:
        assert it["facility_id"] not in shown
        assert it["distance_label"] != "0.0 km"
    for t in list(_texts(s)) + list(_texts(f)):
        assert not BANNED.search(t), t
    lk = why_not._lookups(idx)
    for it in s["items"]:
        if it["reason_code"] in ("claim_only", "public_record_silent") and it["quote_or_absence"]:
            n = it["quote_or_absence"]["n_facts"]
            assert n == lk.n_facts(idx.id_to_pos[it["facility_id"]])
