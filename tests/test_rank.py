"""WP5 ranking engine tests.

Synthetic tests build a small world around one point and run in both scoring
modes (v0 and probability). Real-data tests skip when the shared data folder is
absent.
"""
from __future__ import annotations

import json
import math
import time

import numpy as np
import pandas as pd
import pytest

from vf import paths, rank
from vf import services as S

LAT0, LON0 = 20.0, 78.0
KM_LAT = 1 / 111.195  # degrees of latitude per km


def north(km: float) -> float:
    return LAT0 + km * KM_LAT


ORIGIN = {"status": "ok", "lat": LAT0, "lon": LON0, "precision_km": 2.0, "label": "Test point", "how": "typed"}
SEA = {"status": "ok", "lat": 8.0, "lon": 65.0, "precision_km": 2.0, "label": "Arabian Sea", "how": "typed"}

# id, km north, grade, strength, n_domains, website, coord_check, type
FAC = [
    ("A", 6.0, 3, "strong", 4, "a.org", "ok", "hospital"),       # strong corroborated, a bit further
    ("B", 1.0, 2, "thin", 1, None, "ok", "hospital"),            # thin supported next door
    ("C", 0.5, 3, "strong", 5, "c.org", "ok", "hospital"),       # corroborated but blocked
    ("D", 2.0, 1, "thin", 3, None, "ok", "hospital"),            # claim only
    ("E", 0.3, 0, "none", 3, None, "ok", "hospital"),            # nothing
    ("F", 30.0, 2, "moderate", 3, "f.org", "ok", "hospital"),    # supported, possible merge (minor)
    ("G", 120.0, 3, "strong", 4, "g.org", "ok", "hospital"),     # beyond radius, within reach
    ("H", 6.1, 3, "strong", 4, "h.org", "ok", "hospital"),       # duplicate of A (keep A)
    ("M", 20.0, 2, "moderate", 3, "m.org", "moved_to_pin", "hospital"),  # fuzzy location
    ("X", 600.0, 3, "strong", 4, "x.org", "ok", "hospital"),     # far away
]
LINE = "dialysis"


def _frames(rows=FAC):
    fac, grades, facts = [], [], []
    for fid, km, g, st, nd, web, cc, ft in rows:
        fac.append(dict(facility_id=fid, name=f"Hospital {fid}", facility_type=ft, operator_type="private",
                        lat=north(km), lon=LON0, coord_check=cc, coord_pin_km=120.0 if cc == "moved_to_pin" else 1.0,
                        pin="000000", city_raw="Testpur", state_raw="Teststate", specialties=["nephrology"] if g >= 1 else [],
                        official_website=web, source_domains=[f"d{i}.in" for i in range(nd)], n_source_domains=nd,
                        source_confidence=min(nd, 6) / 6 * 0.4 + (0.2 if web else 0.0) + 0.2,
                        description=f"Hospital {fid} description."))
        items = []
        if g >= 2:
            t1 = f"{fid}: dialysis unit with 10 machines"
            facts.append(dict(fact_id=f"{fid}1", facility_id=fid, field="equipment", text=t1))
            items.append(dict(requirement="dialysis", tag="dialysis", fact_id=f"{fid}1", field="equipment", text=t1))
        if g >= 3:
            t2 = f"{fid}: nephrologist on call"
            facts.append(dict(fact_id=f"{fid}2", facility_id=fid, field="capability", text=t2))
            items.append(dict(requirement="dialysis unit, machines or nephrologist", tag="dialysis_unit",
                              fact_id=f"{fid}2", field="capability", text=t2))
        if g == 1:
            items.append(dict(requirement="listed specialty", tag=None, fact_id=None, field="specialties", text="nephrology"))
        facts.append(dict(fact_id=f"{fid}9", facility_id=fid, field="capability", text=f"{fid}: general wards"))
        grades.append(dict(facility_id=fid, service_line=LINE, grade=g, grade_label=S.GRADE_LABELS[g],
                           tags_present=[], evidence=[i["text"] for i in items], evidence_items=items,
                           missing_for_next=[] if g == 3 else ["dialysis unit, machines or nephrologist"],
                           n_supporting_facts=len(items), evidence_strength=st, method="regex", recipe_version="test"))
    fac = pd.DataFrame(fac)
    grades = pd.DataFrame(grades)
    facts = pd.DataFrame(facts)
    flags = pd.DataFrame([
        dict(facility_id="C", flag="location_conflict", service_line=None, severity="major", signal="coord_unresolved",
             reason="The record's location is 140 km from its PIN code's area.", quote=None, check_version="t"),
        dict(facility_id="F", flag="possible_merge", service_line=None, severity="minor", signal="name_joins_two",
             reason="The name joins two facilities.", quote="Hospital F and Clinic Q", check_version="t"),
    ])
    dups = pd.DataFrame([dict(facility_id_a="A", facility_id_b="H", distance_km=0.1, name_score=95.0,
                              signal="near_same_name", keep="A")])
    return fac, grades, facts, flags, dups


P_OFFERS = {"A": 0.9, "B": 0.55, "C": 0.95, "D": 0.8, "F": 0.7, "G": 0.85, "H": 0.9, "M": 0.6, "X": 0.9}


