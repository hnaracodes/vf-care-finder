"""Tests for vf.recipes: taxonomy, v0.2 and v0.1 regexes (and v0), grade rules for all
twelve lines, structured evidence, evidence strength, source confidence.

All fast. The data tests read silver facts, the gold label texts and the gold outputs,
and skip when they are absent.
"""
import hashlib
import itertools
import re
from datetime import date

import numpy as np
import pandas as pd
import pytest

from vf import paths, recipes
from vf.recipes import grade_line, tag_fact

DOC = paths.code_root() / "docs" / "06_capability_recipes.md"

# ---------------------------------------------------------------------------
# taxonomy and patterns
# ---------------------------------------------------------------------------


def test_taxonomy_shape():
    assert len(recipes.EVIDENCE_TAGS_V01) == 13
    assert len(recipes.NEW_TAGS_V02) == 11
    assert len(recipes.EVIDENCE_TAGS) == 24
    assert recipes.EVIDENCE_TAGS[:13] == recipes.EVIDENCE_TAGS_V01  # v0.1 positions unchanged
    assert len(recipes.NOISE_TAGS) == 7
    assert len(set(recipes.ALL_TAGS)) == 31
    assert set(recipes.TAG_PATTERNS) == set(recipes.EVIDENCE_TAGS_V01)
    assert set(recipes.NEW_TAG_RULES) == set(recipes.NEW_TAGS_V02)


def test_tag_definitions_cover_every_tag_in_plain_words():
    assert set(recipes.TAG_DEFINITIONS) == set(recipes.ALL_TAGS)
    for tag, text in recipes.TAG_DEFINITIONS.items():
        assert text and text == text.strip() and "\n" not in text, tag
        assert "—" not in text and "–" not in text, tag  # no em or en dashes
    for rule in recipes.TAGGING_RULES:
        assert "—" not in rule


def test_v0_1_patterns_are_verbatim_from_recipes_doc():
    """The v0.1 regexes must equal the v0.1 table in docs/06_capability_recipes.md."""
    doc = DOC.read_text()
    section = doc.split("## Tag taxonomy", 1)[1].split("## Facility grades", 1)[0]
    row = re.compile(r"^\| `(\w+)` \| [^|]+ \| `(.+)` \|$")
    from_doc = {}
    for line in section.splitlines():
        m = row.match(line)
        if m:
            from_doc[m.group(1)] = m.group(2).replace("\\|", "|")
    assert from_doc == recipes.TAG_PATTERNS
    # v0 differs only in the two patterns v0.1 fixed
    diff = {t for t in recipes.EVIDENCE_TAGS_V01 if recipes.TAG_PATTERNS[t] != recipes.TAG_PATTERNS_V0[t]}
    assert diff == {"csection", "dialysis_unit"}


def test_v0_2_tags_are_the_recipes_doc_v0_2_tags():
    doc = DOC.read_text()
    assert recipes.RECIPE_VERSION == "v0.2"
    section = doc.split("## v0.2: twelve service lines", 1)[1].split("### New grade rules", 1)[0]
    row = re.compile(r"^\| `(\w+)` \| [^|]+ \| `")
    tags = [m.group(1) for line in section.splitlines() if (m := row.match(line))]
    assert tuple(tags) == recipes.NEW_TAGS_V02


def test_v0_1_patterns_are_pinned():
    """Any change to a v0.1 pattern changes v0.1 results; it needs a new version, not an edit."""
    blob = "\n".join([recipes.NEGATION_PATTERN, recipes.VBAC_PATTERN]
                     + [f"{t}={recipes.TAG_PATTERNS[t]}" for t in recipes.EVIDENCE_TAGS_V01]
                     + [f"{t}={recipes.TAG_PATTERNS_V0[t]}" for t in recipes.EVIDENCE_TAGS_V01])
    assert hashlib.sha256(blob.encode()).hexdigest() == V01_PATTERN_SHA256


#: sha256 of the v0.1 and v0 patterns as released (recipe v0.1, commit 94a914b).
V01_PATTERN_SHA256 = "1166d2be1aaa2d9b59636b5738cb960055f2cddad2fc25e917deb85154cb91cf"


V01_POSITIVES = [
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
]


@pytest.mark.parametrize("text, tag", V01_POSITIVES)
@pytest.mark.parametrize("version", ["v0.1", "v0.2"])
def test_tag_fact_positive_v0_1_tags(text, tag, version):
    assert tag in tag_fact(text, version)


