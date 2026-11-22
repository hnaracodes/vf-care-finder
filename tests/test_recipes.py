"""Tests for vf.recipes: taxonomy, v0.1 regexes (and v0), grade rules, evidence, source confidence.

All fast. The last test reads the gold outputs and skips if they are absent.
"""
import re
from datetime import date

import numpy as np
import pandas as pd
import pytest

from vf import paths, recipes
from vf.recipes import grade_line, tag_fact

# ---------------------------------------------------------------------------
# taxonomy and patterns
# ---------------------------------------------------------------------------


def test_taxonomy_shape():
    assert len(recipes.EVIDENCE_TAGS) == 13
    assert len(recipes.NOISE_TAGS) == 7
    assert len(set(recipes.ALL_TAGS)) == 20
    assert set(recipes.TAG_DEFINITIONS) == set(recipes.ALL_TAGS)
    assert set(recipes.TAG_PATTERNS) == set(recipes.EVIDENCE_TAGS)


def test_patterns_are_verbatim_from_recipes_doc():
    """The current regexes must equal the table in docs/06_capability_recipes.md."""
    doc = (paths.code_root() / "docs" / "06_capability_recipes.md").read_text()
    row = re.compile(r"^\| `(\w+)` \| [^|]+ \| `(.+)` \|$")
    from_doc = {}
    for line in doc.splitlines():
        m = row.match(line)
        if m:
            from_doc[m.group(1)] = m.group(2).replace("\\|", "|")
    assert from_doc == recipes.TAG_PATTERNS
    assert recipes.RECIPE_VERSION == "v0.1"
    assert "Recipe version: `v0.1`" in doc
    # v0 differs only in the two patterns v0.1 fixed
    diff = {t for t in recipes.EVIDENCE_TAGS if recipes.TAG_PATTERNS[t] != recipes.TAG_PATTERNS_V0[t]}
    assert diff == {"csection", "dialysis_unit"}


@pytest.mark.parametrize("text, tag", [
    ("Maternity ward with 20 beds", "ob_delivery"),
    ("Labour room available", "ob_delivery"),
    ("Labor ward", "ob_delivery"),
    ("Normal delivery and family planning", "ob_delivery"),
    ("Obstetric care", "ob_delivery"),
    ("Performs C-section deliveries", "csection"),
    ("c section", "csection"),
    ("Caesarean delivery", "csection"),
    ("cesarean section", "csection"),
    ("LSCS performed", "csection"),
    ("In-house blood bank", "blood"),
    ("Blood transfusion services", "blood"),
    ("Anaesthetist available", "anesthesia"),
    ("Anesthesia machine", "anesthesia"),
    ("Modular operation theatre", "ot"),
    ("Two operating theaters", "ot"),
    ("Open 24/7", "h24"),
    ("24x7 pharmacy", "h24"),
    ("Open 24 hours", "h24"),
    ("24hrs emergency", "h24"),
    ("Round the clock care", "h24"),
    ("Level III NICU", "nicu"),
    ("SNCU with 12 cots", "nicu"),
    ("Neonatal intensive care unit", "nicu"),
    ("Dialysis services", "dialysis"),
    ("Hemodialysis machines", "dialysis_unit"),
    ("Haemodialysis unit", "dialysis_unit"),
    ("Dialysis unit with 6 beds", "dialysis_unit"),
    ("Visiting nephrologist", "dialysis_unit"),
    ("10-bed ICU", "icu"),
    ("Intensive care", "icu"),
    ("Critical care unit", "icu"),
    ("Ventilator support", "ventilator"),
    ("Emergency services", "emergency"),
    ("Trauma care", "emergency"),
    ("Casualty ward", "emergency"),
    ("Empanelled under Ayushman Bharat", "scheme"),
    ("PMJAY accepted", "scheme"),
    ("CGHS approved", "scheme"),
])
def test_tag_fact_positive(text, tag):
    assert tag in tag_fact(text)


@pytest.mark.parametrize("text", [
    "Nephrology department",          # a specialty word is not dialysis
    "Critical care medicine",         # not "critical care unit"
    "Gynecology clinic",
    "Picu and micu beds",             # \bicu\b needs a word boundary
    "Rated 4.3 on JustDial with 72 ratings",
    "Located in Rampura, Kota, Rajasthan",
    "Wheelchair-accessible parking available",
])
def test_tag_fact_negative(text):
    assert tag_fact(text) == []