def _prob_table():
    return pd.DataFrame([dict(facility_id=f, service_line=LINE, p_offers=p, p_low=p - 0.1, p_high=min(p + 0.05, 1.0),
                              top_factors=[], model_version="synthetic", n_labels_line=100)
                         for f, p in P_OFFERS.items()])


def _po():
    return pd.DataFrame({"lat": [north(k) for k in (0.0, 3.0, 8.0)], "lon": [LON0] * 3, "pin": ["000000"] * 3})


@pytest.fixture(params=["v0", "probability"])
def idx(request):
    fac, grades, facts, flags, dups = _frames()
    sp = _prob_table() if request.param == "probability" else None
    return rank.build_index(fac, grades, facts=facts, flags=flags, duplicates=dups, service_probability=sp,
                            post_offices=_po())


def _grade(i, fid):
    return int(i.lines[LINE].grade[i.id_to_pos[fid]])


# ---------------------------------------------------------------------------
# synthetic, both modes
# ---------------------------------------------------------------------------

def test_gates_exclude_grade_0_and_1(idx):
    a = rank.rank(idx, LINE, ORIGIN)
    assert a.status == "ok"
    assert a.results
    for c in a.results:
        assert _grade(idx, c.facility_id) >= 2
    ids = {c.facility_id for c in a.results}
    assert "D" not in ids and "E" not in ids


def test_blocked_never_in_results_but_listed_as_unverified(idx):
    a = rank.rank(idx, LINE, ORIGIN, k=10)
    assert "C" not in {c.facility_id for c in a.results}
    unv = {c.facility_id: c for c in a.unverified_nearby}
    assert "C" in unv and "140 km" in unv["C"].confidence_reason
    assert "D" in unv  # claim only, inside the radius
    assert all(c.score is None for c in a.unverified_nearby)


def test_parts_rebuild_the_score(idx):
    a = rank.rank(idx, LINE, ORIGIN, k=10)
    for c in a.results:
        assert c.recompute_score() == pytest.approx(c.score, abs=1e-12)
    pen = [c for c in a.results if c.parts.get("penalties")]
    if idx.is_probability(LINE):
        assert all(c.parts["combine"] == "product" for c in a.results)
    else:
        assert pen and pen[0].facility_id == "F"
        assert pen[0].parts["penalties"][0]["value"] == S.PENALTIES["possible_merge"]


def _pair_world(rows, p=None):
    """A world of only ``rows`` (FAC layout), every record with the same source
    trust, so evidence and distance are the only things that differ. ``p``:
    facility_id -> p_offers for probability mode."""
    fac, grades, facts, _, _ = _frames(rows)
    fac["source_confidence"] = 0.6
    sp = None
    if p is not None:
        sp = pd.DataFrame([dict(facility_id=f, service_line=LINE, p_offers=v, p_low=v - 0.05, p_high=v + 0.05,
                                top_factors=[], model_version="synthetic", n_labels_line=100) for f, v in p.items()])
    return rank.build_index(fac, grades, facts=facts, flags=None, duplicates=None, service_probability=sp)


def test_strong_grade3_beats_thin_grade2_next_door():
    """Equal trust, no flags: only evidence and distance differ. A strong
    corroborated record 6 km away beats a thin supported one 1 km away."""
    rows = [("A", 6.0, 3, "strong", 3, None, "ok", "hospital"), ("B", 1.0, 2, "thin", 3, None, "ok", "hospital")]
    i = _pair_world(rows)
    a = rank.rank(i, LINE, ORIGIN)
    assert [c.facility_id for c in a.results] == ["A", "B"]
    pa, pb = a.results[0].parts, a.results[1].parts
    assert pa["trust"] == pb["trust"] and not pa["penalties"] and not pb["penalties"]
    assert pa["evidence"] - pb["evidence"] == pytest.approx(0.25)  # 0.5 x (1.0 - 0.5)
    # even at the radius edge the thin record cannot catch up: inside the radius
    # the distance part moves a score by at most 0.3 x (1 - e^-1) = 0.19 < 0.25
    i = _pair_world([("A", 49.0, 3, "strong", 3, None, "ok", "hospital"), rows[1]])
    assert rank.rank(i, LINE, ORIGIN).results[0].facility_id == "A"


def test_distance_wins_on_purpose_between_equal_strength_grades():
    """Where the v0 weights let distance win, by design: corroborated against
    supported at the same (moderate) strength is a 0.15 evidence gap, smaller
    than the 0.19 the distance part can move inside the radius. The crossover
    for a supported record next door (1 km) is at about 34 km
    (0.3 x (e^-1/50 - e^-d/50) = 0.15)."""
    near = ("S", 1.0, 2, "moderate", 3, None, "ok", "hospital")
    a = rank.rank(_pair_world([near, ("C", 20.0, 3, "moderate", 3, None, "ok", "hospital")]), LINE, ORIGIN)
    assert [c.facility_id for c in a.results] == ["C", "S"]  # 0.82 against 0.76 (trust 0.12 each)
    a = rank.rank(_pair_world([near, ("C", 48.0, 3, "moderate", 3, None, "ok", "hospital")]), LINE, ORIGIN)
    assert [c.facility_id for c in a.results] == ["S", "C"]  # 0.76 against 0.73
    assert a.results[0].score - a.results[1].score > S.TIE_EPS  # a real win, not a tie
    assert "closer" in a.results[0].why_here


