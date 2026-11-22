"""'Before you go, ask' (D044 item 3): the phone questions a health worker should
ask before the patient travels, derived from what one record does not show.

Pure templates over the record (D030): no LLM writes any of this text. Every
question has a ``why`` that cites a fact the record states or a gap in it, and
a ``source`` naming the check that raised it. A gap is a gap in the record,
never a statement about the facility: the wording is always "no fact in the
record mentions", never "does not have" (CLAUDE.md trap 12).

Two entry points for the app:

* ``questions_for(card, service_line, record=None)``: 2 to 4 questions for one
  ``vf.rank.Card`` (or its ``to_dict()``). ``record`` is the facility's
  ``RecordInfo`` (record-wide tags, round-the-clock and scheme mentions, how
  many other records share its phone). Without it the questions use only what
  the card quotes, and the ``why`` says so ("none of the facts quoted here").
* ``call_info(facility_id, phone_book=None, reveal=False)``: the record's phone
  for a Call button, masked unless ``reveal`` is set and demo mode is off.

Phone numbers never enter ``RecordInfo`` (it holds only a sharing count, and
any run of 8 or more digits in a stored quote is replaced by ``[phone number]``).
Every quote in a ``why`` is masked the same way. The one exception is opt-in:
``save_record_info(..., phone_book=book)`` adds the call numbers as columns
(``PHONE_COLUMNS``) so a deployed app, which has no raw file, can still offer
``call_info``; ``load_phone_book(bundle_dir)`` reads them back, and
``call_info`` masks them unless ``reveal`` is set and demo mode is off. Call numbers are kept
only when they are Indian (+91 with 10 national digits, or a 1800/1860 toll-free
number). ``python -m vf.questions`` prints the measurement on the real grades.
"""
from __future__ import annotations

import json
import math
import os
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from . import recipes as R
from . import services as S

QUESTIONS_VERSION = "questions_v2"
MIN_QUESTIONS = 2
MAX_QUESTIONS = 4
#: Longest quote shown in a ``why`` before it is cut at a word boundary with "...".
MAX_QUOTE_CHARS = 140
#: A facility position looser than this (km) gets the address question.
FUZZY_POSITION_KM = S.ORIGIN_FUZZY_KM
#: Env var: when "1", ``call_info`` masks numbers even if ``reveal=True``.
DEMO_ENV = "VF_DEMO_MODE"

#: Lines where the patient is usually travelling now, so the lead question says
#: "right now" and the record is checked for round-the-clock service.
EMERGENCY_LINES = frozenset({"emoc", "nicu", "icu", "cath_lab", "trauma", "burns", "neurosurgery"})

#: The lead question: always first, always about the service itself.
LEAD: dict[str, str] = {
    "emoc": "Can you do an emergency C-section right now?",
    "nicu": "Do you have a NICU bed free for a newborn right now?",
    "dialysis": "Can you give this patient a dialysis session, and when is the next free slot?",
    "icu": "Do you have an ICU bed with a ventilator free right now?",
    "cardiac_surgery": "Do your own surgeons do heart surgery (bypass or valve) here, and how soon can the patient be seen?",
    "cath_lab": "Can you do an emergency angioplasty for a heart attack right now?",
    "mri": "Can you do an MRI scan here, and how soon?",
    "ct": "Can you do a CT scan here today?",
    "trauma": "Can you take a seriously injured accident patient right now?",
    "burns": "Can you admit a patient with serious burns right now?",
    "neurosurgery": "Is a neurosurgeon available for a head injury right now?",
    "oncology": "Do you give chemotherapy or radiotherapy here, and how soon can a new patient start?",
}
LEAD_SEARCH = "Do you offer {what} here, and can you see the patient today?"
LEAD_SEARCH_GENERIC = "Do you offer the service this patient needs, and can you see them today?"

#: The service as a noun phrase, for the branch question.
SERVICE_NOUN: dict[str, str] = {
    "emoc": "the C-section service", "nicu": "the NICU", "dialysis": "dialysis", "icu": "the ICU",
    "cardiac_surgery": "heart surgery", "cath_lab": "the cath lab", "mri": "the MRI scanner",
    "ct": "the CT scanner", "trauma": "trauma care", "burns": "the burns unit",
    "neurosurgery": "neurosurgery", "oncology": "cancer treatment",
}

