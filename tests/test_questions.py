"""Tests for vf.questions ("Before you go, ask", D044 item 3).

Hand-made cards in the ``vf.rank.Card.to_dict()`` shape. Phone numbers here are
obviously fake (runs of zeros), and no test prints a revealed number.
"""
from __future__ import annotations

import itertools
import json
import re

import pandas as pd
import pytest

from vf import paths
from vf import questions as Q
from vf import services as S

FAKE_PHONE = "+910000000042"
FAKE_PHONE_2 = "+910000000077"


def card(**kw) -> dict:
    base = {
        "facility_id": "f1", "name": "Test Hospital", "type": "hospital", "operator": "private",
        "city": "Lucknow", "state": "Uttar Pradesh", "lat": 26.85, "lon": 80.95,
        "distance_km": 3.5, "location_precision_km": 1.0, "location_note": "Straight-line distance.",
        "grade": 2, "grade_label": "supported", "confidence": "medium", "confidence_reason": "",
        "score": 0.7, "parts": {}, "evidence": [], "missing": [], "sources":
            {"n_domains": 3, "domains": ["a.in", "b.in", "c.in"], "official_website": "a.in", "also_listed_as": []},
        "why_here": "", "could_be_wrong": "", "evidence_strength": "moderate", "flags": [],
    }
    base.update(kw)
    return base


def ev(requirement, quote, field="procedure", fact_id="x1"):
    return {"requirement": requirement, "quote": quote, "field": field, "fact_id": fact_id}


def missing(*names):
    return [f"no fact in the record mentions {n}" for n in names]


CSECTION = ev("caesarean section", "Performs Lower Segment Caesarean Section (LSCS)")


def rec(**kw) -> Q.RecordInfo:
    base = dict(facility_id="f1", tags=frozenset(), h24_cotags=frozenset(), h24_quote=None,
                scheme_quote=None, has_phone=True, phone_shared_with=0)
    base.update(kw)
    return Q.RecordInfo(**base)


def sources_of(qs):
    return [q["source"] for q in qs]


# ---------------------------------------------------------------------------
# The brief's example and the lead question
# ---------------------------------------------------------------------------

def test_emoc_csection_without_blood_asks_for_blood_tonight():
    c = card(evidence=[CSECTION], missing=missing("blood bank or transfusion", "anaesthesia or operating theatre"))
    qs = Q.questions_for(c, "emoc", rec(tags=frozenset({"csection"})))
    assert qs[0]["question"] == "Can you do an emergency C-section right now?"
    assert qs[0]["source"] == "service"
    assert "Performs Lower Segment Caesarean Section (LSCS)" in qs[0]["why"]
    blood = [q for q in qs if q["question"] == "Is blood available for transfusion tonight?"]
    assert len(blood) == 1
    assert blood[0]["source"] == "missing_for_next"
    assert blood[0]["why"] == "No fact in the record mentions a blood bank or blood transfusion."
    assert 2 <= len(qs) <= 4


def test_raw_missing_for_next_is_accepted():
    c = card(evidence=[CSECTION], missing=[], missing_for_next=["blood bank or transfusion"])
    qs = Q.questions_for(c, "emoc", rec(tags=frozenset({"csection"})))
    assert any("transfusion" in q["question"] for q in qs)


@pytest.mark.parametrize("line", S.SERVICE_LINES)
def test_every_line_leads_with_the_service(line):
    qs = Q.questions_for(card(), line, rec())
    assert qs[0]["question"] == Q.LEAD[line]
    assert qs[0]["source"] == "service"
    if line in Q.EMERGENCY_LINES:
        assert "right now" in qs[0]["question"]


def test_grade_one_lead_cites_the_claim_and_the_gap():
    c = card(grade=1, evidence=[ev("maternity or delivery service", "Offers labour room services.", "capability")],
             missing=missing("caesarean section"), evidence_strength="thin")
    qs = Q.questions_for(c, "emoc", rec(tags=frozenset({"ob_delivery"})))
    assert qs[0]["why"].startswith('The record only claims it: "Offers labour room services."')
    assert "No fact in the record mentions a caesarean section." in qs[0]["why"]
    # the lead covers the C-section gap: no second C-section question
    assert sum("C-section" in q["question"] for q in qs) == 1
    # grade 1 records still get the corroboration gaps the record's tags show
    assert any("transfusion" in q["question"] for q in qs)