def test_tag_fact_order_and_normalization():
    assert tag_fact("24x7 ICU with Ventilator support") == ["h24", "icu", "ventilator"]
    assert tag_fact("  BLOOD\tBANK   and  ICU ") == ["blood", "icu"]
    # raw text and its text_norm give the same tags
    raw = "Haemodialysis  Unit,\nOpen 24 Hrs"
    assert tag_fact(raw) == tag_fact(raw.lower()) == ["h24", "dialysis", "dialysis_unit"]


def test_tag_fact_empty():
    assert tag_fact(None) == []
    assert tag_fact("") == []
    assert tag_fact("   ") == []


def test_tag_facts_matches_tag_fact():
    texts = ["ICU", "icu", None, "Blood bank", "nothing here", "ICU"]
    assert recipes.tag_facts(texts) == [tag_fact(t) for t in texts]


def test_v0_is_reproducible():
    """v0 as released, for the gold-set design; any change here breaks that design."""
    assert tag_fact("No ICU facility", version="v0") == ["icu"]                    # negation not handled
    assert tag_fact("Dedicated paediatric section", version="v0") == ["csection"]  # no \b before c[- ]?section
    assert tag_fact("Haemodialysis", version="v0") == ["dialysis", "dialysis_unit"]
    assert tag_fact("caesarean section facility not available on-site", version="v0") == ["csection"]
    assert tag_fact("Vaginal birth after cesarean (VBAC)", version="v0") == ["csection"]
    with pytest.raises(ValueError):
        tag_fact("ICU", version="v9")


@pytest.mark.parametrize("text, tags", [
    # 1. word boundary before "c section"
    ("Dedicated paediatric section", []),
    ("all economic sections of society", []),
    ("case under IPC sections 312, 316", []),
    ("C section deliveries", ["csection"]),
    # 2. VBAC alone is not a caesarean; a real caesarean mention next to it still counts
    ("provides vaginal birth after cesarean (vbac) services", []),
    ("offers vaginal birth after c-section", []),
    ("VBAC and elective caesarean section", ["csection"]),
    # 3. haemodialysis alone is dialysis, not a named unit
    ("Haemodialysis", ["dialysis"]),
    ("provides hemodialysis treatment", ["dialysis"]),
    # 4. negation and absence cues remove every evidence tag
    ("caesarean section facility not available on-site; referrals to lnjp kurukshetra", []),
    ("Blood bank not available", []),
    ("does not provide emergency services", []),
    ("dialysis services not explicitly specified in provided content", []),
    ("no specific procedures listed for the blood bank in the provided content", []),
    ("lack of government icu", []),
    # ...but bare "no" and "non" are not cues
    ("non-invasive ventilation in icu", ["icu"]),
    ("icu no.1 has 11 beds", ["icu"]),
    ("free dialysis center offering dialysis services at no cost", ["dialysis", "dialysis_unit"]),
    ("there are no other blood banks in golaghat", ["blood"]),
])
def test_v0_1_fixes(text, tags):
    assert tag_fact(text) == tags
    assert recipes.tag_facts([text]) == [tags]


def test_known_crudeness_is_kept():
    """Still crude in v0.1; these pin the behavior so any change is a version bump."""
    assert tag_fact("No ICU facility") == ["icu"]                         # absence without a listed cue
    assert tag_fact("Neonatal intensive care unit") == ["nicu", "icu"]    # NICU also counts as ICU
    assert tag_fact("Obstetrics and gynaecology department") == ["ob_delivery"]  # specialty-like text
    assert recipes.is_negated("ICU not available") and not recipes.is_negated("ICU available")


# ---------------------------------------------------------------------------
# grade rules
# ---------------------------------------------------------------------------

OB = "gynecologyAndObstetrics"


@pytest.mark.parametrize("tags, specialties, grade", [
    ([], [], 0),
    ([], [OB], 1),
    ([], ["obstetricsAndMaternityCare"], 1),
    ([], ["maternalFetalMedicineOrPerinatology"], 1),
    (["ob_delivery"], [], 1),
    (["nicu", "h24"], [], 0),                         # bonus tags alone are not a claim
    (["blood", "ot", "anesthesia"], [OB], 1),         # enabling pieces without csection
    (["csection"], [], 2),                            # supported without a specialty claim
    (["csection", "blood"], [OB], 2),
    (["csection", "ot", "anesthesia"], [OB], 2),      # no blood
    (["csection", "blood", "ot"], [], 3),
    (["csection", "blood", "anesthesia"], [OB], 3),
    (["csection", "blood", "anesthesia", "ot", "nicu", "h24"], [OB], 3),
])
def test_grade_emoc(tags, specialties, grade):
    assert grade_line("emoc", tags, specialties) == grade


