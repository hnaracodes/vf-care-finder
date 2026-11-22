"""Tests for vf.public_list (D044 item 5: the government list beside our records)."""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from vf import paths, public_list as pl

AS_OF = date(2026, 11, 21)
NASHIK = "6720cccc-3f42-4bf6-9bc7-33a16492741c"
NASHIK_TEXT = ("Government list (LaQshya, NHSRC, 8 Apr 2026, page 35 row 1109): maternity operating theatre "
               "certified, valid to 31 Dec 2026. Outside data, not from this record.")


def cert(sno, page, dept, status="certified", valid="2026-12-31"):
    return {"sno": sno, "page": page, "department": dept, "certification_status": status,
            "result_date": "2026-01-01", "validity_date": valid}


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------

def test_normalise_expands_abbreviations():
    assert pl.normalise_name("DH Nashik") == "district hospital nashik"
    assert pl.normalise_name("Govt MC & H Salem") == "government medical college and h salem"
    assert pl.normalise_name("SDH Karad") == "sub district hospital karad"
    # PIN codes dropped, other numbers kept (Chandigarh's sector hospitals differ only by number)
    assert "422001" not in pl.normalise_name("District Hospital, Nashik 422001")
    assert pl.core_name("Civil Hospital-45") == "45"
    assert pl.core_name("Civil Hospital-22") != pl.core_name("Civil Hospital Sector 45")


def test_core_name_drops_generic_words():
    assert pl.core_name("District Hospital, Nashik") == "nashik"
    assert pl.core_name("DH Nashik") == "nashik"
    assert pl.core_name("Government Medical College and Hospital, Theni") == "theni"
    assert pl.core_name(None) == ""


def test_unit_type():
    assert pl.unit_type("District Hospital, Nashik") == "DH"
    assert pl.unit_type("Durgapur Sub Divisional Hospital") == "SDH"
    assert pl.unit_type("Taluk Headquarters Hospital Punalur") == "SDH"
    assert pl.unit_type("Murshidabad Medical College & Hospital") == "MC"
    assert pl.unit_type("Community Health Centre Murbad") == "CHC"
    assert pl.unit_type("SSG Hospital") is None


def test_type_compatible_treats_missing_as_unknown():
    assert pl.type_compatible("DH", "DH") is True
    assert pl.type_compatible("DH", "SDH") is True
    assert pl.type_compatible("CHC", "MC") is False
    assert pl.type_compatible("DH", None) is None
    assert pl.type_compatible("DH", float("nan")) is None


def test_name_scores_strict_core_catches_extra_words():
    s = pl.name_scores("Taluk Hospital Sullia", "KVG Medical College and Hospital, Sullia")
    assert s["core_set"] == 100 and s["core_sort"] < 90
    s = pl.name_scores("District Hospital nashik", "District Hospital, Nashik")
    assert s["core_set"] == 100 and s["core_sort"] == 100


# ---------------------------------------------------------------------------
# The line
# ---------------------------------------------------------------------------

def test_line_text_matches_the_cited_format():
    assert pl.line_text(cert(1109, 35, "Maternity OT"), AS_OF) == NASHIK_TEXT


def test_line_text_expired_conditional_and_unstated():
    t = pl.line_text(cert(1108, 35, "Labour Room", "conditionally_certified", "2026-07-01"), AS_OF)
    # D051: a lapsed certificate is worded against the list, never as an expired facility
    assert ("labour room conditionally certified; certificate on the 8 Apr 2026 list, valid to 1 Jul 2026, "
            "not renewed on that list.") in t
    assert "expired" not in t
    t = pl.line_text(cert(2137, 68, None, valid="2029-02-02"), AS_OF)
    assert "unit (department not stated) certified, valid to 2 Feb 2029." in t
    for x in (t, NASHIK_TEXT):
        assert x.endswith("Outside data, not from this record.")
        assert chr(0x2014) not in x  # no em-dashes


def _links(review="correct", certs=None, fid="F1"):
    return pd.DataFrame([{
        "facility_uid": "U1", "facility_id": fid, "lq_name": "DH Example",
        "certs": certs if certs is not None else [
            cert(10, 1, "Labour Room", "conditionally_certified", "2026-07-01"),
            cert(11, 1, "Maternity OT", "certified", "2026-12-31"),
        ],
        "review": review,
    }])


def test_line_prefers_current_maternity_ot():
    p = pl.PublicList(_links())
    line = p.line("F1", as_of=AS_OF)
    assert line["row"] == 11 and line["page"] == 1 and line["department"] == "Maternity OT"
    assert line["current"] is True and line["validity"] == "2026-12-31"
    assert line["retrieved"] == "2026-11-21" and line["listed_as"] == "DH Example"
    assert set(["text", "source", "page", "row", "department", "validity", "retrieved"]) <= set(line)
    assert [x["row"] for x in p.lines("F1", as_of=AS_OF)] == [11, 10]


def test_expired_only_is_worded_or_dropped():
    p = pl.PublicList(_links(certs=[cert(10, 1, "Labour Room", valid="2025-02-24")]))
    line = p.line("F1", as_of=AS_OF)
    assert line["current"] is False and "valid to 24 Feb 2025, not renewed on that list" in line["text"]
    assert p.line("F1", as_of=AS_OF, current_only=True) is None


@pytest.mark.parametrize("review", ["wrong", "unsure", "unreviewed"])
def test_only_correct_links_produce_lines(review):
    p = pl.PublicList(_links(review=review))
    assert p.line("F1", as_of=AS_OF) is None
    assert p.facility_ids() == set()


