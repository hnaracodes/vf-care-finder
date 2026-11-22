"""Tests for recipe v0.3 (D048): a rule-level regrade of the frozen v0.2 tags.

Hand-made records check each v0.3 rule; data tests check that v0.2 grades are still
reproduced from the frozen tags, and that the v0.3 output quotes facts verbatim. Data
tests skip when the files are absent.
"""
import importlib.util
import itertools
import json

import pandas as pd
import pytest

from vf import paths, recipes
from vf.recipes import facility_line_grades, grade_line

V3 = recipes.RULE_VERSION_V03
OB = "gynecologyAndObstetrics"


def facts_of(*texts):
    """(text, tags, field, fact_id) tuples with the v0.2 regex tags, like the frozen file."""
    return [(t, recipes.tag_fact(t, "v0.2"), "capability", f"f{i}") for i, t in enumerate(texts)]


def line(facts, name, version=V3, specialties=()):
    return next(g for g in facility_line_grades(facts, specialties, version=version) if g.service_line == name)


# ---------------------------------------------------------------------------
# v0.1 and v0.2 stay reproducible
# ---------------------------------------------------------------------------


def test_v02_rules_untouched():
    assert recipes.RECIPE_VERSION == "v0.2"
    assert recipes.RULE_VERSIONS == ("v0.1", "v0.2")
    assert recipes.RULES_BY_VERSION["v0.2"] is recipes.GRADE_RULES
    assert recipes.RULES_BY_VERSION["v0.1"] is recipes.GRADE_RULES_V01
    assert all(not r.corroborated_alts and not r.note_groups and not r.claim_next_name
               for v in ("v0.1", "v0.2") for r in recipes.RULES_BY_VERSION[v].values())
    assert "v0.3" not in recipes.TAGGER_VERSIONS  # no v0.3 tagger: tags are frozen


def test_v02_answers_unchanged_on_v03_cases():
    """The cases v0.3 changes still get their v0.2 answer under version='v0.2'."""
    assert line(facts_of("NICU", "ICU with ventilators"), "nicu", "v0.2").grade == 3
    assert line(facts_of("SNCU (Special Newborn Care Unit)"), "nicu", "v0.2").grade == 2
    assert line(facts_of("Caesarean section", "Blood bank"), "emoc", "v0.2").grade == 2
    assert line(facts_of("Cath lab", "ICU", "24x7 emergency"), "cath_lab", "v0.2").grade == 3
    assert line(facts_of("Dialysis", "In-house nephrologist"), "dialysis", "v0.2").grade == 3
    lg = line(facts_of("NICU", "ICU with ventilators"), "nicu", "v0.2")
    assert lg.level_note is None and lg.level_note_items == []


def test_unchanged_lines_share_v02_rules():
    for name in recipes.SERVICE_LINES:
        same = recipes.GRADE_RULES_V03[name] is recipes.GRADE_RULES[name]
        assert same == (name not in recipes.CHANGED_LINES_V03), name
    assert tuple(recipes.GRADE_RULES_V03) == recipes.SERVICE_LINES


# ---------------------------------------------------------------------------
# v0.3 rules on hand-made records
# ---------------------------------------------------------------------------


def test_nicu_sncu_only_is_claimed():
    lg = line(facts_of("District Hospital has a Special Newborn Care Unit (SNCU)"), "nicu")
    assert lg.grade == 1
    assert [i["requirement"] for i in lg.evidence_items] == ["names an SNCU (level II), not a NICU"]
    assert lg.evidence_items[0]["tag"] == "nicu"
    assert lg.missing_for_next == [recipes.GRADE_RULES_V03["nicu"].supported_names[0]]


def test_nicu_generic_ventilator_does_not_corroborate():
    lg = line(facts_of("Neonatal Intensive Care Unit (NICU)", "ICU with ventilators"), "nicu")
    assert lg.grade == 2
    assert lg.tags_present == ["nicu", "ventilator"]  # real tags only
    assert line(facts_of("NICU", "SNCU with ventilators"), "nicu").grade == 2  # FBNC: SNCU + ventilator is not NICU


