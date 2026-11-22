"""Capability recipes: the fact tag taxonomy, the regex baseline tagger, the
facility grade rules, evidence selection, evidence strength, and source
confidence.

This is ``docs/06_capability_recipes.md`` as code, recipe version ``v0.2``
(twelve service lines, 24 evidence tags). Two layers, kept separate on purpose:

1. Fact level. ``tag_fact(text)`` gives a fact zero or more evidence tags.

   * The 13 v0.1 tags keep their v0.1 regexes verbatim, so v0.2 gives exactly
     the v0.1 answer on them. v0.1 fixed three failure modes of v0 (a word
     boundary before "c section" and VBAC wording for ``csection``, no
     "haemodialysis"-alone branch in ``dialysis_unit``, and a negation or
     absence cue removes every tag). ``tag_fact(text, version="v0.1")`` gives
     the 13 v0.1 tags only; ``version="v0"`` reproduces v0 for the gold-set
     design.
   * The 11 v0.2 tags (``NEW_TAGS_V02``) were written by reading 50 random
     matches of each starter pattern in the recipes doc, and their near
     misses, in ``silver/facts.parquet`` (never the gold labels). Each is a
     ``TagRule``: a ``pattern`` that always tags; an optional ``name_pattern``
     for specialty or staff wording ("neurosurgery", "radiation oncology",
     "spine surgery") that tags only when the fact is not a list of
     departments and not a staff biography; a ``mask`` of look-alike phrases
     removed first ("gastric bypass", "sun burn", "dental cone-beam CT"); and a
     ``veto`` that drops the tag for the whole fact ("peripheral angioplasty"
     without any coronary word). On top of the v0.1 negation cue, a fact that
     refers the service out, outsources it, only plans it, or states it is
     absent (``NEW_TAG_CONTEXT_VETO``) gets none of the v0.2 tags. Wording that
     rejects a procedure ("alternative to bypass surgery", "without spine
     surgery") is masked for cardiac_surgery, cath_lab and neurosurgery.

   The regexes are a crude baseline measured against the gold set (WP3 part 2).
   Do not tune them without bumping ``RECIPE_VERSION`` and logging a decision.

2. Facility level. ``facility_line_grades`` turns a facility's tagged facts and
   specialties into a grade 0 to 3 per service line with a deterministic rule
   (``GRADE_RULES``), and attaches the quoted facts that justify it: a list of
   texts (``evidence``, as in v0.1) and one structured item per satisfied
   requirement (``evidence_items``), plus what is missing for the next grade,
   the number of supporting facts and an evidence strength. The rules take tags
   from any tagger (regex, teacher, student), so WP4 reuses them unchanged.
   ``version="v0.1"`` grades the three v0.1 lines with the v0.1 rules.

``source_confidence`` is a separate 0 to 1 score for how much to trust the pages
a facility's facts came from. It never changes a grade.

Recipe v0.3 (D048) is a rule-level regrade only: same tags, same taggers, new
grade rules for nicu, emoc, cath_lab and dialysis, aligned to WHO 2009 Table 4,
FRU 2025 s2.2, FBNC 2025, the PM-JAY Cardiology/CTVS and Nephrology criteria
and PMNDP. Some v0.3 requirements look at the wording of a tagged fact
("a ``nicu`` fact that says NICU, not only SNCU"). Those are ``QUALIFIERS_V03``:
derived per-fact markers computed from the fact's own tags and text, never new
tags. ``facility_line_grades(..., version="v0.3")`` adds them itself; callers of
``grade_line`` or ``grade_facility`` with v0.3 must pass tags from
``qualify_tags``. ``RECIPE_VERSION`` stays ``v0.2`` (the tagger version), and
``tag_fact`` has no v0.3.

Typical use::

    from vf import recipes
    recipes.tag_fact("24x7 ICU with ventilator support")   # ['h24', 'icu', 'ventilator']
    recipes.tag_fact("ICU not available")                  # [] (negation cue)
    recipes.tag_fact("MRI outsourced to a nearby centre")  # [] (referral or outsourcing)
    recipes.grade_facility(["csection", "blood", "ot"], ["gynecologyAndObstetrics"])["emoc"]  # 3
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field as dc_field
from datetime import date, datetime
from typing import Iterable, Mapping, NamedTuple, Optional, Sequence

from .clean import normalize_text

#: Bump when a pattern, rule, or the confidence formula changes, and log it in
#: docs/08_decisions_log.md. Written into every output row and sidecar.
RECIPE_VERSION = "v0.2"
#: Versions ``tag_fact`` can reproduce.
TAGGER_VERSIONS: tuple[str, ...] = ("v0", "v0.1", "v0.2")
#: Versions the grade rules can reproduce with a matching tagger (the regex
#: baseline's ``--version`` choices).
RULE_VERSIONS: tuple[str, ...] = ("v0.1", "v0.2")
#: v0.3 is a rule-level regrade of v0.2 tags (D048): grade rules only, no tagger.
RULE_VERSION_V03 = "v0.3"
#: Every grade-rule version ``facility_line_grades`` accepts.
GRADE_RULE_VERSIONS: tuple[str, ...] = RULE_VERSIONS + (RULE_VERSION_V03,)

# ---------------------------------------------------------------------------
# Tag taxonomy
# ---------------------------------------------------------------------------

#: The 13 v0.1 evidence tags, in the recipes-doc order.
EVIDENCE_TAGS_V01: tuple[str, ...] = (
    "ob_delivery",
    "csection",
    "blood",
    "anesthesia",
    "ot",
    "h24",
    "nicu",
    "dialysis",
    "dialysis_unit",
    "icu",
    "ventilator",
    "emergency",
    "scheme",
)

#: The 11 evidence tags added in v0.2 for the nine new service lines.
NEW_TAGS_V02: tuple[str, ...] = (
    "neonatal_resp",
    "cardiac_surgery",
    "cath_lab",
    "ccu",
    "mri",
    "ct",
    "trauma",
    "neurosurgery",
    "burns",
    "chemo",
    "radiotherapy",
)

#: All 24 evidence tags (v0.2). ``tag_fact`` returns tags in this order. The
#: v0.1 tags come first with unchanged positions, so code that indexes them
#: (the gold-set design) is unaffected.
EVIDENCE_TAGS: tuple[str, ...] = EVIDENCE_TAGS_V01 + NEW_TAGS_V02

#: Evidence tags each tagger version can emit.
EVIDENCE_TAGS_BY_VERSION: dict[str, tuple[str, ...]] = {
    "v0": EVIDENCE_TAGS_V01,
    "v0.1": EVIDENCE_TAGS_V01,
    "v0.2": EVIDENCE_TAGS,
}

#: Noise tags. No regex: they exist so the teacher and the student learn what to
#: ignore, and so the gold table can report them.
NOISE_TAGS: tuple[str, ...] = (
    "reputation",
    "location",
    "staff_name",
    "accessibility",
    "photo_artifact",
    "web_presence",
    "other_clinical",
)

ALL_TAGS: tuple[str, ...] = EVIDENCE_TAGS + NOISE_TAGS

#: One-line definition of every tag. The single source for the gold-set
#: labelers and the teacher prompt (WP3, WP4). Label against these, not the
#: regexes. ``TAGGING_RULES`` below apply to every evidence tag.
TAG_DEFINITIONS: dict[str, str] = {
    # v0.1 evidence tags
    "ob_delivery": "Conducts deliveries or has a maternity or labour ward",
    "csection": "Performs caesarean sections (C-section, LSCS); a vaginal birth after a past caesarean (VBAC) alone is not one",
    "blood": "Blood bank, blood storage, or blood transfusion",
    "anesthesia": "Anaesthesia service or an anaesthetist",
    "ot": "Operating theatre",
    "h24": "Round-the-clock service (24/7, 24 hours)",
    "nicu": "Neonatal intensive or special care unit (NICU, SNCU)",
    "dialysis": "Provides dialysis; the nephrology specialty alone is not dialysis",
    "dialysis_unit": "Named dialysis unit, dialysis machines, or a nephrologist",
    "icu": "Intensive care unit or critical care unit",
    "ventilator": "Mechanical ventilation (ventilators)",
    "emergency": "Emergency, casualty, or trauma service",
    "scheme": "Accepts a public insurance scheme (Ayushman Bharat, PM-JAY, CGHS)",
    # v0.2 evidence tags
    "neonatal_resp": "Breathing support for newborns: neonatal or bubble CPAP, neonatal ventilator, HFOV; not adult CPAP for sleep apnoea",
    "cardiac_surgery": "Heart surgery performed: CABG or coronary bypass, valve repair or replacement, open-heart surgery, heart transplant, or a named cardiac surgery service; not gastric or peripheral bypass, not catheter valve procedures",
    "cath_lab": "Cardiac catheterisation: cath lab, coronary angiography, angioplasty, PTCA or PCI, coronary stents; not carotid, renal or peripheral angioplasty",
    "ccu": "Cardiac intensive care: CCU, ICCU, coronary or cardiac care unit",
    "mri": "MRI scanner or MRI scans on site; not reading reports of scans done elsewhere",
    "ct": "CT scanner or CT scans on site, including PET-CT; not dental cone-beam CT or a radiotherapy CT simulator",
    "trauma": "Trauma or accident care: trauma centre or unit, accident and emergency, polytrauma, trauma surgery; not dental, eye, birth or psychological trauma",
    "neurosurgery": "Brain or spine surgery performed: neurosurgery service, craniotomy, brain tumour or spine surgery, head injury management; not neurology consults alone",
    "burns": "Burn care: burns unit, ward or ICU, burn treatment or burn surgery; not sunburn or heartburn",
    "chemo": "Chemotherapy given at the facility, including day-care chemotherapy and HIPEC",
    "radiotherapy": "Radiotherapy at the facility: linear accelerator, cobalt unit, brachytherapy, IMRT, IGRT, SBRT, radiosurgery, or a radiation oncology service; not radiology imaging",
    # noise tags
    "reputation": "Ratings, reviews, rankings, years of experience",
    "location": "Address or place statement",
    "staff_name": "Names a person",
    "accessibility": "Parking, wheelchair access",
    "photo_artifact": "Looks like a photo caption, not an equipment list",
    "web_presence": "About the web page itself",
    "other_clinical": "A real clinical fact that no evidence tag covers",
}

#: Rules that apply to every evidence tag, for labelers and the teacher prompt.
TAGGING_RULES: tuple[str, ...] = (
    "No evidence is not evidence of absence, and a stated absence is not evidence: a fact that says a service is "
    "not available, not provided, or not specified gets no evidence tag.",
    "Referral is not service: a fact that refers patients elsewhere, outsources the service, or names a tie-up "
    "gets no tag for that service.",
    "Plans and needs are not service: proposed, planned, under construction, coming soon, or a stated need for "
    "equipment get no tag.",
    "A specialty word in a list of departments or specialties is a claim, not a service: \"departments include "
    "cardiology, neurosurgery, oncology\" is not neurosurgery. A named unit or procedure in a list still counts "
    "(\"ICU, CCU and burns unit\").",
    "A staff biography (\"Dr X, neurosurgeon with 15 years of experience\") is not evidence that the facility "
    "offers the service.",
    "A fact may carry several tags (\"24x7 ICU with ventilator\" is h24, icu, ventilator).",
)

#: v0.1 regexes for the 13 v0.1 tags, verbatim from docs/06_capability_recipes.md.
#: They run on ``normalize_text(text)``: lowercased, whitespace runs collapsed to
#: one space. Unchanged in v0.2.
TAG_PATTERNS: dict[str, str] = {
    "ob_delivery": r"maternity|labou?r (room|ward)|normal delivery|childbirth|obstetric",
    "csection": r"\bc[- ]?section|caesarean|cesarean|\blscs\b",
    "blood": r"blood bank|blood storage|blood transfusion",
    "anesthesia": r"an(a)?esthe",
    "ot": r"operation theat|operating theat|operating room",
    "h24": r"24/7|24x7|24 ?hours|24 ?hrs|24-hour|round the clock",
    "nicu": r"\bnicu\b|\bsncu\b|neonatal intensive",
    "dialysis": r"dialysis",
    "dialysis_unit": r"dialysis (unit|machine|centre|center)|nephrologist",
    "icu": r"\bicu\b|intensive care|critical care unit",
    "ventilator": r"ventilator",
    "emergency": r"emergency|trauma|casualty",
    "scheme": r"ayushman|pmjay|cghs",
}

#: v0 as first released (no negation, no VBAC handling). Kept so the gold-set
#: design (scripts/wp3_gold_sample.py) stays reproducible and WP3 scoring can
#: show what v0.1 changed. Not used for grades.
TAG_PATTERNS_V0: dict[str, str] = {
    **TAG_PATTERNS,
    "csection": r"c[- ]?section|caesarean|cesarean|\blscs\b",
    "dialysis_unit": r"dialysis (unit|machine|centre|center)|h(a)?emodialysis|nephrologist",
}

#: v0.1: a fact matching this states that something is absent, not offered,
#: referred out of view, or not specified, so it carries no evidence tag (the
#: gold labeling rule). Deliberately narrow: bare "no" and "non" are not cues
#: ("non-invasive ventilation in icu", "icu no.1", "dialysis at no cost").
#: Applies to all 24 tags in v0.2.
NEGATION_PATTERN = (
    r"\bnot (be |been |yet )?(available|offered|provided|present|performed|done|operational|functional)\b"
    r"|\bnot (explicitly |specifically |clearly )?(specified|mentioned|listed|stated|detailed|confirmed)\b"
    r"|\b(does|do|did) not\b|\b(doesn't|don't|didn't)\b"
    r"|\bunavailable\b|\bnon[- ]?(functional|operational|availability)\b"
    r"|^no\b.*\b(listed|mentioned|specified|available|provided|offered)\b"
    r"|\bno (specific|explicit)\b|\bthere (is|are) no (?!other\b)"
    r"|\b(lack|lacks|lacking|absence) of\b"
)
#: v0.1: vaginal birth after caesarean names a past caesarean, not the service.
#: Removed from the text before the csection pattern runs.
VBAC_PATTERN = r"vaginal birth after (a )?(c(a)?esarean|c[- ]?section)( section)?|\bvbac\b"


@dataclass(frozen=True)
class TagRule:
    """How one v0.2 tag is matched on a normalized fact.

    The tag fires when, after removing ``mask`` matches and any exception clause
    (``EXCEPT_MASK``), ``pattern`` matches, or ``name_pattern`` matches and the
    fact is neither a list of specialties (``SPECIALTY_LIST_CUE``) nor a staff
    biography (``STAFF_CUE``); and neither ``veto`` nor the shared
    ``NEW_TAG_CONTEXT_VETO`` matches.
    """

    pattern: str
    name_pattern: Optional[str] = None
    mask: Optional[str] = None
    veto: Optional[str] = None


#: Shared by mri and ct: wording about reading or experience, not a scanner.
_IMAGING_VETO = (
    r"\bsecond opinion\b|\bexpertise\b|\bradiologists? with\b|\bcombined (\w+ ){0,6}experience\b"
    r"|\b(\w+ ){0,2}experience in\b|\b(review|interpret)\w* (of )?(outside |external |prior )?(mri|ct)\b"
    r"|\b(mri|ct) (reports?|films?|images?) (are |is )?(reviewed|interpreted)\b"
)

#: Shared by cardiac_surgery, cath_lab and neurosurgery: wording that rejects a
#: procedure or offers something in its place ("EECP, a natural alternative to
#: bypass surgery", "spine care without spine surgery", "CTO angioplasty, avoiding
#: bypass surgery") is removed before the patterns run, from the cue to the end of
#: its clause. A mask, not a veto, so the procedure the fact does claim still tags
#: ("coronary angioplasty ... without surgery" keeps cath_lab).
_ANTI_PROCEDURE_MASK = (
    r"\b(without|avoid\w*|alternatives? (to|for)|instead of|no need (for|of))\b[^,;.()]{0,60}"
    r"|biochemical angioplast\w*"
)

#: v0.2 tag rules. Read 50 random starter-pattern matches and the near misses of
#: each tag in silver/facts.parquet before writing (WP3 step 1). Notes on what the
#: starter pattern got wrong are in the comments.
NEW_TAG_RULES: dict[str, TagRule] = {
    # Starter matched CPAP for sleep apnoea and adult "cpap machines". Now needs a
    # newborn context word in the same fact, or bubble CPAP / infant flow drivers.
    "neonatal_resp": TagRule(
        pattern=(
            r"bubble cpap|infant flow|nasal cpap driver"
            r"|^(?=.*(neonat|newborn|\binfants?\b|\bnicu\b|\bsncu\b|\bscnu\b|\bnnicu\b|preterm|prematur))"
            r".*(cpap|ventilat|\bhfov\b|high[- ]frequency (oscillat|ventilat)|\bnippv\b|high[- ]flow nasal|\bh?hfnc\b)"
        ),
        mask=r"sleep apn\w*|obstructive apn\w*",
    ),
    # Starter matched gastric, peripheral, vascular and brain (cerebral) bypass, and
    # balloon / percutaneous valve procedures; and "cardiac surgery" inside long
    # department lists and staff bios.
    "cardiac_surgery": TagRule(
        pattern=(
            r"\bcabg\b|coronary (artery )?bypass|bypass (surgery|surgeries|graft\w*)|open[- ]heart|beating[- ]heart"
            r"|off[- ]pump|heart[- ]lung machine|cardiopulmonary bypass|valve (replacement|repair|surgery|surgeries)"
            r"|heart transplant"
        ),
        name_pattern=(
            r"cardiac surg|cardio[- ]?thoracic\w*( (and|&) vascular)? surg|cardiovascular (and |& )?thoracic surg"
            r"|\bctvs\b|\bcvts\b|heart surg"
        ),
        mask=(
            r"(gastric|bariatric|peripheral|vascular|femoral|fem[- ]?pop\w*|aorto[- ]?\w+|arterial|cerebrovascular"
            r"|cerebral|brain|neuro\w*|ec[- ]ic|high[- ]flow|low[- ]flow|limb|distal|roux[- ]en[- ]y|biliary|mini gastric|endovascular"
            r"|venous)( and \w+)? bypass( surgery| surgeries| graft\w*)?"
            r"|(percutaneous|transcatheter|balloon)( \S+){0,4}? valv\w*( repair| replacement)?|bypass graft vessel"
            # bariatric bypass named by its target ("duodenojejunal bypass ... for obesity and diabetes")
            r"|(duodeno[- ]?jejunal|jejuno[- ]?ileal|ileal|intestinal|metabolic)( and \w+)? bypass( surgery| surgeries)?"
            r"|bypass( surgery| surgeries)? (for|in) (morbid )?(obesity|diabet\w*|weight loss)"
            # catheter valve procedures (TAVI, TAVR, MitraClip) and "valve replacement without surgery"
            r"|\b(tavi|tavr|tmvr|mitra ?clip)\b[^,;.]{0,40}?valv\w*( repair| replacement| implantation)?"
            r"|valv\w* (replacement|repair|implantation) without\b[^,;.()]*"
            r"|" + _ANTI_PROCEDURE_MASK
        ),
        # vascular bypass with no heart word ("aortic bypass surgery", "bypass grafting of peripheral vessels")
        veto=(
            r"^(?!.*(coronar|heart|cardi|cabg|valv|thorac|off[- ]pump|mammary|\bctvs\b|\bcvts\b))"
            r".*\b(vascular|endovascular|peripheral|vessels?|aort\w*|limbs?|femoral|carotid)\b"
        ),
    ),
    # Starter was mostly right. Carotid, renal, peripheral and intracranial
    # angioplasty with no coronary word is not a cath lab for heart attacks.
    "cath_lab": TagRule(
        pattern=(
            r"cath ?lab|catheteri[sz]ation lab|angioplast|coronary angiogra|\bptca\b|\bp?pci\b|(coronary|cardiac) stent"
        ),
        # CT coronary angiography is a scan, not a cath lab; plus the shared anti-procedure wording
        mask=(
            r"\bct (coronary )?angiogra\w*|coronary (ct|computed tomograph\w*) angiogra\w*"
            r"|non[- ]?invasive coronary angiogra\w*|coronary angiogra\w* (by|using|with) (ct|computed)\b[^,;.]*"
            r"|" + _ANTI_PROCEDURE_MASK
        ),
        veto=(
            r"^(?!.*(coronar|cardiac|heart|\bptca\b|\bp?pci\b|cath ?lab|catheteri[sz]ation lab|myocard|stemi))"
            r".*\b(carotid|renal|peripheral|femoral|limb|below[- ]knee|venous|cerebral|intracranial|neuro|tibial"
            r"|iliac|vertebral|mesenteric|biliary|esophag|oesophag|urethral|fistula)"
        ),
    ),
    # Starter was right; widened to other names for the same unit.
    "ccu": TagRule(
        pattern=(
            r"\bccu\b|\biccu\b|\bcicu\b|cardiac (intensive|critical) care|intensive cardiac care|intensive coronary care"
            r"|coronary care unit|cardiac care unit|cardiac icu|\bcvicu\b|\bcticu\b"
        ),
    ),
    # Starter matched outsourced MRI (shared veto), radiologists' experience and
    # report reading, and "MRI-compatible" equipment.
    "mri": TagRule(
        pattern=r"\bmri\b|magnetic resonance|\bfmri\b",
        mask=r"mri[- ](compatible|safe|conditional)",
        veto=_IMAGING_VETO,
    ),
    # Starter missed bare "ct" in imaging lists ("x-ray, ct, mri"), PET-CT and
    # CT-guided procedures, and matched dental cone-beam CT and CT simulators.
    "ct": TagRule(
        pattern=(
            r"\bct ?scan|computed tomograph|\d+[- ]?slice|\bmdct\b|\bhrct\b|\bpet[- ]?ct\b"
            r"|\bct[- ](guided|angio\w*|imaging|brain|chest|abdomen|kub|coronary)"
            r"|^(?=.*(scan|imag|radiolog|\bmri\b|x-?ray|ultraso|\busg\b|sonograph|diagnost|tomograph|contrast)).*\bct\b"
        ),
        mask=(
            r"cone[- ]beam (ct|computed tomograph\w*)|\bcbct\b|\bct[- ]?simulat\w*|planning ct"
            r"|\bct (surgery|surgeons?|surgical)\b|\bct ?vs\b"
        ),
        veto=_IMAGING_VETO,
    ),
    # Starter matched dental, nasal, orbital and facial trauma, "trauma
    # counselling", and "trauma care" inside long specialty lists. Missed
    # "emergency and trauma", "trauma management", "traumatology".
    "trauma": TagRule(
        pattern=(
            r"\btrauma (cent(re|er)s?|unit|ward|team|icu|bay|department|emergency|(and|&) emergency|(and|&) critical)"
            r"|polytrauma|neurotrauma|accident (and|&) emergency|emergency (and|&|/) trauma|road traffic accident"
            r"|(critical|major|head) (and |& )?trauma"
        ),
        name_pattern=(
            r"\btrauma (care|surgery|surgeries|services?|management|program\w*)|\btraumatology"
            r"|orthop(a)?edic (and |& )?trauma|fractures? (and|&) trauma|trauma (and|&) fractures?"
        ),
        mask=(
            r"(dental|tooth|teeth|facial|maxillofacial|birth|emotional|psycholog\w*|childhood|sexual|ocular|orbital"
            r"|eye|nasal|acoustic|sports) trauma|trauma[- ](focused|informed|counsel\w*|therapy)|ao trauma"
        ),
        # a dental, eye or counselling practice's "trauma care" (not inside a long multi-specialty list)
        veto=(
            r"^(?!(.*[,;]){3}).*\b(dental|dentist\w*|teeth|tooth|toothache|maxillofacial|psycholog\w*|counsel\w*"
            r"|ptsd|ophthalm\w*|orbital|ocular|eyes?)\b"
        ),
    ),
    # Starter matched "neurosurgery" inside long department lists and staff bios
    # (about a third of its matches), and orthopaedic spine-surgery lists. Spine
    # surgery is kept (the doc's definition) but treated as a specialty name.
    "neurosurgery": TagRule(
        pattern=(
            r"craniotom|craniectom|brain tumou?r (surgery|surgeries|removal|resection|excision)|brain (and spine )?surg"
            r"|head injur(y|ies) (management|surgery|care|treatment)|neuro[- ]?navigation|neurosurgical microscope"
            r"|deep brain stimulation|\bvp shunt|ventriculoperitoneal|aneurysm clipping"
        ),
        name_pattern=(
            r"neuro[- ]?surg|(spine|spinal) surg|microdiscectomy|laminectomy|discectomy|spinal fusion"
        ),
        mask=_ANTI_PROCEDURE_MASK,
    ),
    # Starter matched "sun burn treatment" and "burns management" in specialty
    # lists. Missed "burn surgery", "treatment of burns", "burns and plastic
    # surgery".
    "burns": TagRule(
        pattern=(
            r"\bburns? (unit|ward|icu|cent(re|er)|beds?|surgery|surgeries|treatment|dressing|resuscitation|department"
            r"|dept|injur\w*|patients|wound)|treatment (of|for) burns?|burns? (and|&) (intensive|critical) care"
            r"|escharotom|skin bank"
        ),
        name_pattern=r"\bburns? (management|care|services)|burns? (and|&|/) plastic|plastic (surgery )?(and|&|/) burns?",
        mask=r"sun ?burns?|heart ?burns?|razor burns?|freezer burns?",
    ),
    # Starter was right; added chemoport, HIPEC, PIPAC, chemoradiation. Skin
    # clinics' photochemotherapy (PUVA for vitiligo, psoriasis) is not cancer care.
    "chemo": TagRule(
        pattern=r"chemotherap|\bchemo\b|chemoport|\bhipec\b|\bpipac\b|chemoradi",
        mask=r"photo[- ]?chemotherap\w*",
        veto=r"\b(leucoderma|leukoderma|vitiligo|psoriasis|puva)\b",
    ),
    # Starter matched "radiation oncology" in lists of oncology specialties and
    # staff bios, and plans ("in development"). Missed named machines and
    # techniques (truebeam, cyberknife, gamma knife, imrt, igrt, sbrt).
    "radiotherapy": TagRule(
        pattern=(
            r"radiotherap|radiation (therap|treatment)|\blinac\b|linear accelerators?|brachytherap|\bcobalt[- ]?60\b"
            r"|cobalt (machine|unit|teletherapy)|teletherapy|\b(imrt|igrt|sbrt|vmat|3d[- ]?crt)\b|tomotherap|cyberknife"
            r"|gamma knife|radiosurg|truebeam|halcyon|rapid ?arc|chemoradi|bhabhatron"
        ),
        # a bare "radiation oncology" is a specialty (often in ratings and staff text); only service wording counts
        name_pattern=(
            r"radiation oncology (department|dept|services?|unit|cent(re|er)|division|program\w*|facilit\w*"
            r"|treatment|care)"
        ),
    ),
}

#: v0.2: a fact that refers the service out, outsources it, only plans or needs
#: it, or states it is absent gets none of the v0.2 tags (the v0.1 tags are
#: unaffected, so v0.1 answers stay identical). "Referral centre" and "accepts
#: referrals" are not cues, nor is "treatment planning". The absence cue adds what
#: the v0.1 negation cue misses ("No MRI facility at ..."); "no.1 trauma centre"
#: is not a cue.
NEW_TAG_CONTEXT_VETO = (
    r"\brefer(s|red|ring)? (\w+ ){0,3}(to|out|for)\b|\breferrals? (to|for)\b|\btie[- ]?ups? with\b|\btied[- ]up with\b"
    r"|\boutsourc\w*|\bthe need (for|of)\b|\bin need of\b|\bplanned\b|\bproposed\b|\bproposals?\b"
    r"|\bplans? (to|for)\b|\bupcoming\b|\bcoming (soon|next)\b|\bnext year\b|\bscheduled to\b"
    r"|\bwould (be|provide|offer|have)\b|\bunder construction\b|\bin development\b"
    r"|^no\b(?!\.? ?\d)|\bno (\w+ ){0,4}(facilit\w*|services?|machines?|scanners?)\b"
    r"|\bwill be (set up|started|established|installed|launched|commissioned|available)\b"
    r"|\bto be (set up|started|established|installed|launched|commissioned)\b"
)
#: v0.2: an exception clause is removed before the v0.2 patterns run
#: ("CGHS rates for all treatment except cardiac surgery").
EXCEPT_MASK = r"\b(except|excluding|other than|apart from)\b[^).;]*"
#: v0.2: the fact lists departments or specialties, so a specialty name in it
#: (``TagRule.name_pattern``) is a claim, not a service.
SPECIALTY_LIST_CUE = (
    r"(specialt(y|ies)|specialit(y|ies)|speciali[sz]ations?|departments|disciplines|consulting|super[- ]?special\w*)\b"
    r"[^.]{0,40}?\b(include|includes|including|such as|like|across)\b"
    r"|:|([,;][^,;]*){3,}"
)
#: v0.2: the fact is about a person or a training course, so a specialty name in
#: it is not evidence of the service.
STAFF_CUE = (
    r"\bdr\b|\bdr\.|\bdoctors?\b|\bconsultants?\b|\bexperience\b|\bserved\b|\bspeciali[sz](es|ed|ing) in\b"
    r"|\bmember of\b|\btrained\b|\bfellowship\b|\bcut-?off\b|\bdnb\b|\bm\.?ch\b|\bcourse\b|\bstudents?\b"
    r"|\bfaculty\b|\bhead of\b|\bdirector\b"
)

_TAG_RX: dict[str, re.Pattern] = {t: re.compile(TAG_PATTERNS[t]) for t in EVIDENCE_TAGS_V01}
_TAG_RX_V0: dict[str, re.Pattern] = {t: re.compile(TAG_PATTERNS_V0[t]) for t in EVIDENCE_TAGS_V01}
_NEG_RX = re.compile(NEGATION_PATTERN)
_VBAC_RX = re.compile(VBAC_PATTERN)
_CTX_RX = re.compile(NEW_TAG_CONTEXT_VETO)
_EXCEPT_RX = re.compile(EXCEPT_MASK)
_LIST_RX = re.compile(SPECIALTY_LIST_CUE)
_STAFF_RX = re.compile(STAFF_CUE)


class _CompiledRule(NamedTuple):
    pattern: re.Pattern
    name_pattern: Optional[re.Pattern]
    mask: Optional[re.Pattern]
    veto: Optional[re.Pattern]


_NEW_RX: dict[str, _CompiledRule] = {
    t: _CompiledRule(*(re.compile(p) if p else None for p in (r.pattern, r.name_pattern, r.mask, r.veto)))
    for t, r in NEW_TAG_RULES.items()
}


def is_negated(text: Optional[str]) -> bool:
    """True when a fact has a negation or absence cue (``NEGATION_PATTERN``)."""
    return bool(text) and bool(_NEG_RX.search(normalize_text(str(text))))


def _tags_v01(s: str) -> list[str]:
    out = []
    for t in EVIDENCE_TAGS_V01:
        target = _VBAC_RX.sub(" ", s) if t == "csection" else s
        if _TAG_RX[t].search(target):
            out.append(t)
    return out


def _new_tag_reason(tag: str, s: str, base: str, context: bool) -> Optional[str]:
    """Why a v0.2 tag does or does not fire on normalized ``s`` (``base`` has the
    exception clause removed). None when its patterns do not match at all."""
    r = _NEW_RX[tag]
    x = r.mask.sub(" ", base) if r.mask else base
    strong = bool(r.pattern.search(x))
    named = bool(r.name_pattern.search(x)) if r.name_pattern else False
    if not (strong or named):
        return None
    if context:
        return "context_veto"
    if r.veto and r.veto.search(x):
        return "tag_veto"
    if strong:
        return "pattern"
    if _LIST_RX.search(s):
        return "specialty_list"
    if _STAFF_RX.search(s):
        return "staff_bio"
    return "name_pattern"


def tag_reasons(text: Optional[str]) -> dict[str, str]:
    """Explain the v0.2 tags for one fact: ``{tag: reason}`` for every v0.2 tag
    whose pattern matched. ``pattern`` and ``name_pattern`` mean the tag fires;
    ``negated``, ``context_veto``, ``tag_veto``, ``specialty_list`` and
    ``staff_bio`` mean it was suppressed. For reading facts and debugging."""
    if text is None:
        return {}
    s = normalize_text(str(text))
    if not s:
        return {}
    base = _EXCEPT_RX.sub(" ", s)
    context = bool(_CTX_RX.search(s))
    neg = bool(_NEG_RX.search(s))
    out = {}
    for t in NEW_TAGS_V02:
        why = _new_tag_reason(t, s, base, context)
        if why is not None:
            out[t] = "negated" if neg else why
    return out


_FIRES = frozenset({"pattern", "name_pattern"})


def tag_fact(text: Optional[str], version: str = RECIPE_VERSION) -> list[str]:
    """Evidence tags for one fact, in ``EVIDENCE_TAGS`` order.

    ``version`` is ``"v0.2"`` (the current recipe: 24 tags), ``"v0.1"`` (the 13
    v0.1 tags, exactly as released) or ``"v0"`` (the original patterns, for the
    gold-set design and comparison only). On the 13 v0.1 tags, v0.2 and v0.1
    always agree. Accepts raw ``text`` or ``text_norm``: the text is normalized
    first, so both give the same answer. None or blank gives [].
    """
    if version not in TAGGER_VERSIONS:
        raise ValueError(f"unknown tagger version {version!r}; expected one of {TAGGER_VERSIONS}")
    if text is None:
        return []
    s = normalize_text(str(text))
    if not s:
        return []
    if version == "v0":
        return [t for t in EVIDENCE_TAGS_V01 if _TAG_RX_V0[t].search(s)]
    if _NEG_RX.search(s):
        return []
    out = _tags_v01(s)
    if version == "v0.1":
        return out
    base = _EXCEPT_RX.sub(" ", s)
    context = bool(_CTX_RX.search(s))
    for t in NEW_TAGS_V02:
        if _new_tag_reason(t, s, base, context) in _FIRES:
            out.append(t)
    return out


def tag_facts(texts: Iterable[Optional[str]], version: str = RECIPE_VERSION) -> list[list[str]]:
    """``tag_fact`` over many texts, computing each distinct normalized text once."""
    cache: dict[str, list[str]] = {}
    out = []
    for text in texts:
        key = "" if text is None else normalize_text(str(text))
        tags = cache.get(key)
        if tags is None:
            tags = cache[key] = tag_fact(key, version)
        out.append(list(tags))
    return out


# ---------------------------------------------------------------------------
# Facility grades
# ---------------------------------------------------------------------------

#: The twelve v0.2 service lines, in the contract order (docs/07, D025).
SERVICE_LINES: tuple[str, ...] = (
    "emoc", "nicu", "dialysis", "icu", "cardiac_surgery", "cath_lab",
    "mri", "ct", "trauma", "burns", "neurosurgery", "oncology",
)
SERVICE_LINES_V01: tuple[str, ...] = ("emoc", "dialysis", "icu")

#: Grade number to label. Index with the grade.
GRADE_LABELS: tuple[str, ...] = ("none", "claimed", "supported", "corroborated")

#: Default number of quoted facts kept as evidence per (facility, service line).
MAX_EVIDENCE = 5

#: Fields a fact can come from (``silver/facts.parquet``; ``description`` is
#: allowed by the contract but not used as a fact source in v0.2).
FACT_FIELDS: tuple[str, ...] = ("capability", "procedure", "equipment", "description")


@dataclass(frozen=True)
class LineRule:
    """Grade rule for one service line.

    A requirement is a tuple of groups. It holds when every group holds, and a
    group holds when the facility has at least one of its tags. So
    ``({"csection"}, {"blood"}, {"anesthesia", "ot"})`` reads
    "csection and blood and (anesthesia or ot)". ``supported_names`` and
    ``corroborated_names`` give each group its plain-language requirement name
    for the cards, in the same order.

    v0.2: ``min_distinct_facts`` and ``min_distinct_fields`` add a repeat
    condition to ``corroborated``: the tags of its first group must be carried
    by at least ``min_distinct_facts`` distinct facts (distinct normalized
    text), or appear in at least ``min_distinct_fields`` different fields
    (0 turns the field route off). ``repeat_name`` names that requirement. The
    defaults (1, 0) add nothing.
    """

    line: str
    #: claimed if the facility lists any of these specialties...
    claim_specialties: frozenset
    #: ...or has any of these tags
    claim_tags: frozenset
    supported: tuple
    corroborated: tuple
    #: Recorded in ``tags_present`` but never needed for a grade (emoc: h24, nicu).
    bonus_tags: tuple
    #: Every tag relevant to this line, in evidence priority order.
    tags: tuple
    supported_names: tuple = ()
    corroborated_names: tuple = ()
    #: Plain-language name of the claim tags, for grade 1 evidence items.
    claim_name: str = ""
    min_distinct_facts: int = 1
    min_distinct_fields: int = 0
    repeat_name: str = ""
    #: v0.3: other ways to reach corroborated, each a (groups, names) pair read
    #: like ``corroborated``. Grade 3 holds when ``corroborated`` or any of these
    #: holds; the first that holds is the one quoted. Empty in v0.1 and v0.2.
    corroborated_alts: tuple = ()
    #: v0.3: what grade 0 is missing, when the default "a related specialty
    #: listed or a <claim_name> mention" would read badly.
    claim_next_name: str = ""
    #: v0.3: a note on a grade-3 record when these groups also hold (emoc: the
    #: FRU pieces). Never changes the grade.
    note_groups: tuple = ()
    note_names: tuple = ()
    note_text: str = ""

    @property
    def corroborated_options(self) -> tuple:
        """((groups, names), ...): ``corroborated`` first, then the alternatives."""
        return ((self.corroborated, self.corroborated_names),) + tuple(self.corroborated_alts)

    @property
    def rule_tags(self) -> tuple:
        """Tags that the grade rule uses (claim, supported, corroborated), in priority order."""
        used = set(self.claim_tags).union(*self.supported, *self.corroborated)
        for groups, _ in self.corroborated_alts:
            used = used.union(*groups)
        return tuple(t for t in self.tags if t in used)

    @property
    def has_repeat(self) -> bool:
        return self.min_distinct_facts > 1 or self.min_distinct_fields > 1


def _groups(*groups: Iterable[str]) -> tuple:
    return tuple(frozenset(g) for g in groups)


_EMOC = LineRule(
    # Emergency obstetric care. WHO comprehensive EmOC signal functions add
    # caesarean section and blood transfusion to the basic ones.
    line="emoc",
    claim_specialties=frozenset({
        "gynecologyAndObstetrics",
        "obstetricsAndMaternityCare",
        "maternalFetalMedicineOrPerinatology",
    }),
    claim_tags=frozenset({"ob_delivery"}),
    supported=_groups({"csection"}),
    corroborated=_groups({"csection"}, {"blood"}, {"anesthesia", "ot"}),
    bonus_tags=("nicu", "h24"),
    tags=("csection", "blood", "anesthesia", "ot", "ob_delivery", "nicu", "h24"),
    supported_names=("caesarean section",),
    corroborated_names=("caesarean section", "blood bank or transfusion", "anaesthesia or operating theatre"),
    claim_name="maternity or delivery service",
)
_DIALYSIS = LineRule(
    line="dialysis",
    claim_specialties=frozenset({"nephrology"}),
    claim_tags=frozenset(),
    supported=_groups({"dialysis"}),
    corroborated=_groups({"dialysis"}, {"dialysis_unit"}),
    bonus_tags=(),
    tags=("dialysis", "dialysis_unit"),
    supported_names=("dialysis",),
    corroborated_names=("dialysis", "dialysis unit, machines or nephrologist"),
)
_ICU = LineRule(
    line="icu",
    claim_specialties=frozenset({"criticalCareMedicine"}),
    claim_tags=frozenset(),
    supported=_groups({"icu"}),
    corroborated=_groups({"icu"}, {"ventilator"}),
    bonus_tags=(),
    tags=("icu", "ventilator"),
    supported_names=("intensive care unit",),
    corroborated_names=("intensive care unit", "ventilator"),
)

#: v0.1 rules: three lines. Grades from these are what v0.1 produced.
GRADE_RULES_V01: dict[str, LineRule] = {"emoc": _EMOC, "dialysis": _DIALYSIS, "icu": _ICU}

#: v0.2 rules: twelve lines (docs/06_capability_recipes.md, "New grade rules").
#: emoc, dialysis and icu are the v0.1 rules unchanged.
GRADE_RULES: dict[str, LineRule] = {
    "emoc": _EMOC,
    "nicu": LineRule(
        line="nicu",
        claim_specialties=frozenset({"neonatologyPerinatalMedicine"}),
        claim_tags=frozenset(),
        supported=_groups({"nicu"}),
        corroborated=_groups({"nicu"}, {"neonatal_resp", "ventilator"}),
        bonus_tags=(),
        tags=("nicu", "neonatal_resp", "ventilator"),
        supported_names=("newborn intensive care unit",),
        corroborated_names=("newborn intensive care unit", "newborn breathing support or ventilator"),
    ),
    "dialysis": _DIALYSIS,
    "icu": _ICU,
    "cardiac_surgery": LineRule(
        line="cardiac_surgery",
        claim_specialties=frozenset({"cardiacSurgery", "cardiothoracicSurgery"}),
        claim_tags=frozenset(),
        supported=_groups({"cardiac_surgery"}),
        corroborated=_groups({"cardiac_surgery"}, {"icu", "ccu"}, {"blood"}),
        bonus_tags=(),
        tags=("cardiac_surgery", "ccu", "icu", "blood"),
        supported_names=("heart surgery",),
        corroborated_names=("heart surgery", "intensive or cardiac care unit", "blood bank or transfusion"),
    ),
    "cath_lab": LineRule(
        line="cath_lab",
        claim_specialties=frozenset({"cardiology", "interventionalRadiology"}),
        claim_tags=frozenset(),
        supported=_groups({"cath_lab"}),
        corroborated=_groups({"cath_lab"}, {"ccu", "icu"}, {"h24", "emergency"}),
        bonus_tags=(),
        tags=("cath_lab", "ccu", "icu", "h24", "emergency"),
        supported_names=("cath lab, angiography or angioplasty",),
        corroborated_names=("cath lab, angiography or angioplasty", "cardiac or intensive care unit",
                            "24-hour or emergency service"),
    ),
    "mri": LineRule(
        line="mri",
        claim_specialties=frozenset({"radiology", "diagnosticRadiology"}),
        claim_tags=frozenset(),
        supported=_groups({"mri"}),
        corroborated=_groups({"mri"}),
        bonus_tags=(),
        tags=("mri",),
        supported_names=("MRI scan",),
        corroborated_names=("MRI scan",),
        min_distinct_facts=2,
        min_distinct_fields=2,
        repeat_name="MRI named in a second fact or a second field",
    ),
    "ct": LineRule(
        line="ct",
        claim_specialties=frozenset({"radiology", "diagnosticRadiology"}),
        claim_tags=frozenset(),
        supported=_groups({"ct"}),
        corroborated=_groups({"ct"}),
        bonus_tags=(),
        tags=("ct",),
        supported_names=("CT scan",),
        corroborated_names=("CT scan",),
        min_distinct_facts=2,
        min_distinct_fields=2,
        repeat_name="CT named in a second fact or a second field",
    ),
    "trauma": LineRule(
        line="trauma",
        claim_specialties=frozenset({"emergencyMedicine", "orthopedicSurgery"}),
        claim_tags=frozenset({"emergency"}),
        supported=_groups({"trauma"}),
        corroborated=_groups({"trauma"}, {"ot"}, {"blood", "icu"}, {"h24"}),
        bonus_tags=(),
        tags=("trauma", "ot", "blood", "icu", "h24", "emergency"),
        supported_names=("trauma care",),
        corroborated_names=("trauma care", "operating theatre", "blood bank or intensive care unit",
                            "24-hour service"),
        claim_name="emergency service",
    ),
    "burns": LineRule(
        line="burns",
        claim_specialties=frozenset({"burnAndTraumaPlasticSurgery", "plasticSurgery", "reconstructivePlasticSurgery"}),
        claim_tags=frozenset(),
        supported=_groups({"burns"}),
        corroborated=_groups({"burns"}, {"icu"}, {"ot"}),
        bonus_tags=(),
        tags=("burns", "icu", "ot"),
        supported_names=("burn care",),
        corroborated_names=("burn care", "intensive care unit", "operating theatre"),
    ),
    "neurosurgery": LineRule(
        line="neurosurgery",
        claim_specialties=frozenset({"neurosurgery", "spineNeurosurgery", "skullBaseNeurosurgery"}),
        claim_tags=frozenset(),
        supported=_groups({"neurosurgery"}),
        corroborated=_groups({"neurosurgery"}, {"icu"}, {"ct"}),
        bonus_tags=(),
        tags=("neurosurgery", "icu", "ct"),
        supported_names=("brain or spine surgery",),
        corroborated_names=("brain or spine surgery", "intensive care unit", "CT scan"),
    ),
    "oncology": LineRule(
        line="oncology",
        claim_specialties=frozenset({"medicalOncology", "surgicalOncology", "radiationOncology",
                                     "gynecologicalOncology"}),
        claim_tags=frozenset(),
        supported=_groups({"chemo", "radiotherapy"}),
        corroborated=_groups({"chemo"}, {"radiotherapy"}),
        bonus_tags=(),
        tags=("chemo", "radiotherapy"),
        supported_names=("chemotherapy or radiotherapy",),
        corroborated_names=("chemotherapy", "radiotherapy"),
    ),
}

# ---------------------------------------------------------------------------
# Recipe v0.3: a rule-level regrade of the v0.2 tags (D048)
# ---------------------------------------------------------------------------


class Qualifier(NamedTuple):
    """A v0.3 marker on one fact: the fact carries tag ``base`` and its
    normalized text matches ``pattern`` and not ``veto``. Not a tag: the tag
    definitions and taggers are unchanged, the rule just reads the wording of a
    fact the tagger already tagged."""

    base: str
    pattern: str
    veto: Optional[str]
    meaning: str
    #: removed from the text before ``pattern`` runs
    mask: Optional[str] = None


#: A nicu fact that names a NICU (level III), not only an SNCU (level II).
#: Within the v0.2 nicu tag (regex ``\bnicu\b|\bsncu\b|neonatal intensive``)
#: the complement is exactly the SNCU-only facts.
NICU_NAMED_PATTERN = r"\bnicu\b|neonatal intensive"
#: A level III (tertiary) newborn care mention.
LEVEL3_PATTERN = r"\blevel[- ]?(iii|3|three)\b|\btertiary (level |care )?(nicu|neonatal intensive)"
#: Newborn context for a ventilator fact. SNCU alone is not newborn intensive
#: context: FBNC 2025, "Simply adding ventilators to SNCU does not qualify the unit as NICU".
NEWBORN_CONTEXT_PATTERN = r"neonat|new[- ]?born|\bnicu\b|\bnnicu\b|preterm|prematur|\binfants?\b"
SNCU_ONLY_VETO = r"^(?!.*(\bnicu\b|neonatal intensive|newborn intensive)).*\bsncu\b"
#: A dialysis_unit fact that names a unit, machines or haemodialysis, so a
#: nephrologist alone (staffing) does not corroborate dialysis.
DIALYSIS_UNIT_NAMED_PATTERN = (
    r"dialysis (unit|machine|cent(re|er)|ward|room|department|dept|bed|station)|\bh(a)?emo[- ]?dialysis"
)

#: Transfusion to a fetus or newborn is not WHO signal function 9 (blood
#: transfusion for the mother). Found by reading the v0.3 emoc promotions:
#: "Fetal Blood Transfusion", "Intrauterine blood transfusion".
FETAL_TRANSFUSION_MASK = (
    r"(intra[- ]?uterine|fetal|foetal|in[- ]?utero|neonatal|exchange)( \w+){0,2} (blood )?transfusions?"
)

QUALIFIERS_V03: dict[str, Qualifier] = {
    "blood_maternal": Qualifier("blood", TAG_PATTERNS["blood"], None,
                                "a blood fact that still names a blood bank, storage or transfusion once fetal, "
                                "intrauterine, neonatal and exchange transfusion wording is removed",
                                FETAL_TRANSFUSION_MASK),
    "nicu_named": Qualifier("nicu", NICU_NAMED_PATTERN, None,
                            "a nicu fact naming a NICU or neonatal intensive care, not only an SNCU"),
    "nicu_level3": Qualifier("nicu", rf"^(?=.*({NICU_NAMED_PATTERN})).*({LEVEL3_PATTERN})", None,
                             "a NICU fact that says level III (or tertiary)"),
    "ventilator_newborn": Qualifier("ventilator", NEWBORN_CONTEXT_PATTERN, SNCU_ONLY_VETO,
                                    "a ventilator fact that is about newborns (not an SNCU-only fact)"),
    "neonatal_resp_not_sncu": Qualifier("neonatal_resp", r".", SNCU_ONLY_VETO,
                                        "a neonatal_resp fact that is not about an SNCU alone"),
    "dialysis_unit_named": Qualifier("dialysis_unit", DIALYSIS_UNIT_NAMED_PATTERN, None,
                                     "a dialysis_unit fact naming a unit, machines or haemodialysis, "
                                     "not a nephrologist alone"),
}
QUALIFIERS_BY_VERSION: dict[str, dict[str, Qualifier]] = {RULE_VERSION_V03: QUALIFIERS_V03}

_QUAL_RX: dict[str, dict[str, tuple]] = {
    v: {n: (q.base, re.compile(q.pattern), re.compile(q.veto) if q.veto else None,
            re.compile(q.mask) if q.mask else None) for n, q in qs.items()}
    for v, qs in QUALIFIERS_BY_VERSION.items()
}


def qualify_tags(text: Optional[str], tags: Iterable[str], version: str = RULE_VERSION_V03) -> frozenset:
    """A fact's tags plus the ``version`` qualifiers that hold on it. Versions
    without qualifiers return the tags unchanged."""
    out = set(tags)
    rx = _QUAL_RX.get(version)
    if not rx or text is None:
        return frozenset(out)
    s = normalize_text(str(text))
    for name, (base, pat, veto, mask) in rx.items():
        if base not in out or (veto and veto.search(s)):
            continue
        if pat.search(mask.sub(" ", s) if mask else s):
            out.add(name)
    return frozenset(out)


def base_tag(tag: str, version: str = RULE_VERSION_V03) -> str:
    """The real tag behind a qualifier (``nicu_named`` gives ``nicu``); tags pass through."""
    q = QUALIFIERS_BY_VERSION.get(version, {}).get(tag)
    return q.base if q else tag


#: Plain citations, used in the requirement names below (docs/06, "v0.3").
_WHO8 = "WHO 2009 Table 4, signal function 8"
_WHO9 = "WHO 2009 Table 4, signal function 9"
_FBNC = "FBNC 2025: NICU is level III, SNCU is level II"
_PMJAY_C = "PM-JAY Cardiology/CTVS criteria"
_PMJAY_N = "PM-JAY Nephrology criteria item 1; PMNDP"

_NICU_SUPPORTED_V03 = f"NICU or neonatal intensive care named ({_FBNC})"

#: v0.3 rules: the v0.2 rules with nicu, emoc, cath_lab and dialysis replaced.
GRADE_RULES_V03: dict[str, LineRule] = {
    **GRADE_RULES,
    "emoc": LineRule(
        # WHO 2009 Table 4: comprehensive EmOC is signal functions 1-7 "plus:
        # (8) Perform surgery (e.g., caesarean section) (9) Perform blood
        # transfusion". Anaesthesia and OT are no longer required for grade 3;
        # anaesthesia and a 24-hour service give an FRU-level note (FRU 2025 s2.2).
        # Fetal, intrauterine, neonatal and exchange transfusion do not count as
        # signal function 9 (blood_maternal).
        line="emoc",
        claim_specialties=_EMOC.claim_specialties,
        claim_tags=frozenset({"ob_delivery"}),
        supported=_groups({"csection"}),
        corroborated=_groups({"csection"}, {"blood_maternal"}),
        bonus_tags=("blood", "anesthesia", "ot", "nicu", "h24"),
        tags=("csection", "blood_maternal", "blood", "anesthesia", "ot", "ob_delivery", "nicu", "h24"),
        supported_names=(f"caesarean section ({_WHO8})",),
        corroborated_names=(f"caesarean section ({_WHO8})", f"blood bank or transfusion ({_WHO9})"),
        claim_name="maternity or delivery service",
        note_groups=_groups({"anesthesia"}, {"h24"}),
        note_names=("anaesthesia (FRU 2025 s2.2 criterion 4)", "24-hour service (FRU 2025 s2.2 criterion 1)"),
        note_text=("FRU-level: anaesthesia and a 24-hour service are also named, two more of the FRU 2025 s2.2 "
                   "criteria; the 24-hour fact may not be about obstetric care"),
    ),
    "nicu": LineRule(
        # FBNC 2025: SNCU "delivers level II care"; NICU is "Tertiary Care-NICU
        # (Level III)"; "Simply adding ventilators to SNCU does not qualify the
        # unit as NICU". A generic ventilator elsewhere in the record no longer
        # corroborates.
        line="nicu",
        claim_specialties=frozenset({"neonatologyPerinatalMedicine"}),
        claim_tags=frozenset({"nicu"}),
        supported=_groups({"nicu_named"}),
        corroborated=_groups({"nicu_named"}, {"neonatal_resp_not_sncu", "ventilator_newborn", "nicu_level3"}),
        bonus_tags=("neonatal_resp", "ventilator"),
        tags=("nicu_named", "neonatal_resp_not_sncu", "ventilator_newborn", "nicu_level3", "nicu", "neonatal_resp",
              "ventilator"),
        supported_names=(_NICU_SUPPORTED_V03,),
        corroborated_names=(_NICU_SUPPORTED_V03,
                            "newborn breathing support, a ventilator in a newborn fact, or a level III mention "
                            "(FBNC 2025)"),
        claim_name="names an SNCU (level II), not a NICU",
        claim_next_name="a related specialty listed, or an SNCU or NICU mention",
    ),
    "cath_lab": LineRule(
        # PM-JAY Cardiology/CTVS: "6. Fully equipped Catheterization Laboratory
        # Unit", "3. ICU Facility with cardiac monitoring and ventilator
        # support"; advanced criteria: "fully equipped with ICCU/SICU/ NICU/
        # relevant Intensive Care Unit". h24 and emergency are no longer required.
        line="cath_lab",
        claim_specialties=frozenset({"cardiology", "interventionalRadiology"}),
        claim_tags=frozenset(),
        supported=_groups({"cath_lab"}),
        corroborated=_groups({"cath_lab"}, {"ccu"}),
        corroborated_alts=((
            _groups({"cath_lab"}, {"icu"}, {"ventilator"}),
            (f"cath lab, angiography or angioplasty ({_PMJAY_C} item 6)",
             f"intensive care unit ({_PMJAY_C} item 3)", f"ventilator support ({_PMJAY_C} item 3)"),
        ),),
        bonus_tags=("h24", "emergency"),
        tags=("cath_lab", "ccu", "icu", "ventilator", "h24", "emergency"),
        supported_names=(f"cath lab, angiography or angioplasty ({_PMJAY_C} item 6)",),
        corroborated_names=(f"cath lab, angiography or angioplasty ({_PMJAY_C} item 6)",
                            "cardiac care unit (CCU or ICCU; PM-JAY advanced criteria)"),
    ),
    "dialysis": LineRule(
        # PM-JAY Nephrology: "1. Dialysis unit"; PMNDP: "Haemodialysis requires
        # a dialysis machine"; PMNDP has the nephrologist "visit the dialysis
        # facility fortnightly/monthly", so a nephrologist is staffing, not a unit.
        line="dialysis",
        claim_specialties=frozenset({"nephrology"}),
        claim_tags=frozenset(),
        supported=_groups({"dialysis"}),
        corroborated=_groups({"dialysis"}, {"dialysis_unit_named"}),
        bonus_tags=("dialysis_unit",),
        tags=("dialysis", "dialysis_unit_named", "dialysis_unit"),
        supported_names=("dialysis",),
        corroborated_names=("dialysis", f"dialysis unit, machines or haemodialysis named, not a nephrologist alone "
                                        f"({_PMJAY_N})"),
    ),
}
GRADE_RULES_V03 = {line: GRADE_RULES_V03[line] for line in SERVICE_LINES}

#: The v0.3 lines whose rules differ from v0.2.
CHANGED_LINES_V03: tuple[str, ...] = ("emoc", "nicu", "dialysis", "cath_lab")

RULES_BY_VERSION: dict[str, dict[str, LineRule]] = {
    "v0.1": GRADE_RULES_V01, "v0.2": GRADE_RULES, RULE_VERSION_V03: GRADE_RULES_V03,
}
SERVICE_LINES_BY_VERSION: dict[str, tuple[str, ...]] = {
    "v0.1": SERVICE_LINES_V01, "v0.2": SERVICE_LINES, RULE_VERSION_V03: SERVICE_LINES,
}


def _rules(version: str) -> dict[str, LineRule]:
    if version not in RULES_BY_VERSION:
        raise ValueError(f"unknown recipe version {version!r}; expected one of {GRADE_RULE_VERSIONS}")
    return RULES_BY_VERSION[version]


def _rule(line: str, version: str) -> LineRule:
    rules = _rules(version)
    if line not in rules:
        raise ValueError(f"service line {line!r} is not in recipe {version}")
    return rules[line]


def _holds(groups: tuple, tags: set) -> bool:
    return all(g & tags for g in groups)


def _corroborated_option(rule: LineRule, tags: set) -> Optional[tuple]:
    """(groups, names) of the first corroborated option that holds on ``tags``, else None."""
    for groups, names in rule.corroborated_options:
        if _holds(groups, tags):
            return groups, names
    return None


def _repeat_holds(rule: LineRule, tag_support: Optional[Mapping[str, tuple]]) -> bool:
    """The corroborated repeat condition. ``tag_support`` maps a tag to
    (distinct normalized texts carrying it, set or count of fields it appears in)."""
    if not rule.has_repeat:
        return True
    if not tag_support:
        return False
    texts: set = set()
    fields: set = set()
    n_texts = n_fields = 0
    for t in rule.corroborated[0]:
        sup = tag_support.get(t)
        if not sup:
            continue
        a, b = sup
        if isinstance(a, (set, frozenset)):
            texts |= a
        else:
            n_texts = max(n_texts, int(a))
        if isinstance(b, (set, frozenset)):
            fields |= b
        else:
            n_fields = max(n_fields, int(b))
    n_texts = max(n_texts, len(texts))
    n_fields = max(n_fields, len(fields))
    if n_texts >= rule.min_distinct_facts:
        return True
    return rule.min_distinct_fields > 0 and n_fields >= rule.min_distinct_fields


def grade_line(
    line: str,
    tags: Iterable[str],
    specialties: Iterable[str] = (),
    version: str = RECIPE_VERSION,
    tag_support: Optional[Mapping[str, tuple]] = None,
) -> int:
    """Grade 0 to 3 for one service line from a facility's tags and specialties.

    ``tags`` is the union of the tags on all the facility's facts. The highest
    level whose rule holds wins, so a facility with a C-section mention is
    ``supported`` even without an obstetric specialty.

    ``tag_support`` (v0.2, needed only for ``mri`` and ``ct`` corroboration)
    maps a tag to ``(distinct texts, fields)``, each a count or a set. Without
    it a repeat condition cannot hold, so ``mri`` and ``ct`` stop at grade 2;
    ``facility_line_grades`` always passes it.
    """
    rule = _rule(line, version)
    t = set(tags)
    if _corroborated_option(rule, t) is not None and _repeat_holds(rule, tag_support):
        return 3
    if _holds(rule.supported, t):
        return 2
    if rule.claim_specialties.intersection(specialties) or rule.claim_tags & t:
        return 1
    return 0


def grade_facility(
    tags: Iterable[str],
    specialties: Iterable[str] = (),
    version: str = RECIPE_VERSION,
    tag_support: Optional[Mapping[str, tuple]] = None,
) -> dict[str, int]:
    """Grade on every service line of ``version``: ``{"emoc": g, "nicu": g, ...}``."""
    t = set(tags)
    s = set(specialties)
    return {line: grade_line(line, t, s, version, tag_support) for line in SERVICE_LINES_BY_VERSION[version]}


def _required_groups(rule: LineRule, grade: int, tags: Optional[set] = None) -> tuple:
    if grade == 3:
        opt = _corroborated_option(rule, tags) if tags is not None else None
        return opt[0] if opt else rule.corroborated
    if grade == 2:
        return rule.supported
    if grade == 1 and rule.claim_tags:
        return (rule.claim_tags,)
    return ()


def _required_names(rule: LineRule, grade: int, tags: Optional[set] = None) -> tuple:
    if grade == 3:
        opt = _corroborated_option(rule, tags) if tags is not None else None
        return opt[1] if opt else rule.corroborated_names
    if grade == 2:
        return rule.supported_names
    if grade == 1 and rule.claim_tags:
        return (rule.claim_name,)
    return ()


class Fact(NamedTuple):
    """One fact of a facility, as the grade code sees it."""

    text: str
    tags: frozenset
    field: Optional[str] = None
    fact_id: Optional[str] = None


def _as_facts(facts: Sequence) -> list[Fact]:
    """Accept (text, tags), (text, tags, field) or (text, tags, field, fact_id) tuples, or Facts."""
    out = []
    for f in facts:
        text, tags, *rest = f
        fld = rest[0] if len(rest) > 0 else None
        fid = rest[1] if len(rest) > 1 else None
        out.append(Fact(text, frozenset(tags), fld, fid))
    return out


def _qualify(facts: Sequence[Fact], version: str) -> list[Fact]:
    """Add the ``version`` qualifiers to each fact's tags (v0.3); a no-op otherwise."""
    if version not in QUALIFIERS_BY_VERSION:
        return list(facts)
    return [f._replace(tags=qualify_tags(f.text, f.tags, version)) for f in facts]