def test_probability_mode_equal_chance_closer_wins():
    """In probability mode the grade only gates; the chance of confirmation
    carries the evidence. With equal chances, the closer record wins on purpose;
    with a clearly higher chance, the further one does."""
    rows = [("A", 6.0, 3, "strong", 3, None, "ok", "hospital"), ("B", 1.0, 2, "thin", 3, None, "ok", "hospital")]
    a = rank.rank(_pair_world(rows, p={"A": 0.7, "B": 0.7}), LINE, ORIGIN)
    assert [c.facility_id for c in a.results] == ["B", "A"]
    a = rank.rank(_pair_world(rows, p={"A": 0.9, "B": 0.6}), LINE, ORIGIN)
    assert [c.facility_id for c in a.results] == ["A", "B"]


@pytest.mark.parametrize("mode", ["v0", "probability"])
def test_inside_radius_comes_before_better_score_beyond(mode):
    """A supported record inside the radius is listed before a corroborated one
    at 2.5 x the radius, even when the far one scores higher (S.ORDER_RULE)."""
    rows = [("IN", 45.0, 2, "moderate", 3, None, "ok", "hospital"),
            ("OUT", 2.5 * S.RADII_KM[LINE], 3, "strong", 3, None, "ok", "hospital")]
    i = _pair_world(rows, p={"IN": 0.6, "OUT": 0.95} if mode == "probability" else None)
    a = rank.rank(i, LINE, ORIGIN)
    assert a.status == "ok"
    assert [c.facility_id for c in a.results] == ["IN", "OUT"]
    if mode == "v0":
        assert a.results[1].score > a.results[0].score  # the tier decides, not the score
    assert "inside the 50 km radius" in a.results[0].why_here
    assert "Beyond the usual 50 km" in a.results[1].location_note
    assert a.how_we_decided["order_rule"] == S.ORDER_RULE
    assert "1 of them is inside the 50 km radius" in a.how_we_decided["headline"]


def test_abstention_in_empty_region(idx):
    a = rank.rank(idx, LINE, SEA)
    assert a.status == "no_reliable_option"
    assert a.results == []
    assert "no facility in this dataset shows reliable evidence" in a.how_we_decided["headline"]
    assert a.beyond_radius is not None and a.beyond_radius.distance_km > S.RADII_KM[LINE]
    assert _grade(idx, a.beyond_radius.facility_id) >= 2


def test_abstention_when_only_claims_nearby(idx):
    fac, grades, facts, flags, dups = _frames()
    grades.loc[grades.grade >= 2, ["grade", "grade_label"]] = [1, "claimed"]
    i = rank.build_index(fac, grades, facts=facts, flags=flags, duplicates=dups)
    a = rank.rank(i, LINE, ORIGIN)
    assert a.status == "no_reliable_option" and a.beyond_radius is None
    assert a.unverified_nearby


def test_quotes_exist_in_facts(idx):
    _, _, facts, _, _ = _frames()
    keyset = set(zip(facts.facility_id, facts.field, facts.text, facts.fact_id))
    a = rank.rank(idx, LINE, ORIGIN, k=10)
    for c in a.results:
        assert c.evidence
        for e in c.evidence:
            assert (c.facility_id, e["field"], e["quote"], e["fact_id"]) in keyset


def test_duplicate_shown_once_with_also_listed_as(idx):
    a = rank.rank(idx, LINE, ORIGIN, k=10)
    ids = [c.facility_id for c in a.results]
    assert "A" in ids and "H" not in ids
    a_card = next(c for c in a.results if c.facility_id == "A")
    assert [x["facility_id"] for x in a_card.sources["also_listed_as"]] == ["H"]


def test_fuzzy_location_distance_not_to_one_decimal(idx):
    a = rank.rank(idx, LINE, ORIGIN, k=10)
    m = next(c for c in a.results if c.facility_id == "M")
    assert m.location_precision_km == S.LOCATION_PRECISION_KM["moved_to_pin"]
    assert m.distance_km == round(m.distance_km)
    assert "PIN" in m.location_note
    b = next(c for c in a.results if c.facility_id == "B")
    assert b.distance_km == pytest.approx(1.0, abs=0.05)


def test_ambiguous_and_not_found(idx):
    a = rank.rank(idx, LINE, {"status": "ambiguous", "candidates": [{"label": "X, Bihar"}, {"label": "X, Maharashtra"}]})
    assert a.status == "needs_location_choice" and len(a.location["candidates"]) == 2 and not a.results
    b = rank.rank(idx, LINE, {"status": "not_found", "how": "No such place."})
    assert b.status == "location_not_found"