@pytest.mark.parametrize("text", [
    "Nephrology department",          # a specialty word is not dialysis
    "Critical care medicine",         # not "critical care unit"
    "Gynecology clinic",
    "Picu and micu beds",             # \bicu\b needs a word boundary
    "Rated 4.3 on JustDial with 72 ratings",
    "Located in Rampura, Kota, Rajasthan",
    "Wheelchair-accessible parking available",
])
@pytest.mark.parametrize("version", ["v0.1", "v0.2"])
def test_tag_fact_negative(text, version):
    assert tag_fact(text, version) == []


def test_tag_fact_order_and_normalization():
    assert tag_fact("24x7 ICU with Ventilator support") == ["h24", "icu", "ventilator"]
    assert tag_fact("  BLOOD\tBANK   and  ICU ") == ["blood", "icu"]
    # raw text and its text_norm give the same tags
    raw = "Haemodialysis  Unit,\nOpen 24 Hrs"
    assert tag_fact(raw) == tag_fact(raw.lower()) == ["h24", "dialysis", "dialysis_unit"]
    # v0.2 tags come after the v0.1 tags, in EVIDENCE_TAGS order
    assert tag_fact("24x7 ICCU and cath lab with MRI and CT scan") == ["h24", "cath_lab", "ccu", "mri", "ct"]


def test_tag_fact_empty():
    for version in recipes.TAGGER_VERSIONS:
        assert tag_fact(None, version) == []
        assert tag_fact("", version) == []
        assert tag_fact("   ", version) == []


def test_tag_facts_matches_tag_fact():
    texts = ["ICU", "icu", None, "Blood bank", "nothing here", "ICU", "MRI scanner", "mri scanner"]
    for version in recipes.TAGGER_VERSIONS:
        assert recipes.tag_facts(texts, version) == [tag_fact(t, version) for t in texts]


def test_v0_is_reproducible():
    """v0 as released, for the gold-set design; any change here breaks that design."""
    assert tag_fact("No ICU facility", version="v0") == ["icu"]                    # negation not handled
    assert tag_fact("Dedicated paediatric section", version="v0") == ["csection"]  # no \b before c[- ]?section
    assert tag_fact("Haemodialysis", version="v0") == ["dialysis", "dialysis_unit"]
    assert tag_fact("caesarean section facility not available on-site", version="v0") == ["csection"]
    assert tag_fact("Vaginal birth after cesarean (VBAC)", version="v0") == ["csection"]
    assert tag_fact("MRI and CT scan", version="v0") == []                         # no v0.2 tags
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
@pytest.mark.parametrize("version", ["v0.1", "v0.2"])
def test_v0_1_fixes(text, tags, version):
    assert tag_fact(text, version) == tags
    assert recipes.tag_facts([text], version) == [tags]


def test_known_crudeness_is_kept():
    """Still crude in v0.1 and v0.2; these pin the behavior so any change is a version bump."""
    for v in ("v0.1", "v0.2"):
        assert tag_fact("No ICU facility", v) == ["icu"]                         # absence without a listed cue
        assert tag_fact("Neonatal intensive care unit", v) == ["nicu", "icu"]    # NICU also counts as ICU
        assert tag_fact("Obstetrics and gynaecology department", v) == ["ob_delivery"]  # specialty-like text
    assert recipes.is_negated("ICU not available") and not recipes.is_negated("ICU available")
    # v0.2's absence cue covers the new tags only, so v0.1 tags keep this crudeness
    assert tag_fact("No ICU or MRI facility") == ["icu"]
    assert recipes.tag_reasons("No ICU or MRI facility") == {"mri": "context_veto"}