def _tag_support(facts: Sequence[Fact], tags: Iterable[str]) -> dict[str, tuple]:
    """{tag: (distinct normalized texts, fields)} over the facts carrying it."""
    out: dict[str, tuple] = {}
    for t in tags:
        texts, fields = set(), set()
        for f in facts:
            if t in f.tags:
                texts.add(normalize_text(f.text))
                fields.add(f.field)
        if texts:
            out[t] = (texts, fields)
    return out


def _candidates(facts: Sequence[Fact], wanted: set) -> list[tuple[int, frozenset]]:
    """(index into facts, rule tags) for facts carrying a wanted tag, first
    occurrence of each normalized text only."""
    cands, seen = [], set()
    for i, f in enumerate(facts):
        tset = f.tags & wanted
        if not tset:
            continue
        key = normalize_text(f.text)
        if key in seen:
            continue
        seen.add(key)
        cands.append((i, tset))
    return cands


def _cover(cands: list, groups: tuple, k: int) -> tuple[list[int], dict[int, int]]:
    """Pick candidates covering every group in rule order (the defining fact
    first). For each uncovered group, take the candidate that covers it and the
    most other uncovered groups, earliest on ties. Returns (chosen candidate
    positions, {group position: candidate position})."""
    chosen: list[int] = []
    group_of: dict[int, int] = {}
    uncovered = list(range(len(groups)))
    while uncovered and len(chosen) < k:
        head = groups[uncovered[0]]
        best, best_n = None, 0
        for j, (_, tset) in enumerate(cands):
            if j in chosen or not (head & tset):
                continue
            n = sum(1 for gi in uncovered if groups[gi] & tset)
            if n > best_n:
                best, best_n = j, n
        if best is None:  # no fact covers this group; cannot happen for a grade the tags reached
            uncovered.pop(0)
            continue
        chosen.append(best)
        for gi in uncovered:
            if groups[gi] & cands[best][1]:
                group_of[gi] = best
        uncovered = [gi for gi in uncovered if gi not in group_of]
    return chosen, group_of