def test_unknown_facility_gives_none():
    assert pl.PublicList(_links()).line("nope", as_of=AS_OF) is None


def test_annotate_tags_every_card_section():
    p = pl.PublicList(_links())
    ans = {"results": [{"facility_id": "F1"}, {"facility_id": "F2"}],
           "unverified_nearby": [{"facility_id": "F1"}],
           "beyond_radius": {"facility_id": "F2"}}
    out = p.annotate(ans, as_of=AS_OF)
    assert out is ans
    assert ans["results"][0]["public_list"]["row"] == 11
    assert ans["results"][1]["public_list"] is None
    assert ans["unverified_nearby"][0]["public_list"]["row"] == 11
    assert ans["beyond_radius"]["public_list"] is None
    assert p.annotate({"results": [], "beyond_radius": None}) == {"results": [], "beyond_radius": None}


def test_load_missing_file_is_empty(tmp_path):
    p = pl.load(tmp_path / "absent.parquet")
    assert p.line("anything") is None
    p = pl.load(tmp_path)  # a folder without the file, like a bundle without it
    assert p.line("anything") is None


def test_apply_review_and_summarise():
    c = _links(review=None).drop(columns="review")
    rev = pd.DataFrame([{"facility_uid": "U1", "facility_id": "F1", "review": " Correct ", "review_reason": "x"}])
    out = pl.apply_review(c, rev)
    assert out["review"].tolist() == ["correct"]
    out = pl.apply_review(c, rev.assign(review="maybe"))
    assert out["review"].tolist() == ["unreviewed"]
    s = pl.summarise(pl.apply_review(c, rev), AS_OF)
    assert s["correct_records"] == 1 and s["records_with_current_maternity_ot"] == 1
    assert s["precision_correct_over_decided"] == 1.0
    empty = pl.summarise(pl.apply_review(c, rev.assign(review="wrong")), AS_OF)
    assert empty["correct_records"] == 0 and empty["records_with_current_maternity_ot"] == 0


# ---------------------------------------------------------------------------
# Candidates on synthetic data
# ---------------------------------------------------------------------------

def _lq_rows():
    base = dict(page=1, department="Maternity OT", certification_status="certified",
                result_date=pd.Timestamp("2026-01-01"), validity_date=pd.Timestamp("2026-12-31"))
    return pd.DataFrame([
        dict(sno=1, facility_uid="A", facility_name="DH Alpha", facility_type="DH", state_ut="S1",
             district_key="S1|Alpha", district_raw="Alpha", **base),
        dict(sno=2, facility_uid="B", facility_name="SDH Betaganj", facility_type="SDH", state_ut="S1",
             district_key="S1|Beta", district_raw="Beta", **base),
    ])


def _fac():
    return pd.DataFrame([
        dict(facility_id="f1", name="District Hospital, Alpha", operator_type="public", pin="100001", city_raw="Alpha"),
        dict(facility_id="f2", name="Alpha Fertility Clinic", operator_type="private", pin="100001", city_raw="Alpha"),
        dict(facility_id="f3", name="Betaganj Sub District Hospital", operator_type="public", pin="200001",
             city_raw="Betaganj"),
        dict(facility_id="f4", name="Alpha Homoeopathic Medical College", operator_type="private", pin="100001",
             city_raw="Alpha"),
    ])


def test_candidate_links_district_and_state_pass_without_osm_columns():
    pin_d = {"100001": {"S1|Alpha"}, "200001": {"S1|Gamma"}}  # f3's PIN sits in another district
    lq = _lq_rows()
    assert not any(c.startswith("osm_") for c in lq.columns)
    c = pl.candidate_links(lq, _fac(), pin_d)
    a = c[c.facility_uid == "A"]
    assert a.iloc[0]["facility_id"] == "f1" and a.iloc[0]["match_scope"] == "district"
    assert "f2" not in set(c.facility_id)  # private, no government name: not in the pool
    assert "f4" in set(a.facility_id)  # private medical college: in the pool, for review
    b = c[c.facility_uid == "B"]
    assert b["facility_id"].tolist() == ["f3"] and b.iloc[0]["match_scope"] == "state"
    assert c["type_compatible"].dtype == "boolean"
    assert c.iloc[0]["certs"][0]["sno"] == 1


def test_pin_district_keys_keeps_every_rank():
    pc = pd.DataFrame({"pin": ["1", "1"], "state_pin": ["X", "X"], "district_pin": ["A", "B"], "rank": [1, 2]})
    dc = pd.DataFrame({"state_pin": ["X", "X"], "district_pin": ["A", "B"], "district_key": ["X|A", "X|B"]})
    assert pl.pin_district_keys(pc, dc) == {"1": {"X|A", "X|B"}}


# ---------------------------------------------------------------------------
# Data checks (skip when the built files are absent)
# ---------------------------------------------------------------------------

LINKS = paths.silver_dir() / pl.LINKS_FILE
REVIEW = paths.labels_dir() / pl.REVIEW_FILE
needs_links = pytest.mark.skipif(not LINKS.exists(), reason="run scripts/wp13_laqshya_links.py first")


@needs_links
def test_built_links_are_reviewed_and_cite_real_list_rows():
    links = pd.read_parquet(LINKS)
    assert len(links) >= 60
    assert not any(c.startswith("osm_") for c in links.columns)
    assert links["review"].isin(pl.REVIEW_VALUES).all(), "every candidate is reviewed"
    rev = pd.read_csv(REVIEW, dtype=str, keep_default_na=False)
    assert len(rev) == len(links)
    assert (rev["review_reason"].str.len() > 0).all()
    lq = pd.read_parquet(paths.silver_dir() / "ext_laqshya.parquet").set_index("sno")
    for r in links[links.review.eq("correct")].itertuples():
        for c in r.certs:
            row = lq.loc[c["sno"]]
            assert int(row.page) == c["page"] and row.facility_uid == r.facility_uid
            assert pd.Timestamp(row.validity_date).date().isoformat() == c["validity_date"]