# ---------------------------------------------------------------------------
# v0.2 tags
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text, tag", [
    ("bubble CPAP for newborns in the SNCU", "neonatal_resp"),
    ("transport incubator with a neonatal ventilator", "neonatal_resp"),
    ("cpap and ventilator support in nicu", "neonatal_resp"),
    ("two infant flow drivers and a nasal CPAP driver", "neonatal_resp"),
    ("Coronary artery bypass surgery (CABG)", "cardiac_surgery"),
    ("performs heart valve replacement surgeries", "cardiac_surgery"),
    ("first private open heart surgery centre established in 1985", "cardiac_surgery"),
    ("cardiothoracic surgery service", "cardiac_surgery"),
    ("Cardiac surgery department", "cardiac_surgery"),
    ("Full flat panel Philips cath lab", "cath_lab"),
    ("primary angioplasty for acute heart attacks", "cath_lab"),
    ("percutaneous coronary intervention (PCI), including STEMI PCI", "cath_lab"),
    ("has a 5-bed ICCU with round-the-clock intensivist", "ccu"),
    ("admits patients to coronary care unit (ccu) for immediate care", "ccu"),
    ("Siemens 1.5 Tesla MRI scanner", "mri"),
    ("provides magnetic resonance imaging (MRI)", "mri"),
    ("fully digital 3 tesla mri with in-bore ambient experience", "mri"),
    ("Siemens 32-slice CT scanner", "ct"),
    ("offers diagnostic imaging services including MRI, CT, ultrasound, and X-ray", "ct"),
    ("PET-CT imaging", "ct"),
    ("CT-guided biopsy", "ct"),
    ("on-site 32-slice ct scanner with 24/7 reporting", "ct"),
    ("24 x 7 trauma unit", "trauma"),
    ("provides 24x7 accident & emergency care", "trauma"),
    ("trauma care including polytrauma management and emergency chest injuries", "trauma"),
    ("orthopedic and trauma surgery", "trauma"),
    ("Craniotomy for brain tumour", "neurosurgery"),
    ("Neurosurgery services are provided", "neurosurgery"),
    ("performs endoscopic spine surgery", "neurosurgery"),
    ("Has dedicated burns ICU", "burns"),
    ("Burn unit with 6 beds equipped with ventilators", "burns"),
    ("offers burn management services", "burns"),
    ("Treatment of burns", "burns"),
    ("Chemotherapy day care ward", "chemo"),
    ("Hyperthermic intraperitoneal chemotherapy (HIPEC)", "chemo"),
    ("TrueBeam linac radiotherapy is offered", "radiotherapy"),
    ("HDR brachytherapy machine", "radiotherapy"),
    ("Gamma knife radiosurgery system", "radiotherapy"),
    ("Radiation oncology services are provided", "radiotherapy"),
])
def test_v0_2_tag_positive(text, tag):
    assert tag in tag_fact(text)
    assert tag not in tag_fact(text, version="v0.1")


@pytest.mark.parametrize("text, tag", [
    # adult CPAP for sleep apnoea
    ("CPAP device for sleep apnea treatment", "neonatal_resp"),
    ("cpap/bipap therapy is managed for sleep apnea", "neonatal_resp"),
    ("CPAP machines", "neonatal_resp"),
    # gastric, peripheral, vascular bypass; catheter valve procedures; specialty lists; staff bios
    ("Laparoscopic sleeve gastrectomy and gastric bypass", "cardiac_surgery"),
    ("performs peripheral bypass surgery", "cardiac_surgery"),
    ("Brain Bypass Surgery", "cardiac_surgery"),
    ("percutaneous valve repair (balloon mitral valvotomy)", "cardiac_surgery"),
    ("specialties include cardiology, cardiothoracic surgery, neurology, neurosurgery, urology", "cardiac_surgery"),
    ("Dr. A is a cardiac surgeon with 20 years of experience", "cardiac_surgery"),
    ("CGHS rates for all procedures (except cardiac surgery and interventional procedures)", "cardiac_surgery"),
    # carotid, renal and peripheral angioplasty; a cardiology department alone
    ("performs carotid angioplasty and stenting", "cath_lab"),
    ("peripheral angioplasty for treatment of leg disease", "cath_lab"),
    ("Cardiology OPD", "cath_lab"),
    # referral, outsourcing, plans, reading reports, radiologist experience
    ("We refer cases for MRI to a nearby diagnostic centre", "mri"),
    ("MRI services offered via outsourced provider", "mri"),
    ("MRI machine (in development)", "mri"),
    ("two radiologists with over 20 years of combined mri and ct experience", "mri"),
    ("MRI-compatible ventilator", "mri"),
    ("tie-up with X diagnostics for CT scan", "ct"),
    ("plans to install a 32-slice CT scanner", "ct"),
    ("CT scan coming soon", "ct"),
    ("in-house cone beam computed tomography (CBCT) scanner for 3D dental imaging", "ct"),
    ("CT simulator for radiotherapy treatment planning", "ct"),
    ("Cardiothoracic (CT) surgery department", "ct"),
    ("Scan reports reviewed by our doctors", "ct"),
    # psychological, dental, eye trauma
    ("Trauma counselling for survivors", "trauma"),
    ("Birth trauma care", "trauma"),
    ("emergency dental care (dental trauma, knocked-out teeth)", "trauma"),
    ("emergency and trauma services for orbital injuries", "trauma"),
    ("specializes in trauma care and dental care", "trauma"),
    ("a multi-specialty trauma center is under construction", "trauma"),
    # neurology only, specialty lists, staff bios
    ("Neurology consultations", "neurosurgery"),
    ("departments include cardiology, nephrology, neurology, neurosurgery, urology", "neurosurgery"),
    ("Dr. X is a consultant neurosurgeon at Y hospital", "neurosurgery"),
    ("m.ch neurosurgery course duration is 3 years", "neurosurgery"),
    # heartburn, sunburn, lists of specialties
    ("Treatment for heartburn and acidity", "burns"),
    ("Sun burn treatment", "burns"),
    ("specialization includes burns management, cardiology, general medicine, orthopaedics", "burns"),
    # specialty words are not services
    ("Oncology OPD", "chemo"),
    ("Leucoderma clinic provides treatment including chemotherapy medications", "chemo"),
    ("PUVA photochemotherapy for psoriasis", "chemo"),
    ("Medical oncology department", "chemo"),
    ("Radiology department with X-ray", "radiotherapy"),
    ("Low-radiation portable X-ray technology", "radiotherapy"),
    ("specialties include medical oncology, surgical oncology, radiation oncology, and haematology",
     "radiotherapy"),
    ("in development: radiotherapy and cancer care center", "radiotherapy"),
    ("Listed as Top Radiation Oncology Hospital in Chennai (Practo)", "radiotherapy"),
    ("collaboration among radiation oncologists and medical oncologists", "radiotherapy"),
    ("Offers cancer treatment through Surgical Oncology, Medical Oncology, and Radiation Oncology", "radiotherapy"),
])
def test_v0_2_tag_negative(text, tag):
    assert tag not in tag_fact(text)


