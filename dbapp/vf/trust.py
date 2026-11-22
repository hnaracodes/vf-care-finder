"""Trust checks (WP11): when a facility record should not be taken at face value.

Each check is a pure function over plain values (or small frames) so it can be
unit-tested on hand-made rows. ``scripts/wp11_trust_checks.py`` does the data
loading, the geography lookups, and writes ``gold/facility_flags.parquet`` and
``gold/facility_duplicates.parquet`` (contracts in ``docs/07_data_contracts.md``).

Flags say what the *record* may be, never what the facility is.

Checks and their signals (``signal`` column):

* ``location_conflict``: ``coord_excluded`` (major; WP1 set the location to null),
  ``coord_unresolved`` (major, blocking), ``moved_to_pin`` (minor),
  ``state_mismatch`` and ``city_mismatch`` (minor; the record's state or city
  text names a different place from its PIN, while the point agrees with the PIN).
* ``possible_merge``: ``name_joins_two``, ``text_other_address``,
  ``shared_website``, ``specialty_breadth``. Minor; major when two independent
  signals fire. After the hand review (``reports/trust_checks.md``) some
  signals only count as corroboration: see ``MERGE_SIGNALS_ALONE``.
  Per line (``service_line`` set, major, blocking that line), on a record that
  already has a merge flag: ``evidence_names_other`` (a supporting fact for the
  line names another institution or a place over ``EVIDENCE_PLACE_KM`` away)
  and ``evidence_beside_other`` (a supporting fact that names nothing sits
  next to a fact naming such a place). See ``evidence_other_place``.
* ``implausible_claim`` (per service line): ``type_cannot_offer`` and
  ``few_beds``. Major (blocking for that line).
* duplicates: pairs within 1 km with matching distinctive names, or the same
  official website and PIN. A name match below ``DUP_SURE_SCORE`` (one name's
  words inside the other's, or a near spelling) needs the websites not to
  disagree, and the words-inside rule needs the same website.
"""
from __future__ import annotations

import re
from typing import Iterable, Mapping, Optional, Sequence

import numpy as np
import pandas as pd
from rapidfuzz import fuzz
from rapidfuzz.distance import Levenshtein

from . import geo

CHECK_VERSION = "wp11_v3"

FLAG_COLUMNS = ["facility_id", "flag", "service_line", "severity", "signal", "reason", "quote", "check_version"]
DUP_COLUMNS = ["facility_id_a", "facility_id_b", "distance_km", "name_score", "signal", "keep"]

# ---------------------------------------------------------------------------
# 1. Location conflict
# ---------------------------------------------------------------------------

#: State spellings in ``state_raw`` (after ``geo.norm_state``) mapped to the PIN
#: directory's state names. Only unambiguous abbreviations and misspellings.
STATE_TEXT_ALIASES: dict[str, str] = {
    "TAMILNADU": "TAMIL NADU", "TN": "TAMIL NADU",
    "UP": "UTTAR PRADESH", "U P": "UTTAR PRADESH", "UTTARPRADESH": "UTTAR PRADESH",
    "UTTAR PRADES H": "UTTAR PRADESH",
    "ORISSA": "ODISHA", "CHATTISGARH": "CHHATTISGARH", "CHHATISGARH": "CHHATTISGARH", "CG": "CHHATTISGARH",
    "PONDICHERRY": "PUDUCHERRY", "U T OF PUDUCHERRY": "PUDUCHERRY",
    "NEW DELHI": "DELHI", "DELHI NCR": "DELHI", "NCR DELHI": "DELHI", "NCT DELHI": "DELHI", "NCT": "DELHI",
    "DL": "DELHI", "NCR": "DELHI",
    "TS": "TELANGANA", "TELENGANA": "TELANGANA",
    "MP": "MADHYA PRADESH", "M P": "MADHYA PRADESH", "MADHYAPRADESH": "MADHYA PRADESH",
    "UTTARANCHAL": "UTTARAKHAND", "UTTRANCHAL": "UTTARAKHAND",
    "GJ": "GUJARAT", "BR": "BIHAR", "MH": "MAHARASHTRA",
    "KASHMIR": "JAMMU AND KASHMIR", "J AND K": "JAMMU AND KASHMIR", "JAMMU J AND K": "JAMMU AND KASHMIR",
    "DAMAN AND DIU": "DADRA AND NAGAR HAVELI AND DAMAN AND DIU",
    "DADRA AND NAGAR HAVELI": "DADRA AND NAGAR HAVELI AND DAMAN AND DIU",
    "CHANDIGARH UT": "CHANDIGARH", "PUNJAB REGION": "PUNJAB",
}

#: Pairs of states that a record may name for the same place without a real
#: conflict: states split since 2000 (old pages keep the parent's name) and
#: the city regions that cross state lines (Delhi NCR, Chandigarh tricity).
COMPATIBLE_STATES: frozenset[frozenset[str]] = frozenset(frozenset(p) for p in [
    ("ANDHRA PRADESH", "TELANGANA"),
    ("BIHAR", "JHARKHAND"),
    ("MADHYA PRADESH", "CHHATTISGARH"),
    ("UTTAR PRADESH", "UTTARAKHAND"),
    ("JAMMU AND KASHMIR", "LADAKH"),
    ("DELHI", "HARYANA"),
    ("DELHI", "UTTAR PRADESH"),
    ("CHANDIGARH", "PUNJAB"),
    ("CHANDIGARH", "HARYANA"),
])

#: ``coord_check`` values where the record's point agrees with its PIN.
POINT_AGREES_WITH_PIN = frozenset({"ok", "ok_same_district"})


def pin_state(s) -> str:
    """PIN-directory state name in comparable form (no leading "THE")."""
    n = geo.norm_state(s)
    return n[4:] if n.startswith("THE ") else n


def state_of_text(state_raw, states: Iterable[str]) -> Optional[str]:
    """The one state a ``state_raw`` value names, or None.

    Exact name or alias first; otherwise a state name found as whole words
    inside the text ("Ghaziabad Uttar Pradesh"). None when the text names no
    state (a city such as "Thane") or more than one.
    """
    if state_raw is None or (isinstance(state_raw, float) and np.isnan(state_raw)):
        return None
    states = set(states)
    n = pin_state(state_raw)
    n = STATE_TEXT_ALIASES.get(n, n)
    if n in states:
        return n
    hits = {s for s in states if re.search(rf"\b{re.escape(s)}\b", n)}
    hits = {h for h in hits if not any(h != o and h in o for o in hits)}  # "PRADESH" inside longer names
    return hits.pop() if len(hits) == 1 else None


