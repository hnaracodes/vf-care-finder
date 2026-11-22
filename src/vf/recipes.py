"""Capability recipes: the fact tag taxonomy, the regex baseline tagger, the
facility grade rules, and source confidence.

This is ``docs/06_capability_recipes.md`` as code, recipe version ``v0.1``. Two
layers, kept separate on purpose:

1. Fact level. ``tag_fact(text)`` gives a fact zero or more evidence tags. The
   regexes are a crude baseline, copied verbatim from the recipes doc. v0.1
   fixes three failure modes of v0 that changed facility grades: ``csection``
   needs a word boundary before "c section" and ignores VBAC wording, the
   ``dialysis_unit`` pattern no longer fires on "haemodialysis" alone, and a
   fact with a negation or absence cue ("not available", "does not provide",
   "not specified") gets no evidence tag. Other crudeness remains (bare
   "obstetric", "nicu" also fires ``icu``). ``tag_fact(text, version="v0")``
   reproduces v0, which the gold-set design and the v0 scores were built on.
   Error rates are measured against the gold set (WP3 part 2), so do not tune
   the patterns here without bumping ``RECIPE_VERSION`` and logging a decision.
2. Facility level. ``grade_line`` and ``grade_facility`` turn a facility's tags
   and specialties into a grade per service line with a deterministic rule. They
   take tags from any tagger (regex, teacher, student), so WP4 reuses them
   unchanged. ``facility_line_grades`` also picks up to five quoted facts as
   evidence for each grade.

``source_confidence`` is a separate 0 to 1 score for how much to trust the pages
a facility's facts came from. It never changes a grade.

Typical use::

    from vf import recipes
    recipes.tag_fact("24x7 ICU with ventilator support")   # ['h24', 'icu', 'ventilator']
    recipes.tag_fact("ICU not available")                  # [] (v0.1 negation cue)
    recipes.grade_facility(["csection", "blood", "ot"], ["gynecologyAndObstetrics"])
    # {'emoc': 3, 'dialysis': 0, 'icu': 0}
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable, Mapping, Optional, Sequence

from .clean import normalize_text

#: Bump when a pattern, rule, or the confidence formula changes, and log it in
#: docs/08_decisions_log.md. Written into every output row and sidecar.
RECIPE_VERSION = "v0.1"
#: Versions ``tag_fact`` can reproduce.
TAGGER_VERSIONS: tuple[str, ...] = ("v0", "v0.1")

# ---------------------------------------------------------------------------
# Tag taxonomy
# ---------------------------------------------------------------------------

#: Evidence tags, in the recipes-doc order. ``tag_fact`` returns tags in this order.
EVIDENCE_TAGS: tuple[str, ...] = (
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

#: One-line meaning of every tag (for the teacher prompt and for the gold set).
TAG_DEFINITIONS: dict[str, str] = {
    "ob_delivery": "Conducts deliveries or has a maternity or labour ward",
    "csection": "Performs caesarean sections",
    "blood": "Blood bank, blood storage, or transfusion",
    "anesthesia": "Anesthesia service or anesthetist",
    "ot": "Operating theatre",
    "h24": "Round-the-clock service",
    "nicu": "Neonatal intensive or special care",
    "dialysis": "Provides dialysis",
    "dialysis_unit": "Named dialysis unit, dialysis machines, or a nephrologist",
    "icu": "Intensive care unit",
    "ventilator": "Mechanical ventilation",
    "emergency": "Emergency or trauma care",
    "scheme": "Accepts a public insurance scheme",
    "reputation": "Ratings, reviews, years of experience",
    "location": "Address or place statement",
    "staff_name": "Names a person",
    "accessibility": "Parking, wheelchair access",
    "photo_artifact": "Looks like a photo caption, not an equipment list",
    "web_presence": "About the web page itself",
    "other_clinical": "A real clinical fact outside the three service lines",
}

#: v0.1 regexes, verbatim from docs/06_capability_recipes.md. They run on
#: ``normalize_text(text)``: lowercased, whitespace runs collapsed to one space.
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

_TAG_RX: dict[str, re.Pattern] = {t: re.compile(TAG_PATTERNS[t]) for t in EVIDENCE_TAGS}
_TAG_RX_V0: dict[str, re.Pattern] = {t: re.compile(TAG_PATTERNS_V0[t]) for t in EVIDENCE_TAGS}
_NEG_RX = re.compile(NEGATION_PATTERN)
_VBAC_RX = re.compile(VBAC_PATTERN)


def is_negated(text: Optional[str]) -> bool:
    """True when a fact has a v0.1 negation or absence cue (``NEGATION_PATTERN``)."""
    return bool(text) and bool(_NEG_RX.search(normalize_text(str(text))))


def tag_fact(text: Optional[str], version: str = RECIPE_VERSION) -> list[str]:
    """Evidence tags for one fact, in ``EVIDENCE_TAGS`` order.

    ``version`` is ``"v0.1"`` (the current recipe) or ``"v0"`` (the original
    patterns, for the gold-set design and comparison only). Accepts raw ``text``
    or ``text_norm``: the text is normalized first, so both give the same
    answer. None or blank gives [].
    """
    if version not in TAGGER_VERSIONS:
        raise ValueError(f"unknown tagger version {version!r}; expected one of {TAGGER_VERSIONS}")
    if text is None:
        return []
    s = normalize_text(str(text))
    if not s:
        return []
    if version == "v0":
        return [t for t in EVIDENCE_TAGS if _TAG_RX_V0[t].search(s)]
    if _NEG_RX.search(s):
        return []
    out = []
    for t in EVIDENCE_TAGS:
        target = _VBAC_RX.sub(" ", s) if t == "csection" else s
        if _TAG_RX[t].search(target):
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

SERVICE_LINES: tuple[str, ...] = ("emoc", "dialysis", "icu")

#: Grade number to label. Index with the grade.
GRADE_LABELS: tuple[str, ...] = ("none", "claimed", "supported", "corroborated")

#: Default number of quoted facts kept as evidence per (facility, service line).
MAX_EVIDENCE = 5


@dataclass(frozen=True)
class LineRule:
    """Grade rule for one service line.

    A requirement is a tuple of groups. It holds when every group holds, and a
    group holds when the facility has at least one of its tags. So
    ``({"csection"}, {"blood"}, {"anesthesia", "ot"})`` reads
    "csection and blood and (anesthesia or ot)".
    """

    line: str
    #: claimed if the facility lists any of these specialties...
    claim_specialties: frozenset
    #: ...or has any of these tags
    claim_tags: frozenset
    supported: tuple
    corroborated: tuple
    #: Recorded in ``tags_present`` but never needed for a grade (v0: emoc h24, nicu).
    bonus_tags: tuple
    #: Every tag relevant to this line, in evidence priority order.
    tags: tuple

    @property
    def rule_tags(self) -> tuple:
        """Tags that the grade rule uses (claim, supported, corroborated), in priority order."""
        used = set(self.claim_tags).union(*self.supported, *self.corroborated)
        return tuple(t for t in self.tags if t in used)


def _groups(*groups: Iterable[str]) -> tuple:
    return tuple(frozenset(g) for g in groups)


GRADE_RULES: dict[str, LineRule] = {
    # Emergency obstetric care. WHO comprehensive EmOC signal functions add
    # caesarean section and blood transfusion to the basic ones.
    "emoc": LineRule(
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
    ),
    "dialysis": LineRule(
        line="dialysis",
        claim_specialties=frozenset({"nephrology"}),
        claim_tags=frozenset(),
        supported=_groups({"dialysis"}),
        corroborated=_groups({"dialysis"}, {"dialysis_unit"}),
        bonus_tags=(),
        tags=("dialysis", "dialysis_unit"),
    ),
    "icu": LineRule(
        line="icu",
        claim_specialties=frozenset({"criticalCareMedicine"}),
        claim_tags=frozenset(),
        supported=_groups({"icu"}),
        corroborated=_groups({"icu"}, {"ventilator"}),
        bonus_tags=(),
        tags=("icu", "ventilator"),
    ),
}


def _holds(groups: tuple, tags: set) -> bool:
    return all(g & tags for g in groups)


def grade_line(line: str, tags: Iterable[str], specialties: Iterable[str] = ()) -> int:
    """Grade 0 to 3 for one service line from a facility's tags and specialties.

    ``tags`` is the union of the tags on all the facility's facts. The highest
    level whose rule holds wins, so a facility with a C-section mention is
    ``supported`` even without an obstetric specialty.
    """
    rule = GRADE_RULES[line]
    t = set(tags)
    if _holds(rule.corroborated, t):
        return 3
    if _holds(rule.supported, t):
        return 2
    if rule.claim_specialties.intersection(specialties) or rule.claim_tags & t:
        return 1
    return 0


def grade_facility(tags: Iterable[str], specialties: Iterable[str] = ()) -> dict[str, int]:
    """Grade on every service line: ``{"emoc": g, "dialysis": g, "icu": g}``."""
    t = set(tags)
    s = set(specialties)
    return {line: grade_line(line, t, s) for line in SERVICE_LINES}


def _required_groups(rule: LineRule, grade: int) -> tuple:
    if grade == 3:
        return rule.corroborated
    if grade == 2:
        return rule.supported
    if grade == 1 and rule.claim_tags:
        return (rule.claim_tags,)
    return ()


def select_evidence(
    facts: Sequence[tuple[str, Iterable[str]]],
    line: str,
    grade: int,
    k: int = MAX_EVIDENCE,
) -> list[str]:
    """Up to ``k`` fact texts that justify ``grade`` on ``line``.

    ``facts`` is the facility's (text, tags) pairs in a stable order. Only facts
    carrying a tag the grade rule uses are candidates (bonus tags such as h24 are
    not evidence). Repeated texts (same normalized form) count once.

    Order: first a small set of facts that together cover every requirement of
    the grade reached (for corroborated EmOC: a C-section fact, a blood fact, an
    anesthesia or OT fact), then the rest round-robin across the rule's tags in
    priority order, so the list stays varied. Grade 0 has no evidence.
    """
    if grade <= 0 or k <= 0:
        return []
    rule = GRADE_RULES[line]
    rule_tags = rule.rule_tags
    wanted = set(rule_tags)

    cands: list[tuple[str, frozenset]] = []
    seen: set[str] = set()
    for text, tags in facts:
        tset = frozenset(tags) & wanted
        if not tset:
            continue
        key = normalize_text(text)
        if key in seen:
            continue
        seen.add(key)
        cands.append((text, tset))

    chosen: list[int] = []
    # 1. cover the grade's requirements in rule order (the defining fact first).
    #    For each uncovered group, take the fact that covers it and the most
    #    other uncovered groups, earliest on ties.
    uncovered = list(_required_groups(rule, grade))
    while uncovered and len(chosen) < k:
        head = uncovered[0]
        best, best_n = None, 0
        for i, (_, tset) in enumerate(cands):
            if i in chosen or not (head & tset):
                continue
            n = sum(1 for g in uncovered if g & tset)
            if n > best_n:
                best, best_n = i, n
        if best is None:  # no fact covers this group; cannot happen for a grade the tags reached
            uncovered.pop(0)
            continue
        chosen.append(best)
        uncovered = [g for g in uncovered if not (g & cands[best][1])]

    # 2. fill round-robin across tags in priority order
    by_tag = {t: [i for i, (_, tset) in enumerate(cands) if t in tset] for t in rule_tags}
    while len(chosen) < k:
        added = False
        for t in rule_tags:
            nxt = next((i for i in by_tag[t] if i not in chosen), None)
            if nxt is not None:
                chosen.append(nxt)
                added = True
                if len(chosen) >= k:
                    break
        if not added:
            break
    return [cands[i][0] for i in chosen]


@dataclass(frozen=True)
class LineGrade:
    """One row of ``gold/capability_grades.parquet``, minus the facility-level fields."""

    service_line: str
    grade: int
    grade_label: str
    tags_present: list
    evidence: list


def facility_line_grades(
    facts: Sequence[tuple[str, Iterable[str]]],
    specialties: Iterable[str] = (),
    k: int = MAX_EVIDENCE,
) -> list[LineGrade]:
    """Grade one facility on every service line, with evidence.

    ``facts`` is the facility's (text, tags) pairs from any tagger, in a stable
    order (``facts.parquet`` order). ``tags_present`` lists the line's tags found
    on the facility, bonus tags included, in the rule's priority order.
    """
    facts = [(text, frozenset(tags)) for text, tags in facts]
    all_tags: set = set().union(*(t for _, t in facts)) if facts else set()
    spec = set(specialties)
    out = []
    for line in SERVICE_LINES:
        rule = GRADE_RULES[line]
        g = grade_line(line, all_tags, spec)
        out.append(LineGrade(
            service_line=line,
            grade=g,
            grade_label=GRADE_LABELS[g],
            tags_present=[t for t in rule.tags if t in all_tags],
            evidence=select_evidence(facts, line, g, k),
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
    if isinstance(official_website, str):
        has_site = bool(official_website.strip())
    else:  # bool, numpy bool, None, NaN
        has_site = not _missing(official_website) and bool(official_website)
    staff = False if _missing(staff_named) else bool(staff_named)
    return {
        "domains": min(n, DOMAINS_FULL) / DOMAINS_FULL,
        "official_website": 1.0 if has_site else 0.0,
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


def _check_rules(rules: Mapping[str, LineRule]) -> None:
    """Invariants every rule set must keep. Runs at import."""
    assert tuple(rules) == SERVICE_LINES
    for rule in rules.values():
        known = set(EVIDENCE_TAGS)
        assert set(rule.tags) <= known, rule.line
        assert set(rule.bonus_tags) <= set(rule.tags), rule.line
        assert set(rule.claim_tags).union(*rule.supported, *rule.corroborated) <= set(rule.tags), rule.line
        # corroborated must imply supported, so grades are monotone
        for g in rule.supported:
            assert any(c <= g for c in rule.corroborated), rule.line
    assert abs(sum(CONFIDENCE_WEIGHTS.values()) - 1.0) < 1e-9
    assert not math.isclose(RECENCY_FULL_YEARS, RECENCY_ZERO_YEARS)


_check_rules(GRADE_RULES)