def test_score_status_and_json(idx):
    a = rank.rank(idx, LINE, ORIGIN)
    d = json.loads(a.to_json())
    want = "calibrated" if idx.is_probability(LINE) else "provisional"
    assert d["how_we_decided"]["score_status"] == want
    assert [g["gate"] for g in d["how_we_decided"]["gates"]] == ["usable location", "within reach", "recommendable", "within radius"]
    for c in d["results"]:
        assert set(c) >= {"facility_id", "name", "distance_km", "grade_label", "confidence", "parts", "evidence",
                          "missing", "sources", "why_here", "could_be_wrong"}
        assert c["confidence"] in ("high", "medium", "low")
        assert "grade 2" not in c["why_here"].lower() and "grade 3" not in c["why_here"].lower()


def test_probability_threshold_gates(idx):
    if not idx.is_probability(LINE):
        pytest.skip("probability mode only")
    S.P_OFFERS_THRESHOLD[LINE] = 0.6
    try:
        a = rank.rank(idx, LINE, ORIGIN, k=10)
        assert "B" not in {c.facility_id for c in a.results}  # p 0.55 below 0.6
        unv = {c.facility_id: c for c in a.unverified_nearby}
        assert "B" in unv  # nearest three non-recommendable inside the radius: C 0.5, B 1.0, D 2.0 km
        assert "55%" in unv["B"].confidence_reason and "60%" in unv["B"].confidence_reason
    finally:
        S.P_OFFERS_THRESHOLD.pop(LINE, None)


def test_ties_broken_by_distance():
    fac, grades, facts, flags, dups = _frames()
    keep = fac.facility_id.isin(["A", "B"])
    fac, grades = fac[keep].copy(), grades[grades.facility_id.isin(["A", "B"])].copy()
    # same grade and strength; A at 10 km with slightly more trust, B at 9 km
    fac.loc[fac.facility_id == "A", ["lat", "source_confidence"]] = [north(10.0), 0.56]
    fac.loc[fac.facility_id == "B", ["lat", "source_confidence"]] = [north(9.0), 0.50]
    grades["grade"], grades["evidence_strength"] = 3, "moderate"
    i = rank.build_index(fac, grades, facts=facts, flags=flags.iloc[0:0], duplicates=None)
    a = rank.rank(i, LINE, ORIGIN)
    assert abs(a.results[0].score - a.results[1].score) < S.TIE_EPS
    assert [c.facility_id for c in a.results] == ["B", "A"]
    assert a.results[0].why_here.startswith("Tied with")


def test_v01_grades_are_refused():
    """No stubs: grades without evidence_items have no fact ids to cite."""
    fac, _, facts, _, _ = _frames()
    grades = pd.DataFrame([dict(facility_id="A", service_line="emoc", grade=3, grade_label="corroborated",
                                tags_present=["csection"], evidence=["Caesarean section performed"],
                                source_confidence=0.7, method="regex", recipe_version="v0.1")])
    with pytest.raises(ValueError, match="evidence_items"):
        rank.build_index(fac, grades, facts=facts, flags=None)


def test_no_flags_means_none_and_is_said():
    fac, grades, facts, _, _ = _frames()
    i = rank.build_index(fac, grades, facts=facts, flags=None)
    assert i.flags_source == "none"
    a = rank.rank(i, LINE, ORIGIN)
    assert any("No trust checks are loaded" in n for n in a.how_we_decided["notes"])
    assert "C" in {c.facility_id for c in a.results}  # nothing blocks it without the checks


def test_flags_source_is_check_version(idx):
    assert idx.flags_source == "t"


def test_cards_carry_flags_and_strength(idx):
    a = rank.rank(idx, LINE, ORIGIN, k=10)
    f = next(c for c in a.results if c.facility_id == "F")
    assert f.flags == [{"flag": "possible_merge", "severity": "minor", "signal": "name_joins_two",
                        "reason": "The name joins two facilities.", "quote": "Hospital F and Clinic Q"}]
    assert f.evidence_strength == "moderate"
    assert next(c for c in a.results if c.facility_id == "B").evidence_strength == "thin"
    c = next(c for c in a.unverified_nearby if c.facility_id == "C")
    assert c.flags and c.flags[0]["severity"] == "major"
    d = json.loads(a.to_json())
    assert all({"flags", "evidence_strength"} <= set(x) for x in d["results"])


def test_line_specific_major_flag_blocks_only_that_line():
    fac, grades, facts, flags, dups = _frames()
    g2 = grades.copy()
    g2["service_line"] = "icu"
    flags = pd.concat([flags, pd.DataFrame([dict(facility_id="A", flag="implausible_claim", service_line="icu",
                                                 severity="major", signal="type_cannot_offer",
                                                 reason="A clinic would not normally offer intensive care.",
                                                 quote="clinic", check_version="t")])], ignore_index=True)
    i = rank.build_index(fac, pd.concat([grades, g2], ignore_index=True), facts=facts, flags=flags, duplicates=dups)
    assert "A" not in {c.facility_id for c in rank.rank(i, "icu", ORIGIN, k=10).results}
    assert "A" in {c.facility_id for c in rank.rank(i, LINE, ORIGIN, k=10).results}