@pytest.mark.parametrize("text, tag", [
    # absence the v0.1 negation cue misses (review of WP3A, D-entry pending)
    ("No CT scan or MRI facility on-site", "mri"),
    ("No CT scan or MRI facility on-site", "ct"),
    ("No MRI facility at Civil Hospital Palwal", "mri"),
    ("No MRI facility", "mri"),
    # bare plans and proposals
    ("AI-automated MRI planned", "mri"),
    ("CT scanner machine planned for installation at the medical college", "ct"),
    ("Future-ready cath lab planned", "cath_lab"),
    ("CT scan facility planned", "ct"),
    ("PET-CT (planned)", "ct"),
    ("Cath lab coming next year", "cath_lab"),
    ("Proposal for new MRI machine", "mri"),
    ("plans for an invasive cardiology cath lab", "cath_lab"),
    ("centre would provide free CT scans and MRI", "mri"),
    # wording that rejects a procedure or offers something in its place
    ("EECP therapy is an effective natural alternative to bypass surgery.", "cardiac_surgery"),
    ("EECP therapy can delay or avoid bypass surgery or angioplasty", "cardiac_surgery"),
    ("EECP therapy can delay or avoid bypass surgery or angioplasty", "cath_lab"),
    ("EECP therapy is presented as an alternative to angioplasty and bypass surgery", "cath_lab"),
    ("Non-invasive cardiac treatment includes EECP, Biochemical Angioplasty (BCA)", "cath_lab"),
    ("heart blockage treatment without bypass surgery", "cardiac_surgery"),
    ("Provides spine care without spine surgery", "neurosurgery"),
    ("Claims to have avoided over 4000 spine surgeries", "neurosurgery"),
    ("aims to avoid conventional spine surgery", "neurosurgery"),
    # catheter valve procedures, bariatric and vascular bypass
    ("TAVI/TAVR (valve replacement without surgery)", "cardiac_surgery"),
    ("MitraClip procedure for mitral valve repair", "cardiac_surgery"),
    ("laparoscopic duodenojejunal bypass surgeries for obesity and diabetes", "cardiac_surgery"),
    ("bypass surgery for morbid obesity", "cardiac_surgery"),
    ("aortic bypass surgery", "cardiac_surgery"),
    ("vascular surgery includes abdominal aortic aneurysm repair and bypass grafts", "cardiac_surgery"),
    # CT coronary angiography is a scan, not a cath lab
    ("128-slice CT coronary angiography", "cath_lab"),
    ("performs non-invasive coronary angiography by CT scan", "cath_lab"),
])
def test_v0_2_absence_plan_and_anti_procedure_cues(text, tag):
    assert tag not in tag_fact(text)


@pytest.mark.parametrize("text, tags", [
    # the procedure the fact does claim still tags
    ("coronary angioplasty uses a balloon catheter to open a diseased artery without surgery", ["cath_lab"]),
    ("performs CTO angioplasty to treat critical coronary artery blockage, avoiding bypass surgery", ["cath_lab"]),
    ("minimally invasive heart surgeries performed off-pump to avoid cardiopulmonary bypass", ["cardiac_surgery"]),
    ("coronary angioplasty (with or without stent)", ["cath_lab"]),
    ("offers minimally invasive spine surgery and non-surgical treatment for spinal conditions", ["neurosurgery"]),
    ("heart team offers TAVI, CABG and angioplasty", ["cardiac_surgery", "cath_lab"]),
    ("mammary artery bypass grafting", ["cardiac_surgery"]),
    ("cardiothoracic and vascular surgery", ["cardiac_surgery"]),
    # "no.1" is a ranking, "planning" is treatment planning
    ("No.1 brain, spine and trauma centre", ["trauma"]),
    ("Monaco treatment planning system used for IMRT planning", ["radiotherapy"]),
    ("CT scanner used for pre-operative planning", ["ct"]),
])
def test_v0_2_cues_keep_real_claims(text, tags):
    new = [t for t in tag_fact(text) if t in recipes.NEW_TAGS_V02]
    assert new == tags