def states_compatible(a: str, b: str) -> bool:
    return a == b or frozenset((a, b)) in COMPATIBLE_STATES


def _km(d) -> str:
    return "far" if d is None or pd.isna(d) else f"{float(d):.0f} km"


def nice_place(s: str) -> str:
    """'MAHARASHTRA|PUNE' -> 'Pune, Maharashtra'; 'KERALA' -> 'Kerala'."""
    if not s:
        return ""
    parts = [p.strip().title() for p in str(s).split("|")]
    return ", ".join(reversed(parts)) if len(parts) == 2 else parts[0]


def location_conflict(
    coord_check: Optional[str],
    coord_pin_km: Optional[float],
    pin: Optional[str],
    state_raw: Optional[str] = None,
    city_raw: Optional[str] = None,
    text_state: Optional[str] = None,
    pin_states: Iterable[str] = (),
    city_agrees_pin: Optional[bool] = None,
    city_other_districts: Iterable[str] = (),
    pin_district: Optional[str] = None,
) -> Optional[dict]:
    """The ``location_conflict`` flag for one record, or None.

    ``text_state``: the state ``state_raw`` names (``state_of_text``).
    ``pin_states``: the PIN-directory states of the record's PIN (comparable form).
    ``city_agrees_pin``: whether ``city_raw`` names a place of the PIN (None when
    the city text names no place). ``city_other_districts``: PIN-directory
    districts (``STATE|DISTRICT``) the city text names instead, already
    restricted to the text's state when it names one.

    The state and city text checks run only where the point agrees with the
    PIN (``ok``, ``ok_same_district``): elsewhere WP1 already compared the city
    text, and the record is flagged from ``coord_check``. A wrong state next to
    a city that names the PIN's place is a typo, not a conflict, and is not
    flagged.
    """
    signals, reasons, quote = [], [], None
    severity = None
    if coord_check == "excluded_unresolved":
        severity = "major"
        signals.append("coord_excluded")
        reasons.append(f"The record's map point is {_km(coord_pin_km)} from its PIN code's area and its city "
                       "text does not settle which is right, so the record has no usable location.")
        quote = city_raw
    elif coord_check == "kept_unresolved":
        severity = "major"
        signals.append("coord_unresolved")
        reasons.append(f"The record's map point is {_km(coord_pin_km)} from its PIN code's area, and its city "
                       "text does not settle which is right.")
        quote = city_raw
    elif coord_check == "moved_to_pin":
        severity = "minor"
        signals.append("moved_to_pin")
        reasons.append(f"The record's scraped map point was {_km(coord_pin_km)} from its PIN code's area; it is "
                       "shown at the PIN code's centre, so its position is approximate.")
        quote = city_raw
    elif coord_check in POINT_AGREES_WITH_PIN:
        pin_states = [s for s in pin_states if s and s != "NA"]
        others = sorted(set(city_other_districts) - {pin_district})
        city_bad = city_agrees_pin is False and bool(others)
        state_bad = (text_state is not None and bool(pin_states) and city_agrees_pin is not True
                     and not any(states_compatible(text_state, s) for s in pin_states))
        where_pin = nice_place(pin_district) if pin_district else " or ".join(s.title() for s in pin_states)
        if state_bad:
            signals.append("state_mismatch")
            reasons.append(f"The record's state reads \"{state_raw}\", but its PIN code {pin} and map point are "
                           f"in {where_pin}.")
            quote = state_raw
        if city_bad:
            signals.append("city_mismatch")
            named = ", ".join(nice_place(o) for o in others[:2])
            reasons.append(f"The record's city reads \"{city_raw}\" ({named}), but its PIN code {pin} and map "
                           f"point are in {where_pin}.")
            quote = quote if quote is not None else city_raw
        if signals:
            severity = "minor"
    if not signals:
        return None
    return {"flag": "location_conflict", "service_line": None, "severity": severity,
            "signal": "+".join(signals), "reason": " ".join(reasons), "quote": quote}


# ---------------------------------------------------------------------------
# 2. Possible merged record
# ---------------------------------------------------------------------------

#: A word or phrase that makes a name part a facility name.
_FACILITY_NOUN = (r"(?:hospitals?|clinics?|nursing home|polyclinic|poly clinic|diagnostics?|labs?|laboratory|"
                  r"netralaya|maternity home|medical cent(?:re|er)|health cent(?:re|er)|dispensary)")
_NAME_JOIN = re.compile(r"\s(?:&|and|\+)\s|\s?/\s?|\s\|\s", re.I)
#: Where a name's address or place suffix starts.
_NAME_SUFFIX = re.compile(r",|\s[-–—]\s|\(|\bnear\b|\bopp\b|\bin\s+[A-Z]", re.I)

#: Words that do not make a name part distinctive: facility nouns, specialties,
#: services, honorifics and filler. A second name part needs a word outside this
#: list ("Sheetal Nursing Home") to count as another facility's name, so
#: "X Hospital & Research Centre" or "X Clinic & Implant Centre" do not fire.
GENERIC_NAME_WORDS = frozenset("""
hospital hospitals clinic clinics centre center centres centers nursing home homes polyclinic poly diagnostic
diagnostics lab labs laboratory laboratories netralaya institute institutes research foundation dispensary
care health healthcare medical medicare multispeciality multispecialty multi speciality specialty specialities
super superspeciality superspecialty general maternity mother child children childrens women womens woman kids
baby eye eyes dental dentistry dentist dentists oral teeth tooth implant implants orthodontic orthodontics
maxillofacial cosmetic cosmetology skin hair laser aesthetic aesthetics ent heart cardiac cardiology cancer
kidney urology nephrology neuro neurology brain spine ortho orthopaedic orthopedic trauma accident joint
replacement fracture surgical surgery surgeries ivf fertility infertility test tube diabetes diabetic thyroid
gastro liver piles stone stones physio physiotherapy rehabilitation rehab speech hearing therapy imaging scan
scans mri ct x ray xray pathology path cytology sonography ultrasound ultra endoscopy icu iccu nicu emergency
critical ayurveda ayurvedic ayurved homoeopathic homeopathic homoeo homeopathy panchakarma siddha unani yoga
wellness lifestyle allergy chest lung lungs pain psychiatry neuropsychiatry sexology de addiction deaddiction
mental vision retina cornea glaucoma lasik plastic reconstructive day blood bank pharmacy medicals medicines
medicine dialysis transplant transplantation organ vascular well sugar affiliated associated
and of the a an for in at by dr drs doctor doctors s pvt private ltd limited unit units branch main city new
advanced advance modern family complete total best prime global international national memorial charitable
trust society mission seva sciences science college studio spa solutions services service point hub kendra
sadan bhavan sri shri shree smt smile smiles pearls
road street marg chowk lane colony sector floor complex near opp opposite post bazar bazaar market plot shop
""".split())