@needs_links
def test_nashik_end_to_end():
    p = pl.load()
    line = p.line(NASHIK, as_of=AS_OF)
    assert line is not None and line["text"] == NASHIK_TEXT
    assert (line["page"], line["row"], line["department"], line["validity"]) == (35, 1109, "Maternity OT",
                                                                                   "2026-12-31")
    grades = paths.gold_dir() / "capability_grades.parquet"
    if grades.exists():
        g = pd.read_parquet(grades)
        e = g[(g.facility_id == NASHIK) & (g.service_line == "emoc")].iloc[0]
        assert "csection" not in list(e.tags_present)  # the record never names a caesarean


@needs_links
def test_public_line_default_and_no_line_for_wrong_links():
    pl.default.cache_clear()
    assert pl.public_line(NASHIK, as_of=AS_OF)["row"] == 1109
    links = pd.read_parquet(LINKS)
    correct = set(links.loc[links.review.eq("correct"), "facility_id"])
    for fid in set(links.loc[~links.review.eq("correct"), "facility_id"]) - correct:
        assert pl.public_line(fid, as_of=AS_OF) is None


# ---------------------------------------------------------------------------
# v2: outside lists (D055)
# ---------------------------------------------------------------------------

LINE_KEYS = {"source_id", "source_name", "publisher", "list_date", "what", "status", "citation", "text"}
LANE_KEYS = {"source_id", "source_name", "name", "district", "state", "pin", "lat", "lon", "position_precision_km",
             "position_note", "minutes_label", "km", "what", "citation", "text"}
TAIL = "Outside data, not from this record."
LKO = (26.8497, 80.9440)  # PIN 226001, Lucknow


def _lane_row(source_id, line, name, lat, lon, prec=2.0, note="PIN area", uid=None, valid_to=None,
              what="licensed by AERB for radiotherapy"):
    uid = uid or f"{source_id}:{name}"
    return {"source_id": source_id, "list_uid": uid, "service_line": line, "name": name, "district": "LUCKNOW",
            "state": "UTTAR PRADESH", "pin": "226001", "lat": lat, "lon": lon, "position_precision_km": prec,
            "position_note": note, "what": what, "citation": f"cite {uid}", "locator": "page 1 row 1",
            "valid_to": valid_to, "group_key": uid}


def _outside(lane_rows=(), facts=(), lq=None):
    lane = pd.DataFrame(list(lane_rows), columns=pl._LANE_COLS)
    card = pd.DataFrame(list(facts), columns=pl._CARD_COLS)
    return pl.OutsideLists(card, lane, pl.PublicList(lq if lq is not None else _links(fid="F1")), {})


FACTS = [
    {"facility_id": "F1", "source_id": "nhp_bloodbank", "list_uid": "nhpbb-1", "what": "licensed blood bank",
     "citation": "NHP row 1", "locator": "row Sr No 1", "valid_to": "2017-01-08"},
    {"facility_id": "F2", "source_id": "aerb_rt", "list_uid": "aerb-1", "what": "licensed by AERB for radiotherapy",
     "citation": "AERB p1 r1", "locator": "page 1 row 1", "valid_to": None},
    {"facility_id": "F2", "source_id": "ncg", "list_uid": "ncg-1", "what": "member cancer centre of the National "
     "Cancer Grid", "citation": "NCG s1 r1", "locator": "section 'Uttar Pradesh' row 1", "valid_to": None},
]


def test_card_lines_shape_wording_and_service_filter(monkeypatch):
    monkeypatch.setenv("VF_OUTSIDE_CARD_SOURCES", "nhp_bloodbank")  # off by default (D057: third-party copy)
    o = _outside(facts=FACTS)
    emoc = o.lines_for("F1", "emoc", as_of=AS_OF)
    assert [x["source_id"] for x in emoc] == ["laqshya", "nhp_bloodbank"]
    for x in emoc:
        assert set(x) == LINE_KEYS and x["text"].endswith(TAIL) and chr(0x2014) not in x["text"]
        assert x["status"] in ("current", "lapsed", "undated")
    lq, bb = emoc
    assert lq["status"] == "current" and lq["text"].startswith("Government list (LaQshya, NHSRC, 8 Apr 2026")
    assert bb["status"] == "lapsed"
    assert bb["text"] == ("Government directory (National Health Portal, data.gov.in file published 10 Aug 2017, "
                          "read from a third-party copy, row Sr No 1): licensed blood bank, licence renewal date "
                          "on the list 8 Jan 2017, not renewed on that list. " + TAIL)
    # blood banks are card facts for emoc, cardiac surgery and trauma only; oncology shows AERB and NCG
    assert o.lines_for("F1", "oncology", as_of=AS_OF) == []
    assert [x["source_id"] for x in o.lines_for("F1", "trauma", as_of=AS_OF)] == ["nhp_bloodbank"]
    onc = o.lines_for("F2", "oncology", as_of=AS_OF)
    assert [x["source_id"] for x in onc] == ["aerb_rt", "ncg"]
    assert onc[0]["status"] == "undated" and "the list gives no licence dates" in onc[0]["text"]
    assert "membership is not an accreditation of a service" in onc[1]["text"]
    # a search (no service line) shows every card fact except blood banks
    assert [x["source_id"] for x in o.lines_for("F1", None, as_of=AS_OF)] == ["laqshya"]


