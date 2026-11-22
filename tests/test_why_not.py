"""'Why not the nearer ones?' and the gate funnel (vf.why_not, D044 item 2).

A small synthetic world around one point, in both scoring modes, plus a
real-data check on the brief's queries that skips when the bundle is absent.
"""
from __future__ import annotations

import copy
import json
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


def _frames(rows=FAC):
    fac, grades, facts = [], [], []
    for fid, km, g, st, op, ft, cc, spec, extra in rows:
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


@pytest.fixture(params=["v1", "probability", "v0"])
def idx(request):
    """The shipped ranker (score spec v1-final), probability mode, and the legacy v0 score behind its flag."""
    fac, grades, facts, flags, dups = _frames()
    sp = _prob() if request.param == "probability" else None
    ver = "v0" if request.param == "v0" else None
    return rank.build_index(fac, grades, facts=facts, flags=flags, duplicates=dups, service_probability=sp,
                            rank_version=ver)


@pytest.fixture
def v0():
    """Recipe grades without probabilities, on the index's default ranker (v1-final since D048). The name
    predates v1: it means "not probability mode"."""
    fac, grades, facts, flags, dups = _frames()
    return rank.build_index(fac, grades, facts=facts, flags=flags, duplicates=dups)


@pytest.fixture
def legacy():
    """The legacy v0 score ranker, behind its flag."""
    fac, grades, facts, flags, dups = _frames()
    return rank.build_index(fac, grades, facts=facts, flags=flags, duplicates=dups, rank_version="v0")


def _answer(i, k=1, origin=ORIGIN):
    return rank.rank(i, LINE, origin, k=k).to_dict()


def _by_id(items):
    return {x["facility_id"]: x for x in items}


BANNED = re.compile(r"\bcannot\b|\bcan't\b|does not have|doesn't have|\bno such\b|\u2014|\u2013")


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
    approx = [it["position_approximate"] for it in items]
    assert approx == sorted(approx)  # known positions first, then approximate ones
    for flag in (False, True):
        kms = [it["distance_km"] if it["distance_km"] is not None else it["distance_km_to_point"]
               for it in items if it["position_approximate"] is flag]
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
    assert s["n_nearer"] == 6  # H, T, B, C, P, MG: known positions closer than A
    assert s["n_approx_nearer"] == 3  # S1, S2 (shared point) and MV (moved to the PIN centre)
    assert s["first_card"]["facility_id"] == "A"
    assert s["heading"].startswith("Why not the nearer ones? 6 records closer than Hospital A list or claim dialysis")
    assert "3 more records have an approximate position" in s["heading"]
    assert not any(it["position_approximate"] for it in s["items"])


def test_claim_only_in_the_records_terms(v0):
    b = _by_id(why_not.why_not_nearer(v0, LINE, ORIGIN, _answer(v0), k=50))["B"]
    assert b["reason_code"] == "claim_only"
    assert b["reason_plain"] == ("Lists nephrology as a specialty, but we found no fact naming dialysis among "
                                 "its 2 facts.")
    assert b["quote_or_absence"]["kind"] == "absence"
    assert b["quote_or_absence"]["n_facts"] == 2
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
    assert f["consistency"]["matches_ranker"] is True, f["consistency"]
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
    assert f["consistency"]["matches_ranker"] is True, f["consistency"]
    shown = {c["facility_id"] for c in ans["results"]}
    for it in s["items"]:
        assert it["facility_id"] not in shown
        assert it["distance_label"] != "0.0 km"
    for t in list(_texts(s)) + list(_texts(f)):
        assert not BANNED.search(t), t
    lk = why_not._lookups(idx)
    for it in s["items"]:
        if it["reason_code"] in ("claim_only", "public_record_silent") and it["quote_or_absence"]:
            pos = idx.id_to_pos[it["facility_id"]]
            assert it["quote_or_absence"]["n_facts"] == lk.n_facts(pos)
            assert why_not.literal_hits(idx, line, pos) == []  # an absence line only when no fact hits