#: Requirement name (``vf.recipes`` ``*_names``) -> question. ``(line, name)`` keys override.
REQUIREMENT_ASK: dict[Any, str] = {
    "blood bank or transfusion": "Is blood available for transfusion if it is needed?",
    ("emoc", "blood bank or transfusion"): "Is blood available for transfusion tonight?",
    ("cardiac_surgery", "blood bank or transfusion"): "Is blood available for transfusion during the surgery?",
    "anaesthesia or operating theatre": "Is an anaesthetist on duty and an operating theatre free right now?",
    "newborn breathing support or ventilator": "Do you have CPAP or a ventilator for newborns, free right now?",
    "dialysis unit, machines or nephrologist": "How many dialysis machines do you have, and is a kidney doctor (nephrologist) on staff?",
    "intensive or cardiac care unit": "Is an intensive care bed free for the patient after the surgery?",
    "cardiac or intensive care unit": "Is a cardiac care (CCU) or ICU bed free right now?",
    "24-hour or emergency service": "Is the cath lab open round the clock, including at night?",
    "24-hour service": "Is the trauma team there round the clock, including at night?",
    "MRI named in a second fact or a second field": "Is the MRI machine working, and is it here rather than at a partner centre?",
    "CT named in a second fact or a second field": "Is the CT scanner working, and is it here rather than at a partner centre?",
    "operating theatre": "Are a surgeon and an operating theatre free right now?",
    "blood bank or intensive care unit": "Do you have blood for transfusion and an ICU bed free?",
    "intensive care unit": "Is an ICU bed free for this patient?",
    "CT scan": "Do you have a working CT scanner here for head scans?",
    "chemotherapy": "Do you give chemotherapy here?",
    "radiotherapy": "Do you give radiotherapy here, or would the patient be sent elsewhere for it?",
}
#: Requirement name -> why, where "No fact in the record mentions <name>." reads badly.
REQUIREMENT_WHY: dict[str, str] = {
    "MRI named in a second fact or a second field": "Only one fact in the record names MRI.",
    "CT named in a second fact or a second field": "Only one fact in the record names a CT scan.",
}
#: Requirement name -> the phrase used after "mentions", with its article.
REQUIREMENT_PHRASE: dict[str, str] = {
    "caesarean section": "a caesarean section",
    "blood bank or transfusion": "a blood bank or blood transfusion",
    "anaesthesia or operating theatre": "anaesthesia or an operating theatre",
    "newborn intensive care unit": "a newborn intensive care unit",
    "newborn breathing support or ventilator": "newborn breathing support or a ventilator",
    "dialysis unit, machines or nephrologist": "a dialysis unit, dialysis machines or a nephrologist",
    "intensive care unit": "an intensive care unit",
    "ventilator": "a ventilator",
    "intensive or cardiac care unit": "an intensive or cardiac care unit",
    "cath lab, angiography or angioplasty": "a cath lab, angiography or angioplasty",
    "cardiac or intensive care unit": "a cardiac or intensive care unit",
    "24-hour or emergency service": "round-the-clock or emergency service",
    "MRI scan": "an MRI scan",
    "CT scan": "a CT scan",
    "operating theatre": "an operating theatre",
    "blood bank or intensive care unit": "a blood bank or an intensive care unit",
    "24-hour service": "round-the-clock (24-hour) service",
}
#: Requirement names that are about round-the-clock service (share the ``h24`` key).
H24_REQUIREMENTS = frozenset({"24-hour or emergency service", "24-hour service"})
#: Requirements the lead question already asks, besides each line's own service names.
LEAD_COVERS_EXTRA: dict[str, frozenset] = {"icu": frozenset({"ventilator"})}

#: Public schemes, wider than the recipe's ``scheme`` regex (which misses "PM-JAY").
SCHEME_PATTERN = r"ayushman|\bpm[- ]?jay\b|\bab[- ]?pmjay\b|\bcghs\b|jan arogya"
_SCHEME_RX = re.compile(SCHEME_PATTERN)
SCHEME_NAMES = "Ayushman Bharat, PM-JAY or CGHS"

#: Absence cues the recipe's ``NEGATION_PATTERN`` misses ("Lacks trauma care facilities", "Not open 24 hours").
_EXTRA_NEG_RX = re.compile(r"\black(s|ing)?\b|\bnot (open|working|running|functioning)\b")
#: A digit run with phone separators; masked when it holds 8 or more digits.
_NUMBER_RX = re.compile(r"\+?\d[\d \-().]{6,}\d")
_DATE_RX = re.compile(r"^\d{1,2}[-./]\d{1,2}[-./]\d{2,4}$|^(19|20)\d\d ?- ?(19|20)\d\d$"
                      r"|^(19|20)\d\d[-./](0?[1-9]|1[0-2])[-./](0?[1-9]|[12]\d|3[01])$")  # ISO dates: "retrieved 2026-11-21"
PHONE_MASK = "[phone number]"

#: Selection priority (lower first). The output keeps this order, lead first.
PRIORITY: dict[str, int] = {
    "service": 0,
    "flag:implausible_claim": 5,
    "location": 10,
    "missing_for_next": 20,
    "flag:possible_merge": 25,
    "h24": 30,
    "missing_for_next_more": 35,
    "shared_phone": 40,
    "cost": 45,
    "evidence_strength": 50,
    "default": 90,
}


# ---------------------------------------------------------------------------
# Record-wide facts the card does not carry
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RecordInfo:
    """What the whole record shows, beyond the facts quoted on one card.

    ``tags``: every evidence tag on any of the record's facts (``gold/fact_tags``).
    ``h24_cotags``: the other tags on facts that also carry ``h24``, so "24x7
    pharmacy" does not count as a round-the-clock ICU. ``h24_quote``: one
    round-the-clock fact, preferring a facility-wide one ("Open 24x7", no other
    tags) over one about another service. ``h24_quote_cotags``: the other tags
    on that quoted fact (empty for a facility-wide fact, None if unknown).
    ``scheme_quote``: the first scheme fact. Quotes are verbatim except that
    digit runs of 8 or more become ``[phone number]``. ``phone_shared_with``:
    how many other records list the same call number, leaving out records the
    duplicate check pairs with this one (0 if unique or no phone).
    """

    facility_id: str
    tags: frozenset = frozenset()
    h24_cotags: frozenset = frozenset()
    h24_quote: Optional[str] = None
    scheme_quote: Optional[str] = None
    has_phone: bool = False
    phone_shared_with: int = 0
    h24_quote_cotags: Optional[frozenset] = None

    def to_row(self) -> dict:
        d = asdict(self)
        d["tags"] = sorted(self.tags)
        d["h24_cotags"] = sorted(self.h24_cotags)
        d["h24_quote_cotags"] = None if self.h24_quote_cotags is None else sorted(self.h24_quote_cotags)
        d["h24_quote"] = mask_numbers(self.h24_quote)
        d["scheme_quote"] = mask_numbers(self.scheme_quote)
        return d


def _as_record(record) -> Optional[RecordInfo]:
    if record is None or isinstance(record, RecordInfo):
        return record
    if isinstance(record, Mapping):
        qc = record.get("h24_quote_cotags")
        qc_missing = qc is None or (isinstance(qc, float) and math.isnan(qc))
        return RecordInfo(
            facility_id=str(record.get("facility_id", "")),
            tags=frozenset(_as_list(record.get("tags"))),
            h24_cotags=frozenset(_as_list(record.get("h24_cotags"))),
            h24_quote=mask_numbers(_str_or_none(record.get("h24_quote"))),
            scheme_quote=mask_numbers(_str_or_none(record.get("scheme_quote"))),
            has_phone=bool(record.get("has_phone", False)),
            phone_shared_with=int(record.get("phone_shared_with") or 0),
            h24_quote_cotags=None if qc_missing else frozenset(str(x) for x in _as_list(qc)),
        )
    raise TypeError(f"record must be a RecordInfo or a mapping, not {type(record).__name__}")