@pytest.mark.parametrize("tags, specialties, grade", [
    ([], [], 0),
    ([], ["nephrology"], 1),
    (["dialysis_unit"], [], 0),             # "nephrologist" alone: no claim in v0
    (["dialysis_unit"], ["nephrology"], 1),
    (["dialysis"], [], 2),
    (["dialysis", "dialysis_unit"], [], 3),
])
def test_grade_dialysis(tags, specialties, grade):
    assert grade_line("dialysis", tags, specialties) == grade


@pytest.mark.parametrize("tags, specialties, grade", [
    ([], [], 0),
    ([], ["criticalCareMedicine"], 1),
    (["ventilator"], [], 0),
    (["ventilator"], ["criticalCareMedicine"], 1),
    (["icu"], [], 2),
    (["icu", "ventilator"], [], 3),
])
def test_grade_icu(tags, specialties, grade):
    assert grade_line("icu", tags, specialties) == grade


def test_grade_facility_all_lines():
    g = recipes.grade_facility(["csection", "blood", "ot", "dialysis", "icu"], [OB, "nephrology"])
    assert g == {"emoc": 3, "dialysis": 2, "icu": 2}
    assert recipes.grade_facility([], []) == {"emoc": 0, "dialysis": 0, "icu": 0}
    assert tuple(g) == recipes.SERVICE_LINES


def test_grades_are_monotone_in_tags():
    """Adding tags never lowers a grade."""
    import itertools
    for line, rule in recipes.GRADE_RULES.items():
        tags = rule.tags
        for r in range(len(tags) + 1):
            for subset in itertools.combinations(tags, r):
                base = grade_line(line, subset)
                for extra in tags:
                    assert grade_line(line, set(subset) | {extra}) >= base, (line, subset, extra)


# ---------------------------------------------------------------------------
# evidence
# ---------------------------------------------------------------------------

def _facts(*pairs):
    return [(text, tag_fact(text)) for text in pairs]


def test_select_evidence_covers_requirements_first():
    facts = _facts(
        "Open 24/7",                          # bonus only: never evidence
        "Modern operation theatre",
        "Maternity ward",
        "In-house blood bank",
        "Performs caesarean sections",
        "Second operation theatre",
        "Normal delivery",
    )
    assert recipes.grade_line("emoc", {t for _, ts in facts for t in ts}) == 3
    ev = recipes.select_evidence(facts, "emoc", 3)
    assert ev[:3] == ["Performs caesarean sections", "In-house blood bank", "Modern operation theatre"]
    assert len(ev) == 5
    assert "Open 24/7" not in ev


def test_select_evidence_prefers_a_fact_covering_several_groups():
    facts = _facts("Blood bank", "OT", "C-section in our operation theatre with blood bank")
    ev = recipes.select_evidence(facts, "emoc", 3)
    assert ev[0] == "C-section in our operation theatre with blood bank"


def test_select_evidence_limits_and_dedupes():
    facts = _facts(*["ICU"] * 3, "icu", *[f"ICU wing {i}" for i in range(10)], "Ventilator")
    ev = recipes.select_evidence(facts, "icu", 3)
    assert len(ev) == 5
    assert ev[0] == "ICU" and ev[1] == "Ventilator"
    assert len({e.lower() for e in ev}) == 5
    assert recipes.select_evidence(facts, "icu", 3, k=2) == ["ICU", "Ventilator"]


def test_select_evidence_grade_zero_and_specialty_claim():
    facts = _facts("Open 24/7", "Level III NICU")
    assert recipes.select_evidence(facts, "emoc", 0) == []
    # claimed by specialty only: no fact carries a rule tag, so no evidence
    assert recipes.select_evidence(facts, "emoc", 1) == []
    # claimed by tag: the ob_delivery fact is the evidence
    facts = _facts("Maternity home", "Open 24/7")
    assert recipes.select_evidence(facts, "emoc", 1) == ["Maternity home"]