def test_candidate_cap_shows_as_not_scored(v0, monkeypatch):
    monkeypatch.setattr(S, "MAX_CANDIDATES", 1)
    ans = _answer(v0, k=3)
    assert [c["facility_id"] for c in ans["results"]] == ["T"]  # only the nearest recommendable is scored
    f = why_not.funnel(v0, LINE, ORIGIN, answer_dict=ans)
    assert f["consistency"]["matches_ranker"], f["consistency"]
    st = {s["step"]: s for s in f["steps"]}
    assert st["within_radius"]["n"] == 3 and st["within_radius"]["n_distinct"] == 2  # A and H are one facility
    assert _drop_ids(st["shown"], "not_scored") == {"A", "H"}



# ---------------------------------------------------------------------------
# review fixes: no absence from the grade alone, claim wording, masking,
# distinct fact counts, approximate positions, duplicate labels, consistency
# ---------------------------------------------------------------------------

def _emoc_index():
    """An emoc world: one card at 10 km and grade-1 records whose facts use, negate or omit caesarean section."""
    rows = [  # id, km north, grade, operator, facts
        ("CARD", 10.0, 2, "private", ["Emergency caesarean section 24x7"]),
        ("MIS", 1.0, 1, "private", ["Caesarian Section", "General wards", "general  wards", "General wards."]),
        ("NEG", 2.0, 1, "public", ["Caesarean section facility not available on-site; referrals to District Hospital",
                                   "OPD"]),
        ("NEGP", 2.5, 1, "private", ["C-section not offered"]),
        ("VB", 3.0, 1, "private", ["Offers VBAC (vaginal birth after caesarean)"]),
        ("SIL", 3.5, 1, "public", ["Maternity ward"]),
        ("CL", 4.0, 1, "private", ["OPD services"]),
    ]
    fac, grades, facts = [], [], []
    for fid, km, g, op, texts in rows:
        fac.append(dict(facility_id=fid, name=f"Hospital {fid}", facility_type="hospital", operator_type=op,
                        lat=north(km), lon=LON0, coord_check="ok", pin="000000", city_raw="Testpur",
                        state_raw="Teststate", specialties=["gynecologyAndObstetrics"], official_website=None,
                        source_domains=["d0.in", "d1.in"], n_source_domains=2, source_confidence=0.6))
        for j, t in enumerate(texts):
            facts.append(dict(fact_id=f"{fid}{j}", facility_id=fid, field="procedure", text=t))
        items = [dict(requirement="caesarean section", tag="csection", fact_id=f"{fid}0", field="procedure",
                      text=texts[0])] if g >= 2 else []
        grades.append(dict(facility_id=fid, service_line="emoc", grade=g, grade_label=S.GRADE_LABELS[g],
                           tags_present=[], evidence=[i["text"] for i in items], evidence_items=items,
                           missing_for_next={1: ["caesarean section"], 2: ["blood bank or transfusion"]}[g],
                           n_supporting_facts=len(items), evidence_strength="thin", method="regex",
                           recipe_version="test"))
    return rank.build_index(pd.DataFrame(fac), pd.DataFrame(grades), facts=pd.DataFrame(facts))


@pytest.fixture
def emoc():
    i = _emoc_index()
    ans = rank.rank(i, "emoc", ORIGIN).to_dict()
    assert [c["facility_id"] for c in ans["results"]] == ["CARD"]
    return i, ans, _by_id(why_not.why_not_nearer(i, "emoc", ORIGIN, ans, k=50))