def _repeat_fact(facts: Sequence[Fact], rule: LineRule, first: int) -> Optional[int]:
    """Index of a fact that satisfies the repeat condition together with fact
    ``first``: another distinct text carrying the defining tags, else the same
    tags in another field."""
    head = rule.corroborated[0]
    key0, fld0 = normalize_text(facts[first].text), facts[first].field
    other_field = None
    for i, f in enumerate(facts):
        if i == first or not (f.tags & head):
            continue
        if normalize_text(f.text) != key0:
            return i
        if other_field is None and rule.min_distinct_fields > 0 and f.field != fld0:
            other_field = i
    return other_field


def _select(facts: Sequence[Fact], rule: LineRule, grade: int, k: int):
    """Shared by ``select_evidence`` and ``facility_line_grades``.

    Returns (evidence fact indices in order, {group position: fact index},
    repeat fact index or None)."""
    if grade <= 0 or k <= 0:
        return [], {}, None
    rule_tags = rule.rule_tags
    cands = _candidates(facts, set(rule_tags))
    all_tags = set().union(*(f.tags for f in facts)) if facts else set()
    groups = _required_groups(rule, grade, all_tags)
    chosen, group_of = _cover(cands, groups, k)
    group_fact = {gi: cands[j][0] for gi, j in group_of.items()}

    repeat = None
    if grade == 3 and rule.has_repeat and 0 in group_fact:
        repeat = _repeat_fact(facts, rule, group_fact[0])
        if repeat is not None and len(chosen) < k:
            j = next((j for j, (i, _) in enumerate(cands) if i == repeat), None)
            if j is not None and j not in chosen:
                chosen.append(j)

    # fill round-robin across tags in priority order
    by_tag = {t: [j for j, (_, tset) in enumerate(cands) if t in tset] for t in rule_tags}
    while len(chosen) < k:
        added = False
        for t in rule_tags:
            nxt = next((j for j in by_tag[t] if j not in chosen), None)
            if nxt is not None:
                chosen.append(nxt)
                added = True
                if len(chosen) >= k:
                    break
        if not added:
            break
    return [cands[j][0] for j in chosen], group_fact, repeat