def test_facility_line_grades():
    facts = _facts("Caesarean section", "Blood bank", "24x7 pharmacy", "Level III NICU", "Hemodialysis")
    rows = recipes.facility_line_grades(facts, ["criticalCareMedicine"])
    by = {r.service_line: r for r in rows}
    assert [r.service_line for r in rows] == list(recipes.SERVICE_LINES)
    assert (by["emoc"].grade, by["emoc"].grade_label) == (2, "supported")
    assert by["emoc"].tags_present == ["csection", "blood", "nicu", "h24"]
    assert by["emoc"].evidence == ["Caesarean section", "Blood bank"]
    # v0.1: "Hemodialysis" alone is dialysis without a named unit, so supported, not corroborated
    assert (by["dialysis"].grade, by["dialysis"].grade_label) == (2, "supported")
    assert by["dialysis"].evidence == ["Hemodialysis"]
    assert (by["icu"].grade, by["icu"].grade_label) == (1, "claimed")
    assert by["icu"].tags_present == [] and by["icu"].evidence == []
    empty = recipes.facility_line_grades([], [])
    assert [(r.grade, r.evidence) for r in empty] == [(0, [])] * 3


# ---------------------------------------------------------------------------
# source confidence
# ---------------------------------------------------------------------------

AS_OF = recipes.SNAPSHOT_DATE


def test_source_confidence_bounds():
    assert recipes.source_confidence(10, "https://x.org", AS_OF, True) == 1.0
    assert recipes.source_confidence(0, None, None, None) == 0.0
    assert recipes.source_confidence(0, None, None, False) == 0.0


def test_source_confidence_terms():
    # domains saturate at 6
    assert recipes.source_confidence(3, None, None, None) == 0.2
    assert recipes.source_confidence(6, None, None, None) == 0.4
    assert recipes.source_confidence(60, None, None, None) == 0.4
    assert recipes.source_confidence(0, "https://x.org", None, None) == 0.2
    assert recipes.source_confidence(0, "   ", None, None) == 0.0
    assert recipes.source_confidence(0, True, None, None) == 0.2
    assert recipes.source_confidence(0, np.True_, None, None) == 0.2
    assert recipes.source_confidence(0, np.False_, None, None) == 0.0
    assert recipes.source_confidence(0, None, None, np.True_) == 0.2


def test_recency_score():
    assert recipes.recency_score(date(2026, 1, 1)) == 1.0          # under a year old
    assert recipes.recency_score(date(2023, 11, 21)) == pytest.approx(0.5, abs=0.01)  # three years
    assert recipes.recency_score(date(2015, 1, 1)) == 0.0
    assert recipes.recency_score(date(2027, 1, 1)) == 0.0          # future date is an error
    assert recipes.recency_score(pd.Timestamp("2026-06-01")) == 1.0
    assert recipes.recency_score("2026-06-01") == 1.0
    for missing in (None, pd.NaT, pd.NA, np.nan, "not a date"):
        assert recipes.recency_score(missing) == 0.0


def test_source_confidence_handles_pandas_missing():
    assert recipes.source_confidence(pd.NA, pd.NA, pd.NaT, pd.NA) == 0.0
    assert recipes.source_confidence(np.int64(4), np.nan, None, pd.NA) == pytest.approx(0.4 * 4 / 6, abs=1e-4)


# ---------------------------------------------------------------------------
# outputs (skip until scripts/wp3_regex_baseline.py has run)
# ---------------------------------------------------------------------------

GRADES = paths.gold_dir() / "capability_grades.parquet"
FACILITIES = paths.silver_dir() / "facilities.parquet"


@pytest.mark.skipif(not (GRADES.exists() and FACILITIES.exists()), reason="gold grades not built")
def test_capability_grades_output_shape():
    g = pd.read_parquet(GRADES)
    if (g["method"] != "regex").any():
        pytest.skip("grades were overwritten by a later method")
    fac = pd.read_parquet(FACILITIES, columns=["facility_id"])
    assert list(g.columns) == ["facility_id", "service_line", "grade", "grade_label", "tags_present",
                               "evidence", "source_confidence", "method", "recipe_version"]
    assert len(g) == 3 * len(fac)
    assert not g.duplicated(["facility_id", "service_line"]).any()
    assert set(g["service_line"]) == set(recipes.SERVICE_LINES)
    assert g["grade"].between(0, 3).all()
    assert (g["evidence"].map(len) <= recipes.MAX_EVIDENCE).all()
    assert g["source_confidence"].between(0, 1).all()