def test_locate_resolution_object_accepted(idx):
    from vf.locate import Resolution
    res = Resolution("ok", LAT0, LON0, 3.0, "city", "Testpur, Test district, Teststate", "typed",
                     [], "TESTSTATE", "TEST", "city:x", "Testpur")
    a = rank.rank(idx, LINE, res)
    assert a.status == "ok" and a.location["place_id"] == "city:x" and a.location["kind"] == "city"
    amb = Resolution("ambiguous", candidates=[{"place_id": "district:a", "label": "A, Bihar", "kind": "district"},
                                              {"place_id": "district:b", "label": "A, Maharashtra", "kind": "district"}])
    b = rank.rank(idx, LINE, amb)
    assert b.status == "needs_location_choice"
    assert [c["place_id"] for c in b.location["candidates"]] == ["district:a", "district:b"]
    out = rank.rank(idx, LINE, Resolution("outside_india", how="That point is outside India."))
    assert out.status == "location_not_found"


def test_load_index_requires_trust_checks(tmp_path):
    fac, grades, _, flags, dups = _frames()
    fac.to_parquet(tmp_path / "facilities.parquet")
    grades.to_parquet(tmp_path / "grades.parquet")
    dups.to_parquet(tmp_path / "duplicates.parquet")
    with pytest.raises(FileNotFoundError, match="trust checks"):
        rank.load_index(tmp_path)
    flags.to_parquet(tmp_path / "flags.parquet")
    i = rank.load_index(tmp_path)
    assert i.flags_source == "t" and rank.rank(i, LINE, ORIGIN).status == "ok"


def test_ungraded_line_falls_back_to_keyword(idx):
    a = rank.rank(idx, "mri", ORIGIN)
    assert a.method == "keyword" and a.service == "mri"
    assert all(c.confidence == "low" for c in a.results)


def test_keyword_search_low_confidence_and_verbatim(idx):
    _, _, facts, _, _ = _frames()
    a = rank.search(idx, "dialysis unit machines", ORIGIN)
    assert a.method == "keyword" and a.status == "ok"
    keyset = set(zip(facts.facility_id, facts.text))
    for c in a.results:
        assert c.confidence == "low" and c.grade_label == "keyword match, no service recipe"
        assert c.recompute_score() == pytest.approx(c.score)
        for e in c.evidence:
            assert e["field"] == "specialties" or (c.facility_id, e["quote"]) in keyset
    assert "C" not in {c.facility_id for c in a.results}  # whole-record blocking flag


def test_keyword_index_roundtrip(tmp_path, idx):
    kw = idx.ensure_keyword()
    kw.save(tmp_path / "keyword_index")
    kw2 = rank.load_keyword_index(tmp_path / "keyword_index")
    kw2.bind(idx.id_to_pos)
    assert np.allclose(kw.similarities("dialysis"), kw2.similarities("dialysis"))


def test_search_duplicate_shown_once(idx):
    """search() (also the fallback for ungraded lines) shows one card per WP11
    duplicate group. A and H are duplicates and both match the words."""
    a = rank.search(idx, "dialysis unit machines", ORIGIN, k=10)
    ids = [c.facility_id for c in a.results]
    assert "A" in ids and "H" not in ids
    card = next(c for c in a.results if c.facility_id == "A")
    assert [x["facility_id"] for x in card.sources["also_listed_as"]] == ["H"]
    assert a.how_we_decided["gates"][-1]["n_distinct"] == a.how_we_decided["gates"][-1]["n"] - 1
    assert f"of {len(set(ids))} keyword matches" in a.how_we_decided["headline"]


def test_search_unverified_one_per_group_and_not_a_shown_group():
    """Blocked keyword matches: one per duplicate group, and none from a group
    already shown as a card. B and C are both blocked and duplicates; F is shown
    as a card and its duplicate G is blocked."""
    fac, grades, facts, flags, dups = _frames()
    extra = pd.DataFrame([dict(facility_id=f, flag="location_conflict", service_line=None, severity="major",
                               signal="coord_unresolved", reason=f"{f} location conflict.", quote=None,
                               check_version="t") for f in ("B", "G")])
    flags = pd.concat([flags, extra], ignore_index=True)
    fac.loc[fac.facility_id == "G", "lat"] = north(31.0)  # inside the 100 km search radius
    dups = pd.concat([dups, pd.DataFrame([
        dict(facility_id_a="C", facility_id_b="B", distance_km=0.5, name_score=95.0, signal="near_same_name", keep="C"),
        dict(facility_id_a="F", facility_id_b="G", distance_km=1.0, name_score=95.0, signal="near_same_name", keep="F"),
    ])], ignore_index=True)
    i = rank.build_index(fac, grades, facts=facts, flags=flags, duplicates=dups)
    a = rank.search(i, "dialysis unit machines", ORIGIN, k=10)
    shown = {c.facility_id for c in a.results}
    unv = [c.facility_id for c in a.unverified_nearby]
    assert "F" in shown and "G" not in unv
    assert ("B" in unv) != ("C" in unv)
    assert len(unv) == len(set(unv))