def test_misspelt_fact_is_quoted_not_called_absent(emoc):
    _, _, items = emoc
    m = items["MIS"]
    assert m["reason_code"] == "fact_not_counted"
    assert m["reason_plain"] == ('Lists obstetrics and gynaecology as a specialty, and a fact reads "Caesarian Section", '
                                 "but our rules did not count it as evidence of caesarean section.")
    assert m["quote_or_absence"] == {"kind": "quote", "text": "Caesarian Section", "field": "procedure",
                                     "fact_id": "MIS0", "negated": False, "n_hits": 1}
    assert "none of" not in m["reason_plain"] and "found no fact" not in m["reason_plain"]


def test_vbac_fact_is_quoted(emoc):
    v = emoc[2]["VB"]
    assert v["reason_code"] == "fact_not_counted"
    assert '"Offers VBAC (vaginal birth after caesarean)"' in v["reason_plain"]


def test_negated_fact_is_quoted_and_not_called_silent(emoc):
    _, _, items = emoc
    n = items["NEG"]
    assert n["reason_code"] == "fact_states_absence"
    assert n["tone"] == "neutral"
    assert n["reason_plain"] == ("Public hospital: its record lists obstetrics and gynaecology as a specialty, and a fact "
                                 'reads "Caesarean section facility not available on-site; referrals to District '
                                 'Hospital". Ask the hospital.')
    assert "silent" not in n["reason_plain"] and "silence" not in n["reason_plain"]
    assert n["quote_or_absence"]["negated"] is True
    p = items["NEGP"]
    assert p["reason_code"] == "fact_states_absence" and p["tone"] == "grey"
    assert p["reason_plain"].endswith('a fact reads "C-section not offered".')


def test_absence_only_when_no_fact_hits(emoc):
    i, _, items = emoc
    assert items["SIL"]["reason_code"] == "public_record_silent"
    assert "we found no fact naming caesarean section among" not in items["SIL"]["reason_plain"]  # one fact
    assert "we found nothing naming caesarean section in its one fact" in items["SIL"]["reason_plain"]
    assert items["CL"]["reason_code"] == "claim_only"
    for fid in ("SIL", "CL"):
        assert why_not.literal_hits(i, "emoc", i.id_to_pos[fid]) == []
    for t in _texts(list(items.values())):
        assert not BANNED.search(t), t


def test_literal_pattern_spelling_variants():
    rx = why_not.literal_pattern("emoc")
    from vf.recipes import normalize_text
    for t in ("Caesarian Section", "Performs caesarian section", "LSCS done", "C-section", "C section", "cesarean",
              "Caesarean delivery", "VBAC"):
        assert rx.search(normalize_text(t)), t
    for t in ("General wards", "Normal delivery", "Maternity ward", "Section 8 company"):
        assert not rx.search(normalize_text(t)), t
    d = why_not.literal_pattern("dialysis")
    assert d.search("haemodialysis") and d.search("peritoneal dialisis") and not d.search("diabetes clinic")


def test_fact_counts_are_distinct_texts(emoc):
    i, _, items = emoc
    lk = why_not._lookups(i)
    p = i.id_to_pos["MIS"]
    assert lk.n_fact_rows(p) == 4
    assert lk.n_facts(p) == 2  # "General wards" three times counts once
    assert [t for t, _, _ in lk.facts(p)] == ["Caesarian Section", "General wards"]


def test_funnel_items_carry_the_same_reasons(emoc):
    i, ans, items = emoc
    f = why_not.funnel(i, "emoc", ORIGIN, answer_dict=ans)
    assert f["consistency"]["matches_ranker"] is True, f["consistency"]
    rec = {s["step"]: s for s in f["steps"]}["recommendable"]
    codes = {g["reason_code"]: set(g["ids"]) for g in rec["dropped"]}
    assert codes["fact_not_counted"] == {"MIS", "VB"}
    assert codes["fact_states_absence"] == {"NEG", "NEGP"}
    row = {x["facility_id"]: x for g in rec["dropped"] for x in g["items"]}
    for fid in ("MIS", "NEG", "SIL", "CL"):
        assert row[fid]["reason_plain"] == items[fid]["reason_plain"]