def test_v0_2_negation_applies_to_new_tags():
    assert tag_fact("MRI not available") == []
    assert tag_fact("Cath lab does not exist yet; does not perform angioplasty") == []
    assert recipes.tag_reasons("MRI not available") == {"mri": "negated"}


def test_tag_reasons_explain_suppression():
    r = recipes.tag_reasons("departments include cardiology, nephrology, neurology, neurosurgery, urology")
    assert r == {"neurosurgery": "specialty_list"}
    assert recipes.tag_reasons("Dr. X is a consultant neurosurgeon") == {"neurosurgery": "staff_bio"}
    assert recipes.tag_reasons("outsourced mri services") == {"mri": "context_veto"}
    assert recipes.tag_reasons("carotid angioplasty") == {"cath_lab": "tag_veto"}
    assert recipes.tag_reasons("Cath lab") == {"cath_lab": "pattern"}
    assert recipes.tag_reasons("Neurosurgery department") == {"neurosurgery": "name_pattern"}
    assert recipes.tag_reasons(None) == {} and recipes.tag_reasons("ICU") == {}


def test_new_tags_never_change_v0_1_tags_on_fixed_examples():
    """Context and list vetoes are for v0.2 tags only: v0.1 tags keep their v0.1 answer."""
    for text in ["ICU; we refer cases for MRI to a nearby centre", "ventilator outsourced",
                 "departments include cardiology, icu, neurosurgery, urology", "blood bank planned",
                 "No ICU or MRI facility", "EECP, an alternative to bypass surgery, in the ICU"]:
        v01 = tag_fact(text, version="v0.1")
        assert [t for t in tag_fact(text) if t in recipes.EVIDENCE_TAGS_V01] == v01


# ---------------------------------------------------------------------------
# regression on data (skip without data)
# ---------------------------------------------------------------------------

GOLD_LABELS = paths.data_dir() / "labels" / "gold_facts.csv"
FACTS = paths.silver_dir() / "facts.parquet"


@pytest.mark.skipif(not GOLD_LABELS.exists(), reason="gold label file absent")
def test_v0_2_equals_v0_1_on_old_tags_for_every_gold_text():
    """Reads the gold texts only (not the labels)."""
    texts = pd.read_csv(GOLD_LABELS, usecols=["text_norm"], keep_default_na=False)["text_norm"]
    old = set(recipes.EVIDENCE_TAGS_V01)
    bad = [t for t in texts if [x for x in tag_fact(t) if x in old] != tag_fact(t, version="v0.1")]
    assert bad == []


@pytest.mark.skipif(not FACTS.exists(), reason="silver facts absent")
def test_v0_2_equals_v0_1_on_old_tags_for_silver_facts():
    texts = pd.read_parquet(FACTS, columns=["text_norm"])["text_norm"].drop_duplicates()
    texts = texts.sample(min(30_000, len(texts)), random_state=0)
    old = set(recipes.EVIDENCE_TAGS_V01)
    bad = [t for t in texts if [x for x in tag_fact(t) if x in old] != tag_fact(t, version="v0.1")]
    assert bad == []


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
@pytest.mark.parametrize("version", ["v0.1", "v0.2"])
def test_grade_emoc(tags, specialties, grade, version):
    assert grade_line("emoc", tags, specialties, version) == grade


@pytest.mark.parametrize("tags, specialties, grade", [
    ([], [], 0),
    ([], ["nephrology"], 1),
    (["dialysis_unit"], [], 0),             # "nephrologist" alone: no claim
    (["dialysis_unit"], ["nephrology"], 1),
    (["dialysis"], [], 2),
    (["dialysis", "dialysis_unit"], [], 3),
])
@pytest.mark.parametrize("version", ["v0.1", "v0.2"])
def test_grade_dialysis(tags, specialties, grade, version):
    assert grade_line("dialysis", tags, specialties, version) == grade


@pytest.mark.parametrize("tags, specialties, grade", [
    ([], [], 0),
    ([], ["criticalCareMedicine"], 1),
    (["ventilator"], [], 0),
    (["ventilator"], ["criticalCareMedicine"], 1),
    (["icu"], [], 2),
    (["icu", "ventilator"], [], 3),
])
@pytest.mark.parametrize("version", ["v0.1", "v0.2"])
def test_grade_icu(tags, specialties, grade, version):
    assert grade_line("icu", tags, specialties, version) == grade


