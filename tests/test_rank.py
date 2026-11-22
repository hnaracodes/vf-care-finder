"""WP5 ranking engine tests.

Synthetic tests build a small world around one point and run under the shipped
ranker (score spec v1-final, D048) and the two legacy modes kept for the v0
against v1 comparison (the v0 additive score and probability mode). Tests of
v0-only behaviour build their index with ``rank_version="v0"``; the v1 rules
(windows, allowance, further-away line, labels) are tested at the end of the
synthetic section. Real-data tests skip when the shared data folder is absent.
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


@pytest.fixture(params=["v1", "v0", "probability"])
def idx(request):
    fac, grades, facts, flags, dups = _frames()
    sp = _prob_table() if request.param == "probability" else None
    version = {"v1": "v1", "v0": "v0", "probability": None}[request.param]
    return rank.build_index(fac, grades, facts=facts, flags=flags, duplicates=dups, service_probability=sp,
                            post_offices=_po(), rank_version=version)


def _legacy(i) -> bool:
    return i.rank_version == "v0"


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
    if not _legacy(idx):  # v1 has no score: gates and an order rule
        assert a.results and all(c.score is None and c.recompute_score() is None for c in a.results)
        assert all(c.parts["combine"] == "v1" and c.parts["penalties"] == [] for c in a.results)
        return
    for c in a.results:
        assert c.recompute_score() == pytest.approx(c.score, abs=1e-12)
    pen = [c for c in a.results if c.parts.get("penalties")]
    if idx.is_probability(LINE):
        assert all(c.parts["combine"] == "product" for c in a.results)
    else:
        assert pen and pen[0].facility_id == "F"
        assert pen[0].parts["penalties"][0]["value"] == S.PENALTIES["possible_merge"]


def _pair_world(rows, p=None, version="v0"):
    """A world of only ``rows`` (FAC layout), every record with the same source
    trust, so evidence and distance are the only things that differ. ``p``:
    facility_id -> p_offers for probability mode. Legacy ranker by default:
    these tests pin the v0 behaviour kept for the comparison."""
    fac, grades, facts, _, _ = _frames(rows)
    fac["source_confidence"] = 0.6
    sp = None
    if p is not None:
        sp = pd.DataFrame([dict(facility_id=f, service_line=LINE, p_offers=v, p_low=v - 0.05, p_high=v + 0.05,
                                top_factors=[], model_version="synthetic", n_labels_line=100) for f, v in p.items()])
    return rank.build_index(fac, grades, facts=facts, flags=None, duplicates=None, service_probability=sp,
                            rank_version=None if p is not None else version)


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
    if _legacy(idx):
        assert "no facility in this dataset shows reliable evidence" in a.how_we_decided["headline"]
        assert a.beyond_radius.distance_km > S.RADII_KM[LINE]
    else:
        h = a.how_we_decided["headline"]
        assert h.startswith("No reliable option nearby. No facility in this dataset shows clear evidence of dialysis "
                            "within about 1 hour's estimated drive")
        assert "not proof that no care exists" in h
        assert a.further_away and a.further_away[0].facility_id == a.beyond_radius.facility_id
        assert a.beyond_radius.minutes > S.WINDOWS[LINE].minutes and a.beyond_radius.inside_window is False
    assert a.beyond_radius is not None
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
    if not _legacy(idx):
        assert m.distance_label == "position approximate" and m.position_approximate
        assert b.distance_label == "1.0 km straight-line" and not b.position_approximate


def test_ambiguous_and_not_found(idx):
    a = rank.rank(idx, LINE, {"status": "ambiguous", "candidates": [{"label": "X, Bihar"}, {"label": "X, Maharashtra"}]})
    assert a.status == "needs_location_choice" and len(a.location["candidates"]) == 2 and not a.results
    b = rank.rank(idx, LINE, {"status": "not_found", "how": "No such place."})
    assert b.status == "location_not_found"


def test_score_status_and_json(idx):
    a = rank.rank(idx, LINE, ORIGIN)
    d = json.loads(a.to_json())
    if _legacy(idx):
        want = "calibrated" if idx.is_probability(LINE) else "provisional"
        assert d["how_we_decided"]["score_status"] == want
    else:
        assert d["how_we_decided"]["score_status"].startswith("v1-final") and d["rank_version"] == "v1"
        for c in d["results"]:
            assert {"minutes", "minutes_low", "minutes_high", "minutes_label", "distance_label",
                    "position_approximate", "window_min", "inside_window"} <= set(c)
        assert {"further_away", "window", "rank_version"} <= set(d)
    assert [g["gate"] for g in d["how_we_decided"]["gates"]] == ["usable location", "within reach", "recommendable", "within radius"]
    for c in d["results"]:
        assert set(c) >= {"facility_id", "name", "distance_km", "grade_label", "confidence", "parts", "evidence",
                          "missing", "sources", "why_here", "could_be_wrong"}
        # v1 never shows a confidence level (D048 (6)); the field stays, empty, for compatibility
        assert c["confidence"] in ("high", "medium", "low") if _legacy(idx) else c["confidence"] is None
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
    i = rank.build_index(fac, grades, facts=facts, flags=flags.iloc[0:0], duplicates=None, rank_version="v0")
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
        assert c.confidence == ("low" if _legacy(idx) else None) and c.grade_label == "keyword match, no service recipe"
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
    key = "recommendable" if _legacy(idx) else "within radius"  # v1 counts the facilities inside the window
    assert f"of {g[key]['n_distinct']} recommendable" in a.how_we_decided["headline"]


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
    if not _legacy(idx):
        # 70 km is more than a quarter of the window's ~48 km: a range, and where you start matters (D048)
        for c in a.results:
            # Testpur (140 km across) is wider than the window reaches: no multi-hour range, an "unclear" line
            assert "drive time unclear: Testpur spans about 140 km" in c.minutes_label
            assert a.window["origin_too_loose"] and a.how_we_decided["headline"].startswith("Uncertain: Testpur")
            assert c.minutes_low < c.minutes < c.minutes_high
            assert c.distance_label.startswith(("about", "under")) or c.distance_label == "position approximate"
        assert any("Testpur spans about 140 km" in n and "depends on where you start" in n
                   for n in a.how_we_decided["notes"])
        assert all("depends on" not in c.minutes_label for c in fine.results)


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
# score spec v1-final (D048): windows, allowance, further-away line, labels
# ---------------------------------------------------------------------------

from vf import travel as T  # noqa: E402


def _km_for(minutes: float, factor: float = 1.0) -> float:
    """Straight-line km whose estimate is ``minutes`` (no state: factor 1.00)."""
    return (minutes / (T.A_MIN * factor)) ** (1 / T.B_EXP)


def _v1_world(rows, places=None, extra_lines=()):
    fac, grades, facts, _, _ = _frames(rows)
    if extra_lines:
        grades = pd.concat([grades] + [grades.assign(service_line=l) for l in extra_lines], ignore_index=True)
    return rank.build_index(fac, grades, facts=facts, flags=None, duplicates=None, places=places, rank_version="v1")


def _row(fid, minutes, grade, strength="moderate", cc="ok"):
    return (fid, _km_for(minutes), grade, strength, 3, None, cc, "hospital")


def test_v1_nearest_first_and_the_allowance():
    # a corroborated record 10 minutes further comes first (inside the 15-minute allowance) ...
    a = rank.rank(_v1_world([_row("S", 5, 2), _row("C", 15, 3)]), LINE, ORIGIN)
    assert [c.facility_id for c in a.results] == ["C", "S"]
    assert "evidence allowance" in a.results[0].why_here and a.results[0].parts["allowance_applies"]
    # ... one 20 minutes further does not
    a = rank.rank(_v1_world([_row("S", 5, 2), _row("C", 25, 3)]), LINE, ORIGIN)
    assert [c.facility_id for c in a.results] == ["S", "C"]
    assert "does not move the corroborated record ahead" in a.results[0].why_here
    # "at most 15 minutes further": exactly at the allowance the corroborated record wins
    a = rank.rank(_v1_world([_row("S", 5, 2), _row("C", 20, 3)]), LINE, ORIGIN)
    assert [c.facility_id for c in a.results] == ["C", "S"]
    # thin or strong evidence, trust and warnings do not reorder: two supported records go by minutes
    a = rank.rank(_v1_world([_row("T", 9, 2, "thin"), _row("G", 12, 2, "strong")]), LINE, ORIGIN)
    assert [c.facility_id for c in a.results] == ["T", "G"]
    # allowance 0 is plain nearest first
    a = rank.rank(_v1_world([_row("S", 5, 2), _row("C", 15, 3)]), LINE, ORIGIN, allowance=0)
    assert [c.facility_id for c in a.results] == ["S", "C"]


def test_v1_nearest_recommendable_is_always_shown():
    """D048 Order: when three corroborated records within the allowance push the nearest
    supported record past k, it takes the last place, and says why."""
    rows = [_row("N", 3, 2), _row("C1", 6, 3), _row("C2", 7, 3), _row("C3", 9, 3)]
    a = rank.rank(_v1_world(rows), LINE, ORIGIN)
    assert [c.facility_id for c in a.results] == ["C1", "C2", "N"]
    n = a.results[-1]
    assert n.nearest_in_window and not any(c.nearest_in_window for c in a.results[:-1])
    assert n.why_here.startswith("The nearest recommendable facility") and "always shown" in n.why_here
    assert "The last card is the nearest one, always shown." in a.how_we_decided["headline"]
    assert "always shown" in a.how_we_decided["order_rule"]
    # the card above the nearest compares with it, in the minutes the cards show
    assert "evidence allowance" in a.results[1].why_here and "Hospital N" in a.results[1].why_here
    # k = 1 asks for the top pick alone: the allowance's pick, nothing reserved
    one = rank.rank(_v1_world(rows), LINE, ORIGIN, k=1)
    assert [c.facility_id for c in one.results] == ["C1"]
    # k = 2: the nearest takes the second place
    two = rank.rank(_v1_world(rows), LINE, ORIGIN, k=2)
    assert [c.facility_id for c in two.results] == ["C1", "N"]
    # when the nearest is already shown, nothing changes
    a = rank.rank(_v1_world([_row("N", 3, 2), _row("C1", 6, 3), _row("S", 9, 2)]), LINE, ORIGIN)
    assert [c.facility_id for c in a.results] == ["C1", "N", "S"] and a.results[1].nearest_in_window
    assert "always shown" not in a.how_we_decided["headline"]
    p = rank.pick_v1(_v1_world(rows), LINE, LAT0, LON0)
    i = _v1_world(rows)
    assert [i.fac.facility_id.iat[x] for x in p["shown3"]] == ["C1", "C2", "N"] and p["nearest_reserved"]


def test_v1_flagged_record_gets_no_allowance():
    """A corroborated record with a possible-merge or location-conflict warning keeps its
    minutes: it is not moved ahead of a nearer supported record (fix round, Nov 22)."""
    rows = [_row("S", 5, 2), _row("C", 15, 3)]
    fac, grades, facts, _, _ = _frames(rows)
    for flag in ("possible_merge", "location_conflict"):
        flags = pd.DataFrame([dict(facility_id="C", flag=flag, service_line=LINE, severity="minor", signal="t",
                                   reason="The name joins two facilities.", quote=None, check_version="t")])
        i = rank.build_index(fac, grades, facts=facts, flags=flags, duplicates=None, rank_version="v1")
        a = rank.rank(i, LINE, ORIGIN)
        assert [c.facility_id for c in a.results] == ["S", "C"], flag
        c = a.results[1]
        assert not c.parts["allowance_applies"] and c.parts["allowance_denied_by"] == [flag.replace("_", " ")]
        assert "does not apply to Hospital C" in a.results[0].why_here
    # another minor warning does not deny it
    flags = pd.DataFrame([dict(facility_id="C", flag="implausible_claim", service_line=LINE, severity="minor",
                               signal="t", reason="x", quote=None, check_version="t")])
    i = rank.build_index(fac, grades, facts=facts, flags=flags, duplicates=None, rank_version="v1")
    assert [c.facility_id for c in rank.rank(i, LINE, ORIGIN).results] == ["C", "S"]


def test_v1_candidate_cap_exception_is_said():
    """The 50-candidate cap stays (the why-not strip mirrors it), and when a corroborated
    record beyond it would have come first under the allowance, How we decided says so."""
    n = S.MAX_CANDIDATES + 5
    rows = [_row(f"S{j:02d}", 5 + 0.01 * j, 2) for j in range(n)] + [_row("C", 12, 3)]
    a = rank.rank(_v1_world(rows), LINE, ORIGIN)
    assert a.results[0].facility_id == "S00"
    assert a.how_we_decided["cap_exception"] == ["C"]
    assert any(f"Only the {S.MAX_CANDIDATES} nearest" in x for x in a.how_we_decided["notes"])
    a = rank.rank(_v1_world(rows[:3] + rows[-1:]), LINE, ORIGIN)
    assert a.results[0].facility_id == "C" and "cap_exception" not in a.how_we_decided


def test_v1_too_loose_origin_says_uncertain_and_compares_km():
    """A place wider than the window reaches: the headline says the answer is uncertain,
    the cards say the drive time is unclear, and why_here compares km from the point."""
    rows = [_row("S", 5, 2), _row("C", 15, 3), _row("T", 30, 2)]
    a = rank.rank(_v1_world(rows), LINE, dict(ORIGIN, precision_km=30.0))  # 60 km across > ~48 km
    assert a.window["origin_too_loose"] and a.window["origin_precision_km"] == 30.0
    assert a.how_we_decided["headline"].startswith("Uncertain: Test point spans about 60 km")
    assert all(c.minutes_label.startswith("drive time unclear") for c in a.results)
    assert all(" min " not in c.why_here for c in a.results[:-1]) and "from the point" in a.results[0].why_here
    tight = rank.rank(_v1_world(rows), LINE, dict(ORIGIN, precision_km=20.0))  # 40 km across: a range
    assert not tight.window["origin_too_loose"] and "depends on where" in tight.results[0].minutes_label


def test_v1_why_here_difference_matches_the_shown_minutes():
    rows = [_row("S", 6.3, 2), _row("C", 12.6, 3)]
    a = rank.rank(_v1_world(rows), LINE, ORIGIN)
    w = a.results[0].why_here
    assert "about 15 min against 6 min" in w and "only about 9 min further" in w, w
    rows = [_row("S", 53.0, 2), _row("C", 54.0, 3)]
    w = rank.rank(_v1_world(rows), LINE, ORIGIN).results[0].why_here
    assert "only slightly further (both about 55 min)" in w, w


def _strings(obj, skip=("quote", "name", "text", "requirement", "requirements")):
    """Every string value in an answer dict, except quoted record text and names."""
    if isinstance(obj, dict):
        for k_, v in obj.items():
            if k_ not in skip:
                yield from _strings(v, skip)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _strings(v, skip)
    elif isinstance(obj, str):
        yield obj


V1_BANNED = ("confiden", "validated", "likely", "calibrat", "chance", "\u2014")


def test_v1_answers_and_search_have_no_banned_wording():
    """D048 (6) and the spec's card rules: no "confidence", "validated", "likely",
    "calibrated" or "chance" in any shown text under v1, ranked or keyword."""
    fac, grades, facts, flags, dups = _frames()
    i = rank.build_index(fac, grades, facts=facts, flags=flags, duplicates=dups, rank_version="v1")
    answers = [rank.rank(i, LINE, ORIGIN), rank.rank(i, LINE, dict(ORIGIN, precision_km=70.0)),
               rank.search(i, "dialysis unit machines", ORIGIN), rank.search(i, "zzz qqq", ORIGIN),
               rank.rank(i, LINE, dict(ORIGIN, lat=north(-400)))]
    for a in answers:
        assert all(c.confidence is None for c in a.results + a.unverified_nearby + a.further_away)
        for t in _strings(a.to_dict()):
            low = t.lower()
            for b in V1_BANNED:
                assert b not in low, (b, t)


def test_v1_planned_care_has_the_30_minute_allowance():
    i = _v1_world([_row("S", 5, 2), _row("C", 30, 3)], extra_lines=("mri",))
    assert S.WINDOWS["mri"].allowance == 30 and S.WINDOWS["dialysis"].allowance == 15
    assert [c.facility_id for c in rank.rank(i, "mri", ORIGIN).results] == ["C", "S"]
    assert [c.facility_id for c in rank.rank(i, LINE, ORIGIN).results] == ["S", "C"]
    assert rank.rank(i, "mri", ORIGIN).how_we_decided["allowance_min"] == 30


def test_v1_beyond_the_window_is_never_a_top_card():
    W = S.WINDOWS[LINE].minutes
    i = _v1_world([_row("IN", W - 5, 2), _row("OUT", W + 20, 3), _row("FAR", 4 * W, 3)])
    a = rank.rank(i, LINE, ORIGIN)
    assert a.status == "ok" and [c.facility_id for c in a.results] == ["IN"]
    assert [c.facility_id for c in a.further_away] == ["OUT"]  # FAR is beyond 3 x the window
    out = a.further_away[0]
    assert out.inside_window is False and "Beyond the 60-minute window" in out.location_note
    assert out.why_here.startswith("Further away:")
    assert "1 more with evidence is further away" in a.how_we_decided["headline"]
    # three inside: no further-away line
    i = _v1_world([_row("A1", 5, 2), _row("A2", 10, 2), _row("A3", 15, 2), _row("OUT", W + 5, 3)])
    a = rank.rank(i, LINE, ORIGIN)
    assert [c.facility_id for c in a.results] == ["A1", "A2", "A3"] and a.further_away == []


def test_v1_abstention_wording_and_further_away():
    W = S.WINDOWS["emoc"].minutes
    i = _v1_world([_row("OUT", W + 30, 3), ("CL", _km_for(10), 1, "thin", 3, None, "ok", "hospital")],
                  extra_lines=("emoc",))
    a = rank.rank(i, "emoc", dict(ORIGIN, label="Testpur, Test district, Teststate"))
    h = a.how_we_decided["headline"]
    assert a.status == "no_reliable_option" and a.results == []
    assert ("No facility in this dataset shows clear evidence of emergency C-section and high-risk delivery "
            "within about 2 hours' estimated drive (free-flow) of Testpur.") in h
    assert "This is a gap in our data, not proof that no care exists." in h
    assert a.beyond_radius.facility_id == "OUT" == a.further_away[0].facility_id
    assert [c.facility_id for c in a.unverified_nearby] == ["CL"]  # the claim inside the window, to call


def test_v1_card_minutes_and_labels():
    i = _v1_world([_row("B", 0, 2), ("N", 1.0, 2, "moderate", 3, None, "ok", "hospital")])
    a = rank.rank(i, LINE, ORIGIN)
    n = next(c for c in a.results if c.facility_id == "N")
    t = T.travel_minutes(1.0, factor=1.0)
    assert n.minutes == pytest.approx(t, abs=0.05)
    assert (n.minutes_low, n.minutes_high) == pytest.approx((t * math.exp(-0.52), t * math.exp(0.52)), abs=0.05)
    assert n.minutes_label == "about 2 min estimated drive, free-flow; real trips take longer"
    assert n.distance_label == "1.0 km straight-line" and n.window_min == 60 and n.inside_window
    b = next(c for c in a.results if c.facility_id == "B")
    assert b.distance_label != "0.0 km" and "0.0 km" not in b.distance_label


def test_v1_shared_point_is_position_approximate():
    rows = [("P1", 5.0, 2, "moderate", 3, None, "ok", "hospital"), ("P2", 5.0, 2, "moderate", 3, None, "ok", "hospital"),
            ("Q", 7.0, 2, "moderate", 3, None, "ok", "hospital")]
    a = rank.rank(_v1_world(rows), LINE, ORIGIN)
    lab = {c.facility_id: c.distance_label for c in a.results}
    assert lab["P1"] == lab["P2"] == "position approximate" and lab["Q"] == "7.0 km straight-line"
    assert all("position is approximate" in c.minutes_label for c in a.results if c.facility_id != "Q")


def test_v1_one_fact_covering_two_requirements_is_merged():
    fac, grades, facts, _, _ = _frames([("A", 3.0, 3, "strong", 3, None, "ok", "hospital")])
    it = grades.at[0, "evidence_items"]
    grades.at[0, "evidence_items"] = [it[0], dict(it[0], requirement="blood bank or transfusion")]
    i = rank.build_index(fac, grades, facts=facts, rank_version="v1")
    ev = rank.rank(i, LINE, ORIGIN).results[0].evidence
    assert len(ev) == 1
    assert ev[0]["requirement"] == "dialysis; blood bank or transfusion"
    assert ev[0]["note"] == "one fact covers both requirements"
    assert ev[0]["quote"] == it[0]["text"] and ev[0]["fact_id"] == it[0]["fact_id"]


def test_v1_terrain_factor_from_state_or_nearest_pin():
    km = 30.0
    rows = [("H", km, 2, "moderate", 3, None, "ok", "hospital")]
    flat = rank.rank(_v1_world(rows), LINE, ORIGIN).results[0]
    hill = rank.rank(_v1_world(rows), LINE, dict(ORIGIN, state="Arunachal Pradesh"))
    assert flat.minutes == pytest.approx(T.travel_minutes(km, factor=1.0), abs=0.05)
    assert hill.status == "no_reliable_option"  # 3.08 x: about 120 minutes, beyond the 60-minute window
    assert hill.further_away[0].minutes == pytest.approx(T.travel_minutes(km, factor=3.08), abs=0.05)
    assert hill.window["factor"] == 3.08 and "Arunachal Pradesh" in hill.window["factor_text"]
    # no state on the resolution: the nearest PIN point's state is used
    places = pd.DataFrame([dict(place_id="pin:1", kind="pin", name="1", name_norm="1", state="ARUNACHAL PRADESH",
                                district="X", pin="1", lat=LAT0, lon=LON0, precision_km=2.0, n_offices=1, note=None)])
    i = _v1_world(rows, places=places)
    assert i.state_at(LAT0, LON0) == "ARUNACHAL PRADESH"
    assert rank.rank(i, LINE, ORIGIN).window["factor"] == 3.08
    # the sensitivity switch: terrain off is factor 1.00
    assert rank.rank(i, LINE, ORIGIN, terrain=False).results[0].minutes == pytest.approx(flat.minutes)


def test_v1_islands_fall_back_to_the_km_radius():
    rows = [("I", 20.0, 2, "moderate", 3, None, "ok", "hospital"), ("J", 70.0, 3, "strong", 3, None, "ok", "hospital")]
    a = rank.rank(_v1_world(rows), LINE, dict(ORIGIN, state="Lakshadweep"))
    assert a.status == "ok" and [c.facility_id for c in a.results] == ["I"]  # J is beyond the 50 km radius
    c = a.results[0]
    assert c.minutes is None and "sea travel not modelled" in c.minutes_label
    assert a.radius_km == S.RADII_KM[LINE] and a.window["sea_travel_not_modelled"]
    g = {x["gate"]: x for x in a.how_we_decided["gates"]}
    assert "sea travel is not modelled" in g["within radius"]["rule"]


def test_v1_how_we_decided_says_what_every_number_is():
    a = rank.rank(_v1_world([_row("S", 5, 2)]), LINE, ORIGIN)
    h = a.how_we_decided
    assert h["mode"] == "v1" and h["weights"] is None and h["penalties"] is None
    assert h["window"]["minutes"] == 60 and h["window"]["kind"] == "design choice" and "DOPPS" in h["window"]["basis"]
    assert set(h["windows"]) == set(S.SERVICE_LINES)
    assert h["windows"]["emoc"]["kind"] == "cited" and "Holmer" in h["windows"]["emoc"]["basis"]
    assert h["allowance_min"] == 15 and "15 minutes further" in h["order_rule"]
    assert "0.26" in h["travel_model"]["text"] and h["travel_model"]["n_trips"] == 934
    assert h["probability"].startswith("Nothing here is a probability")
    assert [g["id"] for g in h["gates"] if "id" in g] == ["G1", "G2", "G3"]
    text = a.to_json().lower()
    for banned in ("likely", "calibrated", "validated", "chance", "—"):
        assert banned not in text, banned


def test_v1_gate_masks_match_the_answer(idx):
    if _legacy(idx):
        pytest.skip("v1 only")
    a = rank.rank(idx, LINE, ORIGIN)
    d = rank._distances(idx, LAT0, LON0)
    m = rank.gate_masks(idx, LINE, d, ORIGIN)
    g = {x["gate"]: x["n"] for x in a.how_we_decided["gates"]}
    assert g == {"usable location": int(m["usable"].sum()), "within reach": int(m["in_reach"].sum()),
                 "recommendable": int(m["rec"].sum()), "within radius": int(len(m["in_R"]))}
    assert all(idx.id_to_pos[c.facility_id] in m["scored"] for c in a.results)


def test_v0_stays_available_behind_the_flag(idx):
    if _legacy(idx):
        pytest.skip("v1 index")
    a = rank.rank(idx, LINE, ORIGIN, version="v0")
    assert a.rank_version == "v0" and a.how_we_decided["mode"] == "v0"
    assert all(c.parts["combine"] == "sum" and c.score is not None for c in a.results)
    with pytest.raises(ValueError):
        rank.rank(idx, LINE, ORIGIN, version="v9")


def test_v1_pick_matches_rank():
    rows = [_row("S", 5, 2), _row("C", 15, 3), _row("Z", 70, 3)]
    i = _v1_world(rows)
    p = rank.pick_v1(i, LINE, LAT0, LON0)
    a = rank.rank(i, LINE, ORIGIN)
    assert i.fac.facility_id.iat[p["top"]] == a.results[0].facility_id == "C"
    assert i.fac.facility_id.iat[p["nearest"]] == "S" and p["n_in_window"] == 2
    assert rank.pick_v1(i, LINE, LAT0, LON0, window_scale=0.05)["top"] is None


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
            cards = a.results + a.further_away + ([a.beyond_radius] if a.beyond_radius else [])
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
                assert c.score is None and c.parts["combine"] == "v1"
                assert "0.0 km" not in c.distance_label
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
    assert real.recipe_version == "v0.3", real.grades_status  # D048: the v1-final ranker gates on recipe v0.3
    assert real.grades_source == "gold/capability_grades_v03.parquet" and real.rank_version == "v1"
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
        assert c.confidence is None and c.confidence_reason and c.evidence_strength in ("strong", "moderate", "thin")
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


def test_real_ok_answers_only_show_cards_inside_the_window(real):
    """v1: every result card is inside its window, nearest first except where the allowance moved a
    corroborated record up by at most the allowance; further-away cards are all beyond it."""
    n = 0
    for line in real.graded_lines:
        W, A = S.WINDOWS[line].minutes, S.WINDOWS[line].allowance
        for lat, lon in list(POINTS.values()) + [(11.06, 79.54)]:
            a = rank.rank(real, line, _res(lat, lon))
            if a.status != "ok":
                assert not a.results
                continue
            assert all(c.inside_window and c.minutes <= W + 0.05 for c in a.results), line
            assert all(not c.inside_window for c in a.further_away)
            # nearest first under the allowance; a reserved nearest card (always shown) comes last
            keys = [c.parts["order_minutes"] for c in a.results if not (c.nearest_in_window and "always shown" in c.why_here)]
            assert keys == sorted(keys)
            assert sum(bool(c.nearest_in_window) for c in a.results) == 1
            assert min(c.minutes for c in a.results) >= a.results[0].minutes - A - 0.05
            g = {x["gate"]: x for x in a.how_we_decided["gates"]}
            assert f"of {g['within radius']['n_distinct']} recommendable" in a.how_we_decided["headline"]
            n += 1
    assert n > 20


def test_real_ok_answers_show_an_inside_radius_card_first(real):
    """Legacy v0 (kept for the comparison): when the answer is ok, the first card is inside the radius."""
    n = 0
    for line in real.graded_lines:
        for lat, lon in list(POINTS.values()) + [(11.06, 79.54)]:
            a = rank.rank(real, line, _res(lat, lon), version="v0")
            if a.status != "ok":
                continue
            assert a.results[0].distance_km <= a.radius_km + 0.5, (line, a.results[0].name)
            in_r = [c.distance_km <= a.radius_km + 0.5 for c in a.results]
            assert in_r == sorted(in_r, reverse=True)  # inside ones never below beyond ones
            g = {x["gate"]: x for x in a.how_we_decided["gates"]}
            assert f"of {g['recommendable']['n_distinct']} recommendable" in a.how_we_decided["headline"]
            n += 1
    assert n > 20