def test_heading_and_funnel_use_claim_wording(emoc):
    i, ans, _ = emoc
    s = why_not.why_not_strip(i, "emoc", ORIGIN, ans)
    assert s["heading"] == ("Why not the nearer ones? 6 records closer than Hospital CARD list or claim emergency "
                            "C-section and high-risk delivery, or are public hospitals, but were not recommended.")
    f = why_not.funnel(i, "emoc", ORIGIN, answer_dict=ans)
    lab = {s_["step"]: s_["label"] for s_ in f["steps"]}["claim_within_reach"]
    assert lab.startswith("List or claim emergency C-section")
    for t in [s["heading"], lab]:
        assert "mention" not in t


def test_mask_names_hides_every_non_card_name(idx):
    ans = _answer(idx, k=1)
    s = why_not.why_not_strip(idx, LINE, ORIGIN, ans, k=50, mask_names=True)
    f = why_not.funnel(idx, LINE, ORIGIN, answer_dict=ans, mask_names=True)
    assert s["masked"] and f["masked"]
    assert "Hospital A" in s["heading"]  # the card keeps its name
    for it in s["items"]:
        assert it["name"] == "Hospital in Testpur (name hidden)" and it["name_masked"]
    blob = json.dumps(s) + json.dumps(f)
    for fid, *_ in FAC:
        if fid != "A":
            assert not re.search(rf"Hospital {fid}\b", blob), fid
    plain = why_not.why_not_strip(idx, LINE, ORIGIN, ans, k=50)
    assert [x["facility_id"] for x in plain["items"]] == [x["facility_id"] for x in s["items"]]


def _dup_index(extra_rows, pairs, rank_version=None):
    fac, grades, facts, flags, _ = _frames(FAC + extra_rows)
    dups = pd.DataFrame([dict(facility_id_a=a, facility_id_b=b, distance_km=0.1, name_score=95.0,
                              signal="near_same_name", keep=k) for a, b, k in pairs])
    return rank.build_index(fac, grades, facts=facts, flags=flags, duplicates=dups, rank_version=rank_version)


T2 = ("T2", 2.6, 2, "thin", "private", "hospital", "ok", ["nephrology"], [])


@pytest.mark.parametrize("ver", ["v1", "v0"])
def test_duplicate_label_only_when_another_listing_is_a_candidate(ver):
    # T's group prefers B, which is claim-only: T is the group's candidate, so it ranked lower
    i = _dup_index([], [("A", "H", "A"), ("T", "B", "B")], ver)
    ans = _answer(i, k=1)
    t = _by_id(why_not.why_not_nearer(i, LINE, ORIGIN, ans, k=50))["T"]
    assert t["reason_code"] == "ranked_lower", t
    st = {s["step"]: s for s in why_not.funnel(i, LINE, ORIGIN, answer_dict=ans)["steps"]}
    assert _drop_ids(st["shown"], "ranked_lower") == {"T"}
    assert _drop_ids(st["shown"], "duplicate_listing") == {"H"}

    # T2's group prefers T, which is scored: T2 is the other listing
    i = _dup_index([T2], [("A", "H", "A"), ("T", "T2", "T")], ver)
    items = _by_id(why_not.why_not_nearer(i, LINE, ORIGIN, _answer(i, k=1), k=50))
    assert items["T2"]["reason_code"] == "duplicate_listing"
    assert items["T2"]["reason_plain"] == (
        "Likely another listing of Hospital T; the ranker keeps one record per facility." if ver == "v0" else
        "Our duplicate check pairs it with Hospital T, and the ranker keeps that listing: one record per facility.")
    assert items["T"]["reason_code"] == "ranked_lower"

    # the group prefers claim-only B; T and T2 compete and neither is a card
    i = _dup_index([T2], [("A", "H", "A"), ("B", "T", "B"), ("B", "T2", "B")], ver)
    items = _by_id(why_not.why_not_nearer(i, LINE, ORIGIN, _answer(i, k=1), k=50))
    assert items["T"]["reason_code"] == items["T2"]["reason_code"] == "duplicate_listing"
    assert ("neither listing scored high enough to be shown" if ver == "v0" else
            "neither listing comes early enough in the order to be shown") in items["T"]["reason_plain"]