def _name_words(s: str) -> list[str]:
    """Lowercased words, possessives dropped ("children's" -> "children")."""
    out = []
    for w in re.findall(r"[a-z][a-z'’]*", s.lower()):
        w = re.sub(r"['’]s$", "", w).strip("'’")
        if w:
            out.append(w)
    return out


def _strip_place_suffix(part: str) -> str:
    m = _NAME_SUFFIX.search(part)
    return part[: m.start()] if m else part


def name_joins_two(name: Optional[str], place_words: Iterable[str] = ()) -> Optional[str]:
    """The second facility name when ``name`` joins two ("X Hospital & Y Clinic",
    "X Clinic / Dr Y's Clinic"), else None.

    The part before the joiner must end in a facility noun; the part after it
    must contain one, and, once its address suffix and place names are
    removed, a word outside ``GENERIC_NAME_WORDS`` that the first part does
    not also have (so "Arivu Neuro Clinic and Arivu Dental Clinic", one brand at
    one address, does not fire). ``place_words``: lowercased place names
    (cities, districts) that do not count as distinctive.
    """
    if not name:
        return None
    places = {p.lower() for p in place_words}
    parts = _NAME_JOIN.split(name)
    for i in range(1, len(parts)):
        left = " ".join(parts[:i]).strip()
        right = _strip_place_suffix(" ".join(parts[i:])).strip()
        if not re.search(rf"\b{_FACILITY_NOUN}\W*$", left, re.I):
            continue
        if not re.search(rf"\b{_FACILITY_NOUN}\b", right, re.I):
            continue
        left_words = set(_name_words(left))
        own = [w for w in _name_words(right) if len(w) > 1 and w not in GENERIC_NAME_WORDS and w not in places
               and w not in left_words]
        if own:
            return right
    return None


#: A fact that gives the facility's own location: "located in X", "based at X".
_LOCATED = re.compile(
    r"\b(?:located|situated|based)\s+(?:in|at)\s+(.{3,120}?)(?=[.;]|$|\s(?:with|and|near|under|which|offering|"
    r"providing|that|since|for|on|is|it)\s)", re.I)
#: A 6-digit PIN in running text (not part of a longer number, a decimal, or a count).
_PIN_IN_TEXT = re.compile(r"(?<![\d.+:/#])([1-8]\d{2})\s?(\d{3})(?![\d+]|\s?(?:\+|happy|patients|sq|ft|beds|people))",
                          re.I)
#: Contexts where a named place is not the facility's own address.
_NOT_OWN_ADDRESS = re.compile(
    r"\b(landmark|near|nearby|opposite|opp|beside|behind|next to|close to|adjacent|collection cent|"
    r"registered (?:office|address)|registrar|roc\b|cin\b|corporate office|head ?office|headquarter|"
    r"refer|referral|tie[- ]?up|competitor|visiting|previously|worked at|formerly|camp)\b", re.I)


def located_places(text: str) -> list[str]:
    """Place phrases a fact gives as the facility's own location, split at commas."""
    if not text or _NOT_OWN_ADDRESS.search(text):
        return []
    m = _LOCATED.search(text)
    if not m:
        return []
    return [p.strip() for p in re.split(r",|\bin\b|\bof\b|\(|\)", m.group(1)) if p.strip()]


def pins_in_text(text: str) -> list[str]:
    """6-digit PINs a fact gives, outside the contexts in ``_NOT_OWN_ADDRESS``."""
    if not text or _NOT_OWN_ADDRESS.search(text):
        return []
    return [a + b for a, b in _PIN_IN_TEXT.findall(text)]


def other_address(
    texts: Sequence[tuple[str, str]],
    own_groups: set[str],
    own_names: set[str],
    place_groups: Mapping[str, set[str]],
    pin_groups: Mapping[str, set[str]],
    own_pin: Optional[str],
    states: Iterable[str] = (),
) -> Optional[tuple[str, list[str]]]:
    """First fact that places the facility in a district other than its own.

    ``texts``: (field, text) pairs. ``own_groups``: the record's districts
    (``STATE|DISTRICT``: its PIN's, its point's, its city text's).
    ``own_names``: place keys (``geo.place_key``) of the record's own places.
    ``place_groups``: place key -> districts that have a place of that name.
    ``pin_groups``: PIN -> its districts. ``states``: comparable state names,
    used to drop state names as places and to narrow a place to the state the
    fact names.

    Returns (fact text, other districts) or None. When a place name fits
    several districts, only the likeliest are returned (see ``_rank_other``).
    """
    states = set(states)
    for _field, text in texts:
        if not isinstance(text, str):
            continue
        # PINs first: the most specific
        for p in pins_in_text(text):
            if p == own_pin or p not in pin_groups:
                continue
            g = set(pin_groups[p])
            if g and not (g & own_groups):
                return text, sorted(g)
        parts = located_places(text)
        if not parts:
            continue
        # states are recognised before place_key, which drops direction words
        # ("West Bengal" would become "BENGAL" and match the record's own names)
        named_states, keys = set(), set()
        for p in parts:
            n = pin_state(p)
            n = STATE_TEXT_ALIASES.get(n, n)
            if n in states:
                named_states.add(n)
            else:
                keys.add(geo.place_key(p))
        keys = {k for k in keys if len(k) >= 4 and k not in states and k not in STATE_TEXT_ALIASES
                and k not in geo._CITY_STOP}
        keys |= {a for k in keys for a in geo.CITY_ALIASES.get(k, ())}
        if not keys or geo.names_match(keys, own_names):
            continue
        other: set[str] = set()
        for k in keys:
            g = place_groups.get(k, set())
            if named_states:
                g = {x for x in g if x.split("|")[0] in named_states}
            if g & own_groups:  # the fact names a place in the record's own district
                other = set()
                break
            other |= g
        if other:
            return text, _rank_other(other, keys, {g.split("|")[0] for g in own_groups})
    return None


