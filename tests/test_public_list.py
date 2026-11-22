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
    assert "labour room conditionally certified, valid to 1 Jul 2026 (expired by 21 Nov 2026)" in t
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
    assert line["current"] is False and "(expired by 21 Nov 2026)" in line["text"]
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