def test_lapsed_laqshya_line_uses_d051_wording():
    lq = _links(certs=[cert(10, 1, "Maternity OT", valid="2025-07-05")])
    o = _outside(lq=lq)
    (x,) = o.lines_for("F1", "emoc", as_of=AS_OF)
    assert x["status"] == "lapsed"
    assert ("maternity operating theatre certified; certificate on the 8 Apr 2026 list, valid to 5 Jul 2025, "
            "not renewed on that list.") in x["text"]
    assert "expired" not in x["text"].lower()
    # the same wording in a lane entry
    row = _lane_row("laqshya", "emoc", "CHC Example", LKO[0] + 0.05, LKO[1], prec=20.0, note="district centre",
                    valid_to="2025-07-05", what="maternity operating theatre certified")
    (e,) = _outside([row]).nearby("emoc", *LKO, "UTTAR PRADESH", as_of=AS_OF)
    assert "certificate on the 8 Apr 2026 list, valid to 5 Jul 2025, not renewed on that list" in e["text"]
    assert "position approximate (district centre)" in e["text"] and e["text"].endswith(TAIL)


def test_lane_shape_window_filter_and_order():
    rows = [_lane_row("aerb_rt", "oncology", f"Far Cancer Centre {i}", LKO[0] + d, LKO[1])
            for i, d in enumerate((0.30, 0.10, 0.20, 0.90, 3.5))]  # about 33, 11, 22, 100, 390 km north
    o = _outside(rows)
    lane = o.nearby("oncology", *LKO, "UTTAR PRADESH", as_of=AS_OF)
    assert len(lane) == 3 and [e["name"] for e in lane] == ["Far Cancer Centre 1", "Far Cancer Centre 2",
                                                             "Far Cancer Centre 0"]
    for e in lane:
        assert set(e) == LANE_KEYS and e["text"].endswith(TAIL) and "estimated drive" in e["minutes_label"]
        assert "Not linked to any record in this dataset." in e["text"]
        assert "position approximate (PIN area)" in e["text"]
    # the window: dialysis is 60 minutes, so only the 11 km entry fits; nothing outside it is ever shown
    rows_d = [dict(r, service_line="dialysis") for r in rows]
    from vf import travel, services as S
    lim = travel.window_km(S.window("dialysis").minutes, "UTTAR PRADESH")
    got = _outside(rows_d).nearby("dialysis", *LKO, "UTTAR PRADESH", as_of=AS_OF)
    assert got and all(e["km"] <= lim for e in got) and len(got) < len(rows_d)
    # no origin, an unknown line, or a line with no lane: nothing
    assert o.nearby("oncology", None, None) == [] and o.nearby("emoc", *LKO) == [] and o.nearby(None, *LKO) == []


def test_lane_km_fallback_on_islands():
    port_blair = (11.6667, 92.7333)
    row = _lane_row("aerb_rt", "oncology", "Island Cancer Unit", port_blair[0] + 0.02, port_blair[1])
    (e,) = _outside([row]).nearby("oncology", *port_blair, "ANDAMAN AND NICOBAR ISLANDS", as_of=AS_OF)
    assert "straight line" in e["minutes_label"] and "sea travel not modelled" in e["minutes_label"]


def test_lane_dedupes_one_place_listed_by_two_sources(monkeypatch):
    monkeypatch.setenv("VF_OUTSIDE_LANE_SOURCES", "ncg")
    rows = [_lane_row("aerb_rt", "oncology", "Sunrise Cancer Hospital", LKO[0] + 0.01, LKO[1]),
            _lane_row("ncg", "oncology", "SUNRISE CANCER HOSPITAL PVT LTD", LKO[0] + 0.012, LKO[1],
                      what="member cancer centre of the National Cancer Grid"),
            _lane_row("aerb_rt", "oncology", "Moonlight Oncology Centre", LKO[0] + 0.02, LKO[1])]
    lane = _outside(rows).nearby("oncology", *LKO, "UTTAR PRADESH", as_of=AS_OF)
    assert [e["source_id"] for e in lane] == ["aerb_rt", "aerb_rt"]


def test_annotate_answer_adds_and_removes_nothing():
    o = _outside([_lane_row("aerb_rt", "oncology", "Near Cancer Centre", LKO[0] + 0.01, LKO[1])], facts=FACTS)
    ans = {"status": "ok", "results": [{"facility_id": "F2", "name": "x", "public_list": None}],
           "beyond_radius": {"facility_id": "F1"}, "unverified_nearby": [{"facility_id": "F9"}],
           "further_away": [{"facility_id": "F1"}], "notes": ["keep"]}
    import copy
    before = copy.deepcopy(ans)
    out = pl.annotate_answer(ans, "oncology", *LKO, "UTTAR PRADESH", outside=o, as_of=AS_OF)
    assert out is ans
    for k, v in before.items():  # every original key and value is still there
        if isinstance(v, list) and v and isinstance(v[0], dict):
            for a, b in zip(v, out[k]):
                assert {kk: b[kk] for kk in a} == a
        elif isinstance(v, dict):
            assert {kk: out[k][kk] for kk in v} == v
        else:
            assert out[k] == v
    assert set(out) == set(before) | {"outside_nearby"}
    assert [x["source_id"] for x in out["results"][0]["outside_lines"]] == ["aerb_rt", "ncg"]
    assert out["unverified_nearby"][0]["outside_lines"] == []
    assert len(out["outside_nearby"]) == 1 and out["outside_nearby"][0]["source_id"] == "aerb_rt"
    empty = pl.annotate_answer({"results": [], "beyond_radius": None}, "emoc", None, None, outside=o)
    assert empty == {"results": [], "beyond_radius": None, "outside_nearby": []}