def select_evidence(
    facts: Sequence,
    line: str,
    grade: int,
    k: int = MAX_EVIDENCE,
    version: str = RECIPE_VERSION,
) -> list[str]:
    """Up to ``k`` fact texts that justify ``grade`` on ``line``.

    ``facts`` is the facility's (text, tags[, field[, fact_id]]) tuples in a
    stable order. Only facts carrying a tag the grade rule uses are candidates
    (bonus tags such as h24 for emoc are not evidence). Repeated texts (same
    normalized form) count once.

    Order: first a small set of facts that together cover every requirement of
    the grade reached (for corroborated EmOC: a C-section fact, a blood fact, an
    anesthesia or OT fact; for corroborated MRI: two MRI facts), then the rest
    round-robin across the rule's tags in priority order, so the list stays
    varied. Grade 0 has no evidence.
    """
    fs = _qualify(_as_facts(facts), version)
    idx, _, _ = _select(fs, _rule(line, version), grade, k)
    return [fs[i].text for i in idx]


def evidence_strength(
    grade: int,
    n_supporting_facts: int,
    n_source_domains=None,
    official_website=None,
) -> str:
    """``none``, ``thin``, ``moderate`` or ``strong`` (docs/06, v0.2).

    - ``none`` at grade 0; grade 1 is ``thin`` by definition.
    - ``thin``: grade 2 or 3 resting on a single fact, or on a record with one
      source domain or fewer and no official website.
    - ``strong``: grade 3, at least 3 supporting facts, and at least 2 source
      domains or an official website.
    - ``moderate``: everything else at grade 2 or 3.

    Missing domain counts and websites count as 0 and absent.
    """
    if grade <= 0:
        return "none"
    if grade == 1:
        return "thin"
    n_dom = 0 if _missing(n_source_domains) else max(int(n_source_domains), 0)
    site = _has_site(official_website)
    if n_supporting_facts <= 1 or (n_dom <= 1 and not site):
        return "thin"
    if grade == 3 and n_supporting_facts >= 3 and (n_dom >= 2 or site):
        return "strong"
    return "moderate"