def test_grade_one_with_blood_in_record_does_not_ask_for_blood():
    c = card(grade=1, evidence=[], missing=missing("caesarean section"), evidence_strength="thin")
    qs = Q.questions_for(c, "emoc", rec(tags=frozenset({"ob_delivery", "blood", "ot"})))
    assert not any("transfusion" in q["question"] for q in qs)
    assert not any("anaesthetist" in q["question"] for q in qs)


def test_icu_ventilator_gap_folds_into_the_lead():
    c = card(evidence=[ev("intensive care unit", "24x7 ICU", "capability")], missing=missing("ventilator"))
    qs = Q.questions_for(c, "icu", rec(tags=frozenset({"icu", "h24"}), h24_cotags=frozenset({"icu"})))
    assert "No fact in the record mentions a ventilator." in qs[0]["why"]
    assert sum("ventilator" in q["question"] for q in qs) == 1


def test_repeat_requirement_reads_plainly():
    c = card(evidence=[ev("MRI scan", "1.5T MRI", "equipment")],
             missing=missing("MRI named in a second fact or a second field"))
    qs = Q.questions_for(c, "mri", rec(tags=frozenset({"mri"})))
    q = next(q for q in qs if q["source"] == "missing_for_next")
    assert q["why"] == "Only one fact in the record names MRI."


# ---------------------------------------------------------------------------
# Round-the-clock service
# ---------------------------------------------------------------------------

def test_h24_asked_on_emergency_line_when_record_has_none():
    qs = Q.questions_for(card(evidence=[ev("newborn intensive care unit", "Level III NICU", "capability")]),
                         "nicu", rec(tags=frozenset({"nicu"})))
    h = [q for q in qs if q["source"] == "h24"]
    assert len(h) == 1
    assert h[0]["why"] == "No fact in the record mentions round-the-clock (24-hour) service."


def test_h24_elsewhere_is_quoted_and_still_asked():
    r = rec(tags=frozenset({"nicu", "h24", "pharmacy"}), h24_cotags=frozenset({"pharmacy"}),
            h24_quote="24-hour pharmacy", h24_quote_cotags=frozenset({"pharmacy"}))
    qs = Q.questions_for(card(), "nicu", r)
    h = [q for q in qs if q["source"] == "h24"]
    assert len(h) == 1
    assert h[0]["why"] == 'The record mentions round-the-clock service only for something else: "24-hour pharmacy".'


@pytest.mark.parametrize("quote_cotags", [frozenset(), None])
def test_h24_facility_wide_is_not_called_something_else(quote_cotags):
    # "Open 24/7" has no other tags: it is about the whole facility, not another service
    r = rec(tags=frozenset({"csection", "h24"}), h24_cotags=frozenset(), h24_quote="Open 24/7",
            h24_quote_cotags=quote_cotags)
    h = [q for q in Q.questions_for(card(evidence=[CSECTION]), "emoc", r) if q["source"] == "h24"]
    assert len(h) == 1
    assert "something else" not in h[0]["why"]
    assert h[0]["why"] == ('The record says "Open 24/7", but no fact in the record says the C-section service '
                           "is available round the clock.")


def test_h24_tag_without_a_quote_is_not_called_absent():
    r = rec(tags=frozenset({"nicu", "h24"}), h24_cotags=frozenset(), h24_quote=None)
    h = [q for q in Q.questions_for(card(), "nicu", r) if q["source"] == "h24"]
    assert len(h) == 1 and "No fact in the record mentions round-the-clock" not in h[0]["why"]
    assert h[0]["why"].startswith("The record mentions round-the-clock service, but")


def test_h24_on_the_service_is_not_asked():
    r = rec(tags=frozenset({"nicu", "h24"}), h24_cotags=frozenset({"nicu"}), h24_quote="24x7 NICU")
    assert "h24" not in sources_of(Q.questions_for(card(), "nicu", r))