def test_headline_and_gates_count_past_the_cap():
    """More than MAX_CANDIDATES recommendable facilities: the headline and the
    'within radius' gate count all of them; only scoring is capped."""
    n = S.MAX_CANDIDATES + 20
    rows = [(f"R{j:03d}", 0.5 * (j + 1), 2, "moderate", 3, None, "ok", "hospital") for j in range(n)]
    i = _pair_world(rows)
    a = rank.rank(i, LINE, ORIGIN)
    g = {x["gate"]: x for x in a.how_we_decided["gates"]}
    assert g["recommendable"]["n"] == n and g["recommendable"]["n_distinct"] == n
    assert g["recommendable"]["n_scored"] == S.MAX_CANDIDATES
    assert g["within radius"]["n"] == n  # all within 35 km < 50 km
    h = a.how_we_decided["headline"]
    assert h.startswith(f"3 of {n} recommendable facilities")
    assert f"{n} of them are inside the 50 km radius" in h
    assert f"Only the nearest {S.MAX_CANDIDATES} were scored" in h


def test_headline_counts_duplicate_groups_once(idx):
    a = rank.rank(idx, LINE, ORIGIN)
    g = {x["gate"]: x for x in a.how_we_decided["gates"]}
    assert g["recommendable"]["n_distinct"] == g["recommendable"]["n"] - 1  # A and H are one facility
    assert f"of {g['recommendable']['n_distinct']} recommendable" in a.how_we_decided["headline"]


def test_coarse_origin_rounds_distances_and_says_where_from(idx):
    coarse = dict(ORIGIN, precision_km=70.0, label="Testpur district")
    a = rank.rank(idx, LINE, coarse, k=10)
    for c in a.results + a.unverified_nearby:
        assert c.distance_km == round(c.distance_km)
        assert "Measured from the centre of Testpur district" in c.location_note
        assert "about 70 km" in c.location_note
    b = next(c for c in a.results if c.facility_id == "B")
    assert b.distance_km == 1.0
    fine = rank.rank(idx, LINE, ORIGIN, k=10)  # precision 2 km: not coarse
    assert all("Measured from" not in c.location_note for c in fine.results)
    s = rank.search(idx, "dialysis unit machines", coarse)
    assert all("Measured from the centre" in c.location_note for c in s.results)
    wp2 = rank.rank(idx, LINE, dict(coarse, label="Centre of Testpur district, Teststate"))  # WP2 label style
    note = wp2.results[0].location_note
    assert "Measured from the centre of Testpur district, Teststate" in note and "centre of Centre" not in note


def test_load_index_mixed_probability_mode(tmp_path):
    """A bundle whose service_probability covers one line: that line is in
    probability mode, the other stays v0 and is labelled provisional."""
    fac, grades, _, flags, dups = _frames()
    g2 = grades.copy()
    g2["service_line"] = "emoc"
    pd.concat([grades, g2], ignore_index=True).to_parquet(tmp_path / "grades.parquet")
    fac.to_parquet(tmp_path / "facilities.parquet")
    flags.to_parquet(tmp_path / "flags.parquet")
    dups.to_parquet(tmp_path / "duplicates.parquet")
    _prob_table().to_parquet(tmp_path / "service_probability.parquet")
    _po().to_parquet(tmp_path / "post_offices.parquet")
    i = rank.load_index(tmp_path)
    assert i.probability_source == "service_probability"
    assert i.is_probability(LINE) and not i.is_probability("emoc")
    a = rank.rank(i, LINE, ORIGIN)
    assert a.how_we_decided["mode"] == "probability" and a.how_we_decided["score_status"] == "calibrated"
    assert all(c.parts["combine"] == "product" for c in a.results)
    assert a.results[0].parts["evidence"] == pytest.approx(P_OFFERS[a.results[0].facility_id])
    e = rank.rank(i, "emoc", ORIGIN)
    assert e.how_we_decided["mode"] == "v0" and e.how_we_decided["score_status"] == "provisional"
    assert all(c.parts["combine"] == "sum" for c in e.results)
    assert set(rank.simulate(i, LINE, ORIGIN, None, n_origins=3)["policies"]) == set(rank.POLICIES)
    with pytest.raises(RuntimeError):
        rank.simulate(i, "emoc", ORIGIN, "ranked", n_origins=3)


# ---------------------------------------------------------------------------
# simulation (D038)
# ---------------------------------------------------------------------------

def test_expected_trip_closed_form():
    km, end = rank.expected_trip_km([10.0, 5.0], [0.5, 1.0])
    assert km == pytest.approx(12.5) and end == pytest.approx(0.0)
    km, end = rank.expected_trip_km([4.0, 3.0, 2.0], [0.0, 0.5, 0.5])
    assert km == pytest.approx(4 + 3 + 0.5 * 2) and end == pytest.approx(0.25)


def test_simulate_only_in_probability_mode(idx):
    if not idx.is_probability(LINE):
        with pytest.raises(RuntimeError):
            rank.simulate(idx, LINE, ORIGIN, "ranked", 3)
        return
    out = rank.simulate(idx, LINE, ORIGIN, None, n_origins=3)
    assert set(out["policies"]) == set(rank.POLICIES) and out["n_origins"] == 3
    for v in out["policies"].values():
        assert v["expected_km_mean"] > 0 and 0 <= v["share_second_trip"] <= 1
    one = rank.simulate(idx, LINE, ORIGIN, "nearest_claim", n_origins=3)
    assert one["expected_km_mean"] == out["policies"]["nearest_claim"]["expected_km_mean"]
    again = rank.simulate(idx, LINE, ORIGIN, None, n_origins=3)
    assert again == out  # no random draws