@pytest.mark.parametrize("line, tags, specialties, grade", [
    ("nicu", [], ["neonatologyPerinatalMedicine"], 1),
    ("nicu", ["neonatal_resp"], [], 0),
    ("nicu", ["nicu"], [], 2),
    ("nicu", ["nicu", "neonatal_resp"], [], 3),
    ("nicu", ["nicu", "ventilator"], [], 3),
    ("cardiac_surgery", [], ["cardiothoracicSurgery"], 1),
    ("cardiac_surgery", ["cardiac_surgery"], [], 2),
    ("cardiac_surgery", ["cardiac_surgery", "ccu"], [], 2),
    ("cardiac_surgery", ["cardiac_surgery", "ccu", "blood"], [], 3),
    ("cardiac_surgery", ["cardiac_surgery", "icu", "blood"], [], 3),
    ("cath_lab", [], ["cardiology"], 1),
    ("cath_lab", ["ccu", "h24"], [], 0),
    ("cath_lab", ["cath_lab", "icu"], [], 2),
    ("cath_lab", ["cath_lab", "icu", "emergency"], [], 3),
    ("cath_lab", ["cath_lab", "ccu", "h24"], [], 3),
    ("trauma", ["emergency"], [], 1),
    ("trauma", [], ["orthopedicSurgery"], 1),
    ("trauma", ["trauma"], [], 2),
    ("trauma", ["trauma", "ot", "icu"], [], 2),       # no h24
    ("trauma", ["trauma", "ot", "icu", "h24"], [], 3),
    ("trauma", ["trauma", "ot", "blood", "h24"], [], 3),
    ("burns", [], ["plasticSurgery"], 1),
    ("burns", ["burns", "icu"], [], 2),
    ("burns", ["burns", "icu", "ot"], [], 3),
    ("neurosurgery", [], ["spineNeurosurgery"], 1),
    ("neurosurgery", ["neurosurgery", "icu"], [], 2),
    ("neurosurgery", ["neurosurgery", "icu", "ct"], [], 3),
    ("oncology", [], ["medicalOncology"], 1),
    ("oncology", ["chemo"], [], 2),
    ("oncology", ["radiotherapy"], [], 2),
    ("oncology", ["chemo", "radiotherapy"], [], 3),
    ("mri", [], ["radiology"], 1),
    ("mri", ["mri"], [], 2),
    ("ct", [], ["diagnosticRadiology"], 1),
    ("ct", ["ct"], [], 2),
])
def test_grade_new_lines(line, tags, specialties, grade):
    assert grade_line(line, tags, specialties) == grade


def test_imaging_corroboration_needs_two_facts_or_two_fields():
    # without support counts a repeat condition cannot hold
    assert grade_line("mri", ["mri"]) == 2
    assert grade_line("mri", ["mri"], tag_support={"mri": (2, 1)}) == 3    # two distinct texts
    assert grade_line("mri", ["mri"], tag_support={"mri": (1, 2)}) == 3    # one text, two fields
    assert grade_line("mri", ["mri"], tag_support={"mri": (1, 1)}) == 2
    assert grade_line("ct", ["ct"], tag_support={"ct": ({"ct scan", "32-slice ct"}, {"equipment"})}) == 3
    # the repeat condition is only for mri and ct
    assert grade_line("icu", ["icu", "ventilator"], tag_support={}) == 3


def test_line_not_in_version():
    with pytest.raises(ValueError):
        grade_line("mri", ["mri"], version="v0.1")
    with pytest.raises(ValueError):
        grade_line("emoc", [], version="v9")


def test_grade_facility_all_lines():
    g = recipes.grade_facility(["csection", "blood", "ot", "dialysis", "icu"], [OB, "nephrology"])
    assert tuple(g) == recipes.SERVICE_LINES
    assert {k: g[k] for k in ("emoc", "dialysis", "icu")} == {"emoc": 3, "dialysis": 2, "icu": 2}
    assert g["mri"] == 0 and g["burns"] == 0
    assert recipes.grade_facility([], []) == {line: 0 for line in recipes.SERVICE_LINES}
    g01 = recipes.grade_facility(["csection", "blood", "ot", "dialysis", "icu"], [OB, "nephrology"], "v0.1")
    assert g01 == {"emoc": 3, "dialysis": 2, "icu": 2}