def test_h24_not_asked_on_planned_lines():
    for line in ("dialysis", "mri", "oncology", "cardiac_surgery", "ct"):
        assert "h24" not in sources_of(Q.questions_for(card(), line, rec()))


def test_trauma_24h_requirement_is_one_question():
    c = card(evidence=[ev("trauma care", "Level 1 trauma centre", "capability")],
             missing=missing("24-hour service"))
    qs = Q.questions_for(c, "trauma", rec(tags=frozenset({"trauma"})))
    assert sum("round the clock" in q["question"] for q in qs) == 1


# ---------------------------------------------------------------------------
# Trust flags, location, phones, cost, thin support
# ---------------------------------------------------------------------------

def test_location_conflict_asks_to_confirm_address():
    reason = "The record's scraped map point was 212 km from its PIN code's area."
    c = card(flags=[{"flag": "location_conflict", "severity": "minor", "signal": "moved_to_pin",
                     "reason": reason, "quote": "X"}])
    qs = Q.questions_for(c, "dialysis", rec())
    q = next(q for q in qs if q["source"] == "location")
    assert "address" in q["question"]
    assert q["why"] == "The record's location is uncertain: the record's scraped map point was 212 km from its PIN code's area."


def test_fuzzy_position_without_flag_asks_address():
    qs = Q.questions_for(card(location_precision_km=10.0), "dialysis", rec())
    q = next(q for q in qs if q["source"] == "location")
    assert "about 10 km" in q["why"]


def test_possible_merge_asks_which_branch():
    c = card(flags=[{"flag": "possible_merge", "severity": "minor", "signal": "text_other_address",
                     "reason": "The record's own text places it in Pune.", "quote": "Pune"}])
    q = next(q for q in Q.questions_for(c, "emoc", rec()) if q["source"] == "flag:possible_merge")
    assert "another branch" in q["question"]
    assert q["why"] == "The record may combine more than one facility: the record's own text places it in Pune."


def test_implausible_claim_on_unverified_card():
    c = card(grade=1, score=None, flags=[{"flag": "implausible_claim", "severity": "major",
                                          "signal": "type_cannot_offer", "reason": "Listed as a clinic.",
                                          "quote": "clinic"}])
    qs = Q.questions_for(c, "burns", rec())
    assert qs[1]["source"] == "flag:implausible_claim"


def test_shared_phone_question_only_when_shared():
    assert "shared_phone" not in sources_of(Q.questions_for(card(), "emoc", rec(phone_shared_with=0)))
    qs = Q.questions_for(card(evidence=[CSECTION]), "dialysis", rec(phone_shared_with=86))
    q = next(q for q in qs if q["source"] == "shared_phone")
    assert "86 other facility records" in q["why"]


def test_cost_question_private_public_and_scheme():
    q = next(q for q in Q.questions_for(card(), "dialysis", rec()) if q["source"] == "cost")
    assert "PM-JAY" in q["question"] and q["why"] == "No fact in the record mentions Ayushman Bharat, PM-JAY or CGHS."
    q = next(q for q in Q.questions_for(card(operator="public"), "dialysis", rec()) if q["source"] == "cost")
    assert "pay" in q["question"] and "lists it as public" in q["why"]
    r = rec(tags=frozenset({"scheme"}), scheme_quote="Empanelled under PM-JAY")
    q = next(q for q in Q.questions_for(card(), "dialysis", r) if q["source"] == "cost")
    assert q["question"].startswith("Will Ayushman Bharat (PM-JAY) cover")
    assert '"Empanelled under PM-JAY"' in q["why"]
    r = rec(tags=frozenset({"scheme"}), scheme_quote="Empanelled with CGHS")
    q = next(q for q in Q.questions_for(card(), "dialysis", r) if q["source"] == "cost")
    assert "the scheme the record names" in q["question"]