@dataclass(frozen=True)
class LineGrade:
    """One row of ``gold/capability_grades.parquet``, minus the facility-level fields."""

    service_line: str
    grade: int
    grade_label: str
    tags_present: list
    evidence: list
    #: v0.2: one dict per satisfied requirement: requirement, tag, fact_id, field, text
    evidence_items: list = dc_field(default_factory=list)
    #: v0.2: plain-language requirements of the next grade that no fact satisfies
    missing_for_next: list = dc_field(default_factory=list)
    #: v0.2: distinct normalized fact texts carrying a tag of the reached grade's
    #: required groups (one sentence copied into two fields counts once)
    n_supporting_facts: int = 0
    #: v0.2: none, thin, moderate, strong
    evidence_strength: str = "none"
    #: v0.3: a note that never changes the grade (emoc: FRU-level pieces named)
    level_note: Optional[str] = None
    #: v0.3: one evidence item per requirement of the note
    level_note_items: list = dc_field(default_factory=list)


def _item(requirement: str, group: frozenset, rule: LineRule, f: Fact, version: str = RECIPE_VERSION) -> dict:
    tag = next(t for t in rule.tags if t in group and t in f.tags)
    return {"requirement": requirement, "tag": base_tag(tag, version), "fact_id": f.fact_id, "field": f.field,
            "text": f.text}


