"""Service lines as the user sees them: display names, radii, weights, and the
plain-language recipe text for the "How we decided" panel.

This is the one place for these values (``docs/07_data_contracts.md``). The grade
*rules* themselves live in ``vf.recipes``; this module only describes them in
plain words and holds the ranking's product defaults (D027, D028, D039).

Every number here is a stated default, not a fitted or clinically validated
value. Change one only by reading the scenario suite output
(``scripts/wp5_scenarios.py``), and log the change in ``docs/08_decisions_log.md``.
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

LIMITS: tuple[str, ...] = (
    "Distances are straight-line km, not travel time, measured from the point the place name resolved to.",
    "The data is a sample of mostly private, web-visible facilities. Public hospitals are under-covered, so no result often means not in this data.",
    "Grades describe what web pages say, not what a facility can do today. Pages can be stale.",
    "Citations name the fact and its field, not the web page it came from: the data has no per-fact source.",
    "Weights and radii are stated defaults, not fitted or clinically validated.",
)