def test_bundle_roundtrip_and_missing_bundle(tmp_path):
    o = _outside([_lane_row("aerb_rt", "oncology", "Near Cancer Centre", LKO[0] + 0.01, LKO[1])], facts=FACTS)
    sizes = pl.write_bundle(tmp_path, o)
    assert set(sizes) == {pl.CARD_FACTS_FILE, pl.LANE_FILE, pl.OUTSIDE_META_FILE}
    back = pl.load_outside(tmp_path)
    assert back.lines_for("F2", "oncology", as_of=AS_OF) == o.lines_for("F2", "oncology", as_of=AS_OF)
    assert back.nearby("oncology", *LKO, "UTTAR PRADESH", as_of=AS_OF) == o.nearby("oncology", *LKO, "UTTAR PRADESH",
                                                                                   as_of=AS_OF)
    gone = pl.load_outside(tmp_path / "absent")
    assert gone.lines_for("F2", "oncology") == [] and gone.nearby("oncology", *LKO) == []


def test_nice_name_and_place():
    assert pl.nice_name("SRI PRAAGNA HOSPITALS") == "Sri Praagna Hospitals"
    # no dashes, no ' ,', no trailing dots in display names (house style)
    assert pl.nice_name("Fortis Shalimarbagh \u2013 New Delhi") == "Fortis Shalimarbagh, New Delhi"
    assert pl.nice_name("Jus\u2019 Onco \u2013 Affordable Cancer Care Center") == "Jus\u2019 Onco, Affordable Cancer Care Center"
    assert pl.nice_name("Hind Institute Of Medical Sciences , Barabanki..") == "Hind Institute Of Medical Sciences, Barabanki"
    assert pl.nice_name("Tata Memorial Hospital") == "Tata Memorial Hospital"
    assert pl.nice_place("ANDAMAN AND NICOBAR ISLANDS") == "Andaman and Nicobar Islands"


# ---------------------------------------------------------------------------
# v2 review fixes: terms gates, look-alike rules, cross-source dedupe
# ---------------------------------------------------------------------------

def test_terms_gates_default_and_switch(monkeypatch, tmp_path):
    monkeypatch.delenv("VF_OUTSIDE_LANE_SOURCES", raising=False)
    monkeypatch.delenv("VF_OUTSIDE_CARD_SOURCES", raising=False)
    # D057: the lane only from sources whose terms allow reuse with acknowledgement
    assert pl.lane_sources_allowed() == {"laqshya", "aerb_rt"}
    assert "nhp_bloodbank" not in pl.card_sources_allowed()
    rows = [_lane_row(sid, "oncology", f"{sid} Unit", LKO[0] + 0.01 * (i + 1), LKO[1])
            for i, sid in enumerate(("aerb_rt", "ncg"))]
    rows.append(_lane_row("mohfw_trauma", "trauma", "Trauma Unit", LKO[0] + 0.01, LKO[1]))
    o = _outside(rows, facts=FACTS)
    assert [e["source_id"] for e in o.nearby("oncology", *LKO, "UTTAR PRADESH", as_of=AS_OF)] == ["aerb_rt"]
    assert o.nearby("trauma", *LKO, "UTTAR PRADESH", as_of=AS_OF) == []
    assert [x["source_id"] for x in o.lines_for("F1", "emoc", as_of=AS_OF)] == ["laqshya"]
    # the bundle never carries withheld rows, and says what it withheld
    pl.write_bundle(tmp_path, o)
    back = pl.load_outside(tmp_path)
    assert set(back.lane.source_id) == {"aerb_rt"} and "nhp_bloodbank" not in set(back.card_facts.source_id)
    assert back.meta["withheld_terms"]["lane_rows"] == {"ncg": 1, "mohfw_trauma": 1}
    assert back.meta["withheld_terms"]["card_facts"] == {"nhp_bloodbank": 1}
    # Hruday can switch a source on
    monkeypatch.setenv("VF_OUTSIDE_LANE_SOURCES", "ncg")
    assert {e["source_id"] for e in o.nearby("oncology", *LKO, "UTTAR PRADESH", as_of=AS_OF)} == {"aerb_rt", "ncg"}
    monkeypatch.setenv("VF_OUTSIDE_LANE_SOURCES", "all")
    assert len(o.nearby("trauma", *LKO, "UTTAR PRADESH", as_of=AS_OF)) == 1