def mask_numbers(text: Optional[str]) -> Optional[str]:
    """Replace every run of 8 or more digits (spaces, dashes, dots, brackets and
    a leading + allowed inside) with ``[phone number]``. Dates and year ranges
    are left alone. None stays None."""
    if text is None:
        return None

    def sub(m: re.Match) -> str:
        s = m.group(0)
        if sum(ch.isdigit() for ch in s) < 8 or _DATE_RX.match(s.strip()):
            return s
        return PHONE_MASK

    return _NUMBER_RX.sub(sub, str(text))


def is_negative(text: Optional[str]) -> bool:
    """``vf.recipes.is_negated`` plus absence cues it misses ("Lacks ...")."""
    return R.is_negated(text) or bool(text and _EXTRA_NEG_RX.search(R.normalize_text(str(text))))


def _lower_first(reason: str) -> str:
    """Lower-case the first letter of a reason that follows a colon ("The" ->
    "the"), leaving acronyms ("PIN code") alone."""
    if len(reason) >= 2 and reason[0].isupper() and reason[1].islower():
        return reason[0].lower() + reason[1:]
    return reason


def _as_list(v) -> list:
    if v is None:
        return []
    if isinstance(v, str):
        return [v]
    try:
        if isinstance(v, float) and math.isnan(v):
            return []
    except TypeError:
        pass
    return [x for x in list(v) if x is not None]


def _str_or_none(v) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    s = str(v).strip()
    return s if s and s.lower() not in ("null", "nan", "none") else None


# ---------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------

def _get(card, name, default=None):
    if isinstance(card, Mapping):
        return card.get(name, default)
    return getattr(card, name, default)


def _card_dict(card) -> Mapping:
    if isinstance(card, Mapping):
        return card
    if hasattr(card, "to_dict"):
        return card.to_dict()
    raise TypeError("card must be a vf.rank.Card or its to_dict()")


_MISSING_PREFIX = "no fact in the record mentions "


def _missing_requirements(card: Mapping) -> list[str]:
    """Plain requirement names: ``missing_for_next`` if the card carries it raw,
    else the card's ``missing`` lines with the ranker's prefix removed."""
    raw = card.get("missing_for_next")
    if raw is not None:
        return [str(x) for x in _as_list(raw)]
    out = []
    for m in _as_list(card.get("missing")):
        s = str(m)
        out.append(s[len(_MISSING_PREFIX):] if s.lower().startswith(_MISSING_PREFIX) else s)
    return out


def _quote(text: Optional[str], limit: int = MAX_QUOTE_CHARS) -> str:
    t = mask_numbers(" ".join(str(text or "").split()))
    if len(t) <= limit:
        return t
    cut = t[:limit].rsplit(" ", 1)[0].rstrip(",;:")
    return cut + "..."


def _lead_covers(line: str) -> frozenset:
    rule = R.GRADE_RULES.get(line)
    if rule is None:
        return frozenset()
    names = set(rule.supported_names) | set(rule.corroborated_names[:1]) | set(LEAD_COVERS_EXTRA.get(line, ()))
    return frozenset(names)


def _is_claim_requirement(name: str) -> bool:
    return name.startswith("a related specialty")


def _line_core_tags(line: str) -> frozenset:
    """Tags that make an h24 fact about this service (the service's own tags plus emergency)."""
    rule = R.GRADE_RULES.get(line)
    if rule is None:
        return frozenset({"emergency"})
    core = set(rule.claim_tags).union(*rule.supported)
    if rule.corroborated:
        core |= set(rule.corroborated[0])
    return frozenset(core | {"emergency"})


def _plain_missing_why(name: str) -> str:
    return REQUIREMENT_WHY.get(name) or f"No fact in the record mentions {REQUIREMENT_PHRASE.get(name, name)}."


def _requirement_question(line: str, name: str) -> str:
    return REQUIREMENT_ASK.get((line, name)) or REQUIREMENT_ASK.get(name) or f"Do you have {name}?"


def _quoted_tags(card: Mapping) -> set:
    tags: set = set()
    for ev in _as_list(card.get("evidence")):
        q = ev.get("quote") if isinstance(ev, Mapping) else None
        if q:
            tags |= set(R.tag_fact(q))
    return tags


def _quoted_text_has_scheme(card: Mapping) -> Optional[str]:
    for ev in _as_list(card.get("evidence")):
        q = ev.get("quote") if isinstance(ev, Mapping) else None
        if q and not is_negative(q) and _SCHEME_RX.search(R.normalize_text(q)):
            return q
    return None


def _thin_reason(card: Mapping) -> str:
    src = card.get("sources") or {}
    n_dom = src.get("n_domains")
    site = _str_or_none(src.get("official_website"))
    if n_dom is not None and int(n_dom) <= 1 and not site:
        return (f"The record's support is thin: it comes from {int(n_dom)} web source"
                f"{'' if int(n_dom) == 1 else 's'} and has no official website.")
    return "The record's support is thin: a single fact in the record names the service."