def test_consistency_is_not_true_by_default(v0):
    ans = _answer(v0, k=3)
    assert why_not.funnel(v0, LINE, ORIGIN, answer_dict=ans)["consistency"]["matches_ranker"] is True
    no_gates = copy.deepcopy(ans)
    no_gates["how_we_decided"]["gates"] = []
    c = why_not.funnel(v0, LINE, ORIGIN, answer_dict=no_gates)["consistency"]
    assert c["matches_ranker"] is None and c["gates_compared"] == ["shown"]
    wrong = copy.deepcopy(ans)
    wrong["how_we_decided"]["gates"][2]["n"] += 1
    assert why_not.funnel(v0, LINE, ORIGIN, answer_dict=wrong)["consistency"]["matches_ranker"] is False
    bad_card = copy.deepcopy(ans)
    bad_card["results"][0]["facility_id"] = "Q"  # never recommendable
    c = why_not.funnel(v0, LINE, ORIGIN, answer_dict=bad_card)["consistency"]
    assert c["matches_ranker"] is False and c["mismatches"][0]["gate"] == "shown"
    nf = why_not.funnel(v0, LINE, {"status": "not_found", "lat": None, "lon": None})
    assert nf["consistency"]["matches_ranker"] is None


@pytest.mark.parametrize("query,name,code,quote", [
    ("400607", "Highland Super Speciality Hospital", "fact_not_counted", '"Caesarian Section"'),
    ("136131", "Khanpur Kolian Government Hospital", "fact_states_absence",
     '"Caesarean section facility not available on-site; referrals to LNJP Kurukshetra"'),
])
def test_real_records_named_in_the_review(real, query, name, code, quote):
    idx, resolver = real
    res = resolver.resolve(query)
    ans = rank.rank(idx, "emoc", res).to_dict()
    items = [it for it in why_not.why_not_nearer(idx, "emoc", res, ans, k=100) if it["name"] == name]
    f = why_not.funnel(idx, "emoc", res, answer_dict=ans, max_ids=None)
    rows = [x for s in f["steps"] for g in s["dropped"] for x in g.get("items", []) if x["name"] == name]
    # under score spec v1-final (nearest first) the first card can be nearer than the record, so the
    # strip need not list it; the funnel always does, with the same reason
    it = items[0] if items else (rows[0] if rows else None)
    assert it is not None, name
    assert it["reason_code"] == code
    assert quote in it["reason_plain"]
    assert "silent" not in it["reason_plain"] and "none of its" not in it["reason_plain"]
    assert rows and rows[0]["reason_plain"] == it["reason_plain"]


def test_real_lucknow_strip_counts_approximate_apart(real):
    idx, resolver = real
    res = resolver.resolve("226001")
    ans = rank.rank(idx, "emoc", res).to_dict()
    s = why_not.why_not_strip(idx, "emoc", res, ans, k=10, mask_names=True)
    approx = [it["position_approximate"] for it in s["items"]]
    assert approx == sorted(approx)
    assert sum(approx) == min(s["n_approx_nearer"], 10 - min(s["n_nearer"], 10))
    assert "list or claim" in s["heading"] and "mention" not in s["heading"]
    assert "Indira" not in json.dumps(s)


# ---------------------------------------------------------------------------
# score spec v1-final (D048): the reasons follow the order rule, never a score
# ---------------------------------------------------------------------------

#: Words that belong to the legacy score and must not appear in v1 reasons (D048 (6)).
V1_BANNED = re.compile(r"(?i)\bscor|\bconfiden|\blikely\b|\bprobab")