def _missing_for_next(rule: LineRule, grade: int, tags: set, tag_support: Mapping) -> list[str]:
    if grade >= 3:
        return []
    if grade == 2:
        if not rule.corroborated_alts:
            out = [n for g, n in zip(rule.corroborated, rule.corroborated_names) if not (g & tags)]
            if rule.has_repeat and _holds(rule.corroborated, tags) and not _repeat_holds(rule, tag_support):
                out.append(rule.repeat_name)
            return out
        # v0.3: the option with the fewest missing groups; tied options are offered together
        missing = [[n for g, n in zip(groups, names) if not (g & tags)] for groups, names in rule.corroborated_options]
        fewest = min(len(m) for m in missing)
        best = [m for m in missing if len(m) == fewest]
        if len(best) == 1:
            return best[0]
        return [", or ".join(" and ".join(m) for m in best)]
    if grade == 1:
        return [n for g, n in zip(rule.supported, rule.supported_names) if not (g & tags)]
    if rule.claim_next_name:
        return [rule.claim_next_name]
    claim = "a related specialty listed"
    return [claim + (f" or a {rule.claim_name} mention" if rule.claim_tags else "")]


def _note(rule: LineRule, grade: int, facts: Sequence[Fact], version: str) -> tuple[Optional[str], list]:
    """(note text, items) when the rule has a note, the grade is 3 and every note group holds."""
    if grade != 3 or not rule.note_groups:
        return None, []
    items = []
    for group, name in zip(rule.note_groups, rule.note_names):
        f = next((f for f in facts if f.tags & group), None)
        if f is None:
            return None, []
        items.append(_item(name, group, rule, f, version))
    return rule.note_text, items