def _lead(card: Mapping, line: str, missing: list[str], service_text: Optional[str]) -> dict:
    keyword = line not in LEAD or card.get("evidence_strength") == "keyword"
    evidence = [e for e in _as_list(card.get("evidence")) if isinstance(e, Mapping) and e.get("quote")]
    if keyword:
        q = LEAD_SEARCH.format(what=service_text.strip()) if service_text and service_text.strip() else LEAD_SEARCH_GENERIC
        if evidence:
            why = (f'This is a keyword match on "{_quote(evidence[0]["quote"])}"; '
                   "no rule checked that the service is actually offered.")
        else:
            why = "This is a keyword match; no rule checked that the service is actually offered."
        return {"question": q, "why": why, "source": "service"}

    covers = _lead_covers(line)
    grade = card.get("grade")
    grade = int(grade) if grade is not None else 0
    # A fact that says the service is absent is never framed as the record's claim.
    evidence = [e for e in evidence if not is_negative(e["quote"])]
    first = next((e for e in evidence if e.get("requirement") in covers), evidence[0] if evidence else None)
    absent = [m for m in missing if m in covers or _is_claim_requirement(m)]
    if grade >= 2 and first is not None:
        fld = f" ({first['field']})" if first.get("field") else ""
        why = f'The record says "{_quote(first["quote"])}"{fld}, but web pages can be out of date.'
    elif grade == 1 and first is not None:
        why = f'The record only claims it: "{_quote(first["quote"])}".'
    elif grade == 1:
        why = "The record only lists a related specialty."
    elif grade >= 2:
        why = "The record names the service, but web pages can be out of date."
    else:
        why = "No fact in the record mentions this service."
    for m in absent:
        if not _is_claim_requirement(m):
            why += " " + _plain_missing_why(m)
    if grade >= 2 and card.get("evidence_strength") == "thin":
        why += " " + _thin_reason(card)
    return {"question": LEAD[line], "why": why, "source": "service"}


def questions_for(card, service_line: str, record=None, *, service_text: Optional[str] = None,
                  max_questions: int = MAX_QUESTIONS) -> list[dict]:
    """2 to ``max_questions`` (default 4) phone questions for one card.

    ``card``: a ``vf.rank.Card`` or its ``to_dict()``. Reads ``grade``,
    ``evidence`` (quotes), ``missing`` (or a raw ``missing_for_next``),
    ``evidence_strength``, ``flags``, ``location_precision_km``, ``operator``,
    ``sources``. ``service_line``: one of the 12 ids, or ``"search"`` for a
    keyword card (pass the user's words as ``service_text``). ``record``: the
    facility's ``RecordInfo`` or a dict with its fields; ``None`` limits the
    record-wide checks to the quoted facts and says so.

    Returns ``[{question, why, source}]``, the service question first, then by
    ``PRIORITY``. ``source`` is one of ``service``, ``missing_for_next``,
    ``h24``, ``location``, ``flag:possible_merge``, ``flag:implausible_claim``,
    ``shared_phone``, ``cost``, ``evidence_strength``, ``default``.
    """
    c = _card_dict(card)
    rec = _as_record(record)
    line = service_line
    graded = line in LEAD and c.get("evidence_strength") != "keyword"
    missing = _missing_requirements(c) if graded else []
    cands: list[tuple[int, str, dict]] = []  # (priority, key, question)

    def add(prio: str, key: str, question: str, why: str, source: Optional[str] = None):
        cands.append((PRIORITY[prio], key, {"question": question, "why": why, "source": source or prio}))

    lead = _lead(c, line, missing, service_text)
    cands.append((PRIORITY["service"], "service", lead))

    flags = [f for f in _as_list(c.get("flags")) if isinstance(f, Mapping)]
    by_flag: dict[str, list] = {}
    for f in flags:
        by_flag.setdefault(str(f.get("flag")), []).append(f)

    # trust flags
    for f in by_flag.get("implausible_claim", [])[:1]:
        if f.get("service_line") in (None, line) or not graded:
            add("flag:implausible_claim", "implausible",
                "Do you do this yourselves, or do you refer patients to another hospital for it?",
                mask_numbers(f.get("reason")) or "A trust check found the claim unusual for this kind of facility.")
    loc = by_flag.get("location_conflict", [])
    prec = c.get("location_precision_km")
    if loc:
        add("location", "location", "Can you confirm your full address and a landmark near you?",
            "The record's location is uncertain: "
            + _lower_first(mask_numbers(loc[0].get("reason")) or "its map point and PIN code disagree."))
    elif prec is not None and float(prec) > FUZZY_POSITION_KM:
        add("location", "location", "Can you confirm your full address and a landmark near you?",
            f"The record's position is known only to within about {float(prec):.0f} km.")
    for f in by_flag.get("possible_merge", [])[:1]:
        noun = SERVICE_NOUN.get(line, "this service")
        add("flag:possible_merge", "merge",
            f"Is {noun} at this address, and not at another branch or a hospital with a similar name?",
            "The record may combine more than one facility: "
            + _lower_first(mask_numbers(f.get("reason")) or "a trust check flagged it."))

    # recipe requirements the record does not show
    covers = _lead_covers(line)
    reqs = [m for m in missing if m not in covers and not _is_claim_requirement(m)]
    grade = int(c.get("grade") or 0)
    rule = R.GRADE_RULES.get(line)
    if graded and rule is not None and grade < 2 and rec is not None:
        # The card lists only the next grade's gaps; the record's tags show the rest.
        for grp, name in zip(rule.corroborated, rule.corroborated_names):
            if name not in covers and name not in reqs and not (set(grp) & rec.tags):
                reqs.append(name)
    for i, name in enumerate(reqs):
        key = "h24" if name in H24_REQUIREMENTS else f"req:{name}"
        add("missing_for_next" if i == 0 else "missing_for_next_more", key,
            _requirement_question(line, name), _plain_missing_why(name), source="missing_for_next")

    # round-the-clock service, emergency lines only
    if graded and line in EMERGENCY_LINES and not any(k == "h24" for _, k, _ in cands):
        if rec is not None:
            if not (rec.h24_cotags & _line_core_tags(line)):
                noun = SERVICE_NOUN.get(line, "this service")
                if "h24" in rec.tags and rec.h24_quote and rec.h24_quote_cotags:
                    # the quoted fact is tagged with another service, and no h24 fact is facility-wide
                    why = ("The record mentions round-the-clock service only for something else: "
                           f'"{_quote(rec.h24_quote)}".')
                elif "h24" in rec.tags and rec.h24_quote:
                    # facility-wide ("Open 24x7"), or a saved table that does not say
                    why = (f'The record says "{_quote(rec.h24_quote)}", but no fact in the record says '
                           f"{noun} is available round the clock.")
                elif "h24" in rec.tags:
                    why = ("The record mentions round-the-clock service, but no fact in the record says "
                           f"{noun} is available round the clock.")
                else:
                    why = "No fact in the record mentions round-the-clock (24-hour) service."
                add("h24", "h24", "Is this available round the clock, including at night and on Sundays?", why)
        elif "h24" not in _quoted_tags(c):
            add("h24", "h24", "Is this available round the clock, including at night and on Sundays?",
                "None of the facts quoted here mention round-the-clock (24-hour) service.")

    # shared phone number
    if rec is not None and rec.phone_shared_with > 0:
        n = rec.phone_shared_with
        add("shared_phone", "shared_phone", "Which hospital and which branch am I speaking to?",
            f"This record's phone number is also listed on {n} other facility record{'' if n == 1 else 's'}, "
            "so it may be a booking line for several places.")

    # cost
    public = str(c.get("operator") or "").lower() in ("public", "government")
    scheme_q = rec.scheme_quote if rec is not None else _quoted_text_has_scheme(c)
    has_scheme = scheme_q is not None or (rec is not None and "scheme" in rec.tags)
    if has_scheme:
        if scheme_q:
            why = f'The record mentions a scheme ("{_quote(scheme_q)}"), but not which treatments it covers.'
        else:
            why = "The record mentions a public insurance scheme, but not which treatments it covers."
        names_pmjay = bool(scheme_q and re.search(r"ayushman|pm[- ]?jay|jan arogya", R.normalize_text(scheme_q)))
        ask = ("Will Ayushman Bharat (PM-JAY) cover this treatment for this patient?" if names_pmjay or not scheme_q
               else "Will the scheme the record names cover this treatment, and do you accept Ayushman Bharat (PM-JAY)?")
        add("cost", "cost", ask, why)
    else:
        scope = "No fact in the record" if rec is not None else "None of the facts quoted here"
        verb = "mentions" if rec is not None else "mention"
        why = f"{scope} {verb} {SCHEME_NAMES}."
        if public:
            add("cost", "cost", "Will the patient have to pay for anything, such as medicines, blood or tests?",
                f"The record lists it as public. {why}")
        else:
            add("cost", "cost", "Do you accept Ayushman Bharat (PM-JAY), and what will the patient have to pay?", why)

    # thin support
    if graded and grade >= 2 and c.get("evidence_strength") == "thin":
        add("evidence_strength", "thin",
            "Is this done every day by your own staff, or only on some days by a visiting doctor?",
            _thin_reason(c))

    add("default", "default", "Who should the patient ask for on arrival, and is that person on duty today?",
        "The record shows what web pages claimed, not who is on duty today.")

    # dedupe by key and by text, keep the best priority, cap
    seen_keys, seen_text, picked = set(), set(), []
    for prio, key, q in sorted(cands, key=lambda t: t[0]):
        if key in seen_keys or q["question"] in seen_text:
            continue
        seen_keys.add(key)
        seen_text.add(q["question"])
        picked.append((prio, key, q))
    cap = max(MIN_QUESTIONS, int(max_questions))
    out = [q for _, k, q in picked if k != "default"][:cap]
    if len(out) < MIN_QUESTIONS:
        out += [q for _, k, q in picked if k == "default"][: MIN_QUESTIONS - len(out)]
    return out