@pytest.mark.parametrize("version", recipes.RULE_VERSIONS)
def test_grades_are_monotone_in_tags(version):
    """Adding tags (or support) never lowers a grade."""
    for line, rule in recipes.RULES_BY_VERSION[version].items():
        tags = rule.tags
        for support in (None, {t: (2, 2) for t in tags}):
            for r in range(len(tags) + 1):
                for subset in itertools.combinations(tags, r):
                    base = grade_line(line, subset, version=version, tag_support=support)
                    for extra in tags:
                        assert grade_line(line, set(subset) | {extra}, version=version,
                                          tag_support=support) >= base, (line, subset, extra)


def test_rules_have_requirement_names():
    for rule in recipes.GRADE_RULES.values():
        assert len(rule.supported_names) == len(rule.supported)
        assert len(rule.corroborated_names) == len(rule.corroborated)
        assert all(n and n == n.strip() for n in rule.supported_names + rule.corroborated_names)
    assert recipes.GRADE_RULES["mri"].has_repeat and recipes.GRADE_RULES["ct"].has_repeat
    assert sum(r.has_repeat for r in recipes.GRADE_RULES.values()) == 2


# ---------------------------------------------------------------------------
# evidence
# ---------------------------------------------------------------------------

def _facts(*pairs):
    return [(text, tag_fact(text)) for text in pairs]


def _facts_f(*triples):
    """(text, field) pairs -> (text, tags, field, fact_id) with ids f0, f1, ..."""
    return [(text, tag_fact(text), fld, f"f{i}") for i, (text, fld) in enumerate(triples)]


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
    assert recipes.select_evidence(facts, "emoc", 3, version="v0.1") == ev


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
    # "Hemodialysis" alone is dialysis without a named unit, so supported, not corroborated
    assert (by["dialysis"].grade, by["dialysis"].grade_label) == (2, "supported")
    assert by["dialysis"].evidence == ["Hemodialysis"]
    assert (by["icu"].grade, by["icu"].grade_label) == (1, "claimed")
    assert by["icu"].tags_present == [] and by["icu"].evidence == []
    assert by["nicu"].grade == 2 and by["nicu"].missing_for_next == ["newborn breathing support or ventilator"]
    empty = recipes.facility_line_grades([], [])
    assert [(r.grade, r.evidence, r.evidence_items, r.evidence_strength) for r in empty] == \
        [(0, [], [], "none")] * 12
    # v0.1: three lines, same answers on them
    rows01 = recipes.facility_line_grades(facts, ["criticalCareMedicine"], version="v0.1")
    assert [r.service_line for r in rows01] == ["emoc", "dialysis", "icu"]
    for r in rows01:
        assert (r.grade, r.tags_present, r.evidence) == (by[r.service_line].grade, by[r.service_line].tags_present,
                                                         by[r.service_line].evidence)


def test_evidence_items_and_missing_for_next():
    facts = _facts_f(("Performs LSCS round the clock", "procedure"), ("In-house blood bank", "capability"),
                     ("Modular operation theatre", "equipment"), ("Maternity ward", "capability"))
    by = {r.service_line: r for r in recipes.facility_line_grades(facts, [OB], n_source_domains=3)}
    e = by["emoc"]
    assert e.grade == 3 and e.missing_for_next == []
    assert e.evidence_items == [
        {"requirement": "caesarean section", "tag": "csection", "fact_id": "f0", "field": "procedure",
         "text": "Performs LSCS round the clock"},
        {"requirement": "blood bank or transfusion", "tag": "blood", "fact_id": "f1", "field": "capability",
         "text": "In-house blood bank"},
        {"requirement": "anaesthesia or operating theatre", "tag": "ot", "fact_id": "f2", "field": "equipment",
         "text": "Modular operation theatre"},
    ]
    assert e.n_supporting_facts == 3 and e.evidence_strength == "strong"
    # trauma: claimed by the h24-free emergency tag? no emergency fact here, so grade 0
    assert by["trauma"].grade == 0
    assert by["trauma"].missing_for_next == ["a related specialty listed or a emergency service mention"]
    # grade 1 by tag: the claim fact is the item
    facts = _facts_f(("Emergency services", "capability"))
    t = {r.service_line: r for r in recipes.facility_line_grades(facts)}["trauma"]
    assert t.grade == 1 and t.evidence_strength == "thin"
    assert t.evidence_items == [{"requirement": "emergency service", "tag": "emergency", "fact_id": "f0",
                                 "field": "capability", "text": "Emergency services"}]
    assert t.missing_for_next == ["trauma care"]