def _n_supporting(facts: Sequence[Fact], groups: tuple) -> int:
    wanted = set().union(*groups) if groups else set()
    return len({normalize_text(f.text) for f in facts if f.tags & wanted})


def facility_line_grades(
    facts: Sequence,
    specialties: Iterable[str] = (),
    k: int = MAX_EVIDENCE,
    version: str = RECIPE_VERSION,
    n_source_domains=None,
    official_website=None,
) -> list[LineGrade]:
    """Grade one facility on every service line of ``version``, with evidence.

    ``facts`` is the facility's (text, tags, field, fact_id) tuples from any
    tagger, in a stable order (``facts.parquet`` order); field and fact_id may
    be left off, but then ``mri`` and ``ct`` can corroborate only through two
    distinct texts and evidence items carry None for them. ``tags_present``
    lists the line's tags found on the facility, bonus tags included, in the
    rule's priority order. ``n_source_domains`` and ``official_website`` are
    the facility's, for ``evidence_strength``.
    """
    _rules(version)
    fs = _qualify(_as_facts(facts), version)
    quals = QUALIFIERS_BY_VERSION.get(version, {})
    all_tags: set = set().union(*(f.tags for f in fs)) if fs else set()
    spec = set(specialties)
    support = _tag_support(fs, all_tags)
    out = []
    for line in SERVICE_LINES_BY_VERSION[version]:
        rule = _rule(line, version)
        g = grade_line(line, all_tags, spec, version, support)
        idx, group_fact, repeat = _select(fs, rule, g, k)
        groups, names = _required_groups(rule, g, all_tags), _required_names(rule, g, all_tags)
        items = [_item(names[gi], groups[gi], rule, fs[group_fact[gi]], version) for gi in range(len(groups))
                 if gi in group_fact]
        if repeat is not None:
            items.append(_item(rule.repeat_name, rule.corroborated[0], rule, fs[repeat], version))
        n_sup = _n_supporting(fs, groups)
        note, note_items = _note(rule, g, fs, version)
        out.append(LineGrade(
            service_line=line,
            grade=g,
            grade_label=GRADE_LABELS[g],
            # real tags only: qualifiers are rule-internal (v0.3)
            tags_present=[t for t in rule.tags if t in all_tags and t not in quals],
            evidence=[fs[i].text for i in idx],
            evidence_items=items,
            missing_for_next=_missing_for_next(rule, g, all_tags, support),
            n_supporting_facts=n_sup,
            evidence_strength=evidence_strength(g, n_sup, n_source_domains, official_website),
            level_note=note,
            level_note_items=note_items,
        ))
    return out