def test_thin_support_named_in_lead_and_question():
    c = card(evidence=[ev("dialysis", "Dialysis available")], evidence_strength="thin",
             sources={"n_domains": 1, "domains": ["a.in"], "official_website": None})
    qs = Q.questions_for(c, "dialysis", rec(tags=frozenset({"dialysis", "scheme"}), scheme_quote="Ayushman card accepted"))
    assert "1 web source and has no official website" in qs[0]["why"]
    assert "evidence_strength" in sources_of(qs)


def test_without_record_info_scope_is_the_quoted_facts():
    qs = Q.questions_for(card(evidence=[CSECTION]), "emoc", None)
    whys = " ".join(q["why"] for q in qs)
    assert "None of the facts quoted here" in whys
    assert "No fact in the record mentions round-the-clock" not in whys


def test_quoted_h24_counts_without_record_info():
    c = card(evidence=[ev("caesarean section", "24x7 emergency caesarean section")])
    assert "h24" not in sources_of(Q.questions_for(c, "emoc", None))


def test_keyword_search_card():
    c = card(grade=None, grade_label="", evidence_strength="keyword",
             evidence=[ev("keyword match", "Snake bite management", "capability")])
    qs = Q.questions_for(c, "search", rec(), service_text="snake bite antivenom")
    assert qs[0]["question"] == "Do you offer snake bite antivenom here, and can you see the patient today?"
    assert "keyword match" in qs[0]["why"] and '"Snake bite management"' in qs[0]["why"]
    assert "missing_for_next" not in sources_of(qs)


def test_clean_corroborated_record_still_gets_two():
    r = rec(tags=frozenset({"dialysis", "dialysis_unit", "scheme"}), scheme_quote="Ayushman Bharat empanelled")
    qs = Q.questions_for(card(grade=3, evidence_strength="strong"), "dialysis", r)
    assert len(qs) == 2 and qs[0]["source"] == "service"


def test_long_quote_is_cut_at_a_word():
    long = "Performs caesarean section " + "with modern equipment " * 20
    qs = Q.questions_for(card(evidence=[ev("caesarean section", long)]), "emoc", rec())
    assert "..." in qs[0]["why"]
    inner = re.search(r'"(.*)\.\.\."', qs[0]["why"]).group(1)
    assert long.startswith(inner) and len(inner) <= Q.MAX_QUOTE_CHARS


def test_accepts_a_rank_card_object():
    from vf.rank import Card
    obj = Card(**{k: v for k, v in card(evidence=[CSECTION], missing=missing("blood bank or transfusion")).items()})
    qs = Q.questions_for(obj, "emoc", rec().to_row())
    assert any("transfusion" in q["question"] for q in qs)


# ---------------------------------------------------------------------------
# Invariants over many combinations
# ---------------------------------------------------------------------------

FLAG_SETS = [
    [],
    [{"flag": "location_conflict", "severity": "minor", "reason": "Point is off.", "quote": None}],
    [{"flag": "possible_merge", "severity": "minor", "reason": "Two names.", "quote": None},
     {"flag": "location_conflict", "severity": "minor", "reason": "Point is off.", "quote": None}],
    [{"flag": "implausible_claim", "severity": "major", "reason": "A clinic.", "quote": None}],
]
RECORDS = [None, rec(), rec(phone_shared_with=3, tags=frozenset({"h24", "scheme"}), scheme_quote="CGHS")]
BANNED = re.compile("\u2014|" + r"\bcannot\b|does not have|doesn't have|\bgrade [0-3]\b|\bemoc\b|\bg[0-3]\b", re.I)


def _all_cases():
    from vf import recipes as R
    for line, grade, strength, flags, r, op in itertools.product(
            S.SERVICE_LINES, (1, 2, 3), ("thin", "moderate", "strong"), FLAG_SETS, RECORDS, ("private", "public")):
        rule = R.GRADE_RULES[line]
        miss = list(rule.corroborated_names[1:]) if grade == 2 else (list(rule.supported_names) if grade == 1 else [])
        yield line, card(grade=grade, evidence_strength=strength, flags=flags, operator=op,
                         evidence=[ev(rule.supported_names[0], "Some fact")], missing=missing(*miss)), r