# ---------------------------------------------------------------------------
# Phones
# ---------------------------------------------------------------------------

_TOLL_FREE = ("1800", "1860")


def normalize_phone(raw) -> Optional[str]:
    """``+91`` and the national digits, or None.

    Kept: +91 with 10 national digits (a trunk 0 after +91 is dropped), or +91
    with a 1800/1860 toll-free number of 10 or 11 digits. Numbers written
    without a country code get +91 when they are 10 digits, 0 + 10 digits, or a
    1800/1860 number. A leading + (or 00) means the digits carry a country
    code, which must be 91: anything else (+1..., +880..., +961...), and any
    malformed value, returns None so no ``tel:`` link points abroad."""
    s = _str_or_none(raw)
    if s is None:
        return None
    s = s.strip()
    d = re.sub(r"\D", "", s)
    has_cc = s.startswith("+")
    if not has_cc and d.startswith("00") and len(d) > 11:
        d, has_cc = d[2:], True
    if not has_cc:
        if len(d) == 11 and d.startswith("0"):
            d = "91" + d[1:]
        elif len(d) == 10 or (len(d) == 11 and d.startswith(_TOLL_FREE)):
            d = "91" + d
        elif not (len(d) == 12 and d.startswith("91")):
            return None
    if not d.startswith("91"):
        return None
    nat = d[2:]
    if len(nat) == 11 and nat.startswith("0"):
        nat = nat[1:]
    if len(nat) == 10 or (len(nat) == 11 and nat.startswith(_TOLL_FREE)):
        return "+91" + nat
    return None


def mask_phone(phone: Optional[str], keep_last: int = 2) -> Optional[str]:
    """``+91 xxxxxxxx00``: country code and the last ``keep_last`` digits kept."""
    if phone is None:
        return None
    d = re.sub(r"\D", "", str(phone))
    if not d:
        return None
    keep_last = max(0, min(int(keep_last), 4))
    if str(phone).strip().startswith("+") and d.startswith("91") and len(d) > 4:
        body = d[2:]
        tail = body[-keep_last:] if keep_last else ""
        return "+91 " + "x" * (len(body) - len(tail)) + tail
    tail = d[-keep_last:] if keep_last else ""
    return "x" * (len(d) - len(tail)) + tail


@dataclass
class PhoneBook:
    """facility_id -> call number, its field, and how many other records share it.

    ``shared_with`` leaves out records the duplicate check pairs with this one;
    ``duplicate_shared`` counts those. ``unusable``: ids whose record lists a
    phone value but none that is a usable Indian number."""

    phone: dict = field(default_factory=dict)
    source: dict = field(default_factory=dict)
    shared_with: dict = field(default_factory=dict)
    duplicate_shared: dict = field(default_factory=dict)
    unusable: set = field(default_factory=set)

    def __repr__(self) -> str:  # never print numbers
        return f"PhoneBook({len(self.phone)} numbers)"

    def __len__(self) -> int:
        return len(self.phone)


