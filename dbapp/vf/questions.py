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

Phone numbers never enter ``RecordInfo`` (it holds only a sharing count), so a
record-info table can sit in the app bundle without carrying a single number.
``python -m vf.questions`` prints the measurement on the real grades.
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

QUESTIONS_VERSION = "questions_v1"
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
    "operating theatre": "Is an operating theatre and a surgeon free right now?",
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
#: Requirement names that are about round-the-clock service (share the ``h24`` key).
H24_REQUIREMENTS = frozenset({"24-hour or emergency service", "24-hour service"})
#: Requirements the lead question already asks, besides each line's own service names.
LEAD_COVERS_EXTRA: dict[str, frozenset] = {"icu": frozenset({"ventilator"})}

#: Public schemes, wider than the recipe's ``scheme`` regex (which misses "PM-JAY").
SCHEME_PATTERN = r"ayushman|\bpm[- ]?jay\b|\bab[- ]?pmjay\b|\bcghs\b|jan arogya"
_SCHEME_RX = re.compile(SCHEME_PATTERN)
SCHEME_NAMES = "Ayushman Bharat, PM-JAY or CGHS"

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
    pharmacy" does not count as a round-the-clock ICU. ``h24_quote`` and
    ``scheme_quote``: the first such fact, verbatim. ``phone_shared_with``: how
    many other records list the same call number (0 if unique or no phone).
    """

    facility_id: str
    tags: frozenset = frozenset()
    h24_cotags: frozenset = frozenset()
    h24_quote: Optional[str] = None
    scheme_quote: Optional[str] = None
    has_phone: bool = False
    phone_shared_with: int = 0

    def to_row(self) -> dict:
        d = asdict(self)
        d["tags"] = sorted(self.tags)
        d["h24_cotags"] = sorted(self.h24_cotags)
        return d


def _as_record(record) -> Optional[RecordInfo]:
    if record is None or isinstance(record, RecordInfo):
        return record
    if isinstance(record, Mapping):
        return RecordInfo(
            facility_id=str(record.get("facility_id", "")),
            tags=frozenset(_as_list(record.get("tags"))),
            h24_cotags=frozenset(_as_list(record.get("h24_cotags"))),
            h24_quote=_str_or_none(record.get("h24_quote")),
            scheme_quote=_str_or_none(record.get("scheme_quote")),
            has_phone=bool(record.get("has_phone", False)),
            phone_shared_with=int(record.get("phone_shared_with") or 0),
        )
    raise TypeError(f"record must be a RecordInfo or a mapping, not {type(record).__name__}")


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
    t = " ".join(str(text or "").split())
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
    return REQUIREMENT_WHY.get(name) or f"No fact in the record mentions {name}."


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
        if q and _SCHEME_RX.search(R.normalize_text(q)):
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
    first = next((e for e in evidence if e.get("requirement") in covers), evidence[0] if evidence else None)
    absent = [m for m in missing if m in covers or _is_claim_requirement(m)]
    if grade >= 2 and first is not None:
        fld = f" ({first['field']})" if first.get("field") else ""
        why = f'The record says "{_quote(first["quote"])}"{fld}, but web pages can be out of date.'
    elif grade == 1 and first is not None:
        why = f'The record only claims it: "{_quote(first["quote"])}".'
    elif grade == 1:
        why = "The record only lists a related specialty."
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
                f.get("reason") or "A trust check found the claim unusual for this kind of facility.")
    loc = by_flag.get("location_conflict", [])
    prec = c.get("location_precision_km")
    if loc:
        add("location", "location", "Can you confirm your full address and a landmark near you?",
            "The record's location is uncertain: " + (loc[0].get("reason") or "its map point and PIN code disagree."))
    elif prec is not None and float(prec) > FUZZY_POSITION_KM:
        add("location", "location", "Can you confirm your full address and a landmark near you?",
            f"The record's position is known only to within about {float(prec):.0f} km.")
    for f in by_flag.get("possible_merge", [])[:1]:
        noun = SERVICE_NOUN.get(line, "this service")
        add("flag:possible_merge", "merge",
            f"Is {noun} at this address, and not at another branch or a hospital with a similar name?",
            "The record may combine more than one facility: " + (f.get("reason") or "a trust check flagged it."))

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
                if "h24" in rec.tags and rec.h24_quote:
                    why = ("The record mentions round-the-clock service only for something else: "
                           f'"{_quote(rec.h24_quote)}".')
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
        add("cost", "cost", "Will Ayushman Bharat (PM-JAY) cover this treatment for this patient?", why)
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

def normalize_phone(raw) -> Optional[str]:
    """``+<digits>``, or None for a missing or too-short value. Indian numbers
    written without a country code (10 digits, or 0 + 10) get +91."""
    s = _str_or_none(raw)
    if s is None:
        return None
    d = re.sub(r"\D", "", s)
    if len(d) == 11 and d.startswith("0"):
        d = "91" + d[1:]
    elif len(d) == 10:
        d = "91" + d
    if len(d) < 10:
        return None
    return "+" + d


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
    """facility_id -> call number, its field, and how many other records share it."""

    phone: dict = field(default_factory=dict)
    source: dict = field(default_factory=dict)
    shared_with: dict = field(default_factory=dict)

    def __repr__(self) -> str:  # never print numbers
        return f"PhoneBook({len(self.phone)} numbers)"

    def __len__(self) -> int:
        return len(self.phone)


def build_phone_book(raw) -> PhoneBook:
    """From raw facility rows (``vf.io.read_facilities()``: ``unique_id``,
    ``officialPhone``, ``phone_numbers``). The call number is ``officialPhone``,
    else the first entry of ``phone_numbers``. Repeated ``unique_id`` rows count
    once. Sharing is counted over call numbers of distinct facility ids."""
    from . import io

    book = PhoneBook()
    cols = set(raw.columns)
    for r in raw.itertuples(index=False):
        fid = _str_or_none(getattr(r, "unique_id", None) if "unique_id" in cols else getattr(r, "facility_id", None))
        if fid is None or fid in book.phone:
            continue
        num, src = normalize_phone(getattr(r, "officialPhone", None) if "officialPhone" in cols else None), "officialPhone"
        if num is None and "phone_numbers" in cols:
            src = "phone_numbers"
            try:
                lst = io.parse_list(r.phone_numbers)
            except Exception:
                lst = []
            num = next((n for n in (normalize_phone(x) for x in lst) if n), None)
        if num is not None:
            book.phone[fid] = num
            book.source[fid] = src
    counts = Counter(book.phone.values())
    book.shared_with = {fid: counts[n] - 1 for fid, n in book.phone.items()}
    return book


_DEFAULT_BOOK: Optional[PhoneBook] = None


def load_phone_book() -> PhoneBook:
    """The phone book from the raw facilities file (cached). Raw data stays
    local: never write numbers into the repo or a published file."""
    global _DEFAULT_BOOK
    if _DEFAULT_BOOK is None:
        from . import io
        _DEFAULT_BOOK = build_phone_book(io.read_facilities())
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
        return {"facility_id": facility_id, "has_phone": False, "phone": None, "masked": True, "tel": None,
                "source": None, "shared_with": 0,
                "note": "The record lists no phone number. No phone in the record does not mean the facility has none."}
    show = bool(reveal) and not demo_mode()
    shared = int(book.shared_with.get(facility_id, 0))
    note = f"From this facility's record ({book.source.get(facility_id)}). Numbers on web pages can be out of date."
    if shared:
        note += f" The same number is listed on {shared} other record{'' if shared == 1 else 's'}."
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
    h24_q: dict = {}
    scheme_q: dict = {}
    for fid, tg, txt in zip(t["facility_id"], t["tags"], t["text"]):
        if tg:
            tags.setdefault(fid, set()).update(tg)
            if "h24" in tg:
                h24_co.setdefault(fid, set()).update(x for x in tg if x != "h24")
                if fid not in h24_q and isinstance(txt, str):
                    h24_q[fid] = txt
        if fid not in scheme_q and isinstance(txt, str) and not R.is_negated(txt) and (
                "scheme" in tg or _SCHEME_RX.search(R.normalize_text(txt))):
            scheme_q[fid] = txt
    out = {}
    for fid in ids:
        tg = set(tags.get(fid, ()))
        if fid in scheme_q:
            tg.add("scheme")
        out[fid] = RecordInfo(
            facility_id=fid, tags=frozenset(tg), h24_cotags=frozenset(h24_co.get(fid, ())),
            h24_quote=h24_q.get(fid), scheme_quote=scheme_q.get(fid),
            has_phone=bool(phone_book and fid in phone_book.phone),
            phone_shared_with=int(phone_book.shared_with.get(fid, 0)) if phone_book else 0,
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
                                 "has_phone", "phone_shared_with"])


def record_info_from_frame(df) -> dict[str, RecordInfo]:
    return {str(r["facility_id"]): _as_record(r) for r in df.to_dict("records")}


def save_record_info(infos: Mapping[str, RecordInfo], path, meta: Optional[Mapping] = None) -> Path:
    """Atomic parquet write plus a ``.meta.json`` sidecar. Holds no phone numbers."""
    from datetime import datetime, timezone

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    df = record_info_frame(infos)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    try:
        df.to_parquet(tmp, index=False)
        os.replace(tmp, p)
    finally:
        if tmp.exists():
            tmp.unlink()
    side = {"output": p.name, "created_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "module": "vf.questions", "questions_version": QUESTIONS_VERSION, "rows": len(df),
            "columns": list(df.columns), "contains_phone_numbers": False, **dict(meta or {})}
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