@pytest.mark.parametrize("texts", [
    ("NICU", "Neonatal ventilators"),            # ventilator in a newborn fact
    ("NICU", "Bubble CPAP"),                     # neonatal_resp
    ("Level III NICU",),                         # level III mention
    ("NICU Level-3",),
    ("Has a tertiary level NICU",),
])
def test_nicu_corroborated(texts):
    lg = line(facts_of(*texts), "nicu")
    assert lg.grade == 3, texts
    assert all(i["tag"] in recipes.EVIDENCE_TAGS for i in lg.evidence_items)


def test_nicu_level_ii_is_not_level_iii():
    assert line(facts_of("Level II NICU"), "nicu").grade == 2


def test_emoc_caesarean_and_blood_is_corroborated():
    lg = line(facts_of("Caesarean section", "Blood bank on site"), "emoc", specialties=[OB])
    assert lg.grade == 3
    assert [i["tag"] for i in lg.evidence_items] == ["csection", "blood"]
    assert "WHO 2009 Table 4" in lg.evidence_items[0]["requirement"]
    assert lg.level_note is None and lg.level_note_items == []


def test_emoc_fru_note():
    f = facts_of("LSCS performed", "Blood transfusion", "Anaesthetist on call", "24x7 services")
    lg = line(f, "emoc")
    assert lg.grade == 3
    assert lg.level_note and lg.level_note.startswith("FRU-level")
    assert [i["tag"] for i in lg.level_note_items] == ["anesthesia", "h24"]
    assert {i["text"] for i in lg.level_note_items} == {"Anaesthetist on call", "24x7 services"}
    # the note needs grade 3: no blood, no note
    lg2 = line(facts_of("LSCS performed", "Anaesthetist on call", "24x7 services"), "emoc")
    assert lg2.grade == 2 and lg2.level_note is None


def test_emoc_fetal_transfusion_is_not_blood():
    assert line(facts_of("Caesarean delivery", "Fetal Blood Transfusion"), "emoc").grade == 2
    assert line(facts_of("Caesarean delivery", "Intrauterine blood transfusion performed"), "emoc").grade == 2
    assert line(facts_of("Caesarean delivery", "Blood bank and intrauterine transfusion"), "emoc").grade == 3


def test_cath_lab_rules():
    assert line(facts_of("Cath lab", "CCU"), "cath_lab").grade == 3  # h24 / emergency no longer needed
    lg = line(facts_of("Cath lab", "ICU", "Ventilators"), "cath_lab")
    assert lg.grade == 3
    assert [i["tag"] for i in lg.evidence_items] == ["cath_lab", "icu", "ventilator"]
    lg = line(facts_of("Cath lab", "ICU", "24x7 emergency"), "cath_lab")
    assert lg.grade == 2
    assert len(lg.missing_for_next) == 1 and ", or " in lg.missing_for_next[0]  # CCU, or ventilator
    assert line(facts_of("Cath lab"), "cath_lab").missing_for_next == [
        recipes.GRADE_RULES_V03["cath_lab"].corroborated_names[1]]


def test_dialysis_needs_unit_not_nephrologist():
    assert line(facts_of("Dialysis services", "In-house nephrologist"), "dialysis").grade == 2
    assert line(facts_of("Dialysis services", "5-bed dialysis unit"), "dialysis").grade == 3
    assert line(facts_of("Dialysis services", "Hemodialysis machines"), "dialysis").grade == 3
    # haemodialysis alone is the procedure, not an enabling piece (v0.1 change 3)
    assert line(facts_of("Haemodialysis"), "dialysis").grade == 2
    # a dialysis_unit fact (nephrologist) that also names haemodialysis
    assert line(facts_of("Haemodialysis under a nephrologist"), "dialysis").grade == 3