def test_invariants_over_combinations():
    n = 0
    for line, c, r in _all_cases():
        qs = Q.questions_for(c, line, r)
        n += 1
        assert Q.MIN_QUESTIONS <= len(qs) <= Q.MAX_QUESTIONS
        assert qs[0]["source"] == "service" and qs[0]["question"] == Q.LEAD[line]
        assert len({q["question"] for q in qs}) == len(qs)
        for q in qs:
            assert set(q) == {"question", "why", "source"}
            assert q["question"].endswith("?")
            assert q["why"] and q["why"][0].isupper()
            assert not BANNED.search(q["question"] + " " + q["why"]), (line, q)
    assert n > 1000


def test_max_questions_respected_but_never_below_two():
    c = card(evidence=[CSECTION], missing=missing("blood bank or transfusion"), flags=FLAG_SETS[2])
    assert len(Q.questions_for(c, "emoc", rec(), max_questions=3)) == 3
    assert len(Q.questions_for(c, "emoc", rec(), max_questions=1)) == 2


# ---------------------------------------------------------------------------
# Phones
# ---------------------------------------------------------------------------

def test_normalize_and_mask_phone():
    assert Q.normalize_phone("+91 00000 00042") == FAKE_PHONE
    assert Q.normalize_phone("0000000042") == FAKE_PHONE
    assert Q.normalize_phone("00000000042") == FAKE_PHONE  # 0 + 10 digits
    assert Q.normalize_phone("null") is None and Q.normalize_phone("123") is None
    m = Q.mask_phone(FAKE_PHONE)
    assert m == "+91 xxxxxxxx42"
    assert "0000000042" not in m
    assert Q.mask_phone(None) is None


def _raw():
    return pd.DataFrame({
        "unique_id": ["a", "a", "b", "c", "d"],
        "officialPhone": [FAKE_PHONE, FAKE_PHONE, FAKE_PHONE, "null", "null"],
        "phone_numbers": ["null", "null", "null", json.dumps([FAKE_PHONE_2]), "null"],
    })


def test_phone_book_sharing_and_fallback():
    book = Q.build_phone_book(_raw())
    assert len(book) == 3  # a, b, c; d has none; the repeated a row counts once
    assert book.shared_with == {"a": 1, "b": 1, "c": 0}
    assert book.source["c"] == "phone_numbers"
    assert FAKE_PHONE not in repr(book)
    st = Q.phone_stats(book)
    assert st["numbers_on_2plus_records"] == 1 and st["max_records_one_number"] == 2


def test_call_info_masks_by_default_and_in_demo_mode(monkeypatch):
    book = Q.build_phone_book(_raw())
    monkeypatch.delenv(Q.DEMO_ENV, raising=False)
    info = Q.call_info("a", book)
    assert info["masked"] and info["tel"] is None and info["phone"] == "+91 xxxxxxxx42"
    assert "1 other record" in info["note"]
    shown = Q.call_info("a", book, reveal=True)
    assert not shown["masked"] and shown["tel"] == "tel:" + FAKE_PHONE
    monkeypatch.setenv(Q.DEMO_ENV, "1")
    demo = Q.call_info("a", book, reveal=True)
    assert demo["masked"] and demo["tel"] is None and FAKE_PHONE not in json.dumps(demo)
    none = Q.call_info("d", book)
    assert not none["has_phone"] and "does not mean the facility has none" in none["note"]


# ---------------------------------------------------------------------------
# Record info
# ---------------------------------------------------------------------------

def _tables():
    ft = pd.DataFrame({
        "fact_id": ["1", "2", "3", "4", "5"],
        "facility_id": ["a", "a", "a", "b", "b"],
        "tags": [["nicu", "h24"], ["blood"], [], ["h24"], []],
    })
    facts = pd.DataFrame({
        "fact_id": ["1", "2", "3", "4", "5"],
        "text": ["24x7 NICU", "Blood bank on site", "Empanelled under PM-JAY", "24-hour pharmacy",
                 "Ayushman Bharat not available"],
    })
    return ft, facts