# ---------------------------------------------------------------------------
# real data
# ---------------------------------------------------------------------------

REAL = paths.gold_dir() / "capability_grades.parquet"
POINTS = {  # lat, lon
    "jaipur": (26.9124, 75.7873), "lucknow": (26.8467, 80.9462), "patna": (25.5941, 85.1376),
    "pune": (18.5204, 73.8567), "araria": (26.1325, 87.4528), "leh": (34.1526, 77.5771),
}


@pytest.fixture(scope="module")
def real():
    if not REAL.exists() or not (paths.silver_dir() / "facts.parquet").exists():
        pytest.skip("shared data absent")
    return rank.load_index()


@pytest.fixture(scope="module")
def real_facts():
    return pd.read_parquet(paths.silver_dir() / "facts.parquet", columns=["fact_id", "facility_id", "field", "text"])


def _res(lat, lon):
    return {"status": "ok", "lat": lat, "lon": lon, "precision_km": 5.0, "label": "pt", "how": "test"}


def test_real_cards_quote_facts_verbatim(real, real_facts):
    keyset = set(zip(real_facts.facility_id, real_facts.field, real_facts.text, real_facts.fact_id))
    fac = real.fac.set_index("facility_id")
    n = 0
    for line in real.graded_lines:
        for lat, lon in POINTS.values():
            a = rank.rank(real, line, _res(lat, lon), k=3)
            cards = a.results + ([a.beyond_radius] if a.beyond_radius else [])
            for c in cards:
                assert int(real.lines[line].grade[real.id_to_pos[c.facility_id]]) >= 2
                assert not real.lines[line].blocked[real.id_to_pos[c.facility_id]]
                assert c.evidence, (line, c.name)
                for e in c.evidence:
                    if e["field"] == "description":
                        assert e["quote"] in (fac.loc[c.facility_id, "description"] or "")
                    elif e["field"] == "specialties":
                        assert e["quote"] in list(fac.loc[c.facility_id, "specialties"])
                    else:
                        assert (c.facility_id, e["field"], e["quote"], e["fact_id"]) in keyset, (line, c.name, e)
                    n += 1
                assert c.recompute_score() == pytest.approx(c.score, abs=1e-9)
    assert n > 20


def test_real_keyword_quotes_verbatim(real, real_facts):
    keyset = set(zip(real_facts.facility_id, real_facts.text))
    a = rank.search(real, "cataract surgery", _res(*POINTS["jaipur"]))
    assert a.status == "ok"
    for c in a.results:
        for e in c.evidence:
            assert e["field"] == "specialties" or (c.facility_id, e["quote"]) in keyset


def test_real_rank_under_100ms(real):
    times = []
    for line in real.graded_lines:
        for lat, lon in POINTS.values():
            t = time.perf_counter()
            rank.rank(real, line, _res(lat, lon))
            times.append((time.perf_counter() - t) * 1000)
    assert max(times) < 100, times


def test_real_remote_abstains(real):
    a = rank.rank(real, "dialysis", _res(*POINTS["leh"]))
    assert a.status == "no_reliable_option"
    json.dumps(a.to_dict())


def test_real_uses_wp11_and_all_12_lines(real):
    assert real.flags_source.startswith("wp11"), real.flags_source
    assert real.graded_lines == S.SERVICE_LINES
    assert real.recipe_version == "v0.2"
    assert real.dup_of, "duplicate pairs from WP11 should be loaded"


def test_real_implausible_claim_never_recommended(real):
    """A WP11 implausible_claim (major, line-specific) blocks a graded facility for that line."""
    fl = pd.read_parquet(paths.gold_dir() / "facility_flags.parquet")
    imp = fl[(fl.flag == "implausible_claim") & (fl.severity == "major")]
    checked = 0
    for r in imp.itertuples(index=False):
        pos = real.id_to_pos.get(r.facility_id)
        if pos is None or math.isnan(real.lat[pos]) or r.service_line not in real.lines:
            continue
        if int(real.lines[r.service_line].grade[pos]) < 2:
            continue
        a = rank.rank(real, r.service_line, _res(float(real.lat[pos]), float(real.lon[pos])), k=50)
        assert r.facility_id not in {c.facility_id for c in a.results}
        if a.status == "no_reliable_option" and a.beyond_radius is not None:
            assert a.beyond_radius.facility_id != r.facility_id
        checked += 1
        if checked >= 15:
            break
    assert checked > 0