def _reason_texts(strip, fun):
    """The sentences the strip and the funnel write themselves (not quoted facts or names)."""
    out = [strip["heading"]] + [it["reason_plain"] for it in strip["items"]]
    out += [a["reason_plain"] for it in strip["items"] for a in it["also"]]
    for st in fun["steps"]:
        out.append(st["label"])
        for g in st["dropped"] + st.get("added", []):
            out.append(g["reason_plain"])
            out += [x["reason_plain"] for x in g.get("items", []) if x.get("reason_plain")]
    return out


def test_v1_reasons_say_nothing_of_a_score(v0):
    assert v0.rank_version == "v1"
    for k in (1, 3):
        ans = _answer(v0, k=k)
        s = why_not.why_not_strip(v0, LINE, ORIGIN, ans, k=50)
        f = why_not.funnel(v0, LINE, ORIGIN, answer_dict=ans)
        for t in _reason_texts(s, f):
            assert not V1_BANNED.search(t), t


def test_v1_ranked_lower_names_the_card_and_the_allowance(v0):
    t = _by_id(why_not.why_not_nearer(v0, LINE, ORIGIN, _answer(v0, k=1), k=50))["T"]
    assert t["reason_code"] == "ranked_lower"
    assert t["reason_plain"].startswith("Recommendable and inside the 60-minute window, and nearer than Hospital A "
                                        "(about 5 min estimated drive, against about 10 min), but that card is "
                                        "listed first: "), t
    assert "at most 15 minutes further (the evidence allowance)" in t["reason_plain"]
    assert "This record is not corroborated. Missing for that: dialysis unit, machines or nephrologist." \
        in t["reason_plain"]
    assert t["quote_or_absence"]["text"] == "T: haemodialysis available"


def test_v0_wording_stays_behind_the_flag(legacy):
    assert legacy.rank_version == "v0"
    ans = _answer(legacy, k=1)
    t = _by_id(why_not.why_not_nearer(legacy, LINE, ORIGIN, ans, k=50))["T"]
    assert t["reason_plain"].startswith("Recommendable, but it scored below the cards shown: ")
    st = {s["step"]: s for s in why_not.funnel(legacy, LINE, ORIGIN, answer_dict=ans)["steps"]}
    g = [g for g in st["shown"]["dropped"] if g["reason_code"] == "ranked_lower"][0]
    assert g["reason_plain"] == "Recommendable and within the radius, but scored below the cards shown."


# The nearest recommendable record (N1, grade 2) is pushed past three corroborated records by the
# evidence allowance, so it takes the last card; M (grade 2) and D (grade 3, but a possible-merge
# warning denies it the allowance) sit between N1 and the first card.
NEAR_ROWS = [
    ("C1", 6.0, 3, "strong", "private", "hospital", "ok", ["nephrology"], []),
    ("C2", 6.5, 3, "strong", "private", "hospital", "ok", ["nephrology"], []),
    ("C3", 7.0, 3, "strong", "private", "hospital", "ok", ["nephrology"], []),
    ("N1", 2.0, 2, "moderate", "private", "hospital", "ok", ["nephrology"], []),
    ("D", 3.0, 3, "strong", "private", "hospital", "ok", ["nephrology"], []),
    ("M", 4.0, 2, "moderate", "private", "hospital", "ok", ["nephrology"], []),
]


def _near_index(rank_version=None):
    fac, grades, facts, _, dups = _frames(NEAR_ROWS)
    flags = pd.DataFrame([dict(facility_id="D", flag="possible_merge", service_line=None, severity="minor",
                               signal="shared_website", reason="The record shares its website with another branch.",
                               quote="d0.in", check_version="t")])
    return rank.build_index(fac, grades, facts=facts, flags=flags, duplicates=dups.iloc[0:0],
                            rank_version=rank_version)