def test_build_record_info():
    ft, facts = _tables()
    book = Q.build_phone_book(_raw())
    infos = Q.build_record_info(ft, facts, book, facility_ids=["a", "b", "z"])
    a, b, z = infos["a"], infos["b"], infos["z"]
    assert {"nicu", "h24", "blood", "scheme"} <= a.tags  # PM-JAY caught by the wider pattern
    assert a.h24_cotags == frozenset({"nicu"}) and a.h24_quote == "24x7 NICU"
    assert a.scheme_quote == "Empanelled under PM-JAY"
    assert b.scheme_quote is None and "scheme" not in b.tags  # a negated mention is not a scheme
    assert b.h24_cotags == frozenset() and b.h24_quote == "24-hour pharmacy"
    assert z.tags == frozenset() and not z.has_phone
    assert a.phone_shared_with == 1 and b.phone_shared_with == 1
    # the info for "a" makes the NICU card skip the h24 question, "b" asks it with the quote
    assert "h24" not in sources_of(Q.questions_for(card(), "nicu", a))
    assert '"24-hour pharmacy"' in next(q for q in Q.questions_for(card(), "nicu", b) if q["source"] == "h24")["why"]


def test_save_and_load_record_info(tmp_path):
    ft, facts = _tables()
    infos = Q.build_record_info(ft, facts, Q.build_phone_book(_raw()))
    p = Q.save_record_info(infos, tmp_path / "record_info.parquet")
    meta = json.loads((tmp_path / "record_info.parquet.meta.json").read_text())
    assert meta["rows"] == 2 and meta["contains_phone_numbers"] is False
    assert not list(tmp_path.glob(".*.tmp"))
    df = pd.read_parquet(p)
    assert "phone" not in " ".join(c for c in df.columns if c not in ("has_phone", "phone_shared_with"))
    assert FAKE_PHONE not in df.to_json()
    back = Q.load_record_info(tmp_path)
    assert back["a"] == infos["a"]
    # without phone columns, a bundle folder gives no phone book of its own (falls back to the raw file)
    with pytest.raises(KeyError):
        Q.phone_book_from_frame(df)


def test_record_info_with_phone_columns_round_trips_and_is_served_masked(tmp_path, monkeypatch):
    monkeypatch.delenv(Q.DEMO_ENV, raising=False)
    ft, facts = _tables()
    raw = pd.DataFrame({"unique_id": ["a", "b", "z"], "officialPhone": [FAKE_PHONE, FAKE_PHONE, "12"],
                        "phone_numbers": ["null", "null", "null"]})
    book = Q.build_phone_book(raw)
    infos = Q.build_record_info(ft, facts, book, facility_ids=["a", "b", "z"])
    p = Q.save_record_info(infos, tmp_path / "record_info.parquet", phone_book=book)
    meta = json.loads((tmp_path / "record_info.parquet.meta.json").read_text())
    assert meta["contains_phone_numbers"] is True and meta["phone_numbers"] == 2
    assert set(Q.PHONE_COLUMNS) <= set(pd.read_parquet(p).columns)
    back = Q.load_phone_book(tmp_path)
    assert back.phone == book.phone and back.shared_with == book.shared_with
    assert back.source == book.source and back.unusable == book.unusable == {"z"}
    assert Q.load_record_info(tmp_path)["a"] == infos["a"]  # the extra columns do not disturb record info
    masked = Q.call_info("a", back)
    assert masked["masked"] and FAKE_PHONE not in json.dumps(masked) and masked["shared_with"] == 1
    assert Q.call_info("a", back, reveal=True)["tel"] == "tel:" + FAKE_PHONE
    assert not Q.call_info("z", back)["has_phone"]


# ---------------------------------------------------------------------------
# Real data (skipped without the gold tables)
# ---------------------------------------------------------------------------

def _gold_ready() -> bool:
    g = paths.gold_dir()
    return all((g / f).exists() for f in ("capability_grades.parquet", "fact_tags.parquet",
                                          "facility_flags.parquet", "facility_duplicates.parquet"))