def test_name_forms_initials_and_aliases():
    assert pl.join_initials(pl.normalise_name("S. M. S. Medical College")) == "sms medical college"
    assert pl.join_initials(pl.normalise_name("I. P. G. M. E & R")) == "ipgmer"
    assert pl.lane_core("V.S.Hospital") == "vs"
    assert pl.lane_core("IPGMER & SSKM Hospital") == pl.lane_core("PG Hospital") == "sskm"
    # an all-generic name takes its place
    assert pl.lane_core("Government Medical College") == ""
    assert pl.lane_core("Government Medical College", "Anantapur") == "anantapur"
    assert pl.name_parts("Siddharth Gupta Memorial Cancer Hospital, A Unit of Acharya Vinoba Bhave Rural "
                         "Hospital")[1:] == ["Siddharth Gupta Memorial Cancer Hospital",
                                             "Acharya Vinoba Bhave Rural Hospital"]
    assert pl.name_parts("Max Superspeciality Hospital (A Unit of Devki Devi Foundation)")[1:] == [
        "A Unit of Devki Devi Foundation"[10:]]

    def m(a, b, here=frozenset()):
        return pl.name_match(pl.lane_core(a), pl._alias_norm(a), pl.lane_core(b), pl._alias_norm(b),
                             local_places=frozenset(here))[0] >= pl.LOOKALIKE_MIN
    assert m("LD Hospital", "Lal Ded Hospital")
    assert m("S.K. Medical College Hospital, Muzaffarpur", "Sri Krishna Medical College and Hospital",
             {"muzaffarpur", "bihar"})
    assert m("K.R.Hospital", "Krishna Rajendra Hospital")
    assert m("Kasturba Medical College Hospital", "KMC Hospital, Mangalore", {"mangalore"})
    assert m("DR RPGMC Kangra At Tanda", "Dr. Rajendra Prasad Government Medical College Kangra at Tanda",
             {"kangra"})
    assert m("V.S.Hospital", "VS Hospitals") and m("S. M. S. Medical College and Hospital", "SMS Hospital")
    # not the same place
    assert not m("SN Medical College, Agra", "Shakya Nursing And Maternity Home", {"agra"})
    assert not m("S M Deb Civil Hospital", "Silchar Medical College & Hospital", {"cachar", "assam"})
    assert not m("MCH K R Nagara", "Krishna Rajendra Hospital", {"mysuru", "karnataka"})
    assert not m("CH Bairagarh", "The County Hospital and Research Centre")
    assert not m("Apollo Hospitals, Bangalore", "Apollo Sugar Clinic, Padmanabhanagar", {"bangalore"})
    assert not pl.kinds_within({"area"}, {"care"}) and pl.kinds_within({"referral"}, {"referal"})


def _ours(rows):
    return pd.DataFrame([dict(dict(facility_type="hospital", pin=None, city_raw=None, state_raw=None), **r)
                         for r in rows])


def test_lookalike_filter_rules():
    lane = pd.DataFrame([
        _lane_row("laqshya", "emoc", "LD Hospital", 34.0700, 74.8090, uid="lq-ld"),
        _lane_row("aerb_rt", "oncology", "Government Medical College", 32.7300, 74.8600, uid="aerb-gmc"),
        _lane_row("laqshya", "emoc", "DH Raisen", 23.3300, 77.7800, prec=20.0, uid="lq-raisen"),
        _lane_row("aerb_rt", "oncology", "C R GARDI HOSPITAL", 23.1800, 75.7800, uid="aerb-gardi"),
        _lane_row("aerb_rt", "oncology", "KOKILABEN DHIRUBHAI AMBANI HOSPITAL", 19.20, 72.97, uid="aerb-kdah"),
        _lane_row("aerb_rt", "oncology", "Area Hospital Kondapur", 17.4600, 78.3600, uid="aerb-area"),
        _lane_row("aerb_rt", "oncology", "Onco Life Cancer Centre, Ratnagiri", 16.99, 73.30, uid="aerb-onco"),
    ], columns=pl._LANE_COLS)
    lane.loc[lane.list_uid.eq("aerb-gmc"), ["district", "state"]] = ["Jammu", "Jammu and Kashmir"]
    lane.loc[lane.list_uid.eq("lq-raisen"), ["district", "state"]] = ["Raisen", "Madhya Pradesh"]
    lane.loc[lane.list_uid.eq("aerb-gardi"), ["district", "state", "pin"]] = ["Ujjain", "Madhya Pradesh", None]
    lane.loc[lane.list_uid.eq("aerb-kdah"), ["district", "state", "pin"]] = ["Thane", "Maharashtra", None]
    lane.loc[lane.list_uid.eq("aerb-area"), ["district", "state", "pin"]] = ["Rangareddy", "Telangana", None]
    lane.loc[lane.list_uid.eq("aerb-onco"), ["district", "state", "pin"]] = ["Ratnagiri", "Maharashtra", None]
    lane.loc[lane.list_uid.eq("lq-ld"), ["district", "state", "pin"]] = ["Srinagar", "Jammu and Kashmir", None]
    fac = _ours([
        {"facility_id": "ld", "name": "Lal Ded Hospital", "lat": 34.0750, "lon": 74.8150, "city_raw": "Srinagar"},
        {"facility_id": "smgs", "name": "Shri Maharaja Gulab Singh Hospital, Government Medical College Jammu",
         "lat": 32.7310, "lon": 74.8610, "city_raw": "Jammu"},
        {"facility_id": "dh", "name": "District Hospital", "lat": 23.40, "lon": 77.85, "city_raw": "Raisen"},
        {"facility_id": "gardi", "name": "C. R. Gardi Hospital", "lat": 22.72, "lon": 75.86,
         "city_raw": "Ujjain", "state_raw": "Madhya Pradesh"},
        {"facility_id": "kdah", "name": "Kokilaben Dhirubhai Ambani Hospital and Research Centre, Mumbai",
         "lat": 19.1311, "lon": 72.8250, "city_raw": "Mumbai", "state_raw": "Maharashtra"},
        {"facility_id": "care", "name": "Care Hospitals", "lat": 17.47, "lon": 78.37, "city_raw": "Hyderabad"},
        {"facility_id": "onco", "name": "Onco-Life Cancer Centre, Satara", "lat": 17.68, "lon": 74.00,
         "city_raw": "Satara", "state_raw": "Maharashtra"},
    ])
    kept, dropped = pl._lookalike_filter(lane, fac, frozenset({"srinagar", "jammu", "raisen", "delhi"}),
                                         linked=frozenset({"kdah", "onco"}))
    rules = dict(zip(dropped.list_uid, dropped.lookalike_rule))
    assert rules == {"lq-ld": "nearby", "aerb-gmc": "generic_twin", "lq-raisen": "generic_twin",
                     "aerb-gardi": "same_name_far", "aerb-kdah": "linked_elsewhere"}
    # "Area Hospital" is not "Care Hospitals"; a chain's branch 80 km away is another place
    assert set(kept.list_uid) == {"aerb-area", "aerb-onco"}