def _rank_other(groups: set[str], keys: set[str], own_states: set[str]) -> list[str]:
    """The likeliest of the districts a place name can mean: those named after
    the place if any, then among them those in the record's own state if any
    (sorted). "Located in Nagpur" gives Nagpur district, not also a district
    with a village called Nagpur."""
    def rank(g: str) -> tuple[bool, bool]:
        st, dist = g.split("|", 1)
        return (geo.place_key(dist) not in keys, st not in own_states)
    best = min(rank(g) for g in groups)
    return sorted(g for g in groups if rank(g) == best)


#: Website hosts that are platforms, directories, link shorteners or government
#: portals: many unrelated facilities share them, so sharing one says nothing.
PLATFORM_HOST_SUFFIXES = (
    "business.site", "practo.com", "justdial.com", "facebook.com", "instagram.com", "youtube.com",
    "google.com", "g.page", "g.co", "goo.gl", "bit.ly", "sites.google.com", "eka.care", "getmy.clinic",
    "webs.com", "wixsite.com", "blogspot.com", "wordpress.com", "linktr.ee", "lybrate.com", "sehat.com",
    "credihealth.com", "medifee.com", "quikr.com", "sulekha.com", "indiamart.com", "twitter.com", "x.com",
    "linkedin.com", "apollo247.com", "tinyurl.com", "gov.in", "nic.in", "maps.app.goo.gl", "wa.me",
)


def website_key(url) -> Optional[str]:
    """Comparable host of an official website, or None for a platform or a missing value."""
    from .clean import url_domain
    host = url_domain(url)
    if not host:
        return None
    if any(host == s or host.endswith("." + s) for s in PLATFORM_HOST_SUFFIXES):
        return None
    return host


def shared_websites(facility_id: Sequence[str], site: Sequence[Optional[str]],
                    district: Sequence[Optional[str]]) -> dict[str, list[str]]:
    """facility_id -> other facility ids sharing its website from a different district.

    Only sites listed by records in at least two districts count; a record with
    no district is compared by site alone and never counts as "different".
    """
    df = pd.DataFrame({"fid": list(facility_id), "site": list(site), "district": list(district)})
    df = df[df["site"].notna()]
    out: dict[str, list[str]] = {}
    for _, g in df.groupby("site"):
        if len(g) < 2 or g["district"].dropna().nunique() < 2:
            continue
        for r in g.itertuples(index=False):
            if r.district is None or pd.isna(r.district):
                continue
            others = [o.fid for o in g.itertuples(index=False)
                      if o.fid != r.fid and isinstance(o.district, str) and o.district != r.district]
            if others:
                out[r.fid] = others
    return out


def specialty_breadth_limits(facility_type: Sequence[Optional[str]], n_specialties: Sequence[int],
                             q: float = 0.99, min_records: int = 50) -> dict[str, float]:
    """Per facility type, the ``q`` quantile of the specialty count. Types with
    fewer than ``min_records`` records get no limit (too few to set one)."""
    df = pd.DataFrame({"t": list(facility_type), "n": list(n_specialties)})
    df["t"] = df["t"].fillna("unknown")
    sizes = df.groupby("t").size()
    lim = df.groupby("t")["n"].quantile(q)
    return {t: float(v) for t, v in lim.items() if sizes[t] >= min_records}


# ---------------------------------------------------------------------------
# 2b. Possible merge, per line: the line's own evidence is about somewhere else
# ---------------------------------------------------------------------------

#: A named place counts as another city when every point it can mean is
#: farther than this from the record's own location (its point and its PIN's).
#: The same 40 km WP1 uses to compare a point with its PIN.
EVIDENCE_PLACE_KM = 40.0
#: Facts on each side of a supporting fact, in the same field, read for the
#: "comes from" reading (``evidence_beside_other``).
EVIDENCE_CONTEXT = 2

#: Place names in the gazetteer that are also brand, deity or person names in
#: facility text ("Manipal Hospitals", "Krishna Hospital", "Sagar Hospitals"),
#: or common words ("District", "Central", "Park"). Never read as a city.
EVIDENCE_PLACE_STOP = frozenset({
    "DISTRICT", "CENTRAL", "PARK", "CITY", "KRISHNA", "MANIPAL", "ANAND", "SAGAR", "KALYAN", "NIRMAL", "BASTI",
    "DHAR", "GUNA", "MANSA", "GANGA", "AMRITA", "YASHODA", "SURYA", "LIFELINE", "UNITY", "NOBLE", "SUNRISE",
    "GLOBAL", "MEDICAL", "MISSION", "GENERAL", "CIVIL", "RURAL", "MODEL", "SAINT", "MOTHER", "BALAJI",
})

#: Facility nouns that end an institution's name in running text.
_INSTITUTION_NOUN = (r"(?:Hospitals?|Medical College|Medical Sciences|Institute|Nursing Home|Medical Cent(?:re|er)|"
                     r"Clinics?|Health Cent(?:re|er)|Infirmary|Sanatorium|Medicity|Health City)")
#: An institution's name: up to seven capitalised words (or "of", "and", "&")
#: before a facility noun ("Indira Gandhi Government Medical College",
#: "Sir J.J. Group of Hospitals", "Fortis Escorts Hospital").
_INSTITUTION = re.compile(rf"(?:(?:[A-Z][\w.'’&-]*|of|and|&|for|the)\s+){{1,7}}{_INSTITUTION_NOUN}\b")
#: Words that do not tell one institution from another, on top of
#: ``GENERIC_NAME_WORDS``: public-system words ("District Hospital", "Civil
#: Hospital"), listing words ("Top Neurosurgery Hospitals"), and department or
#: clinic names inside a hospital ("Filter Clinic", "Breast Clinic").
_INSTITUTION_GENERIC = frozenset("""
top best leading listed among describes award awards times government govt district sub civil general rural
community primary divisional referral state regional zonal esi esic railway area base military army cantonment
command corporation municipal taluk taluka upgraded teaching tertiary attached group sir tb filter breast liver
stoma picc line one stop tpa ge first german modular pvt chest diabetes palliative memory asthma geriatric
satellite camp mobile day night opd ipd its this that their our all other new old
oncology oncologic oncological radiation chemotherapy neurosurgery neurosciences neuroscience cardiothoracic
paediatric pediatric gynaecology gynecology obstetrics obstetric orthopaedics orthopedics nephrology rheumatology
dermatology psychiatric gastroenterology pulmonology pulmonary respiratory endocrinology neonatal fetal hepatology
spirometry pft telemedicine tele sleep stroke epilepsy headache migraine wound foot vein varicose hernia bariatric
obesity cataract dialysis
""".split())
#: A listing, not an institution: "Listed among Top Neurosurgery Hospitals In Jaipur".
_LISTING = re.compile(r"(top|best|leading|listed|ranked|award\w*|directory|network hospitals?)", re.I)
#: The fact describes a unit inside the record or run with it, not another site.
_CO_LOCATED = re.compile(r"\b(on[- ]?site|in[- ]house|within|inside|unit of|operated by|managed by|run by|"
                         r"powered by|partner\w*|tie[- ]?up|in association|collaborat\w*|franchise|empanel\w*)\b",
                         re.I)