def test_unverified_one_per_duplicate_group():
    """D (claim only) duplicates A: A is a card, so D leaves the unverified list;
    E duplicates C (blocked): only the nearer one of the pair is listed."""
    fac, grades, facts, flags, _ = _frames()
    grades.loc[grades.facility_id == "E", ["grade", "grade_label"]] = [1, "claimed"]
    dups = pd.DataFrame([dict(facility_id_a="A", facility_id_b="D", distance_km=0.1, name_score=95.0,
                              signal="near_same_name", keep="A"),
                         dict(facility_id_a="C", facility_id_b="E", distance_km=0.1, name_score=95.0,
                              signal="near_same_name", keep="C")])
    i = rank.build_index(fac, grades, facts=facts, flags=flags, duplicates=dups)
    a = rank.rank(i, LINE, ORIGIN, k=10)
    unv = [c.facility_id for c in a.unverified_nearby]
    assert "A" in {c.facility_id for c in a.results}
    assert "D" not in unv
    assert ("C" in unv) != ("E" in unv)


def test_real_duplicate_shown_once(real):
    """WP11 duplicate pairs, joined into groups: at most one card per group, and
    that card names every other record of its group as "also listed as"."""
    checked = 0
    groups = {}
    for p_, c in real.dup_canon.items():
        groups.setdefault(c, []).append(p_)
    for canon, members in groups.items():
        for line in real.graded_lines:
            ld = real.lines[line]
            rec = [m for m in members if ld.grade[m] >= 2 and not ld.blocked[m] and not math.isnan(real.lat[m])]
            if len(rec) < 2:
                continue
            a = rank.rank(real, line, _res(float(real.lat[rec[0]]), float(real.lon[rec[0]])), k=50)
            ids = {real.fac.facility_id.iat[m] for m in members}
            shown = [c for c in a.results if c.facility_id in ids]
            assert len(shown) == 1, (line, [c.name for c in shown])
            if canon in rec:
                assert shown[0].facility_id == real.fac.facility_id.iat[canon]
            also = {x["facility_id"] for x in shown[0].sources["also_listed_as"]}
            assert also == ids - {shown[0].facility_id}
            checked += 1
            break
    assert checked > 0


def test_duplicate_chain_is_one_group():
    """A keep over B, B keep over C: one card, A, naming B and C."""
    fac, grades, facts, flags, _ = _frames()
    dups = pd.DataFrame([dict(facility_id_a="A", facility_id_b="H", distance_km=0.1, name_score=95.0,
                              signal="near_same_name", keep="A"),
                         dict(facility_id_a="G", facility_id_b="H", distance_km=0.1, name_score=95.0,
                              signal="near_same_name", keep="H")])
    i = rank.build_index(fac, grades, facts=facts, flags=flags, duplicates=dups)
    a = rank.rank(i, LINE, ORIGIN, k=10)
    ids = [c.facility_id for c in a.results]
    assert "A" in ids and "H" not in ids and "G" not in ids
    card = next(c for c in a.results if c.facility_id == "A")
    assert {x["facility_id"] for x in card.sources["also_listed_as"]} == {"G", "H"}


def test_real_with_locate_resolver(real, real_facts):
    from vf import locate
    keyset = set(zip(real_facts.facility_id, real_facts.field, real_facts.text, real_facts.fact_id))
    res = locate.resolve("Jaipur")
    assert res.status == "ok"
    a = rank.rank(real, "dialysis", res)
    assert a.status == "ok" and len(a.results) == 3
    assert a.location["label"] == res.label and a.location["place_id"] == res.place_id
    for c in a.results:
        assert c.confidence in ("high", "medium", "low") and c.evidence_strength in ("strong", "moderate", "thin")
        for e in c.evidence:
            assert (c.facility_id, e["field"], e["quote"], e["fact_id"]) in keyset
        for f in c.flags:
            assert f["severity"] == "minor"  # a major flag would have blocked it
    amb = locate.resolve("Aurangabad")
    assert rank.rank(real, "emoc", amb).status == "needs_location_choice"
    assert rank.rank(real, "emoc", locate.resolve("Xqzvillepur")).status == "location_not_found"


def test_real_search_duplicate_shown_once(real):
    """The reviewer's case: two records of Anuradha Eye Hospital (Sangli) took
    cards 1 and 2 of a cataract search. Now one card per duplicate group."""
    a = rank.search(real, "cataract surgery", _res(16.0924357, 74.8364175))
    groups = [real.dup_canon.get(real.id_to_pos[c.facility_id], c.facility_id) for c in a.results]
    assert len(groups) == len(set(groups)), [c.name for c in a.results]
    shown = {c.facility_id for c in a.results}
    for c in a.results:
        assert not ({x["facility_id"] for x in c.sources["also_listed_as"]} & shown)


def test_real_ok_answers_show_an_inside_radius_card_first(real):
    """When the answer is ok, the first card is inside the radius."""
    n = 0
    for line in real.graded_lines:
        for lat, lon in list(POINTS.values()) + [(11.06, 79.54)]:
            a = rank.rank(real, line, _res(lat, lon))
            if a.status != "ok":
                continue
            assert a.results[0].distance_km <= a.radius_km + 0.5, (line, a.results[0].name)
            in_r = [c.distance_km <= a.radius_km + 0.5 for c in a.results]
            assert in_r == sorted(in_r, reverse=True)  # inside ones never below beyond ones
            g = {x["gate"]: x for x in a.how_we_decided["gates"]}
            assert f"of {g['recommendable']['n_distinct']} recommendable" in a.how_we_decided["headline"]
            n += 1
    assert n > 20