def test_cross_source_merge_and_one_place_per_lane(monkeypatch):
    monkeypatch.setenv("VF_OUTSIDE_LANE_SOURCES", "all")
    rows = [_lane_row("aerb_rt", "oncology", "DR. BALABHAI NANAVATI HOSPITAL", LKO[0] + 0.01, LKO[1], uid="a1"),
            _lane_row("ncg", "oncology", "Nanavati Max Institute of Cancer Care", LKO[0] + 0.0101, LKO[1],
                      uid="n1"),
            _lane_row("aerb_rt", "oncology", "V.S.Hospital", LKO[0] + 0.02, LKO[1], uid="a2"),
            _lane_row("ncg", "oncology", "V. S. Hospitals Center of Excellence for Advanced Cancer Care",
                      LKO[0] + 0.0201, LKO[1], uid="n2"),
            # district-centre rows share one point: the district name alone never makes them one place
            _lane_row("laqshya", "emoc", "DH Ranchi", LKO[0] + 0.03, LKO[1], note="district centre", uid="l1"),
            _lane_row("ncg", "oncology", "Paras HEC Hospital, Ranchi", LKO[0] + 0.03, LKO[1],
                      note="district centre", uid="n3"),
            _lane_row("laqshya", "emoc", "DH Gumla", LKO[0] + 0.04, LKO[1], note="district centre", uid="l2"),
            _lane_row("mohfw_trauma", "trauma", "District Hospital, Gumla", LKO[0] + 0.04, LKO[1],
                      note="district centre", uid="m2"),
            _lane_row("laqshya", "emoc", "DWH Gumla", LKO[0] + 0.04, LKO[1], note="district centre", uid="l3")]
    lane = pd.DataFrame(rows, columns=pl._LANE_COLS)
    for uid, d in (("l1", "Ranchi"), ("n3", "Ranchi"), ("l2", "Gumla"), ("m2", "Gumla"), ("l3", "Gumla")):
        lane.loc[lane.list_uid.eq(uid), ["district", "state"]] = [d, "Jharkhand"]
    fac = _ours([{"facility_id": "x", "name": "Some Other Hospital", "lat": 0.0, "lon": 0.0}])
    merged, n = pl._merge_cross_source(lane, fac, frozenset({"ranchi", "gumla"}))
    gk = dict(zip(merged.list_uid, merged.group_key))
    assert gk["a1"] == gk["n1"] and gk["a2"] == gk["n2"] and gk["l2"] == gk["m2"]
    assert gk["l1"] != gk["n3"] and gk["l3"] not in (gk["l2"], gk["m2"]) and n == 3
    lane_out = pl.OutsideLists(pd.DataFrame(columns=pl._CARD_COLS), merged, pl.PublicList(_links(fid="F1")), {}
                               ).nearby("oncology", *LKO, "UTTAR PRADESH", as_of=AS_OF)
    assert [e["name"] for e in lane_out][:2] == ["DR. Balabhai Nanavati Hospital",
                                                 "V.S.Hospital"]


def test_annotate_answer_never_shows_a_card_in_the_lane():
    rows = [_lane_row("laqshya", "emoc", "LD Hospital", LKO[0] + 0.01, LKO[1], what="maternity operating "
                      "theatre certified", note="district centre", prec=20.0),
            _lane_row("laqshya", "emoc", "CHC Pattan", LKO[0] + 0.02, LKO[1], what="maternity operating theatre "
                      "certified", note="district centre", prec=20.0)]
    o = _outside(rows)
    ans = {"results": [{"facility_id": "F7", "name": "Lal Ded Hospital", "city": "Srinagar",
                        "lat": LKO[0] + 0.012, "lon": LKO[1]}], "unverified_nearby": []}
    out = pl.annotate_answer(ans, "emoc", *LKO, "UTTAR PRADESH", outside=o, as_of=AS_OF)
    assert [e["name"] for e in out["outside_nearby"]] == ["CHC Pattan"]
    # with no such card, both show
    assert len(o.nearby("emoc", *LKO, "UTTAR PRADESH", as_of=AS_OF)) == 2


# ---------------------------------------------------------------------------
# v2 on the built tables (skip when silver is absent)
# ---------------------------------------------------------------------------

needs_outside = pytest.mark.skipif(not (paths.silver_dir() / "ext_links_aerb_rt.parquet").exists(),
                                   reason="run the wp13_ext_* scripts first")


@pytest.fixture(scope="module")
def built():
    return pl.build_outside(as_of=AS_OF)


@needs_outside
def test_built_card_facts_come_only_from_accepted_links(built):
    for sid in pl.LINKED_SOURCES:
        p = paths.silver_dir() / f"ext_links_{sid}.parquet"
        if not p.exists():
            continue
        links = pd.read_parquet(p)
        acc = set(zip(links.loc[links.accepted.fillna(False).astype(bool), "list_uid"],
                      links.loc[links.accepted.fillna(False).astype(bool), "facility_id"]))
        got = set(zip(*[built.card_facts.loc[built.card_facts.source_id.eq(sid), c] for c in ("list_uid",
                                                                                             "facility_id")]))
        assert got == acc, sid
    # sources with no accepted band give no card facts
    assert not built.card_facts.source_id.isin(["mohfw_trauma", "nabi_burns"]).any()