def _duplicate_partners(duplicates) -> dict:
    """facility_id -> ids the duplicate check pairs with it. Accepts the
    ``gold/facility_duplicates.parquet`` frame (``facility_id_a``,
    ``facility_id_b``) or an iterable of ``(a, b)`` pairs."""
    partners: dict = {}
    if duplicates is None:
        return partners
    if hasattr(duplicates, "columns"):
        pairs = zip(duplicates["facility_id_a"], duplicates["facility_id_b"])
    else:
        pairs = duplicates
    for a, b in pairs:
        a, b = str(a), str(b)
        partners.setdefault(a, set()).add(b)
        partners.setdefault(b, set()).add(a)
    return partners


def build_phone_book(raw, duplicates=None) -> PhoneBook:
    """From raw facility rows (``vf.io.read_facilities()``: ``unique_id``,
    ``officialPhone``, ``phone_numbers``). The call number is ``officialPhone``,
    else the first usable entry of ``phone_numbers`` (see ``normalize_phone``).
    Repeated ``unique_id`` rows count once. Sharing is counted over call numbers
    of distinct facility ids; ``duplicates`` (the WP duplicate pairs) are left
    out of ``shared_with`` and counted in ``duplicate_shared`` instead, so the
    same facility listed twice is not called a shared booking line."""
    from . import io

    book = PhoneBook()
    cols = set(raw.columns)
    seen: set = set()
    for r in raw.itertuples(index=False):
        fid = _str_or_none(getattr(r, "unique_id", None) if "unique_id" in cols else getattr(r, "facility_id", None))
        if fid is None or fid in seen:
            continue
        seen.add(fid)
        official = _str_or_none(getattr(r, "officialPhone", None)) if "officialPhone" in cols else None
        num, src = normalize_phone(official), "officialPhone"
        lst: list = []
        if num is None and "phone_numbers" in cols:
            src = "phone_numbers"
            try:
                lst = [x for x in io.parse_list(r.phone_numbers) if _str_or_none(x)]
            except Exception:
                lst = []
            num = next((n for n in (normalize_phone(x) for x in lst) if n), None)
        if num is not None:
            book.phone[fid] = num
            book.source[fid] = src
        elif official or lst:
            book.unusable.add(fid)
    partners = _duplicate_partners(duplicates)
    by_num: dict = {}
    for fid, n in book.phone.items():
        by_num.setdefault(n, set()).add(fid)
    for fid, n in book.phone.items():
        others = by_num[n] - {fid}
        dup = others & partners.get(fid, set())
        book.shared_with[fid] = len(others - dup)
        book.duplicate_shared[fid] = len(dup)
    return book


_DEFAULT_BOOK: Optional[PhoneBook] = None

#: Columns ``save_record_info(..., phone_book=)`` adds: the call number (``+91...``, in clear, only
#: ever served through ``call_info``, which masks it), its field, records the duplicate check pairs
#: with this one on the same number, and whether the record lists only unusable numbers.
PHONE_COLUMNS = ("call_phone", "call_phone_source", "phone_duplicate_shared", "phone_unusable")


def phone_columns(infos: Mapping[str, "RecordInfo"], book: PhoneBook) -> dict:
    """The ``PHONE_COLUMNS`` values for ``infos``' ids, in ``infos`` order."""
    ids = list(infos)
    return {"call_phone": [book.phone.get(f) for f in ids],
            "call_phone_source": [book.source.get(f) for f in ids],
            "phone_duplicate_shared": [int(book.duplicate_shared.get(f, 0)) for f in ids],
            "phone_unusable": [f in book.unusable for f in ids]}


def phone_book_from_frame(df) -> PhoneBook:
    """A ``PhoneBook`` from a record-info frame written with ``phone_book=`` (``PHONE_COLUMNS`` plus
    ``phone_shared_with``)."""
    missing = [c for c in PHONE_COLUMNS if c not in df.columns]
    if missing:
        raise KeyError(f"record info has no phone columns {missing} (written without phone_book=)")
    book = PhoneBook()

    def col(name):  # nullable strings as None, never "<NA>"
        v = df[name].astype(object)
        return v.where(v.notna(), None)

    for fid, num, src, shared, dup, bad in zip(df["facility_id"], col("call_phone"), col("call_phone_source"),
                                               df["phone_shared_with"], df["phone_duplicate_shared"],
                                               df["phone_unusable"]):
        fid = str(fid)
        num = _str_or_none(num)
        if num is not None:
            book.phone[fid] = num
            book.source[fid] = _str_or_none(src)
            book.shared_with[fid] = int(shared or 0)
            book.duplicate_shared[fid] = int(dup or 0)
        elif bool(bad):
            book.unusable.add(fid)
    return book


def load_phone_book(source=None) -> PhoneBook:
    """The phone book. ``source``: a bundle folder (or its ``record_info.parquet``) written with
    phone columns; read from there when it has them. Otherwise, or with no ``source``, the raw
    facilities file (cached), with the duplicate pairs from ``gold/facility_duplicates.parquet``
    when it exists. Raw data stays local: numbers go only into the app bundle's record-info table,
    never into the repo or a published file."""
    if source is not None:
        import pandas as pd
        sp = Path(source)
        sp = sp / "record_info.parquet" if sp.is_dir() else sp
        if sp.exists():
            import pyarrow.parquet as pq
            if set(PHONE_COLUMNS) <= set(pq.ParquetFile(sp).schema_arrow.names):
                return phone_book_from_frame(pd.read_parquet(sp, columns=["facility_id", "phone_shared_with",
                                                                          *PHONE_COLUMNS]))
    global _DEFAULT_BOOK
    if _DEFAULT_BOOK is None:
        import pandas as pd
        from . import io, paths
        dp = paths.gold_dir() / "facility_duplicates.parquet"
        dups = pd.read_parquet(dp, columns=["facility_id_a", "facility_id_b"]) if dp.exists() else None
        _DEFAULT_BOOK = build_phone_book(io.read_facilities(), dups)
    return _DEFAULT_BOOK