def test_qualifiers():
    q = recipes.qualify_tags
    assert "nicu_named" in q("Level III NICU", ["nicu"]) and "nicu_level3" in q("Level III NICU", ["nicu"])
    assert "nicu_named" not in q("SNCU", ["nicu"])
    assert "ventilator_newborn" not in q("SNCU with ventilator", ["ventilator", "nicu"])
    assert "ventilator_newborn" in q("NICU and SNCU with ventilators", ["ventilator", "nicu"])
    assert q("anything", ["nicu"], "v0.2") == frozenset({"nicu"})
    assert "nicu_named" not in q("NICU", [])  # needs the base tag on the same fact
    assert recipes.base_tag("dialysis_unit_named") == "dialysis_unit" and recipes.base_tag("icu") == "icu"


def test_v03_grades_are_monotone_in_tags():
    for name in recipes.CHANGED_LINES_V03:
        tags = recipes.GRADE_RULES_V03[name].tags
        for r in range(len(tags) + 1):
            for subset in itertools.combinations(tags, r):
                base = grade_line(name, subset, version=V3)
                for extra in tags:
                    assert grade_line(name, set(subset) | {extra}, version=V3) >= base, (name, subset, extra)


def test_grade_zero_missing_text():
    assert line([], "nicu").missing_for_next == ["a related specialty listed, or an SNCU or NICU mention"]
    assert line([], "emoc").missing_for_next == ["a related specialty listed or a maternity or delivery service mention"]


# ---------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------

GOLD = paths.gold_dir()
SILVER = paths.silver_dir()
need_data = pytest.mark.skipif(
    not ((GOLD / "fact_tags.parquet").exists() and (GOLD / "capability_grades.parquet").exists()
         and (SILVER / "facts.parquet").exists() and (SILVER / "facilities.parquet").exists()),
    reason="gold or silver data absent")


def _script():
    spec = importlib.util.spec_from_file_location("wp3_regrade_v03",
                                                  paths.code_root() / "scripts" / "wp3_regrade_v03.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def regraded():
    s = _script()
    fac, facts = s.RB.load_inputs()
    ft = pd.read_parquet(GOLD / "fact_tags.parquet")
    assert (ft["fact_id"].values == facts["fact_id"].values).all()
    per_fac = s.RB.facts_by_facility(facts, ft["tags"].map(list))
    method = ft["method"].iloc[0]
    return s, facts, s.build(fac, per_fac, "v0.2", method), s.build(fac, per_fac, V3, method)


@need_data
def test_v02_grades_reproduce_from_frozen_tags(regraded):
    s, _, g02, _ = regraded
    old = pd.read_parquet(GOLD / "capability_grades.parquet")
    cols = s.RB.GRADES_SCHEMA.names
    assert len(old) == len(g02)
    assert s.frame_diff(old.reset_index(drop=True), g02.reset_index(drop=True), cols) == {c: 0 for c in cols}


@need_data
def test_v03_evidence_verbatim_in_silver_facts(regraded):
    _, facts, _, g03 = regraded
    text_of = dict(zip(facts["fact_id"], facts["text"]))
    items = [it for col in ("evidence_items", "level_note_items") for its in g03[col] for it in its]
    assert items and all(text_of[it["fact_id"]] == it["text"] for it in items)
    texts = set(facts["text"])
    assert all(t in texts for ev in g03["evidence"] for t in ev)


@need_data
def test_v03_changes_only_the_four_lines(regraded):
    _, _, g02, g03 = regraded
    diff = g02.grade.values != g03.grade.values
    assert set(g02.service_line[diff]) <= set(recipes.CHANGED_LINES_V03)


@pytest.mark.skipif(not (GOLD / "capability_grades_v03.parquet").exists(), reason="v0.3 output absent")
def test_v03_file_matches_code_and_inputs(regraded):
    s, _, _, g03 = regraded
    on_disk = pd.read_parquet(GOLD / "capability_grades_v03.parquet")
    meta = json.loads((GOLD / "capability_grades_v03.parquet.meta.json").read_text())
    assert meta["recipe_version"] == V3 and set(on_disk["recipe_version"]) == {V3}
    assert meta["inputs"]["gold/fact_tags.parquet"]["sha256"] == s.RB.sha256(GOLD / "fact_tags.parquet")
    assert list(on_disk.columns) == s.GRADES_V03_SCHEMA.names
    assert (on_disk["grade"].values == g03["grade"].values).all()