# ---------------------------------------------------------------------------
# Source confidence
# ---------------------------------------------------------------------------

#: Reference date for page recency: the date of the raw export.
SNAPSHOT_DATE = date(2026, 11, 21)

#: v0 weights. They sum to 1, so the score is in [0, 1]. Not calibrated: a
#: plain, explainable heuristic over the four signals the recipes doc lists.
CONFIDENCE_WEIGHTS: dict[str, float] = {
    "domains": 0.4,
    "official_website": 0.2,
    "recency": 0.2,
    "staff_named": 0.2,
}
#: Distinct source domains at which the domain term is full (6+ is the top
#: bucket in the data dictionary; median facility has 5).
DOMAINS_FULL = 6
#: Page age in years at which the recency term is full, and at which it is zero.
RECENCY_FULL_YEARS = 1.0
RECENCY_ZERO_YEARS = 5.0


def _missing(v) -> bool:
    if v is None:
        return True
    try:
        return bool(v != v)  # NaN, NaT, pd.NA (pd.NA != pd.NA raises, caught below)
    except TypeError:
        return True


def _has_site(official_website) -> bool:
    if isinstance(official_website, str):
        return bool(official_website.strip())
    return not _missing(official_website) and bool(official_website)  # bool, numpy bool, None, NaN


def _as_date(v) -> Optional[date]:
    if _missing(v):
        return None
    if isinstance(v, datetime):  # includes pd.Timestamp
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        try:
            return date.fromisoformat(v[:10])
        except ValueError:
            return None
    return None


def recency_score(page_updated, as_of: date = SNAPSHOT_DATE) -> float:
    """1 for a page updated within a year of ``as_of``, falling linearly to 0 at
    five years. Missing, unparseable, or future dates give 0."""
    d = _as_date(page_updated)
    if d is None or d > as_of:
        return 0.0
    age = (as_of - d).days / 365.25
    if age <= RECENCY_FULL_YEARS:
        return 1.0
    if age >= RECENCY_ZERO_YEARS:
        return 0.0
    return (RECENCY_ZERO_YEARS - age) / (RECENCY_ZERO_YEARS - RECENCY_FULL_YEARS)


def source_confidence_parts(
    n_source_domains,
    official_website,
    page_updated,
    staff_named,
    as_of: date = SNAPSHOT_DATE,
) -> dict[str, float]:
    """Each signal scaled to [0, 1], before weighting. Missing counts as 0."""
    n = 0 if _missing(n_source_domains) else max(int(n_source_domains), 0)
    staff = False if _missing(staff_named) else bool(staff_named)
    return {
        "domains": min(n, DOMAINS_FULL) / DOMAINS_FULL,
        "official_website": 1.0 if _has_site(official_website) else 0.0,
        "recency": recency_score(page_updated, as_of),
        "staff_named": 1.0 if staff else 0.0,
    }


def source_confidence(
    n_source_domains,
    official_website,
    page_updated,
    staff_named,
    as_of: date = SNAPSHOT_DATE,
) -> float:
    """How much to trust a facility's pages, 0 to 1 (v0 heuristic).

    0.4 x min(distinct source domains, 6) / 6
    + 0.2 x has an official website
    + 0.2 x page recency (1 within a year of the snapshot, 0 at five years or unknown)
    + 0.2 x names affiliated staff

    ``official_website`` may be the URL string (present when non-blank) or a bool.
    Separate from the grade: a grade says how complete the evidence is, this
    says how much to trust where it came from. Caveat: some ``source_urls``
    point at unrelated sites, so the domain count can overstate corroboration.
    """
    parts = source_confidence_parts(n_source_domains, official_website, page_updated, staff_named, as_of)
    score = sum(CONFIDENCE_WEIGHTS[k] * v for k, v in parts.items())
    return round(min(max(score, 0.0), 1.0), 4)


def _check_rules() -> None:
    """Invariants every rule set must keep. Runs at import."""
    assert set(TAG_DEFINITIONS) == set(ALL_TAGS) and len(set(ALL_TAGS)) == len(ALL_TAGS)
    assert set(NEW_TAG_RULES) == set(NEW_TAGS_V02) and set(TAG_PATTERNS) == set(EVIDENCE_TAGS_V01)
    assert set(RULES_BY_VERSION) == set(GRADE_RULE_VERSIONS) == set(SERVICE_LINES_BY_VERSION)
    for qs in QUALIFIERS_BY_VERSION.values():
        assert all(q.base in EVIDENCE_TAGS for q in qs.values()) and not set(qs) & set(ALL_TAGS)
    for version, rules in RULES_BY_VERSION.items():
        assert tuple(rules) == SERVICE_LINES_BY_VERSION[version], version
        known = set(EVIDENCE_TAGS_BY_VERSION.get(version, EVIDENCE_TAGS)) | set(QUALIFIERS_BY_VERSION.get(version, {}))
        for rule in rules.values():
            assert set(rule.tags) <= known, rule.line
            assert set(rule.bonus_tags) <= set(rule.tags), rule.line
            assert set(rule.claim_tags).union(*rule.supported, *rule.corroborated) <= set(rule.tags), rule.line
            assert len(rule.supported_names) == len(rule.supported), rule.line
            assert bool(rule.claim_tags) == bool(rule.claim_name), rule.line
            assert rule.has_repeat == bool(rule.repeat_name), rule.line
            assert not (rule.has_repeat and rule.corroborated_alts), rule.line
            assert len(rule.note_groups) == len(rule.note_names) and bool(rule.note_groups) == bool(rule.note_text)
            assert set().union(*rule.note_groups) <= set(rule.tags) if rule.note_groups else True, rule.line
            for groups, names in rule.corroborated_options:
                assert len(names) == len(groups), rule.line
                assert set().union(*groups) <= set(rule.tags), rule.line
                # every corroborated option must imply supported, so grades are monotone
                for g in rule.supported:
                    assert any(c <= g for c in groups), rule.line
    assert abs(sum(CONFIDENCE_WEIGHTS.values()) - 1.0) < 1e-9
    assert not math.isclose(RECENCY_FULL_YEARS, RECENCY_ZERO_YEARS)


_check_rules()