@needs_outside
def test_built_lane_has_no_blood_banks_and_no_linked_rows(built):
    assert not built.lane.source_id.eq("nhp_bloodbank").any()
    assert set(built.lane.service_line) <= {"emoc", "oncology", "trauma", "burns"}
    linked = set(built.card_facts.list_uid)
    assert not set(built.lane.list_uid) & linked
    for sid in ("aerb_rt", "nabi_burns"):
        lst = pd.read_parquet(paths.silver_dir() / f"ext_{sid}.parquet")
        ok = set(lst.loc[lst.lane_eligible.astype(bool), "list_uid"])
        assert set(built.lane.loc[built.lane.source_id.eq(sid), "list_uid"]) <= ok
    for sid in ("ncg", "mohfw_trauma"):
        lst = pd.read_parquet(paths.silver_dir() / f"ext_{sid}.parquet")
        ok = set(lst.loc[lst.link_status.eq("not_in_dataset"), "list_uid"])
        assert set(built.lane.loc[built.lane.source_id.eq(sid), "list_uid"]) <= ok


@needs_outside
def test_built_brief_places(built):
    # a cancer query near Mumbai: Jaslok Hospital's record carries the AERB licence line
    lines = [x for fid in built.card_facts.loc[built.card_facts.source_id.eq("aerb_rt"), "facility_id"]
             for x in built.lines_for(fid, "oncology", as_of=AS_OF)]
    assert lines and all(x["text"].endswith(TAIL) for x in lines)
    mumbai = built.nearby("oncology", 19.0013, 72.8382, "MAHARASHTRA", as_of=AS_OF)
    assert 1 <= len(mumbai) <= 3 and all(e["source_id"] in ("aerb_rt", "ncg") for e in mumbai)
    # Lucknow 226001 emoc: LaQshya units only, all text ends with the outside-data tail
    lko = built.nearby("emoc", *LKO, "UTTAR PRADESH", as_of=AS_OF)
    assert lko and all(e["source_id"] == "laqshya" and e["text"].endswith(TAIL) for e in lko)
    # dialysis has no outside list: no lane
    assert built.nearby("dialysis", 26.1523, 87.6482, "BIHAR", as_of=AS_OF) == []


@needs_outside
def test_built_publishable_follows_terms(built, monkeypatch):
    monkeypatch.delenv("VF_OUTSIDE_LANE_SOURCES", raising=False)
    monkeypatch.delenv("VF_OUTSIDE_CARD_SOURCES", raising=False)
    pub = pl.publishable(built)
    assert set(pub.lane.source_id) <= {"laqshya", "aerb_rt"}
    assert not pub.card_facts.source_id.eq("nhp_bloodbank").any()
    assert pub.meta["withheld_terms"]["lane_rows"].keys() <= {"ncg", "mohfw_trauma", "nabi_burns"}


@needs_outside
def test_built_review_cases_not_in_the_lane(built):
    """The rows the review found in the lane that are our records (D055 review, 22 Nov 2026)."""
    names = set(built.lane.name.str.lower())
    for n in ("ld hospital", "v.s.hospital", "k d hospital", "s. s. g. hospital", "k.r.hospital",
              "s. m. s. medical college and hospital", "c r gardi hospital", "i. p. g. m. e & r / sskm hospital",
              "ipgmer & sskm hospital", "dh raisen", "cachar cancer hospital & research centre",
              "delhi state cancer institute", "s.k. medical college hospital, muzaffarpur",
              "kokilaben dhirubhai ambani hospital", "curie-abdur razzaque ansari cancer institute"):
        assert n not in names, n


@needs_outside
def test_built_answers_never_show_a_card_in_the_lane(built, monkeypatch):
    monkeypatch.setenv("VF_OUTSIDE_LANE_SOURCES", "all")
    try:
        from vf import rank
        idx = rank.load_index()
    except Exception as e:  # the ranker's inputs are not built here
        pytest.skip(f"ranker index not available: {e}")
    cases = [("oncology", 24.8333, 92.7789, "ASSAM"), ("emoc", 34.0700, 74.8090, "JAMMU AND KASHMIR"),
             ("trauma", 26.1209, 85.3647, "BIHAR"), ("oncology", 26.8930, 75.8200, "RAJASTHAN"),
             ("oncology", 13.0850, 80.2380, "TAMIL NADU"), ("oncology", 19.1351, 72.8340, "MAHARASHTRA")]
    for line, lat, lon, state in cases:
        res = {"status": "ok", "label": "test", "lat": lat, "lon": lon, "state": state, "precision_km": 1.0}
        ans = pl.annotate_answer(rank.rank(idx, line, res, k=3).to_dict(), line, lat, lon, state, outside=built,
                                 as_of=AS_OF)
        cards = [c for sec in pl.CARD_SECTIONS for c in (ans.get(sec) if isinstance(ans.get(sec), list)
                                                         else [ans.get(sec)]) if isinstance(c, dict)]
        for e in ans["outside_nearby"]:
            for c in cards:
                b = pl._alias_norm(c["name"])
                assert pl.lane_core(e["name"]) != pl.lane_core(c["name"]) or not pl.lane_core(c["name"]), (e, c)
                assert not pl.initials_match(pl.lane_core(e["name"]), pl.lane_core(c["name"]), b), (e["name"], c["name"])
        groups = [e["name"] for e in ans["outside_nearby"]]
        assert len(groups) == len(set(groups))
