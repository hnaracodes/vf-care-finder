"""Service lines as the user sees them: display names, drive-time windows, the
evidence allowance, and the plain-language recipe text for the "How we decided"
panel.

This is the one place for these values (``docs/07_data_contracts.md``). The grade
*rules* themselves live in ``vf.recipes``; this module only describes them in
plain words and holds the ranking's product settings.

Score spec v1-final (D048): every number that filters or orders results is
measured, cited to a published standard, or a declared design choice shown in
"How we decided" with a sensitivity check (``reports/ordering_behaviour.md``).
``WINDOWS`` holds each line's window (minutes), its evidence allowance, whether
the window is cited or a design choice, and the basis. The old km radii stay
only for the km fallback (islands, where sea travel is not modelled) and the
legacy v0 ranker (D027, D028), which is kept behind a flag for the v0 against
v1 comparison. Change a value only by reading the scenario suite and the
sensitivity report, and log the change in ``docs/08_decisions_log.md``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

#: The 12 service lines (D025), in display order.
SERVICE_LINES: tuple[str, ...] = (
    "emoc",
    "nicu",
    "dialysis",
    "icu",
    "cardiac_surgery",
    "cath_lab",
    "mri",
    "ct",
    "trauma",
    "burns",
    "neurosurgery",
    "oncology",
)

GRADE_LABELS: tuple[str, ...] = ("none", "claimed", "supported", "corroborated")

#: What each grade label means on screen. Never show a grade number without this.
GRADE_PLAIN: dict[str, str] = {
    "none": "no mention in the record",
    "claimed": "claimed only (a specialty or a general statement, no fact names the service)",
    "supported": "supported (a fact in the record names the service)",
    "corroborated": "corroborated (the service and the things it needs are named in the record)",
}


@dataclass(frozen=True)
class Service:
    id: str
    label: str
    radius_km: float
    #: Plain recipe text, one sentence per grade level, for the panel.
    claimed: str
    supported: str
    corroborated: str
    #: Phrases for the keyword fallback when this line has no grades loaded
    #: (all 12 are graded from recipe v0.2), separated by ";" (each scored on
    #: its own, the best one counts).
    keywords: str
    note: Optional[str] = None

    @property
    def recipe_text(self) -> list[str]:
        out = [
            f"Claimed: {self.claimed}",
            f"Supported: {self.supported}",
            f"Corroborated: {self.corroborated}",
        ]
        if self.note:
            out.append(self.note)
        return out


SERVICES: dict[str, Service] = {
    s.id: s
    for s in (
        Service(
            "emoc", "Emergency C-section and high-risk delivery", 50,
            claimed="lists obstetrics or maternity care, or mentions deliveries or a labour ward.",
            supported="a fact names caesarean section (C-section, LSCS).",
            corroborated="caesarean section, a blood bank or transfusion, and anaesthesia or an operating theatre are all named.",
            keywords="caesarean section; c-section; lscs; emergency obstetric care",
            note="Based on the WHO signal functions for comprehensive emergency obstetric care, which add caesarean section and blood transfusion to basic care.",
        ),
        Service(
            "nicu", "Newborn intensive care (NICU)", 75,
            claimed="lists neonatology.",
            supported="a fact names a NICU or SNCU (newborn intensive or special care unit).",
            corroborated="a NICU and newborn breathing support (CPAP or a ventilator) are both named.",
            keywords="nicu; neonatal intensive care unit; sncu; special newborn care unit",
        ),
        Service(
            "dialysis", "Dialysis", 50,
            claimed="lists nephrology.",
            supported="a fact names dialysis.",
            corroborated="dialysis and a named dialysis unit, dialysis machines, or a nephrologist are named.",
            keywords="dialysis; haemodialysis; hemodialysis",
        ),
        Service(
            "icu", "Intensive care with ventilator", 75,
            claimed="lists critical care medicine.",
            supported="a fact names an intensive care unit (ICU).",
            corroborated="an ICU and a ventilator are both named.",
            keywords="icu; intensive care unit; ventilator; critical care unit",
        ),
        Service(
            "cardiac_surgery", "Heart surgery (bypass, valve)", 250,
            claimed="lists cardiac or cardiothoracic surgery.",
            supported="a fact names heart surgery (bypass, CABG, open heart, valve replacement or repair).",
            corroborated="heart surgery, an ICU or cardiac care unit, and a blood bank are all named.",
            keywords="cardiac surgery; cabg; bypass surgery; open heart surgery; valve replacement; cardiothoracic surgery",
        ),
        Service(
            "cath_lab", "Heart attack care (angiography, angioplasty)", 100,
            claimed="lists cardiology or interventional radiology.",
            supported="a fact names a cath lab, angioplasty, coronary angiography or a cardiac stent.",
            corroborated="a cath lab or angioplasty, an ICU or cardiac care unit, and round-the-clock or emergency service are all named.",
            keywords="cath lab; angioplasty; coronary angiography; ptca; cardiac stent",
        ),
        Service(
            "mri", "MRI scan", 100,
            claimed="lists radiology.",
            supported="a fact names MRI.",
            corroborated="MRI is named in at least two separate facts or two different parts of the record.",
            keywords="mri; magnetic resonance imaging",
        ),
        Service(
            "ct", "CT scan", 75,
            claimed="lists radiology.",
            supported="a fact names a CT scan.",
            corroborated="CT is named in at least two separate facts or two different parts of the record.",
            keywords="ct scan; computed tomography; slice ct",
        ),
        Service(
            "trauma", "Trauma and accident care", 50,
            claimed="lists emergency medicine or orthopaedic surgery, or mentions emergency care.",
            supported="a fact names trauma care (a trauma centre, unit, team or surgery).",
            corroborated="trauma care, an operating theatre, a blood bank or ICU, and round-the-clock service are all named.",
            keywords="trauma centre; trauma care; polytrauma; accident and emergency",
        ),
        Service(
            "burns", "Burn care", 200,
            claimed="lists burns or plastic surgery.",
            supported="a fact names a burns unit, ward or burn care.",
            corroborated="burn care, an ICU and an operating theatre are all named.",
            keywords="burns unit; burn ward; burn care; burn injury",
        ),
        Service(
            "neurosurgery", "Head injury and brain surgery", 150,
            claimed="lists neurosurgery.",
            supported="a fact names neurosurgery, craniotomy, spine surgery or head injury care.",
            corroborated="neurosurgery, an ICU and a CT scan are all named.",
            keywords="neurosurgery; craniotomy; brain surgery; head injury",
        ),
        Service(
            "oncology", "Cancer treatment (chemotherapy, radiotherapy)", 250,
            claimed="lists medical, surgical, radiation or gynaecological oncology.",
            supported="a fact names chemotherapy or radiotherapy.",
            corroborated="chemotherapy and radiotherapy are both named.",
            keywords="chemotherapy; radiotherapy; radiation oncology; linear accelerator",
        ),
    )
}
assert tuple(SERVICES) == SERVICE_LINES

RADII_KM: dict[str, float] = {k: s.radius_km for k, s in SERVICES.items()}


# ---------------------------------------------------------------------------
# Score spec v1-final (D048): drive-time windows and the evidence allowance
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Window:
    """One line's reach gate. ``minutes``: estimated free-flow drive time
    (``vf.travel``) a recommendable facility must be within. ``allowance``: a
    corroborated record may be listed before a nearer supported one if it is at
    most this many minutes further. ``kind``: ``cited`` (a published indicator
    with this number) or ``design choice`` (ours, declared, with its basis).
    Planning thresholds, not clinical deadlines."""
    minutes: float
    allowance: float
    kind: str
    basis: str

    def as_dict(self) -> dict:
        return {"minutes": self.minutes, "allowance": self.allowance, "kind": self.kind, "basis": self.basis}


#: Evidence allowance (minutes) for urgent lines, and for planned care.
ALLOWANCE_URGENT = 15.0
ALLOWANCE_PLANNED = 30.0
#: Lines where care is usually planned, not an emergency: the larger allowance.
PLANNED_CARE: tuple[str, ...] = ("mri", "cardiac_surgery", "oncology")

_LANCET = ("Cited: the Lancet Commission on Global Surgery's 2-hour access indicator (share of people within "
           "2 hours of a facility that can do caesarean section, laparotomy and treatment of open fracture). "
           "Holmer et al. 2019, Br J Surg, https://pmc.ncbi.nlm.nih.gov/articles/PMC6790969, note that it "
           "\"does not capture whether patients actually receive care\".")
_NO_STANDARD = "Design choice: no published travel-time standard was found for this service"

WINDOWS: dict[str, Window] = {
    "emoc": Window(120, ALLOWANCE_URGENT, "cited", _LANCET),
    "trauma": Window(120, ALLOWANCE_URGENT, "cited", _LANCET),
    "cath_lab": Window(90, ALLOWANCE_URGENT, "design choice",
                       "Design choice informed by ESC 2023, which sets \"an absolute time of 120 min from STEMI "
                       "diagnosis to PCI-mediated reperfusion\" (https://academic.oup.com/eurheartj/article/44/38/3720/7243210). "
                       "The 90-minute travel window inside that is ours, not ESC's."),
    "neurosurgery": Window(150, ALLOWANCE_URGENT, "design choice",
                           "Design choice informed by Seelig et al. 1981 (N Engl J Med): comatose patients with an "
                           "acute subdural haematoma did much better when operated within 4 hours of injury. "
                           "The 2.5-hour travel window is ours."),
    "dialysis": Window(60, ALLOWANCE_URGENT, "design choice",
                       "Design choice informed by DOPPS (Moist et al. 2008, Clin J Am Soc Nephrol): longer travel "
                       "time to dialysis went with worse quality of life, adherence and survival. Dialysis is "
                       "repeated two or three times a week, so the window is short. The 60 minutes is ours."),
    "icu": Window(120, ALLOWANCE_URGENT, "design choice", _NO_STANDARD + "; set equal to the 2-hour emergency indicator."),
    "nicu": Window(120, ALLOWANCE_URGENT, "design choice", _NO_STANDARD + "; set equal to the 2-hour emergency indicator."),
    "ct": Window(120, ALLOWANCE_URGENT, "design choice", _NO_STANDARD + "; set equal to the 2-hour emergency indicator."),
    "mri": Window(180, ALLOWANCE_PLANNED, "design choice", _NO_STANDARD + "; MRI is usually planned, so 3 hours."),
    "burns": Window(240, ALLOWANCE_URGENT, "design choice",
                    _NO_STANDARD + "; specialist burn units are few, so 4 hours."),
    "cardiac_surgery": Window(240, ALLOWANCE_PLANNED, "design choice",
                              _NO_STANDARD + "; heart surgery is usually planned, so 4 hours."),
    "oncology": Window(240, ALLOWANCE_PLANNED, "design choice",
                       _NO_STANDARD + "; cancer treatment is planned and repeated, so 4 hours."),
}
assert set(WINDOWS) == set(SERVICE_LINES)
assert all(WINDOWS[k].allowance == ALLOWANCE_PLANNED for k in PLANNED_CARE)

WINDOW_NOTE = ("Windows are planning thresholds for estimated free-flow drive time, not clinical deadlines: "
               "a facility just outside a window may still be the right place to go.")

#: The legacy km radius is used instead of a window where minutes cannot be
#: estimated (islands: sea travel is not modelled).
KM_FALLBACK_NOTE = ("Sea travel is not modelled, so no drive time is estimated here; the old straight-line "
                    "radius for this service is used instead.")

#: "Within reach" (the further-away line and the gate counts) is this multiple of the window.
REACH_FACTOR_V1 = 3.0

ORDER_RULE_V1 = ("Nearest first by estimated drive time. One declared evidence allowance: a corroborated record "
                 "can be listed before a nearer supported one if it is at most {allowance:.0f} minutes further, "
                 "unless the record carries a possible-merge or location-conflict warning. The nearest "
                 "recommendable facility in the window is always shown: if the allowance pushes it off the list, "
                 "it takes the last place. Equal times are ties, listed by straight-line km. Trust, page dates, "
                 "web domains and warnings are shown on the card; they do not reorder it.")

#: Warnings that deny a record the evidence allowance (fix round, Nov 22): a merged
#: record's corroboration may come from another facility, and a conflicting location
#: makes its minutes unreliable. They still do not block or reorder by themselves.
ALLOWANCE_DENY_FLAGS = frozenset({"possible_merge", "location_conflict"})

PROBABILITY_NOTE = ("Nothing here is a probability. Grades say what the facility's own web record names, the drive "
                    "times are estimates from straight-line distance, and the windows and the allowance are declared "
                    "thresholds.")

#: Recipe v0.3 (D048, D052): what each changed line now requires, in plain words,
#: with the standard each rule follows. Lines not listed keep their v0.2 text.
RECIPE_V03: dict[str, dict[str, str]] = {
    "emoc": {
        "claimed": "lists obstetrics or maternity care, or mentions deliveries or a labour ward.",
        "supported": "a fact names caesarean section (WHO 2009, Table 4, signal function 8).",
        "corroborated": ("caesarean section and a blood bank or blood transfusion are both named (signal function 9); "
                         "fetal, newborn and exchange transfusions do not count."),
        "note": ("When anaesthesia and a 24-hour service are also named, the card notes two more of the FRU 2025 "
                 "s2.2 criteria; that note never changes the grade."),
    },
    "nicu": {
        "claimed": "lists neonatology, or names an SNCU (level II care) but not a NICU.",
        "supported": "a fact names a NICU or neonatal intensive care (FBNC 2025: NICU is level III, SNCU is level II).",
        "corroborated": ("a NICU and newborn mechanical ventilation (not CPAP alone), a ventilator in a newborn fact, "
                         "or a self-declared level III NICU (FBNC 2025)."),
    },
    "dialysis": {
        "claimed": "lists nephrology.",
        "supported": "a fact names dialysis.",
        "corroborated": ("dialysis and a named dialysis unit, dialysis machines or haemodialysis; a nephrologist alone "
                         "does not count (PM-JAY Nephrology criteria item 1; PMNDP)."),
    },
    "cath_lab": {
        "claimed": "lists cardiology or interventional radiology.",
        "supported": "a fact names a cath lab, angiography or angioplasty (PM-JAY Cardiology/CTVS criteria item 6).",
        "corroborated": ("a cath lab, angiography or angioplasty, plus a cardiac care unit (CCU or ICCU), or plus an "
                         "adult ICU with ventilator support (PM-JAY Cardiology/CTVS criteria item 3)."),
    },
}

RECIPE_V03_CITATIONS: dict[str, str] = {
    "emoc": ("WHO 2009, Monitoring emergency obstetric care: a handbook, Table 4, signal functions 8 and 9; "
             "FRU 2025 (NHM Revised Guidelines for Operationalizing First Referral Units) s2.2"),
    "nicu": "FBNC 2025 (MoHFW Facility Based Newborn Care Operational Guidelines)",
    "cath_lab": "PM-JAY Hospital Empanelment Criteria, advanced criteria V.A Cardiology/CTVS",
    "dialysis": "PM-JAY Hospital Empanelment Criteria, V.H Nephrology item 1; PMNDP Operational Guidelines",
}

#: The evidence tags behind every grade, as measured (WP4, D041).
TAGGER_MEASURED = ("Evidence tags: the shipped tagger scores macro F1 0.91 (95% interval 0.90 to 0.93) on 777 "
                   "agent-labelled gold facts; weakest tag neurosurgery at 0.58.")


def window(service: str) -> Window:
    return WINDOWS[service]


def recipe_text(service: str, recipe_version: Optional[str] = None) -> list[str]:
    """The panel's recipe lines for ``service`` under ``recipe_version``
    (v0.3 text and citation for the lines v0.3 changed, else the v0.2 text)."""
    svc = SERVICES[service]
    if recipe_version == "v0.3" and service in RECIPE_V03:
        r = RECIPE_V03[service]
        out = [f"Claimed: {r['claimed']}", f"Supported: {r['supported']}", f"Corroborated: {r['corroborated']}"]
        if r.get("note"):
            out.append(r["note"])
        out.append(f"Standard: {RECIPE_V03_CITATIONS[service]}.")
        return out
    return svc.recipe_text


def label(service: str) -> str:
    return SERVICES[service].label if service in SERVICES else service


# ---------------------------------------------------------------------------
# Ranking defaults (D027, D028). The v0 score is provisional (D039).
# ---------------------------------------------------------------------------

#: v0 additive score weights. Sum to 1.
WEIGHTS: dict[str, float] = {"evidence": 0.5, "distance": 0.3, "trust": 0.2}
assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9

#: Evidence part by grade, before the strength adjustment.
GRADE_EVIDENCE: dict[int, float] = {2: 0.7, 3: 1.0}
#: Evidence strength adjustment, then the evidence part is clipped to [0, 1].
STRENGTH_ADJUST: dict[str, float] = {"thin": -0.2, "moderate": 0.0, "strong": 0.1, "none": 0.0}

#: Named penalties for minor (non-blocking) flags, subtracted from the score.
PENALTIES: dict[str, float] = {"possible_merge": 0.15, "location_conflict": 0.10}
#: Penalty for a minor flag of a kind not listed above.
PENALTY_OTHER_MINOR = 0.10

#: Candidates are looked for out to this multiple of the radius...
REACH_FACTOR = 3.0
#: ...and at most this many, nearest first.
MAX_CANDIDATES = 50
#: Scores within this of each other are called a tie and broken by distance.
TIE_EPS = 0.02
#: Order of the cards: every recommendable facility inside the service radius
#: comes before any beyond it, then by score (WP5 fix round). Candidates are
#: still scored out to ``REACH_FACTOR`` x the radius, so cards beyond the radius
#: fill the list only when fewer than three lie inside it. Without this, a
#: corroborated record far outside the radius could outscore every supported
#: one inside it, and the answer would say "options nearby" while showing none.
ORDER_RULE = ("Facilities inside the service radius are listed before any beyond it; "
              "within each group, by score. A higher score beyond the radius never "
              "pushes a facility inside the radius off the list.")
#: A resolved place known only this loosely (km) is treated like a fuzzy
#: facility location: card distances are rounded to whole km and the location
#: note says where they are measured from.
ORIGIN_FUZZY_KM = 2.0
#: Facilities shown in the "also nearby, not verified" list.
MAX_UNVERIFIED = 3

#: Facility location precision (km) by WP1 ``coord_check``, when no better
#: number is known. A scraped point is taken as good to about 1 km; a point
#: moved to its PIN centre is as good as the PIN's spread (from
#: ``silver/places.parquet`` when present, else this default).
LOCATION_PRECISION_KM: dict[str, float] = {
    "ok": 1.0,
    "ok_same_district": 2.0,
    "kept_city_matches_coords": 2.0,
    "no_pin_check": 2.0,
    "kept_unresolved": 25.0,
    "moved_to_pin": 10.0,
}
#: A facility located only this loosely (km) counts as "located only to a district".
DISTRICT_PRECISION_KM = 15.0

LOCATION_NOTES: dict[str, str] = {
    "ok": "Location from the record, consistent with its PIN code.",
    "ok_same_district": "Location from the record, in the same district as its PIN code.",
    "kept_city_matches_coords": "Location from the record; it disagrees with the PIN code but matches the city named.",
    "no_pin_check": "Location from the record; it could not be checked against a PIN code.",
    "kept_unresolved": "Location conflicts with the record's own PIN code and city; it is uncertain.",
    "moved_to_pin": "Placed at the centre of its PIN code area: the scraped location was far from it.",
}

#: Keyword fallback (D026).
SEARCH_RADIUS_KM = 100.0
#: Minimum cosine similarity for a fact to count as a keyword match.
SEARCH_MIN_SIMILARITY = 0.25
#: A specialty name is a claim, not a service (plan doc), so a specialty match
#: counts this fraction of a fact match.
SEARCH_SPECIALTY_WEIGHT = 0.5

# ---------------------------------------------------------------------------
# Probability mode (D039). Set by WP13 from measured precision on held-out
# labels. Until WP13 ships, these are placeholders and probability mode is off.
# ---------------------------------------------------------------------------

#: ``p_offers`` at or above this makes a facility recommendable. Placeholder.
P_OFFERS_THRESHOLD_DEFAULT = 0.5
P_OFFERS_THRESHOLD: dict[str, float] = {}
#: A line whose model saw fewer independent labels than this is "not calibrated". Placeholder.
MIN_LABELS_LINE = 30

#: Simulation (D038): how many facilities each simulated patient may try in turn.
SIM_MAX_VISITS = 10
SIM_DEFAULT_ORIGINS = 200


def p_threshold(service: str) -> float:
    return P_OFFERS_THRESHOLD.get(service, P_OFFERS_THRESHOLD_DEFAULT)


SCORE_FORMULA_V0 = (
    "score = 0.5 x evidence + 0.3 x distance + 0.2 x trust - penalties. "
    "evidence: supported 0.7 or corroborated 1.0, minus 0.2 if the evidence is thin, plus 0.1 if strong, capped at 1. "
    "distance: exp(-km / radius). trust: source confidence from distinct web domains, an official website, "
    "page recency and named staff. penalties: possible merged record 0.15, minor location conflict 0.10."
)
SCORE_FORMULA_PROB = (
    "score = chance an independent source confirms the service x reach, where reach = exp(-km / radius). "
    "Recommendable when that chance is at or above the threshold set from measured precision."
)

LIMITS_V1: tuple[str, ...] = (
    "Drive times are estimates from straight-line distance, free-flow (no traffic, no waiting). Real trips take longer.",
    "Windows are planning thresholds, not clinical deadlines.",
    "The data is a sample of mostly private, web-visible facilities. Public hospitals are under-covered, so no result often means not in this data.",
    "Grades describe what web pages say, not what a facility can do today. Pages can be stale.",
    "Citations name the fact and its field, not the web page it came from: the data has no per-fact source.",
    "Nothing here is a probability.",
)

#: Keyword search under v1: no probability, weights or "validated" wording (D048 (6)).
LIMITS_SEARCH_V1: tuple[str, ...] = (
    "Distances are straight-line km from the point the place name resolved to; keyword search gives no drive time.",
    "A keyword match is not a recipe check: no rule checked that the service is offered.",
    "The data is a sample of mostly private, web-visible facilities. Public hospitals are under-covered, so no result often means not in this data.",
    "Matches describe what web pages say, not what a facility can do today. Pages can be stale.",
    "The keyword radius and the match threshold are declared design choices.",
    "Nothing here is a probability.",
)

LIMITS: tuple[str, ...] = (
    "Distances are straight-line km, not travel time, measured from the point the place name resolved to.",
    "The data is a sample of mostly private, web-visible facilities. Public hospitals are under-covered, so no result often means not in this data.",
    "Grades describe what web pages say, not what a facility can do today. Pages can be stale.",
    "Citations name the fact and its field, not the web page it came from: the data has no per-fact source.",
    "Weights and radii are stated defaults, not fitted or clinically validated.",
)