@pytest.mark.skipif(not _gold_ready(), reason="gold tables absent")
def test_real_grades_blood_gap_and_counts():
    # the grades the ranker's index loads: recipe v0.3 when written (D048), else v0.2
    v03 = paths.gold_dir() / "capability_grades_v03.parquet"
    grades = pd.read_parquet(v03 if v03.exists() else paths.gold_dir() / "capability_grades.parquet",
                             columns=["facility_id", "service_line", "grade", "tags_present"])
    infos = Q.load_record_info(with_phones=False)
    m = Q.measure(infos=infos, grades=grades)
    b = m["blood"]
    assert b["emoc_grade2plus"] > 0 and 0 < b["no_blood_fact"] <= b["emoc_grade2plus"]
    if v03.exists():
        # v0.3 counts only maternal blood (D052): every supported-only record is asked, including the few
        # whose only blood fact is fetal, newborn or exchange transfusion; no corroborated record is
        n_grade2 = int(((grades["service_line"] == "emoc") & (grades["grade"] == 2)).sum())
        assert b["asked_blood_question"] == n_grade2 >= b["no_blood_fact"]
    else:
        # every record with no blood fact gets the blood question, and no other does
        assert b["asked_blood_question"] == b["no_blood_fact"]
    for line, d in m["lines"].items():
        assert set(d["question_counts"]) <= {"2", "3", "4"}, line
        assert d["sources"]["service"] == d["records"], line


# ---------------------------------------------------------------------------
# Review fixes: negated leads, numbers in quotes, foreign phones, duplicates
# ---------------------------------------------------------------------------

DIGIT_RUN = re.compile(r"\d{8,}")


def test_grade_one_lead_skips_a_negated_fact():
    neg = ev("trauma care", "Lacks trauma care facilities; emergencies referred to private hospitals", "description")
    c = card(grade=1, evidence=[neg], missing=missing("trauma care"), evidence_strength="thin")
    lead = Q.questions_for(c, "trauma", rec())[0]
    assert "Lacks trauma" not in lead["why"] and "only claims it" not in lead["why"]
    assert lead["why"].startswith("The record only lists a related specialty.")
    # a positive fact next to the negated one is the one quoted
    pos = ev("emergency service", "Casualty ward with emergency doctors", "capability")
    lead = Q.questions_for(card(grade=1, evidence=[neg, pos], evidence_strength="thin"), "trauma", rec())[0]
    assert lead["why"].startswith('The record only claims it: "Casualty ward with emergency doctors"')


def test_negated_scheme_quote_on_card_is_not_a_scheme():
    c = card(evidence=[CSECTION, ev("other", "Ayushman Bharat not available here", "description")])
    q = next(q for q in Q.questions_for(c, "emoc", None) if q["source"] == "cost")
    assert "mentions a scheme" not in q["why"]


def test_mask_numbers():
    assert Q.mask_numbers("Has a 24/7 helpline at +91 00000 00042") == "Has a 24/7 helpline at [phone number]"
    assert Q.mask_numbers("Call 0000-0000042 or 108") == "Call [phone number] or 108"
    assert Q.mask_numbers("Open 24x7, 365 days, since 2010-2020, on 12-05-2020") == \
        "Open 24x7, 365 days, since 2010-2020, on 12-05-2020"
    assert Q.mask_numbers(None) is None


def test_phone_numbers_in_quotes_never_reach_a_why_or_the_saved_table(tmp_path, monkeypatch):
    monkeypatch.setenv(Q.DEMO_ENV, "1")
    ft = pd.DataFrame({"fact_id": ["1", "2"], "facility_id": ["a", "a"], "tags": [["h24"], ["scheme"]]})
    facts = pd.DataFrame({"fact_id": ["1", "2"],
                          "text": ["24/7 helpline " + FAKE_PHONE, "PM-JAY desk 0000000077"]})
    infos = Q.build_record_info(ft, facts)
    a = infos["a"]
    assert not DIGIT_RUN.search(a.h24_quote) and not DIGIT_RUN.search(a.scheme_quote)
    assert a.h24_quote == "24/7 helpline [phone number]"
    p = Q.save_record_info(infos, tmp_path / "record_info.parquet")
    assert not DIGIT_RUN.search(pd.read_parquet(p).to_json())
    # an unmasked RecordInfo (built by hand) is still masked on save and in the why
    raw = rec(facility_id="a", tags=frozenset({"h24", "scheme"}), h24_quote="24/7 helpline " + FAKE_PHONE,
              h24_quote_cotags=frozenset(), scheme_quote="PM-JAY desk 0000000077")
    p2 = Q.save_record_info({"a": raw}, tmp_path / "raw.parquet")
    assert not DIGIT_RUN.search(pd.read_parquet(p2).to_json())
    c = card(evidence=[ev("caesarean section", "LSCS done, call 0000000042 for appointments")])
    qs = Q.questions_for(c, "emoc", raw) + Q.questions_for(c, "emoc", None)
    for q in qs:
        assert not DIGIT_RUN.search(q["why"].replace(" ", "")), q