def demo_mode() -> bool:
    return os.environ.get(DEMO_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def call_info(facility_id: str, phone_book: Optional[PhoneBook] = None, *, reveal: bool = False) -> dict:
    """The record's phone for a Call button.

    Masked (``+91 xxxxxxxx00``) unless ``reveal=True`` and demo mode
    (``VF_DEMO_MODE=1``) is off; ``tel`` (a ``tel:`` URI) is set only when
    revealed. ``phone_book=None`` loads the raw file once (``load_phone_book``).
    Returns ``{facility_id, has_phone, phone, masked, tel, source, shared_with, note}``.
    """
    book = phone_book if phone_book is not None else load_phone_book()
    num = book.phone.get(facility_id)
    if num is None:
        if facility_id in book.unusable:
            note = ("The record's phone number does not look like a valid Indian number, so no Call link is "
                    "offered. Check the number another way.")
        else:
            note = "The record lists no phone number. No phone in the record does not mean the facility has none."
        return {"facility_id": facility_id, "has_phone": False, "phone": None, "masked": True, "tel": None,
                "source": None, "shared_with": 0, "note": note}
    show = bool(reveal) and not demo_mode()
    shared = int(book.shared_with.get(facility_id, 0))
    dup = int(book.duplicate_shared.get(facility_id, 0))
    note = f"From this facility's record ({book.source.get(facility_id)}). Numbers on web pages can be out of date."
    if shared:
        note += f" The same number is listed on {shared} other record{'' if shared == 1 else 's'}."
    if dup:
        note += (f" It is also on {dup} record{'' if dup == 1 else 's'} that may be this same facility "
                 "listed twice.")
    return {"facility_id": facility_id, "has_phone": True, "phone": num if show else mask_phone(num),
            "masked": not show, "tel": f"tel:{num}" if show else None,
            "source": book.source.get(facility_id), "shared_with": shared, "note": note}


# ---------------------------------------------------------------------------
# Record info from the pipeline tables
# ---------------------------------------------------------------------------

def build_record_info(fact_tags, facts=None, phone_book: Optional[PhoneBook] = None,
                      facility_ids: Optional[Iterable[str]] = None) -> dict[str, RecordInfo]:
    """``fact_tags``: ``gold/fact_tags.parquet`` (fact_id, facility_id, tags).
    ``facts``: ``silver/facts.parquet`` (fact_id, text) for the h24 and scheme
    quotes and the wider scheme pattern; None skips quotes. ``phone_book``:
    sharing counts only, no numbers are kept."""
    import pandas as pd

    t = fact_tags[["fact_id", "facility_id", "tags"]].copy()
    t["tags"] = t["tags"].map(lambda v: tuple(str(x) for x in _as_list(v)))
    if facts is not None:
        t = t.merge(facts[["fact_id", "text"]], on="fact_id", how="left")
    else:
        t["text"] = None
    ids = list(dict.fromkeys(facility_ids)) if facility_ids is not None else list(dict.fromkeys(t["facility_id"]))
    tags: dict = {}
    h24_co: dict = {}
    h24_q: dict = {}  # fid -> (quote, its other tags); a facility-wide fact wins
    scheme_q: dict = {}
    for fid, tg, txt in zip(t["facility_id"], t["tags"], t["text"]):
        if tg:
            tags.setdefault(fid, set()).update(tg)
            if "h24" in tg:
                other = frozenset(x for x in tg if x != "h24")
                h24_co.setdefault(fid, set()).update(other)
                if isinstance(txt, str) and not is_negative(txt) and (
                        fid not in h24_q or (h24_q[fid][1] and not other)):
                    h24_q[fid] = (mask_numbers(txt), other)
        if fid not in scheme_q and isinstance(txt, str) and not is_negative(txt) and (
                "scheme" in tg or _SCHEME_RX.search(R.normalize_text(txt))):
            scheme_q[fid] = mask_numbers(txt)
    out = {}
    for fid in ids:
        tg = set(tags.get(fid, ()))
        if fid in scheme_q:
            tg.add("scheme")
        hq = h24_q.get(fid)
        out[fid] = RecordInfo(
            facility_id=fid, tags=frozenset(tg), h24_cotags=frozenset(h24_co.get(fid, ())),
            h24_quote=hq[0] if hq else None, scheme_quote=scheme_q.get(fid),
            has_phone=bool(phone_book and fid in phone_book.phone),
            phone_shared_with=int(phone_book.shared_with.get(fid, 0)) if phone_book else 0,
            h24_quote_cotags=hq[1] if hq else None,
        )
    return out


def load_record_info(data_dir=None, *, with_phones: bool = True) -> dict[str, RecordInfo]:
    """From ``data_dir`` (default ``vf.paths.data_dir()``): a saved
    ``record_info.parquet`` in it if present (a WP12 bundle), else
    ``gold/fact_tags.parquet`` + ``silver/facts.parquet`` (+ the raw phones for
    sharing counts when ``with_phones``)."""
    import pandas as pd
    from . import paths

    base = Path(data_dir) if data_dir is not None else paths.data_dir()
    saved = base / "record_info.parquet"
    if saved.exists():
        return record_info_from_frame(pd.read_parquet(saved))
    gold = base / "gold" if (base / "gold").exists() else paths.gold_dir()
    silver = base / "silver" if (base / "silver").exists() else paths.silver_dir()
    ft = pd.read_parquet(gold / "fact_tags.parquet", columns=["fact_id", "facility_id", "tags"])
    facts = pd.read_parquet(silver / "facts.parquet", columns=["fact_id", "text"])
    fac_ids = pd.read_parquet(silver / "facilities.parquet", columns=["facility_id"])["facility_id"]
    book = load_phone_book() if with_phones else None
    return build_record_info(ft, facts, book, facility_ids=fac_ids)


def record_info_frame(infos: Mapping[str, RecordInfo]):
    import pandas as pd
    return pd.DataFrame([i.to_row() for i in infos.values()],
                        columns=["facility_id", "tags", "h24_cotags", "h24_quote", "scheme_quote",
                                 "has_phone", "phone_shared_with", "h24_quote_cotags"])


def record_info_from_frame(df) -> dict[str, RecordInfo]:
    return {str(r["facility_id"]): _as_record(r) for r in df.to_dict("records")}


def save_record_info(infos: Mapping[str, RecordInfo], path, meta: Optional[Mapping] = None, *,
                     phone_book: Optional[PhoneBook] = None) -> Path:
    """Atomic parquet write plus a ``.meta.json`` sidecar. Holds no phone numbers unless
    ``phone_book`` is given: then the call numbers are added as ``PHONE_COLUMNS`` (for an app
    bundle, read back with ``load_phone_book(bundle)`` and served only masked by ``call_info``)."""
    from datetime import datetime, timezone

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    df = record_info_frame(infos)
    for col in ("h24_quote", "scheme_quote"):
        if df[col].map(lambda v: isinstance(v, str) and mask_numbers(v) != v).any():
            raise ValueError(f"{col} still holds a digit run that looks like a phone number")
    if phone_book is not None:
        for col, vals in phone_columns(infos, phone_book).items():
            df[col] = vals
        df["call_phone"] = df["call_phone"].astype("string")
        df["call_phone_source"] = df["call_phone_source"].astype("string")
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    try:
        df.to_parquet(tmp, index=False)
        os.replace(tmp, p)
    finally:
        if tmp.exists():
            tmp.unlink()
    side = {"output": p.name, "created_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "module": "vf.questions", "questions_version": QUESTIONS_VERSION, "rows": len(df),
            "columns": list(df.columns), "contains_phone_numbers": phone_book is not None,
            **({"phone_columns": list(PHONE_COLUMNS), "phone_numbers": int(df["call_phone"].notna().sum()),
                "phone_note": "call numbers for vf.questions.call_info, which masks them unless reveal is set "
                              "and demo mode is off"} if phone_book is not None else {}),
            **dict(meta or {})}
    mp = p.with_name(p.name + ".meta.json")
    tmpm = mp.with_name(f".{mp.name}.{os.getpid()}.tmp")
    try:
        tmpm.write_text(json.dumps(side, indent=2, default=str) + "\n")
        os.replace(tmpm, mp)
    finally:
        if tmpm.exists():
            tmpm.unlink()
    return p


# ---------------------------------------------------------------------------
# Measurement on the real grades (python -m vf.questions)
# ---------------------------------------------------------------------------

def emoc_blood_gap(grades) -> dict:
    """Grade 2+ emoc records (a fact names a C-section) with no blood fact anywhere."""
    e = grades[(grades["service_line"] == "emoc") & (grades["grade"] >= 2)]
    no_blood = e["tags_present"].map(lambda t: "blood" not in set(_as_list(t)))
    return {"emoc_grade2plus": int(len(e)), "no_blood_fact": int(no_blood.sum()),
            "share": round(float(no_blood.mean()), 4) if len(e) else None}


def measure(index=None, infos: Optional[Mapping[str, RecordInfo]] = None, grades=None) -> dict:
    """Questions for every grade 2+ record on every line, built from the
    ranker's own cards (``vf.rank``: recommendable cards for unblocked records,
    'not verified' cards for blocked ones). Counts per line, and how often each
    source appears."""
    from . import rank
    from . import paths
    import pandas as pd

    idx = index if index is not None else rank.load_index(load_post_offices=False)
    infos = infos if infos is not None else load_record_info()
    if grades is None:
        grades = pd.read_parquet(paths.gold_dir() / "capability_grades.parquet",
                                 columns=["facility_id", "service_line", "grade", "tags_present"])
    out = {"questions_version": QUESTIONS_VERSION, "blood": emoc_blood_gap(grades), "lines": {}}
    asked_blood = 0
    for line in idx.graded_lines:
        ld = idx.lines[line]
        radius = S.RADII_KM[line]
        mode = "probability" if idx.is_probability(line) else "v0"
        counts, sources, n_rec, n_blk = Counter(), Counter(), 0, 0
        for pos in [int(p) for p in (ld.grade >= 2).nonzero()[0]]:
            blocked = bool(ld.blocked[pos])
            card = (rank._unverified_card if blocked else rank._rec_card)(idx, line, pos, 0.0, radius, mode)
            fid = card.facility_id
            qs = questions_for(card.to_dict(), line, infos.get(fid))
            counts[len(qs)] += 1
            for q in qs:
                sources[q["source"]] += 1
            n_blk += blocked
            n_rec += 1
            if line == "emoc" and any("transfusion" in q["question"] for q in qs):
                asked_blood += 1
        out["lines"][line] = {"records": n_rec, "blocked": n_blk,
                              "question_counts": {str(k): counts[k] for k in sorted(counts)},
                              "mean_questions": round(sum(k * v for k, v in counts.items()) / n_rec, 2) if n_rec else None,
                              "sources": dict(sources.most_common())}
    out["blood"]["asked_blood_question"] = asked_blood
    return out


def phone_stats(book: PhoneBook) -> dict:
    counts = Counter(book.phone.values())
    shared = {n: c for n, c in counts.items() if c >= 2}
    return {"records_with_call_number": len(book.phone),
            "from_officialPhone": sum(1 for s in book.source.values() if s == "officialPhone"),
            "numbers_on_2plus_records": len(shared),
            "records_on_a_shared_number": sum(shared.values()),
            "max_records_one_number": max(shared.values()) if shared else 0}


def main() -> None:  # pragma: no cover - prints the measurement
    infos = load_record_info()
    book = load_phone_book()
    m = measure(infos=infos)
    b = m["blood"]
    print(f"questions {QUESTIONS_VERSION}")
    print(f"emoc grade 2+ records: {b['emoc_grade2plus']}; no blood fact: {b['no_blood_fact']} "
          f"({b['share']:.1%}); blood question asked on {b['asked_blood_question']}")
    ps = phone_stats(book)
    print("phones: " + ", ".join(f"{k} {v}" for k, v in ps.items()))
    for line, d in m["lines"].items():
        print(f"{line:16s} n={d['records']:5d} blocked={d['blocked']:4d} counts={d['question_counts']} "
              f"mean={d['mean_questions']} sources={d['sources']}")


if __name__ == "__main__":  # pragma: no cover
    main()