def test_v1_nearest_on_the_last_card():
    i = _near_index()
    ans = _answer(i, k=3)
    assert [c["facility_id"] for c in ans["results"]] == ["C1", "C2", "N1"]
    assert [c["nearest_in_window"] for c in ans["results"]] == [False, False, True]
    s = why_not.why_not_strip(i, LINE, ORIGIN, ans, k=50)
    items = _by_id(s["items"])
    assert set(items) == {"D", "M"}  # nearer than the first card, not shown; N1 is a card
    for fid in ("D", "M"):
        r = items[fid]["reason_plain"]
        assert items[fid]["reason_code"] == "ranked_lower"
        assert "nearer than Hospital C1 and Hospital C2" in r, r
        assert "those cards are listed first: a corroborated record may come first if it is at most 15 minutes " \
               "further (the evidence allowance)." in r, r
    assert "This record is not corroborated" in items["M"]["reason_plain"]
    assert "The allowance does not apply to this record, which carries a possible merge warning." \
        in items["D"]["reason_plain"]
    f = why_not.funnel(i, LINE, ORIGIN, answer_dict=ans)
    assert f["consistency"]["matches_ranker"] is True, f["consistency"]
    st = {x["step"]: x for x in f["steps"]}
    assert _drop_ids(st["shown"], "ranked_lower") == {"C3", "D", "M"}
    g = [g for g in st["shown"]["dropped"] if g["reason_code"] == "ranked_lower"][0]
    rows = {x["facility_id"]: x["reason_plain"] for x in g["items"]}
    # C3 would have been the third card; the nearest takes that place
    assert rows["C3"].endswith("It would have taken the last card, but that place goes to Hospital N1, the nearest "
                               "recommendable facility, which is always shown."), rows["C3"]
    assert "always shown" not in rows["M"] and "always shown" not in rows["D"]
    assert rows["M"] == items["M"]["reason_plain"]
    assert "the nearest is always shown" in g["reason_plain"]
    for t in _reason_texts(s, f):
        assert not V1_BANNED.search(t), t
        assert not BANNED.search(t), t


def test_v1_reasons_without_the_answer_state_the_rule():
    i = _near_index()
    pos = i.id_to_pos["M"]
    r = why_not.record_reasons(i, LINE, pos)
    assert r and r[0]["reason_code"] == "ranked_lower"
    assert r[0]["reason_plain"].startswith("Recommendable, but other facilities come first. The order is nearest "
                                           "first by estimated drive time")
    assert not V1_BANNED.search(r[0]["reason_plain"])
    legacy = _near_index("v0")
    assert "scored below" in why_not.record_reasons(legacy, LINE, legacy.id_to_pos["M"])[0]["reason_plain"]


def test_v1_candidate_cap_says_put_in_order(v0, monkeypatch):
    monkeypatch.setattr(S, "MAX_CANDIDATES", 1)
    ans = _answer(v0, k=3)
    f = why_not.funnel(v0, LINE, ORIGIN, answer_dict=ans)
    st = {s["step"]: s for s in f["steps"]}
    g = [g for g in st["shown"]["dropped"] if g["reason_code"] == "not_scored"][0]
    assert g["reason_plain"] == "Not put in order: the ranker orders only the 1 nearest recommendable records."
    assert not V1_BANNED.search(g["reason_plain"])


def test_real_v1_reasons_say_nothing_of_a_score(real):
    idx, resolver = real
    if idx.rank_version != "v1":
        pytest.skip("bundle index is not v1")
    for line, query in [("emoc", "226001"), ("dialysis", "Jaipur"), ("icu", "Patna"), ("nicu", "Pune")]:
        res = resolver.resolve(query)
        ans = rank.rank(idx, line, res).to_dict()
        s = why_not.why_not_strip(idx, line, res, ans, k=20)
        f = why_not.funnel(idx, line, res, answer_dict=ans)
        for t in _reason_texts(s, f):
            assert not V1_BANNED.search(t), (line, query, t)