def test_imaging_repeat_items():
    # one text in two fields: corroborated through the field route
    facts = _facts_f(("MRI", "equipment"), ("MRI", "procedure"), ("ICU", "capability"))
    m = {r.service_line: r for r in recipes.facility_line_grades(facts, n_source_domains=4)}["mri"]
    assert m.grade == 3
    assert [(i["requirement"], i["fact_id"], i["field"]) for i in m.evidence_items] == [
        ("MRI scan", "f0", "equipment"), ("MRI named in a second fact or a second field", "f1", "procedure")]
    assert m.evidence == ["MRI"]                      # texts are deduped
    # one sentence in two fields is one supporting fact: corroborated, but thin
    assert m.n_supporting_facts == 1 and m.evidence_strength == "thin"
    # two distinct texts in one field
    facts = _facts_f(("1.5 Tesla MRI scanner", "equipment"), ("MRI brain and spine", "equipment"))
    m = {r.service_line: r for r in recipes.facility_line_grades(facts)}["mri"]
    assert m.grade == 3 and len(m.evidence_items) == 2 and m.evidence == [f[0] for f in facts]
    # one mention only
    facts = _facts_f(("CT scan", "equipment"), ("CT scan", "equipment"))
    c = {r.service_line: r for r in recipes.facility_line_grades(facts)}["ct"]
    assert c.grade == 2 and c.missing_for_next == ["CT named in a second fact or a second field"]
    assert c.evidence_strength == "thin" and c.n_supporting_facts == 1


@pytest.mark.parametrize("grade, n, domains, site, want", [
    (0, 0, 5, "x.org", "none"),
    (1, 0, 5, "x.org", "thin"),
    (2, 1, 5, "x.org", "thin"),        # single fact
    (3, 4, 1, None, "thin"),           # one domain, no website
    (3, 4, 0, "", "thin"),
    (2, 3, 5, "x.org", "moderate"),    # strong needs grade 3
    (3, 2, 5, "x.org", "moderate"),    # strong needs 3 facts
    (3, 3, 2, None, "strong"),
    (3, 3, 1, "https://x.org", "strong"),
    (2, 2, 1, "https://x.org", "moderate"),
    (3, 3, pd.NA, None, "thin"),
])
def test_evidence_strength(grade, n, domains, site, want):
    assert recipes.evidence_strength(grade, n, domains, site) == want


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

V2_COLUMNS = ["facility_id", "service_line", "grade", "grade_label", "tags_present", "evidence",
              "evidence_items", "missing_for_next", "n_supporting_facts", "evidence_strength",
              "source_confidence", "method", "recipe_version"]


@pytest.fixture(scope="module")
def gold_grades():
    if not (GRADES.exists() and FACILITIES.exists()):
        pytest.skip("gold grades not built")
    g = pd.read_parquet(GRADES)
    if (g["method"] != "regex").any():
        pytest.skip("grades were overwritten by a later method")
    if (g["recipe_version"] != recipes.RECIPE_VERSION).any():
        pytest.skip("grades are from another recipe version; rerun scripts/wp3_regex_baseline.py")
    return g


def test_capability_grades_output_shape(gold_grades):
    g = gold_grades
    fac = pd.read_parquet(FACILITIES, columns=["facility_id"])
    assert list(g.columns) == V2_COLUMNS
    assert len(g) == len(recipes.SERVICE_LINES) * len(fac)
    assert not g.duplicated(["facility_id", "service_line"]).any()
    assert set(g["service_line"]) == set(recipes.SERVICE_LINES)
    assert g["grade"].between(0, 3).all()
    assert (g["evidence"].map(len) <= recipes.MAX_EVIDENCE).all()
    assert g["source_confidence"].between(0, 1).all()
    assert set(g["evidence_strength"]) <= {"none", "thin", "moderate", "strong"}


@pytest.mark.skipif(not FACTS.exists(), reason="silver facts absent")
def test_grade_2_evidence_is_verbatim_from_facts(gold_grades):
    """Every grade 2+ row has an evidence fact id, and every quoted text exists verbatim
    in silver/facts.parquet as that fact of that facility, in that field."""
    facts = pd.read_parquet(FACTS, columns=["fact_id", "facility_id", "field", "text"]).set_index("fact_id")
    g2 = gold_grades[gold_grades["grade"] >= 2]
    assert len(g2) > 0
    assert g2["evidence_items"].map(lambda items: any(i["fact_id"] for i in items)).all()
    items = pd.DataFrame([dict(i, facility_id_row=f) for f, its in zip(g2["facility_id"], g2["evidence_items"])
                          for i in its])
    j = items.merge(facts.reset_index(), on="fact_id", how="left", suffixes=("", "_fact"))
    assert j["text_fact"].notna().all()
    assert (j["text"] == j["text_fact"]).all()
    assert (j["facility_id"] == j["facility_id_row"]).all()
    assert (j["field"] == j["field_fact"]).all()
    texts = set(zip(facts["facility_id"], facts["text"]))
    assert all((f, e) in texts for f, ev in zip(g2["facility_id"], g2["evidence"]) for e in ev)