def test_normalize_phone_keeps_only_indian_numbers():
    assert Q.normalize_phone("+91 0000000042") == FAKE_PHONE
    assert Q.normalize_phone("+91 0 0000000042") == FAKE_PHONE  # trunk 0 after +91
    assert Q.normalize_phone("00910000000042") == FAKE_PHONE
    assert Q.normalize_phone("910000000042") == FAKE_PHONE
    assert Q.normalize_phone("1800-000-0000") == "+9118000000000"  # toll-free without country code
    assert Q.normalize_phone("+91 1800 000 0000") == "+9118000000000"
    for bad in ("+1 000 000 0000", "+18000000000", "+880 0000 000000", "+961 0000000", "+0000000042",
                "+91 00000 0000000", "000000000000", "+91000000000"):
        assert Q.normalize_phone(bad) is None, bad


def test_foreign_number_gets_no_call_link():
    raw = pd.DataFrame({"unique_id": ["x", "y"], "officialPhone": ["+1 000 000 0042", "null"],
                        "phone_numbers": [json.dumps(["+880 0000 000000"]), json.dumps(["+1 000 000 0077", FAKE_PHONE_2])]})
    book = Q.build_phone_book(raw)
    assert "x" not in book.phone and "x" in book.unusable
    assert book.phone["y"] == FAKE_PHONE_2  # the first usable entry wins
    info = Q.call_info("x", book, reveal=True)
    assert not info["has_phone"] and info["tel"] is None and "valid Indian number" in info["note"]


def test_duplicate_partners_are_not_a_shared_booking_line():
    raw = pd.DataFrame({"unique_id": ["a", "b", "c"], "officialPhone": [FAKE_PHONE] * 3,
                        "phone_numbers": ["null"] * 3})
    dups = pd.DataFrame({"facility_id_a": ["a"], "facility_id_b": ["b"]})
    book = Q.build_phone_book(raw, dups)
    assert book.shared_with == {"a": 1, "b": 1, "c": 2}
    assert book.duplicate_shared == {"a": 1, "b": 1, "c": 0}
    assert "same facility listed twice" in Q.call_info("a", book)["note"]
    only_dup = Q.build_phone_book(raw.iloc[:2], dups)
    assert only_dup.shared_with == {"a": 0, "b": 0}
    infos = Q.build_record_info(pd.DataFrame({"fact_id": ["1"], "facility_id": ["a"], "tags": [[]]}),
                                None, only_dup)
    assert "shared_phone" not in sources_of(Q.questions_for(card(), "emoc", infos["a"]))


def test_record_info_prefers_a_facility_wide_h24_quote():
    ft = pd.DataFrame({"fact_id": ["1", "2", "3"], "facility_id": ["a"] * 3,
                       "tags": [["h24", "pharmacy"], ["h24"], ["h24"]]})
    facts = pd.DataFrame({"fact_id": ["1", "2", "3"],
                          "text": ["24-hour pharmacy", "Not open 24 hours on Sundays", "Open 24x7"]})
    a = Q.build_record_info(ft, facts)["a"]
    assert a.h24_quote == "Open 24x7" and a.h24_quote_cotags == frozenset()


def test_reason_after_colon_is_lower_cased_but_acronyms_kept():
    assert Q._lower_first("The record's map point") == "the record's map point"
    assert Q._lower_first("PIN code and map point disagree") == "PIN code and map point disagree"