#: A doctor's past posts: "formerly at AIIMS", "ex-Fortis", "trained at".
_PAST_POST = re.compile(r"\b(formerly|previously|former|ex[- ]|trained|worked|earlier|alumn\w*|studied|fellowship|"
                        r"residency|degree|graduat\w*|qualified|mbbs|md|ms|dnb|mch|dm)\b", re.I)


def _compact(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def _institution_words(phrase: str) -> list[str]:
    """Distinctive words of an institution name ("J.J." -> "j", "j" dropped as
    one letter, so "Sir J.J. Group of Hospitals" keeps "jj" via ``_compact``)."""
    p = re.sub(r"(?<=\b[A-Za-z])\.(?=[A-Za-z]\b)", "", phrase)  # J.J. -> JJ
    return [w for w in _name_words(p.replace(".", " "))
            if len(w) > 1 and w not in GENERIC_NAME_WORDS and w not in _HONORIFIC and w not in _INSTITUTION_GENERIC]


class OwnPlace:
    """What a record is: its name's distinctive words, initials and website
    host, and where it is (points, and the place names of its city, PIN and
    point). Built once per record by the WP11 script."""

    def __init__(self, name: Optional[str], website_host: Optional[str] = None,
                 points: Sequence[tuple[float, float]] = (), place_names: Iterable[str] = ()):
        name = name if isinstance(name, str) else ""
        self.name = name
        self.words = set(_institution_words(name))
        self.compact = _compact(name)
        self.initials = "".join(w[0] for w in re.findall(r"[A-Za-z]+", name)
                                if w.lower() not in ("of", "and", "the", "for")).upper()
        self.host = _compact(website_host or "")
        self.points = [(float(a), float(b)) for a, b in points if not (pd.isna(a) or pd.isna(b))]
        self.place_names = {p for p in place_names if p}

    def is_own_institution(self, words: Sequence[str]) -> bool:
        """True when an institution name (its distinctive words) is the record's own."""
        if not words:
            return True  # no distinctive words: cannot tell, so not "another" institution
        if set(words) & self.words:
            return True
        for w in words:
            if len(w) >= 5 and any(len(o) >= 5 and Levenshtein.distance(w, o) <= 1 for o in self.words):
                return True  # one letter apart in a long name: "Kelkar" and "Ketkar", a spelling slip
            if len(w) >= 4 and (w in self.compact or (self.host and w in self.host)):
                return True
            if len(w) >= 3 and len(self.initials) >= 3 and self.initials.startswith(w.upper()):
                return True  # "KIMS" for "Krishna Institute of Medical Sciences"
        joined = "".join(words)
        if len(joined) >= 5 and (joined in self.compact or (self.host and joined in self.host)):
            return True
        return bool(self.words) and max(fuzz.ratio(" ".join(words), w) for w in self.words) >= 85


def institution_mentions(text: str) -> list[tuple[str, list[str]]]:
    """(phrase, distinctive words) for each institution named in ``text``."""
    if not isinstance(text, str):
        return []
    out = []
    for m in _INSTITUTION.finditer(text):
        phrase = re.sub(r"^(?:(?:of|and|&|for|the)\s+)+", "", m.group(0), flags=re.I).strip()
        out.append((phrase, _institution_words(phrase)))
    return out


def place_mentions(text: str, gazetteer: Mapping[str, Sequence[tuple[float, float]]]) -> list[tuple[str, str]]:
    """(text as written, gazetteer key) for each place named in ``text``.

    Places are runs of up to three capitalised words whose ``geo.place_key``
    is a gazetteer key of 4+ letters outside ``EVIDENCE_PLACE_STOP``; the
    longest match wins ("Navi Mumbai" before "Mumbai").
    """
    if not isinstance(text, str):
        return []
    out = []
    for m in re.finditer(r"\b[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,3}\b", text):
        ws = m.group(0).split()
        i = 0
        while i < len(ws):
            hit = None
            for j in range(min(len(ws), i + 3), i, -1):
                k = geo.place_key(" ".join(ws[i:j]))
                if len(k) >= 4 and k in gazetteer and k not in EVIDENCE_PLACE_STOP:
                    hit = (" ".join(ws[i:j]), k, j)
                    break
            if hit:
                out.append(hit[:2])
                i = hit[2]
            else:
                i += 1
    return out


def _place_is_own(key: str, own: OwnPlace, gazetteer, km: float) -> tuple[bool, Optional[float]]:
    """(own?, distance in km to the nearest point the name can mean)."""
    if own.place_names and geo.names_match({key}, own.place_names):
        return True, 0.0
    pts = gazetteer.get(key, ())
    if not own.points or not pts:
        return (not own.points), None  # no location to compare: never "another city"
    d = min(float(geo.haversine_km(a, b, p[0], p[1])) for a, b in own.points for p in pts)
    return d <= km, d


def evidence_names_elsewhere(text: str, own: OwnPlace, gazetteer: Mapping[str, Sequence[tuple[float, float]]],
                             km: float = EVIDENCE_PLACE_KM) -> Optional[dict]:
    """Whether one supporting fact names another institution or another city.

    Returns ``{"kind": "institution" | "city", "named": str, "km": float|None}``
    or None. Another *institution*: a named institution whose distinctive
    words are not the record's (``OwnPlace.is_own_institution``), in a fact
    that does not also name the record's own institution and does not say the
    other one is a unit inside it or run with it (``_CO_LOCATED``) or a
    doctor's past post (``_PAST_POST``). Another *city*: a named place every
    reading of which is over ``km`` from the record, in a fact that names no
    place of the record's own (a list of a chain's cities that includes the
    record's own is not borrowed evidence).
    """
    if not isinstance(text, str) or not text.strip() or _PAST_POST.search(text):
        return None
    places = place_mentions(text, gazetteer)
    far, own_place_named = None, False
    for written, key in places:
        is_own, d = _place_is_own(key, own, gazetteer, km)
        if is_own:
            own_place_named = True
        elif far is None:
            far = (written, d)
    if far is not None and not own_place_named:
        return {"kind": "city", "named": far[0], "km": far[1]}
    if _LISTING.search(text):
        return None
    place_keys = {k for _, k in places}
    other, own_named = None, False
    for phrase, words in institution_mentions(text):
        if words and own.is_own_institution(words):  # "Hubli Super-Speciality Hospital" for its own record
            own_named = True
            continue
        words = [w for w in words if geo.place_key(w) not in place_keys]  # "Fortis Hospital Mohali" -> fortis
        if words and not own.is_own_institution(words) and other is None:
            other = phrase
    if other and not own_named and not _CO_LOCATED.search(text):
        return {"kind": "institution", "named": other, "km": None}
    return None


def evidence_other_place(line: str, items: Sequence[Mapping], own: OwnPlace,
                         gazetteer: Mapping[str, Sequence[tuple[float, float]]],
                         context: Optional[Mapping[str, Sequence[str]]] = None,
                         km: float = EVIDENCE_PLACE_KM) -> Optional[dict]:
    """The per-line ``possible_merge`` flag (major) for one graded line, or None.

    ``items``: the line's ``evidence_items`` (``text``, ``fact_id``,
    ``requirement``). Fires when a supporting fact names another institution or
    city (``evidence_names_elsewhere``), signal ``evidence_names_other``.
    ``context`` (fact_id -> texts of the facts next to it in the same field)
    is for the narrower "comes from" reading: a supporting fact that names no
    place or institution at all, next to facts that name another one and none
    of the record's own, signal ``evidence_beside_other``. The script passes
    ``context`` only for records that already carry a record-level merge flag.
    """
    label = LINE_LABEL.get(line, line)
    hits = [evidence_names_elsewhere(it.get("text"), own, gazetteer, km) for it in items]
    if any(h is None and _names_own(it.get("text"), own, gazetteer, km) for it, h in zip(items, hits)):
        return None  # another supporting fact names the record itself (its name or its own place)
    for it in items:
        t = it.get("text")
        hit = evidence_names_elsewhere(t, own, gazetteer, km)
        if hit:
            where = (f"another facility, {hit['named']}" if hit["kind"] == "institution"
                     else f"{hit['named']}" + (f", about {hit['km']:.0f} km from this record's location"
                                               if hit.get("km") is not None else ", another city"))
            return {"flag": "possible_merge", "service_line": line, "severity": "major",
                    "signal": "evidence_names_other",
                    "reason": (f"The record's evidence for {label} names {where}, so it may describe that "
                               "place and not this one."),
                    "quote": t}
    if context and not any(_names_own(c, own, gazetteer, km, places_only=True)
                           for it in items for c in context.get(it.get("fact_id"), ())):
        # (a supporting fact that sits among facts about the record's own place keeps the line)
        for it in items:
            t = it.get("text")
            if not isinstance(t, str) or place_mentions(t, gazetteer) or any(w for _, w in institution_mentions(t)):
                continue
            near = list(context.get(it.get("fact_id"), ()))
            hits = [evidence_names_elsewhere(c, own, gazetteer, km) for c in near]
            hit = next((h for h in hits if h and h["kind"] == "city"), None)
            if hit:
                return {"flag": "possible_merge", "service_line": line, "severity": "major",
                        "signal": "evidence_beside_other",
                        "reason": (f"The record's evidence for {label} sits among facts about {hit['named']}, "
                                   "not this facility, and the record already mixes in other places' facts, so "
                                   "it may come from that place."),
                        "quote": t}
    return None


def _names_own(text: str, own: OwnPlace, gazetteer, km: float, places_only: bool = False) -> bool:
    """True when a fact names a place of the record's own, or (unless
    ``places_only``) the record's own institution."""
    if not isinstance(text, str):
        return False
    for _, key in place_mentions(text, gazetteer):
        if _place_is_own(key, own, gazetteer, km)[0] and own.points:
            return True
    if places_only:
        return False
    return any(words and own.is_own_institution(words) for _, words in institution_mentions(text))


#: Plain names of the lines for reasons ("emergency C-section and high-risk delivery").
LINE_LABEL = {
    "emoc": "C-section and high-risk delivery", "nicu": "newborn intensive care", "dialysis": "dialysis",
    "icu": "intensive care", "cardiac_surgery": "heart surgery", "cath_lab": "heart attack care (cath lab)",
    "mri": "MRI", "ct": "CT", "trauma": "trauma care", "burns": "burn care",
    "neurosurgery": "brain or spine surgery", "oncology": "cancer treatment",
}


#: Merge signals that fire a flag on their own (hand-review precision 70% or more).
MERGE_SIGNALS_ALONE = frozenset({"name_joins_two", "text_other_address"})
#: Signals that only corroborate: they count toward ``major`` next to a signal
#: in ``MERGE_SIGNALS_ALONE`` but never fire a flag alone. ``shared_website``
#: was 8 of 15 correct alone (chain branches share a site). ``specialty_breadth``
#: (0 of 15) is in neither set: computed and reported, never used.
MERGE_SIGNALS_CORROBORATE = frozenset({"shared_website"})


def merge_flag(signals: Mapping[str, tuple[str, Optional[str]]],
               alone: Iterable[str] = MERGE_SIGNALS_ALONE,
               corroborate: Iterable[str] = MERGE_SIGNALS_CORROBORATE) -> Optional[dict]:
    """Combine merge signals into one ``possible_merge`` flag, or None.

    ``signals``: signal -> (reason sentence, quote). Signals outside ``alone``
    and ``corroborate`` are ignored. Fires when at least one signal in
    ``alone`` is present; severity is ``major`` when two or more counted
    signals fire, else ``minor``. The quote is the first fired signal's (in
    the order name, text, website) that has one.
    """
    alone, corroborate = set(alone), set(corroborate)
    order = ["name_joins_two", "text_other_address", "shared_website", "specialty_breadth"]
    fired = [s for s in order if s in signals] + [s for s in signals if s not in order]
    fired = [s for s in fired if s in alone or s in corroborate]
    if not fired or not any(s in alone for s in fired):
        return None
    quote = next((signals[s][1] for s in fired if signals[s][1]), None)
    return {"flag": "possible_merge", "service_line": None,
            "severity": "major" if len(fired) >= 2 else "minor",
            "signal": "+".join(fired), "reason": " ".join(signals[s][0] for s in fired), "quote": quote}


# ---------------------------------------------------------------------------
# 4. Implausible claim
# ---------------------------------------------------------------------------

#: Lines that need a hospital (theatres, intensive care, round-the-clock staff).
HOSPITAL_ONLY_LINES = ("cardiac_surgery", "neurosurgery", "icu", "burns", "cath_lab", "nicu")
#: Facility types that would not normally offer them.
NON_HOSPITAL_TYPES = frozenset({"clinic", "dentist", "doctor", "pharmacy"})
#: Lines where a record with fewer than ``MIN_BEDS`` beds is implausible.
#: Off by default: 5 of 11 correct in the hand review (``n_beds`` is often a
#: wrong count, or the ICU bed count).
BED_LINES = ("icu", "cardiac_surgery", "neurosurgery")
MIN_BEDS = 10
USE_BEDS = False
#: A name that says the record is a hospital, whatever its ``facility_type``:
#: some "clinic" records are real hospitals (WP11 pitfall).
_HOSPITAL_NAME = re.compile(r"\b(hospitals?|nursing home|medical college|medicity|medical cent(?:re|er)|"
                            r"institute of medical|hospitales|sanatorium|infirmary)\b", re.I)

LINE_PLAIN = {
    "cardiac_surgery": "heart surgery", "neurosurgery": "brain or spine surgery", "icu": "intensive care",
    "burns": "burn care", "cath_lab": "heart attack care (cath lab)", "nicu": "newborn intensive care",
}


#: Words before "hospital" in a description that mean it names another place.
_OTHER_PLACE_BEFORE = re.compile(r"\b(near|opposite|opp|beside|behind|next to|close to|attached to|affiliated|"
                                 r"associated|tie[- ]?up|refer\w*|at|in|from|with|visit\w*|to)\b[^.,;]{0,25}$", re.I)


def name_says_hospital(name: Optional[str]) -> bool:
    return bool(name) and bool(_HOSPITAL_NAME.search(name))


def description_says_hospital(description: Optional[str]) -> bool:
    """True when the description calls the record a hospital or nursing home
    ("Saiorthocare Hospitals Pvt Ltd", "Hospital and Medical Care"), not when it
    names one nearby ("opposite Janapriya Hospital") or elsewhere."""
    if not isinstance(description, str):
        return False
    for m in re.finditer(r"\b(hospitals?|nursing home)\b", description, re.I):
        if not _OTHER_PLACE_BEFORE.search(description[max(0, m.start() - 40): m.start()]):
            return True
    return False


def implausible_claims(facility_type: Optional[str], name: Optional[str], n_beds,
                       grades: Mapping[str, int], description: Optional[str] = None,
                       use_beds: bool = USE_BEDS) -> list[dict]:
    """``implausible_claim`` flags for one record: one per service line.

    ``type_cannot_offer``: a hospital-only line graded 1 or higher on a record
    of a non-hospital type, unless the name or the description says it is a
    hospital. ``few_beds`` (only with ``use_beds``): ``icu``,
    ``cardiac_surgery`` or ``neurosurgery`` graded 1 or higher with ``n_beds``
    present and below ``MIN_BEDS`` (0 beds is read as missing). Both are
    major: blocking for that line.
    """
    out = []
    ft = facility_type if isinstance(facility_type, str) else None
    beds = None if n_beds is None or pd.isna(n_beds) else int(n_beds)
    for line in HOSPITAL_ONLY_LINES:
        g = int(grades.get(line, 0) or 0)
        if g < 1:
            continue
        signals, reasons, quote = [], [], None
        if ft in NON_HOSPITAL_TYPES and not name_says_hospital(name) and not description_says_hospital(description):
            signals.append("type_cannot_offer")
            reasons.append(f"The record is listed as a {ft}, which would not normally offer "
                           f"{LINE_PLAIN[line]}; the claim may come from another facility or a referral.")
            quote = ft
        if use_beds and line in BED_LINES and beds is not None and 0 < beds < MIN_BEDS:
            signals.append("few_beds")
            reasons.append(f"The record lists {beds} beds, fewer than {LINE_PLAIN[line]} would normally need.")
            quote = quote or f"{beds} beds"
        if signals:
            out.append({"flag": "implausible_claim", "service_line": line, "severity": "major",
                        "signal": "+".join(signals), "reason": " ".join(reasons), "quote": quote})
    return out


# ---------------------------------------------------------------------------
# 3. Likely duplicates
# ---------------------------------------------------------------------------

#: Pairs closer than this may be the same facility.
DUP_KM = 1.0
#: Minimum distinctive-name similarity (0 to 100) for a pair within ``DUP_KM``.
DUP_NAME_SCORE = 90.0
#: At or above this score a name match stands on its own. Below it (a near
#: spelling, "Shiva" and "Shivay"), the two records must not give different
#: official websites; the words-inside rule of ``dup_name_score`` only applies
#: when they give the same one. Set after the WP11 fix-round review of every
#: near_same_name pair scoring under 100 (``reports/trust_checks.md``).
DUP_SURE_SCORE = 95.0
#: Honorific and filler words dropped before comparing names.
_HONORIFIC = frozenset({"sri", "shri", "shree", "smt", "dr", "drs", "the", "st", "m", "s"})


def distinctive_name(name: Optional[str], place_words: Iterable[str] = ()) -> str:
    """The words of a name that tell it apart: address suffix, generic words,
    honorifics and place names removed, sorted."""
    if not name:
        return ""
    places = {p.lower() for p in place_words}
    core = _strip_place_suffix(name)
    words = [w for w in _name_words(core)
             if w not in GENERIC_NAME_WORDS and w not in _HONORIFIC and w not in places and len(w) > 1]
    return " ".join(sorted(set(words)))


#: Specialty words that tell facility kinds apart. Two records with the same
#: distinctive name but different kinds ("Divine Dental" and "Divine Nursing
#: Home") are different facilities.
_KINDS = {
    "dental": r"dental|dentist|dentistry|tooth|teeth|orthodont|implant", "eye": r"\beyes?\b|netralaya|vision|retina|lasik",
    "fertility": r"\bivf\b|fertility|test tube", "diagnostic": r"diagnostic|\blabs?\b|laborator|patholog|imaging|scan|x[- ]?ray|ultrasound|sonograph",
    "ayush": r"ayurved|homoeo|homeo|siddha|unani|panchakarma", "ent": r"\bent\b|hearing|speech",
    "ortho": r"ortho(?!dont)|spine|joint|fracture", "skin": r"skin|derma|hair|cosmetic|aesthetic",
    "child": r"child|paediatric|pediatric|kids|baby|fetal|neonat", "cancer": r"cancer|onco",
    "heart": r"heart|cardi", "piles": r"piles|procto", "physio": r"physio|rehab",
    "college": r"college|institute of medical",
}


def name_kinds(name: Optional[str]) -> frozenset[str]:
    """Kinds of facility a name says it is (see ``_KINDS``)."""
    if not name:
        return frozenset()
    n = name.lower()
    return frozenset(k for k, pat in _KINDS.items() if re.search(pat, n))


def dup_name_score(a: str, b: str, subset: bool = True) -> float:
    """Similarity (0 to 100) of two ``distinctive_name`` values; 0 when either is empty.

    Best of plain ratio, ratio without spaces ("shriram" vs "shree ram"), and,
    when ``subset``, 90 when one name's words are all in the other's and it has
    a word of 4+ letters ("charak" and "charak research").
    """
    if not a or not b:
        return 0.0
    s = max(fuzz.ratio(a, b), fuzz.ratio(a.replace(" ", ""), b.replace(" ", "")),
            fuzz.token_sort_ratio(a, b))
    ta, tb = set(a.split()), set(b.split())
    short = ta if len(ta) <= len(tb) else tb
    if subset and (ta <= tb or tb <= ta) and any(len(w) >= 4 for w in short):
        s = max(s, 90.0)
    return float(s)


def _sites_disagree(x, y) -> bool:
    return isinstance(x, str) and isinstance(y, str) and x != y


def find_duplicates(df: pd.DataFrame, place_words: Iterable[str] = (),
                    km: float = DUP_KM, min_score: float = DUP_NAME_SCORE,
                    sure_score: float = DUP_SURE_SCORE, strict: bool = True) -> pd.DataFrame:
    """Likely duplicate pairs.

    ``df`` columns: ``facility_id``, ``name``, ``lat``, ``lon`` (the location to
    use; NaN skipped), ``pin``, ``site`` (``website_key``), ``rank_key`` (higher
    is kept). ``place_words``: lowercased place words dropped from names.

    Pairs within ``km`` with ``dup_name_score >= min_score`` and the same
    ``name_kinds`` (or the same site) get signal ``near_same_name``. With
    ``strict`` (the default), a pair scoring under ``sure_score`` also needs
    sites that do not disagree, and the words-inside rule of
    ``dup_name_score`` counts only for the same site. ``strict=False`` gives
    the looser round-1/2 rule, kept for the review. Pairs with the same site
    and PIN get ``same_site_and_pin`` (both, joined by "+", when both hold).
    """
    from sklearn.neighbors import BallTree

    d = df.reset_index(drop=True)
    places = {p.lower() for p in place_words}
    dn = [distinctive_name(n, places) for n in d["name"]]
    kinds = [name_kinds(_strip_place_suffix(n)) if isinstance(n, str) else frozenset() for n in d["name"]]
    site = d["site"].tolist()
    pairs: dict[tuple[int, int], dict] = {}
    ok = d["lat"].notna() & d["lon"].notna()
    pos = np.flatnonzero(ok.to_numpy())
    if len(pos):
        xy = np.radians(d.loc[ok, ["lat", "lon"]].to_numpy(float))
        tree = BallTree(xy, metric="haversine")
        ind, dist = tree.query_radius(xy, r=km / geo.EARTH_RADIUS_KM, return_distance=True)
        for i, (js, ds) in enumerate(zip(ind, dist)):
            a = pos[i]
            for j, dd in zip(js, ds):
                b = pos[j]
                if b <= a:
                    continue
                same_site = isinstance(site[a], str) and site[a] == site[b]
                s = dup_name_score(dn[a], dn[b], subset=same_site or not strict)
                if strict and s < sure_score and _sites_disagree(site[a], site[b]):
                    continue
                if s >= min_score and (kinds[a] == kinds[b] or same_site):
                    pairs[(a, b)] = {"distance_km": float(dd * geo.EARTH_RADIUS_KM), "name_score": s,
                                     "signals": ["near_same_name"]}
    site_pin = d[d["site"].notna() & d["pin"].notna()].groupby(["site", "pin"]).groups  # labels = positions
    for _, idx in site_pin.items():
        idx = sorted(int(i) for i in idx)
        for x in range(len(idx)):
            for y in range(x + 1, len(idx)):
                a, b = idx[x], idx[y]
                rec = pairs.get((a, b))
                if rec is None:
                    dkm = geo.haversine_km(d.at[a, "lat"], d.at[a, "lon"], d.at[b, "lat"], d.at[b, "lon"])
                    rec = {"distance_km": float(dkm) if not np.isnan(dkm) else np.nan,
                           "name_score": dup_name_score(dn[a], dn[b]), "signals": []}
                    pairs[(a, b)] = rec
                rec["signals"].append("same_site_and_pin")
    rows = []
    for (a, b), rec in pairs.items():
        ia, ib = d.at[a, "facility_id"], d.at[b, "facility_id"]
        if ib < ia:
            ia, ib = ib, ia
        ka = d.loc[d["facility_id"] == ia, "rank_key"].iloc[0]
        kb = d.loc[d["facility_id"] == ib, "rank_key"].iloc[0]
        keep = ia if ka >= kb else ib
        rows.append({"facility_id_a": ia, "facility_id_b": ib, "distance_km": rec["distance_km"],
                     "name_score": rec["name_score"], "signal": "+".join(rec["signals"]), "keep": keep})
    out = pd.DataFrame(rows, columns=DUP_COLUMNS)
    return out.sort_values(["facility_id_a", "facility_id_b"]).reset_index(drop=True)
