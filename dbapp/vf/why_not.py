"""'Why not the nearer ones?' and the gate funnel (D044 item 2).

Two read-only views over a ranked answer, built only from ``vf.rank``'s public
index and answer shapes, so the ranker, server and UI need a few lines each to
use them (see the integration note in the WP5 result):

* ``why_not_nearer(index, service_line, resolution, answer_dict, k=5)``: the
  nearest records that are closer than the first card but were not
  recommended, each with the reason in the record's own terms. Claim-only
  records name what the record lists and say that we found no fact naming the
  service; a record whose fact uses the service's words but was not counted
  quotes that fact; flagged records quote the flag; a public hospital whose
  record is silent gets a neutral line. Every line is shown grey, never struck
  through. Records at an approximate position come after the ones whose
  position is known, and are counted apart in the heading.
* ``funnel(index, service_line, resolution)``: the ranker's gates as ordered
  steps (all records, usable location, claim the service within reach,
  recommendable, within radius, shown), each with its count and the records
  that dropped there, grouped by reason, nearest first.

Wording rules (CLAUDE.md, ``reports/standout_brainstorm.md`` "What not to do"):
no line says a named facility "cannot" or "does not have" anything. An
absence is never asserted from the grade alone: before saying that no fact
names the service, the record's fact texts are searched with the tag patterns
plus spelling variants (``literal_hits``). A hit is quoted, with "our rules did
not count it as evidence", or quoted alone when the fact is a negation. With no
hit the line says "we found no fact naming ...". Fact counts are distinct
normalised fact texts. A record on a shared or approximate point gets
``distance_label = "position approximate"``, never "0.0 km". Every string is a
template filled from the record, a quoted fact, or a WP11 flag (D030); no LLM
text. ``mask_names=True`` (demo mode) replaces the name of every record that is
not a card with its type and city.

Typical use::

    from vf import rank, locate, why_not
    idx = rank.load_index(bundle)
    res = locate.resolve("226001")
    ans = rank.rank(idx, "emoc", res).to_dict()
    why_not.why_not_strip(idx, "emoc", res, ans)
    why_not.funnel(idx, "emoc", res, answer_dict=ans)
"""
from __future__ import annotations

import math
import re
from typing import Any, Mapping, Optional

import numpy as np

from . import rank as _rank
from . import recipes
from . import services as S
from .geo import haversine_km

WHY_NOT_VERSION = "why_not_v2"

#: Label for a record whose map point is shared or approximate (D046).
APPROX_LABEL = "position approximate"
#: A record located more loosely than this (km) has an approximate position.
APPROX_PRECISION_KM = 2.0
#: Origin precision (km) above which distances are given as "about N km".
ORIGIN_FUZZY_KM = float(getattr(S, "ORIGIN_FUZZY_KM", 2.0))
#: Default cap on ids listed per drop reason in the funnel (counts are always complete).
FUNNEL_MAX_IDS = 50
#: Facility types that count as a hospital for the neutral public line.
HOSPITAL_TYPES = frozenset({"hospital", "nursing_home"})
#: Fact fields counted as the record's facts (the fields the grade rules read).
FACT_FIELDS = ("capability", "procedure", "equipment")
#: Longest quote put inside a sentence (the full text stays in ``quote_or_absence``).
QUOTE_MAX = 140

#: The location note ``vf.services`` gives a record moved to its PIN's centre.
_MOVED_NOTE = "centre of its PIN code area"
#: When ``operator_type`` is missing, a name that says government care.
_PUBLIC_NAME = re.compile(
    r"\b(govt|government|district hospital|civil hospital|sub[- ]?district hospital|primary health|"
    r"community health|phc|chc|esic|railway hospital|zila|jila)\b", re.I)

#: Spelling variants searched on top of each supported tag's own pattern, so a
#: misspelt fact ("Caesarian Section") is found before an absence is claimed.
LITERAL_EXTRA = {
    "csection": r"caesar|cesar|cesear|ceasar|ceaser|\bc[- ./]?sec(tion)?s?\b|\blscs\b|\bvbac\b",
    "dialysis": r"dial[iy]s[ie]s|dialy[sz]",
    "icu": r"\bi\.c\.u\b|\bicus\b|intensive care",
}

#: Indian states and union territories, for dropping them from a merged record's place list.
_STATES = frozenset(s.lower() for s in (
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh", "Goa", "Gujarat", "Haryana",
    "Himachal Pradesh", "Jharkhand", "Karnataka", "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur",
    "Meghalaya", "Mizoram", "Nagaland", "Odisha", "Orissa", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu",
    "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand", "Uttaranchal", "West Bengal",
    "Andaman and Nicobar Islands", "Chandigarh", "Dadra and Nagar Haveli", "Daman and Diu", "Delhi",
    "New Delhi", "Jammu and Kashmir", "Ladakh", "Lakshadweep", "Puducherry", "Pondicherry", "India", "UP"))

#: Plain names for specialty codes where splitting the camel case reads badly.
_SPECIALTY_PLAIN = {
    "gynecologyAndObstetrics": "obstetrics and gynaecology",
    "obstetricsAndMaternityCare": "obstetrics and maternity care",
    "maternalFetalMedicineOrPerinatology": "maternal-fetal medicine",
    "neonatologyPerinatalMedicine": "neonatology",
    "orthopedicSurgery": "orthopaedic surgery",
    "burnAndTraumaPlasticSurgery": "burns and trauma plastic surgery",
}
#: Plain facility types for a masked name ("Clinic in Kanpur (name hidden)").
_TYPE_PLAIN = {"hospital": "Hospital", "clinic": "Clinic", "nursing_home": "Nursing home", "dentist": "Dental clinic",
               "doctor": "Doctor's practice", "pharmacy": "Pharmacy"}

_CAMEL = re.compile(r"(?<=[a-z])(?=[A-Z])")


def specialty_plain(code: str) -> str:
    """``gynecologyAndObstetrics`` -> ``obstetrics and gynaecology``."""
    return _SPECIALTY_PLAIN.get(code) or _CAMEL.sub(" ", str(code)).lower()


def label_lc(service_line: str) -> str:
    """The service's display name for use mid-sentence: "emergency C-section and
    high-risk delivery", "MRI scan"."""
    lab = S.label(service_line)
    return lab if lab[1:2].isupper() else lab[:1].lower() + lab[1:]


def _join(words: list) -> str:
    words = [w for w in words if w]
    if len(words) <= 1:
        return "".join(words)
    return ", ".join(words[:-1]) + " and " + words[-1]


def _norm(text) -> str:
    return recipes.normalize_text(str(text or "")).strip(" .;,")


# ---------------------------------------------------------------------------
# Per-index lookups, computed once and kept on the index object
# ---------------------------------------------------------------------------

class _Lookups:
    """Specialties, facts per facility (distinct normalised texts), and which
    records sit on a shared or approximate point. Works for a WP12 bundle index
    (facts come from the keyword index postings) and a local index (silver facts)."""

    def __init__(self, idx: "_rank.Index"):
        self.idx = idx
        n = len(idx.fac)
        fac = idx.fac
        # approximate positions: moved to a PIN centre, loosely located, or on a point another record shares
        prec = np.asarray(idx.precision_km, dtype=float)
        notes = fac["location_note"].fillna("").astype(str) if "location_note" in fac else None
        moved = notes.str.contains(_MOVED_NOTE, regex=False).to_numpy() if notes is not None else np.zeros(n, bool)
        loose = np.nan_to_num(prec, nan=0.0) > APPROX_PRECISION_KM
        usable = ~np.isnan(idx.lat) & ~np.isnan(idx.lon)
        shared = np.zeros(n, dtype=bool)
        if usable.any():
            pts = np.round(np.c_[idx.lat[usable], idx.lon[usable]], 6)
            _, inv, cnt = np.unique(pts, axis=0, return_inverse=True, return_counts=True)
            shared[np.flatnonzero(usable)] = cnt[inv.ravel()] > 1
        self.moved = moved & usable
        self.shared = shared
        self.approx = usable & (moved | loose | shared)

        self._spec: Optional[dict] = None
        self._rows: Optional[dict] = None  # pos -> row indices into the fact arrays
        self._known = False  # facts are loaded at all
        self._distinct: dict = {}  # pos -> [(text, field, fact_id)], one per normalised text

    # specialties -----------------------------------------------------------
    def specialties(self, pos: int) -> list:
        fac = self.idx.fac
        if "specialties" in fac.columns:
            return [str(s) for s in _rank._as_list(fac["specialties"].iat[pos])]
        if self._spec is None:
            self._spec = {}
            kw = self.idx.keyword
            if kw is not None:
                sp = kw.postings[kw.postings["field"] == "specialties"]
                texts = kw.docs["text"].to_numpy(object)
                for fid, doc in zip(sp["facility_id"].to_numpy(object), sp["doc_id"].to_numpy(np.int64)):
                    self._spec.setdefault(fid, []).append(str(texts[doc]))
        return self._spec.get(fac["facility_id"].iat[pos], [])

    # facts -----------------------------------------------------------------
    def _build_facts(self):
        self._rows = {}
        facts = self.idx.facts_for_keyword
        kw = self.idx.keyword
        if facts is not None:
            f = facts[facts["field"].isin(FACT_FIELDS)]
            self._text = f["text"].to_numpy(object)
        elif kw is not None:
            post = kw.postings
            f = post[post["field"].isin(FACT_FIELDS).to_numpy()]
            self._text = kw.docs["text"].to_numpy(object)[f["doc_id"].to_numpy(np.int64)]
        else:
            return
        self._known = True
        self._field = f["field"].to_numpy(object)
        self._fact_id = f["fact_id"].to_numpy(object) if "fact_id" in f.columns else np.full(len(f), None, object)
        for fid, rows in f.groupby("facility_id").indices.items():
            p = self.idx.id_to_pos.get(fid)
            if p is not None:
                self._rows[p] = rows

    def facts(self, pos: int) -> list:
        """``[(text, field, fact_id)]``: the record's facts, one per distinct
        normalised text (data trap 5: texts repeat within a record), in order."""
        if self._rows is None:
            self._build_facts()
        got = self._distinct.get(pos)
        if got is None:
            got, seen = [], set()
            for r in self._rows.get(pos, ()):
                t = str(self._text[r])
                key = _norm(t)
                if not key or key in seen:
                    continue
                seen.add(key)
                fi = self._fact_id[r]
                got.append((t, self._field[r], None if fi is None else str(fi)))
            self._distinct[pos] = got
        return got

    def n_facts(self, pos: int) -> Optional[int]:
        """Distinct normalised fact texts; None when no facts are loaded."""
        if self._rows is None:
            self._build_facts()
        return len(self.facts(pos)) if self._known else None

    def n_fact_rows(self, pos: int) -> Optional[int]:
        """Fact rows as stored, repeats included; None when no facts are loaded."""
        if self._rows is None:
            self._build_facts()
        return len(self._rows.get(pos, ())) if self._known else None

    def fact_texts(self, pos: int) -> list:
        return [t for t, _, _ in self.facts(pos)]


def _lookups(idx) -> _Lookups:
    lk = getattr(idx, "_why_not_lookups", None)
    if lk is None or lk.idx is not idx:
        lk = _Lookups(idx)
        try:
            setattr(idx, "_why_not_lookups", lk)
        except Exception:  # an index type that refuses attributes: recompute each call
            pass
    return lk


# ---------------------------------------------------------------------------
# Literal search of a record's facts, before any absence is stated
# ---------------------------------------------------------------------------

_LITERAL_RX: dict = {}


def literal_pattern(line: str) -> Optional[re.Pattern]:
    """The words that name ``line``'s supported requirement: the tag patterns of
    its supported group(s) (pattern and name pattern, no masks or vetoes), plus
    ``LITERAL_EXTRA`` spelling variants. Runs on ``recipes.normalize_text``."""
    if line in _LITERAL_RX:
        return _LITERAL_RX[line]
    rule = recipes.GRADE_RULES.get(line)
    tags = sorted(set().union(*rule.supported)) if rule is not None and rule.supported else []
    parts = []
    for t in tags:
        tr = recipes.NEW_TAG_RULES.get(t)
        if tr is not None:
            parts += [p for p in (tr.pattern, tr.name_pattern) if p]
        elif t in recipes.TAG_PATTERNS:
            parts.append(recipes.TAG_PATTERNS[t])
        if t in LITERAL_EXTRA:
            parts.append(LITERAL_EXTRA[t])
    rx = re.compile("|".join(f"(?:{p})" for p in parts)) if parts else None
    _LITERAL_RX[line] = rx
    return rx


def literal_hits(idx, line: str, pos: int) -> list:
    """The record's counted facts whose text uses the words of ``line``'s
    supported requirement: ``[{text, field, fact_id, negated}]`` in fact order.
    ``negated`` is ``recipes.is_negated`` (a fact that says the service is not
    available, not offered, not specified, and so on)."""
    rx = literal_pattern(line)
    if rx is None:
        return []
    out = []
    for text, field, fact_id in _lookups(idx).facts(pos):
        if rx.search(recipes.normalize_text(text)):
            out.append({"text": text, "field": field, "fact_id": fact_id, "negated": recipes.is_negated(text)})
    return out


def _q(text: str) -> str:
    """A fact as a quote inside a sentence, shortened at a word boundary."""
    t = " ".join(str(text).split()).rstrip(" .")
    if len(t) > QUOTE_MAX:
        t = t[: QUOTE_MAX - 3].rsplit(" ", 1)[0].rstrip(" ,;:") + "..."
    return f'"{t}"'


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _get(obj, name, default=None):
    if obj is None:
        return default
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _origin(resolution) -> tuple[Optional[float], Optional[float], Optional[float], str]:
    lat, lon = _get(resolution, "lat"), _get(resolution, "lon")
    prec = _get(resolution, "precision_km")
    bad = lambda v: v is None or (isinstance(v, float) and math.isnan(v))  # noqa: E731
    status = _get(resolution, "status", "ok") or "ok"
    if bad(lat) or bad(lon) or status in ("ambiguous", "not_found", "outside_india"):
        return None, None, None, status
    return float(lat), float(lon), (None if bad(prec) else float(prec)), status


def _is_public(idx, pos: int) -> bool:
    op = _rank._str_or_none(idx.fac["operator_type"].iat[pos]) if "operator_type" in idx.fac else None
    if op is not None:
        return op.lower() in ("public", "government")
    return bool(_PUBLIC_NAME.search(str(idx.fac["name"].iat[pos] or "")))


def _is_hospital(idx, pos: int) -> bool:
    t = _rank._str_or_none(idx.fac["facility_type"].iat[pos]) if "facility_type" in idx.fac else None
    if t is not None:
        return t.lower() in HOSPITAL_TYPES
    return bool(re.search(r"\bhospital|nursing home|medical college\b", str(idx.fac["name"].iat[pos] or ""), re.I))


def masked_name(idx, pos: int) -> str:
    """Demo-mode name: "Clinic in Kanpur (name hidden)"."""
    t = _rank._str_or_none(idx.fac["facility_type"].iat[pos]) if "facility_type" in idx.fac else None
    kind = _TYPE_PLAIN.get((t or "").lower(), "Facility")
    city = _rank._str_or_none(idx.fac["city_raw"].iat[pos]) if "city_raw" in idx.fac else None
    return f"{kind} in {city} (name hidden)" if city else f"{kind} (name hidden)"


def _scrub(obj, name: str):
    """Replace ``name`` (case ignored) in every string of ``obj`` but ids."""
    if not name or len(name) < 3:
        return obj
    rx = re.compile(re.escape(name), re.I)

    def go(o, key=None):
        if isinstance(o, str):
            return o if key in ("facility_id", "fact_id") else rx.sub("[name hidden]", o)
        if isinstance(o, dict):
            return {k: go(v, k) for k, v in o.items()}
        if isinstance(o, list):
            return [go(v) for v in o]
        return o
    return go(obj)


def distance_view(idx, pos: int, km: float, origin_precision_km: Optional[float]) -> dict:
    """``{distance_km, distance_label, position_approximate}`` for one record.
    A shared or approximate point gives ``"position approximate"`` and no
    number to show; a loose origin gives whole km ("about 12 km")."""
    lk = _lookups(idx)
    if km is None or (isinstance(km, float) and math.isnan(km)):
        return {"distance_km": None, "distance_label": "unknown distance", "position_approximate": False}
    if lk.approx[pos]:
        return {"distance_km": None, "distance_label": APPROX_LABEL, "position_approximate": True,
                "distance_km_to_point": round(float(km), 1)}
    if origin_precision_km is not None and origin_precision_km > ORIGIN_FUZZY_KM:
        r = int(round(km))
        label = "under 1 km" if r < 1 else f"about {r} km"
        return {"distance_km": float(r), "distance_label": label, "position_approximate": False}
    return {"distance_km": round(float(km), 1), "distance_label": f"{km:.1f} km", "position_approximate": False}


def _flag_sentence(f: dict, places: list) -> str:
    """A WP11 flag as a line for the strip, in the record's terms."""
    kind = f.get("flag")
    if kind == "possible_merge" and len(places) >= 2:
        return f"Record flagged as a possible merge of several branches: its facts name {_join(places)}."
    r = _their((f.get("reason") or "").strip())
    if not r:
        r = {"possible_merge": "it may mix facts from more than one facility.",
             "location_conflict": "its location is in doubt.",
             "implausible_claim": "the claim does not fit the kind of facility the record describes."}.get(
            kind, "it carries a blocking warning.")
    if not r.endswith("."):
        r += "."
    return "Record flagged: " + r


def _their(r: str) -> str:
    """A WP11 reason ("The record's map point is ...") for use mid-sentence ("its map point is ...")."""
    r = (r or "").strip()
    if r.startswith("The record's "):
        return "its " + r[len("The record's "):]
    if r.startswith("The record "):
        return "it " + r[len("The record "):]
    if r[:1].isupper() and not r[1:2].isupper():
        return r[0].lower() + r[1:]
    return r


def merge_places(idx, pos: int) -> list:
    """Distinct places the record's own facts give as its location ("Located
    in Bareilly"), excluding states and its own city, in fact order."""
    from . import trust

    own = (_rank._str_or_none(idx.fac["city_raw"].iat[pos]) or "").lower() if "city_raw" in idx.fac else ""
    out, seen = [], set()
    for t in _lookups(idx).fact_texts(pos):
        parts = trust.located_places(t)
        if not parts:
            continue
        p = parts[0].strip().rstrip(".")
        key = p.lower()
        if not p or key in _STATES or key in seen or (own and key == own):
            continue
        seen.add(key)
        out.append(p)
    return out


def _quote(text: Optional[str], field: Optional[str] = None, fact_id: Optional[str] = None) -> Optional[dict]:
    if not text:
        return None
    return {"kind": "quote", "text": str(text), "field": field, "fact_id": fact_id}


def _absence(text: str, n_facts: Optional[int], n_rows: Optional[int] = None) -> dict:
    return {"kind": "absence", "text": text, "n_facts": n_facts, "n_fact_rows": n_rows}


def _facts_phrase(n: Optional[int], req: str) -> str:
    """"we found no fact naming caesarean section among its 97 facts"."""
    if n is None:
        return f"we found no fact in the record naming {req}"
    if n == 0:
        return "the record has no facts beyond its listing"
    if n == 1:
        return f"we found nothing naming {req} in its one fact"
    return f"we found no fact naming {req} among its {n} facts"


def _supported_req(line: str) -> str:
    rule = recipes.GRADE_RULES.get(line)
    if rule is not None and rule.supported_names:
        return rule.supported_names[0]
    return S.label(line).lower()


# ---------------------------------------------------------------------------
# The ranker's gates, recomputed from the index (same rules as vf.rank.rank)
# ---------------------------------------------------------------------------

def _gates(idx, line: str, d: np.ndarray, resolution=None) -> dict:
    """Masks and the scored set exactly as ``rank.rank`` builds them.
    ``scored`` maps each scored position to its tier (0 inside the radius, 1
    beyond); it is empty when nothing recommendable is inside the radius,
    because the ranker then scores nothing. Under score spec v1-final (D048)
    the ranker's own ``gate_masks`` is used: the radius is the window's
    straight-line equivalent for this origin."""
    if hasattr(_rank, "gate_masks"):
        return _rank.gate_masks(idx, line, d, resolution)
    ld = idx.lines[line]
    R = float(S.SERVICES[line].radius_km)
    reach = float(S.REACH_FACTOR) * R
    usable = ~np.isnan(d)
    prob = ld.p is not None
    claim = ((ld.grade >= 1) | (ld.p > 0)) if prob else (ld.grade >= 1)
    rec_mask = (ld.grade >= 2) & ~ld.blocked
    if prob:
        rec_mask &= ld.p >= S.p_threshold(line)
    in_reach = usable & (d <= reach) & claim
    rec = in_reach & rec_mask
    rec_all = np.flatnonzero(rec)
    rec_all = rec_all[np.argsort(d[rec_all], kind="stable")]
    in_R = rec_all[d[rec_all] <= R]  # uncapped, as the ranker's "within radius" gate counts it
    scored = {} if len(in_R) == 0 else {int(p): 0 if d[p] <= R else 1 for p in rec_all[: S.MAX_CANDIDATES]}
    return {"R": R, "reach": reach, "usable": usable, "prob": prob, "claim": claim, "rec_mask": rec_mask,
            "in_reach": in_reach, "rec": rec, "rec_all": rec_all, "in_R": in_R, "scored": scored}


def _dup_reason(idx, pos: int, shown_ids: frozenset, scored: Optional[Mapping[int, int]]) -> Optional[tuple]:
    """Why a recommendable record is not its duplicate group's card, as
    ``(kind, other_pos)``: ``"shown"`` (another listing is a card), ``"kept"``
    (``rank._dedupe`` keeps another listing in its place) or ``"either"`` (a
    listing in the same tier competes and neither is a card). None when the
    record is the group's candidate, so it simply ranked lower."""
    canon = idx.dup_canon.get(pos)
    if canon is None:
        return None
    others = [o for o in [canon] + list(idx.dup_of.get(canon, [])) if o != pos]
    fid = idx.fac["facility_id"]
    shown_o = [o for o in others if fid.iat[o] in shown_ids]
    if shown_o:
        return ("shown", shown_o[0])
    if scored is None:  # no scored set given: assume the preferred record is the group's candidate
        return ("kept", canon) if canon != pos else None
    cand = [o for o in others if o in scored]
    if not cand or pos not in scored:
        return None
    t = scored[pos]
    best = min(scored[o] for o in cand + [pos])
    if t > best:  # a listing inside the radius takes the group's place
        top = [o for o in cand if scored[o] == best]
        return ("kept", canon if canon in top else top[0])
    if pos == canon:
        return None
    if canon in cand and scored[canon] == t:
        return ("kept", canon)
    same = [o for o in cand if scored[o] == t]
    return ("either", same[0]) if same else None


# ---------------------------------------------------------------------------
# Reasons: why one record was not recommended
# ---------------------------------------------------------------------------

def _claim_basis(idx, line: str, pos: int) -> tuple[str, Optional[dict]]:
    """What a grade-1 record lists or says: ("lists obstetrics and gynaecology
    as a specialty", quote) or ("mentions maternity or delivery service", quote)."""
    ld = idx.lines[line]
    ev = ld.evidence[pos]
    if ev and ev[0].get("quote") and ev[0].get("field") != "specialties":
        return ("mentions " + (ev[0].get("requirement") or "the service"),
                _quote(ev[0]["quote"], ev[0].get("field"), ev[0].get("fact_id")))
    rule = recipes.GRADE_RULES.get(line)
    listed = [s for s in _lookups(idx).specialties(pos) if rule is not None and s in rule.claim_specialties]
    if listed:
        plain = [specialty_plain(s) for s in listed]
        names = (", ".join(plain[:-1]) + ", and " + plain[-1]) if len(plain) > 1 and any(
            " and " in x for x in plain) else _join(plain)
        return (f"lists {names} as a specialty" if len(listed) == 1 else f"lists {names} as specialties",
                {"kind": "quote", "text": listed[0], "field": "specialties", "fact_id": None})
    return ("claims the service", None)


def _absence_reason(idx, line: str, pos: int, g: int, public: bool, major: list) -> Optional[dict]:
    """The first reason for a record of grade 0 or 1. The record's facts are
    searched before any absence is stated (``literal_hits``)."""
    lk = _lookups(idx)
    req = _supported_req(line)
    n, n_rows = lk.n_facts(pos), lk.n_fact_rows(pos)
    kind = "hospital" if _is_hospital(idx, pos) else "facility"
    claim_text, claim_quote = _claim_basis(idx, line, pos) if g == 1 else (None, None)
    neutral = public and not (g == 1 and major)
    tone = "neutral" if neutral else "grey"
    if neutral:
        lead = f"Public {kind}: its record {claim_text}" if claim_text else f"Public {kind}"
    else:
        lead = (claim_text[0].upper() + claim_text[1:]) if claim_text else ""

    hits = literal_hits(idx, line, pos)
    pos_hits = [h for h in hits if not h["negated"]]
    neg_hits = [h for h in hits if h["negated"]]
    if pos_hits or neg_hits:
        h = (pos_hits or neg_hits)[0]
        if claim_text:
            joiner = lead + ", and a fact reads "
        elif neutral:
            joiner = lead + ": a fact reads "
        else:
            joiner = "A fact reads "
        if pos_hits:
            more = len(pos_hits) - 1
            text = (f"{joiner}{_q(h['text'])}, but our rules did not count it as evidence of {req}."
                    + (f" {more} more fact{'s use' if more != 1 else ' uses'} the same words." if more else ""))
            code = "fact_not_counted"
        else:
            text = f"{joiner}{_q(h['text'])}."
            code = "fact_states_absence"
        if neutral:
            text += " Ask the hospital."
        q = _quote(h["text"], h["field"], h["fact_id"])
        q["negated"] = bool(h["negated"])
        q["n_hits"] = len(hits)
        return {"reason_code": code, "reason_plain": text, "quote_or_absence": q, "claim_quote": claim_quote,
                "tone": tone}

    absence = _facts_phrase(n, req)
    qa = _absence(f"We found no fact in the record naming {req}.", n, n_rows)
    if g == 1 and neutral:
        return {"reason_code": "public_record_silent",
                "reason_plain": f"{lead}, but {absence}. That silence is not evidence either way; ask the hospital.",
                "quote_or_absence": qa, "claim_quote": claim_quote, "tone": "neutral"}
    if g == 1:
        return {"reason_code": "claim_only", "reason_plain": f"{lead}, but {absence}.",
                "quote_or_absence": qa, "claim_quote": claim_quote, "tone": "grey"}
    if not public:  # grade 0 with a probability only (probability mode)
        return {"reason_code": "no_mention",
                "reason_plain": f"The record lists no specialty for it, and {absence}.",
                "quote_or_absence": qa, "claim_quote": None, "tone": "grey"}
    return {"reason_code": "public_record_silent",  # grade 0 public hospital (see _concerns)
            "reason_plain": (f"Public {kind}: {absence}. Public hospitals are often thinly described online, "
                             "so this silence is not evidence either way."),
            "quote_or_absence": qa, "claim_quote": None, "tone": "neutral"}


def record_reasons(idx, line: str, pos: int, *, shown_ids: frozenset = frozenset(),
                   shown_names: Optional[Mapping[int, str]] = None,
                   scored: Optional[Mapping[int, int]] = None, mask_names: bool = False) -> list:
    """Every reason this record is not among the cards, most basic first:
    ``[{reason_code, reason_plain, quote_or_absence, tone}]``. ``tone`` is
    ``"neutral"`` for a public hospital's absence line, else ``"grey"``. Empty
    when the record was shown or does not concern the line. ``scored`` (pos ->
    tier, from ``_gates``) lets duplicate listings and unscored records be told
    apart from records that ranked lower; without it the group's preferred
    record is taken as its candidate."""
    ld = idx.lines[line]
    g = int(ld.grade[pos])
    fid = idx.fac["facility_id"].iat[pos]
    if fid in shown_ids:
        return []
    req = _supported_req(line)
    public = _is_public(idx, pos)
    major = list(ld.major_flags.get(pos, []))
    minor = list(ld.minor_flags.get(pos, []))
    prob = ld.p is not None
    rec = g >= 2 and not bool(ld.blocked[pos]) and (not prob or float(ld.p[pos]) >= S.p_threshold(line))
    out = []

    # 1. the record names nothing for this service beyond a claim, or nothing at all
    if g <= 1:
        r = _absence_reason(idx, line, pos, g, public, major)
        if r is not None:
            out.append(r)

    # 2. blocking warnings (WP11 major flags), line-specific first
    major.sort(key=lambda f: (f.get("service_line") is None, {"implausible_claim": 0, "possible_merge": 1}.get(
        f.get("flag"), 2)))
    seen_flags = set()
    for f in major:
        key = (f.get("flag"), f.get("signal"))
        if key in seen_flags:
            continue
        seen_flags.add(key)
        places = merge_places(idx, pos) if f.get("flag") == "possible_merge" else []
        out.append({"reason_code": f"flag_{f.get('flag')}",
                    "reason_plain": _flag_sentence(f, places),
                    "quote_or_absence": _quote(f.get("quote"), "flag", None),
                    "places_named": places or None, "tone": "grey"})

    # 3. probability mode: supported, but the estimated chance is under the bar
    if prob and g >= 2 and not ld.blocked[pos] and float(ld.p[pos]) < S.p_threshold(line):
        out.append({"reason_code": "below_threshold",
                    "reason_plain": (f"Names {req}, but the estimated chance an independent source confirms it "
                                     f"({float(ld.p[pos]):.0%}) is below the bar of {S.p_threshold(line):.0%}."),
                    "quote_or_absence": _quote(ld.evidence[pos][0]["quote"], ld.evidence[pos][0].get("field"),
                                               ld.evidence[pos][0].get("fact_id")) if ld.evidence[pos] else None,
                    "tone": "grey"})

    # 4. recommendable but not on the cards: another listing of a card, not scored, or ranked lower
    if rec:
        ev = ld.evidence[pos]
        q = _quote(ev[0]["quote"], ev[0].get("field"), ev[0].get("fact_id")) if ev else None
        dup = _dup_reason(idx, pos, shown_ids, scored)

        def name_of(o: int) -> str:
            if (shown_names or {}).get(o):
                return str(shown_names[o])
            return masked_name(idx, o) if mask_names else str(idx.fac["name"].iat[o])

        if dup is not None and dup[0] == "shown":
            out.append({"reason_code": "duplicate_listing",
                        "reason_plain": f"Another listing of {name_of(dup[1])}, which is shown once, as its card.",
                        "quote_or_absence": q, "tone": "grey"})
        elif dup is not None and dup[0] == "kept":
            out.append({"reason_code": "duplicate_listing",
                        "reason_plain": f"Likely another listing of {name_of(dup[1])}; the ranker keeps one record "
                                        "per facility.",
                        "quote_or_absence": q, "tone": "grey"})
        elif dup is not None:
            out.append({"reason_code": "duplicate_listing",
                        "reason_plain": f"Likely the same facility as {name_of(dup[1])}, which is listed too; the "
                                        "ranker keeps one record per facility, and neither listing scored high "
                                        "enough to be shown.",
                        "quote_or_absence": q, "tone": "grey"})
        elif scored is not None and pos not in scored:
            out.append({"reason_code": "not_scored",
                        "reason_plain": f"Recommendable, but not scored: the ranker scores only the "
                                        f"{S.MAX_CANDIDATES} nearest recommendable records.",
                        "quote_or_absence": q, "tone": "grey"})
        else:
            bits = []
            if g == 2:
                miss = ld.missing[pos]
                bits.append(f"its record names {req}" + (f", but no fact names {miss[0]}" if miss else ""))
            else:
                bits.append(f"its record names {req} and what it needs")
            if str(ld.strength[pos]) == "thin":
                bits.append("the support is thin")
            for f in minor[:1]:
                if f.get("reason"):
                    bits.append("it carries a warning (" + _their(f["reason"]).rstrip(".") + ")")
            out.append({"reason_code": "ranked_lower",
                        "reason_plain": "Recommendable, but it scored below the cards shown: " + "; ".join(bits) + ".",
                        "quote_or_absence": q, "tone": "grey"})
    return out


def _concerns(idx, line: str, pos: int) -> bool:
    """A record the strip should explain: it claims the service (or has a
    probability for it), or it is a public hospital whose record is silent."""
    ld = idx.lines[line]
    if int(ld.grade[pos]) >= 1 or (ld.p is not None and float(ld.p[pos]) > 0):
        return True
    return _is_public(idx, pos) and _is_hospital(idx, pos)


def _shown(answer_dict: Optional[Mapping]) -> tuple[list, Optional[Mapping]]:
    """Cards the answer recommends (results, then any further-away line or
    beyond-radius card), and the first of them."""
    if not answer_dict:
        return [], None
    cards = list(answer_dict.get("results") or [])
    for key in ("further_away", "beyond_radius_cards"):
        cards += [c for c in (answer_dict.get(key) or []) if isinstance(c, Mapping)]
    br = answer_dict.get("beyond_radius")
    if isinstance(br, Mapping):
        cards.append(br)
    first = cards[0] if cards else None
    return cards, first


def _plural(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def why_not_nearer(index, service_line: str, resolution, answer_dict: Optional[Mapping], k: int = 5,
                   mask_names: bool = False) -> list:
    """The ``k`` nearest records closer than the first card that were not
    recommended: those with a known position first, nearest first, then those
    at an approximate position. Each item::

        {facility_id, name, name_masked, operator, type, lat, lon,
         distance_km, distance_label, position_approximate,
         reason_code, reason_plain, quote_or_absence, claim_quote, tone,
         also: [{reason_code, reason_plain, quote_or_absence}]}

    ``distance_km`` is None when ``position_approximate`` (the label then reads
    "position approximate"). ``tone`` is "neutral" for a public hospital's
    absence line, "grey" otherwise; neither is ever struck through. The first
    card is ``answer_dict["results"][0]``, else its beyond-radius card. With no
    card at all, records within the service radius are listed."""
    return why_not_strip(index, service_line, resolution, answer_dict, k=k, mask_names=mask_names)["items"]


def why_not_strip(index, service_line: str, resolution, answer_dict: Optional[Mapping], k: int = 5,
                  mask_names: bool = False) -> dict:
    """``why_not_nearer`` plus what the strip's heading needs:
    ``{items, n_nearer, n_approx_nearer, first_card: {facility_id, name,
    distance_label} | None, cutoff_km, heading, masked, version}``.
    ``n_nearer`` counts records with a known position closer than the first
    card; ``n_approx_nearer`` those at an approximate position whose map point
    is closer."""
    if service_line not in S.SERVICES:
        raise ValueError(f"unknown service line {service_line!r}")
    empty = {"items": [], "n_nearer": 0, "n_approx_nearer": 0, "first_card": None, "cutoff_km": None, "heading": "",
             "masked": bool(mask_names), "version": WHY_NOT_VERSION}
    if service_line not in index.lines:
        return empty
    lat, lon, oprec, _ = _origin(resolution)
    if lat is None:
        return empty
    d = haversine_km(lat, lon, index.lat, index.lon)
    gt = _gates(index, service_line, d, resolution)
    lk = _lookups(index)
    cards, first = _shown(answer_dict)
    shown_ids = frozenset(str(c.get("facility_id")) for c in cards if c.get("facility_id"))
    shown_names = {index.id_to_pos[c["facility_id"]]: c.get("name") for c in cards
                   if c.get("facility_id") in index.id_to_pos}
    R = gt["R"]
    first_view = None
    if first is not None and first.get("facility_id") in index.id_to_pos:
        fp = index.id_to_pos[first["facility_id"]]
        cutoff = float(d[fp])
        first_view = {"facility_id": first["facility_id"], "name": first.get("name"),
                      **distance_view(index, fp, cutoff, oprec)}
    elif first is not None and first.get("distance_km") is not None:
        cutoff = float(first["distance_km"])
        first_view = {"facility_id": first.get("facility_id"), "name": first.get("name"),
                      "distance_km": cutoff, "distance_label": f"{cutoff:.1f} km", "position_approximate": False}
    else:
        cutoff = R

    near = np.flatnonzero(~np.isnan(d) & (d < cutoff))
    near = near[np.argsort(d[near], kind="stable")]
    precise, approx = [], []
    for p in near:
        p = int(p)
        if not _concerns(index, service_line, p):
            continue
        reasons = record_reasons(index, service_line, p, shown_ids=shown_ids, shown_names=shown_names,
                                 scored=gt["scored"], mask_names=mask_names)
        if reasons:
            (approx if lk.approx[p] else precise).append((p, reasons))
    items = []
    for p, reasons in (precise + approx)[: max(int(k), 0)]:
        top, rest = reasons[0], reasons[1:]
        r = index.fac.iloc[p]
        real = _rank._str_or_none(r.get("name")) or "(no name)"
        it = {
            "facility_id": r["facility_id"],
            "name": masked_name(index, p) if mask_names else real,
            "name_masked": bool(mask_names),
            "operator": _rank._str_or_none(r.get("operator_type")),
            "type": _rank._str_or_none(r.get("facility_type")),
            "lat": float(index.lat[p]), "lon": float(index.lon[p]),
            **distance_view(index, p, float(d[p]), oprec),
            "reason_code": top["reason_code"],
            "reason_plain": top["reason_plain"],
            "quote_or_absence": top.get("quote_or_absence"),
            "claim_quote": top.get("claim_quote"),
            "places_named": top.get("places_named"),
            "tone": top["tone"],
            "also": [{"reason_code": x["reason_code"], "reason_plain": x["reason_plain"],
                      "quote_or_absence": x.get("quote_or_absence")} for x in rest],
        }
        items.append(_scrub(it, real) if mask_names else it)

    n_nearer, n_approx = len(precise), len(approx)
    label = label_lc(service_line)
    claim = _plural(n_nearer, "lists or claims", "list or claim")
    public = _plural(n_nearer, "or is a public hospital", "or are public hospitals")
    approx_tail = (f" {n_approx} more {_plural(n_approx, 'record has', 'records have')} an approximate position "
                   "whose map point is closer." if n_approx else "")
    if first_view is not None:
        nm = first_view["name"]
        if n_nearer:
            heading = (f"Why not the nearer ones? {n_nearer} {_plural(n_nearer, 'record', 'records')} closer than "
                       f"{nm} {claim} {label}, {public}, but {_plural(n_nearer, 'was', 'were')} not recommended."
                       + approx_tail)
        elif n_approx:
            heading = (f"Why not the nearer ones? No record with a known position closer than {nm} lists or claims "
                       f"{label}, or is a public hospital." + approx_tail)
        else:
            heading = f"No record closer than {nm} lists or claims {label}, and no public hospital record is closer."
    else:
        heading = (f"Within {R:.0f} km, {n_nearer} {_plural(n_nearer, 'record', 'records')} {claim} {label}, "
                   f"{public}, but none was recommended." + approx_tail) if (n_nearer or n_approx) else ""
    return {"items": items, "n_nearer": n_nearer, "n_approx_nearer": n_approx, "first_card": first_view,
            "cutoff_km": round(float(cutoff), 2), "heading": heading, "masked": bool(mask_names),
            "version": WHY_NOT_VERSION}


def _n_distinct(idx, pos) -> int:
    """Distinct facilities among ``pos``: one per WP11 duplicate group."""
    return len({idx.dup_canon.get(int(p), -1 - int(p)) for p in pos})


def _id_list(idx, d: np.ndarray, pos: np.ndarray, oprec, max_ids: Optional[int]) -> dict:
    pos = pos[np.argsort(np.nan_to_num(d[pos], nan=np.inf), kind="stable")]
    take = pos if max_ids is None else pos[:max_ids]
    return {"n": int(len(pos)), "ids": [str(idx.fac["facility_id"].iat[int(p)]) for p in take],
            "n_more": int(len(pos) - len(take))}


#: Gates of ``rank``'s ``how_we_decided`` that the funnel checks, and the step each matches.
_EXPECTED_GATES = ("usable location", "within reach", "recommendable", "within radius")


def funnel(index, service_line: str, resolution, *, answer_dict: Optional[Mapping] = None,
           max_ids: Optional[int] = FUNNEL_MAX_IDS, detail_steps: tuple = ("recommendable", "within_radius", "shown"),
           mask_names: bool = False) -> dict:
    """The ranker's gates as ordered steps::

        {service, service_label, radius_km, reach_km, location, status, steps: [
           {step, label, n, n_dropped, dropped: [{reason_code, reason_plain, n, ids, n_more,
                                                  items?: [{facility_id, name, distance_label, reason_plain}]}],
            added?: [...]}],
         consistency: {matches_ranker, mismatches, gates_compared}, version}

    Steps: ``all_records`` -> ``usable_location`` -> ``claim_within_reach`` ->
    ``recommendable`` -> ``within_radius`` -> ``shown``. Counts are complete;
    ``ids`` are nearest first, at most ``max_ids`` per reason (``None``: all).
    Steps in ``detail_steps`` also carry ``items`` with each record's reason.
    ``answer_dict`` is the ``rank`` answer for the same query; without it,
    ``rank`` is called. ``consistency.matches_ranker`` is True when all four
    gate counts in the answer's ``how_we_decided.gates`` match and every card
    is a scored recommendable record, False on any mismatch, and None when
    fewer than four gates could be compared (nothing was checked)."""
    if service_line not in S.SERVICES:
        raise ValueError(f"unknown service line {service_line!r}")
    svc = S.SERVICES[service_line]
    R = float(svc.radius_km)
    reach = float(S.REACH_FACTOR) * R
    n_all = int(len(index.fac))
    out: dict[str, Any] = {"service": service_line, "service_label": svc.label, "radius_km": R, "reach_km": reach,
                           "location": _rank._location_dict(resolution), "masked": bool(mask_names),
                           "version": WHY_NOT_VERSION}
    first_step = {"step": "all_records", "label": "All records in this dataset", "n": n_all, "n_dropped": 0, "dropped": []}
    unchecked = {"matches_ranker": None, "mismatches": [], "gates_compared": []}
    lat, lon, oprec, status = _origin(resolution)
    if lat is None:
        out.update(status="no_location", steps=[first_step],
                   note="No searchable point: pick a place first.", consistency=unchecked)
        return out
    if service_line not in index.lines:
        out.update(status="no_grades", steps=[first_step],
                   note="No recipe grades are loaded for this service; the answer is a keyword search.",
                   consistency=unchecked)
        return out
    if answer_dict is None:
        answer_dict = _rank.rank(index, service_line, resolution).to_dict()
    ld = index.lines[service_line]
    d = haversine_km(lat, lon, index.lat, index.lon)
    gt = _gates(index, service_line, d, resolution)
    R, reach = float(gt["R"]), float(gt["reach"])
    out.update(radius_km=R, reach_km=reach)
    lab = gt.get("labels") or {}
    usable, claim, rec_mask = gt["usable"], gt["claim"], gt["rec_mask"]
    in_reach, rec, rec_all, in_R, scored = gt["in_reach"], gt["rec"], gt["rec_all"], gt["in_R"], gt["scored"]
    prob = gt["prob"]
    cards, _ = _shown(answer_dict)
    results = [c for c in (answer_dict.get("results") or [])]
    shown_ids = [str(c["facility_id"]) for c in cards if c.get("facility_id") in index.id_to_pos]
    shown_set = frozenset(shown_ids)
    shown_pos = np.array([index.id_to_pos[f] for f in shown_ids], dtype=np.int64)
    shown_names = {index.id_to_pos[c["facility_id"]]: c.get("name") for c in cards
                   if c.get("facility_id") in index.id_to_pos}
    label = label_lc(service_line)

    def reasons_of(p: int) -> list:
        return record_reasons(index, service_line, p, shown_ids=shown_set, shown_names=shown_names, scored=scored,
                              mask_names=mask_names)

    def items_for(pos: np.ndarray, step: str, override: Optional[tuple] = None) -> Optional[list]:
        if step not in detail_steps:
            return None
        pos = pos[np.argsort(d[pos], kind="stable")]
        take = pos if max_ids is None else pos[:max_ids]
        rows = []
        for p in take:
            p = int(p)
            rs = [{"reason_code": override[0], "reason_plain": override[1], "tone": "grey"}] if override else reasons_of(p)
            real = str(index.fac["name"].iat[p])
            row = {"facility_id": str(index.fac["facility_id"].iat[p]),
                   "name": masked_name(index, p) if mask_names else real,
                   **distance_view(index, p, float(d[p]), oprec),
                   "reason_code": rs[0]["reason_code"] if rs else None,
                   "reason_plain": rs[0]["reason_plain"] if rs else None,
                   "tone": rs[0]["tone"] if rs else "grey"}
            rows.append(_scrub(row, real) if mask_names else row)
        return rows

    def group(code: str, plain: str, pos: np.ndarray, step: str) -> Optional[dict]:
        if len(pos) == 0:
            return None
        g = {"reason_code": code, "reason_plain": plain, **_id_list(index, d, pos, oprec, max_ids)}
        it = items_for(pos, step, (code, plain) if code in _DISTANCE_CODES else None)
        if it is not None:
            g["items"] = it
        return g

    steps = [first_step]
    # usable location
    no_loc = np.flatnonzero(~usable)
    steps.append({"step": "usable_location", "label": "With a location we can use", "n": int(usable.sum()),
                  "n_dropped": int(len(no_loc)),
                  "dropped": [x for x in [group("no_usable_location",
                                                "No usable location: missing, outside India, or in conflict with "
                                                "its own PIN code with nothing to settle it.", no_loc, "usable_location")] if x]})
    # claim within reach
    no_claim = np.flatnonzero(usable & ~claim)
    far = np.flatnonzero(usable & claim & (d > reach))
    steps.append({"step": "claim_within_reach",
                  "label": lab.get("claim_within_reach", f"List or claim {label} and are within {reach:.0f} km"),
                  "n": int(in_reach.sum()), "n_dropped": int(len(no_claim) + len(far)),
                  "dropped": [x for x in [
                      group("no_mention", f"Our rules found no claim of {label} in the record: no matching specialty "
                                          "and no matching fact.", no_claim, "claim_within_reach"),
                      group("beyond_reach", lab.get("beyond_reach", f"Lists or claims {label}, but more than "
                                            f"{reach:.0f} km away (3 x the {R:.0f} km radius)."), far,
                            "claim_within_reach")] if x]})
    # recommendable
    nr = np.flatnonzero(in_reach & ~rec_mask)
    by_code: dict[str, list] = {}
    plain_of = {}
    for p in nr:
        rs = reasons_of(int(p))
        code = rs[0]["reason_code"] if rs else "not_recommendable"
        by_code.setdefault(code, []).append(int(p))
        plain_of.setdefault(code, _GROUP_PLAIN.get(code, "Not recommendable."))
    rec_groups = [group(c, plain_of[c], np.array(v, dtype=np.int64), "recommendable")
                  for c, v in sorted(by_code.items(), key=lambda kv: (_GROUP_ORDER.get(kv[0], 99), kv[0]))]
    for g_ in rec_groups:
        g_["n_also_blocked"] = int(ld.blocked[np.array(by_code[g_["reason_code"]], dtype=np.int64)].sum())
    steps.append({"step": "recommendable",
                  "label": "Recommendable: a fact in the record names the service, and no blocking warning"
                  + (f", and an estimated chance of independent confirmation at or above {S.p_threshold(service_line):.0%}"
                     if prob else ""),
                  "n": int(rec.sum()), "n_distinct": _n_distinct(index, rec_all), "n_dropped": int(len(nr)),
                  "n_blocked": int((in_reach & ld.blocked).sum()),
                  "dropped": rec_groups})
    # within radius
    beyond = rec_all[d[rec_all] > R]
    steps.append({"step": "within_radius", "label": lab.get("within_radius", f"Within the {R:.0f} km radius"),
                  "n": int(len(in_R)),
                  "n_distinct": _n_distinct(index, in_R), "n_dropped": int(len(beyond)),
                  "dropped": [x for x in [
                      group("beyond_radius", lab.get("beyond_radius", f"Recommendable, but more than {R:.0f} km away."),
                            beyond, "within_radius")] if x]})
    # shown
    in_R_set = set(int(p) for p in in_R)
    shown_in_R = [int(p) for p in shown_pos if int(p) in in_R_set]
    not_shown = [int(p) for p in in_R if index.fac["facility_id"].iat[int(p)] not in shown_set]
    dup, lower, unscored = [], [], []
    for p in not_shown:
        if p not in scored:
            unscored.append(p)
            continue
        rs = reasons_of(p)
        (dup if rs and rs[0]["reason_code"] == "duplicate_listing" else lower).append(p)
    added = np.array([p for p in shown_pos if int(p) not in in_R_set], dtype=np.int64)
    arr = lambda v: np.array(v, dtype=np.int64)  # noqa: E731
    shown_step = {"step": "shown", "label": "Shown as cards", "n": int(len(results)),
                  "n_dropped": int(len(not_shown)),
                  "dropped": [x for x in [
                      group("duplicate_listing", "Another listing of a facility already shown or ranked; one card "
                                                 "per facility.", arr(dup), "shown"),
                      group("ranked_lower", "Recommendable and within the radius, but scored below the cards shown.",
                            arr(lower), "shown"),
                      group("not_scored", f"Not scored: the ranker scores only the {S.MAX_CANDIDATES} nearest "
                                          "recommendable records.", arr(unscored), "shown")] if x],
                  "ids": shown_ids}
    if len(added):
        shown_step["added"] = [{"reason_code": "shown_beyond_radius",
                                "reason_plain": f"Shown on the further-away line: recommendable, but beyond {R:.0f} km.",
                                **_id_list(index, d, added, oprec, max_ids)}]
    shown_step["n_within_radius_shown"] = len(shown_in_R)
    steps.append(shown_step)

    # consistency with the ranker: its own gate counts, and every card a scored recommendable record
    mism = []
    gates = {g.get("gate"): g for g in ((answer_dict.get("how_we_decided") or {}).get("gates") or [])}
    expect = {"usable location": int(usable.sum()), "within reach": int(in_reach.sum()),
              "recommendable": int(rec.sum()), "within radius": int(len(in_R))}
    compared = []
    for gate in _EXPECTED_GATES:
        if gate in gates and gates[gate].get("n") is not None:
            compared.append(gate)
            if int(gates[gate]["n"]) != expect[gate]:
                mism.append({"gate": gate, "ranker": int(gates[gate]["n"]), "funnel": expect[gate]})
    if results:
        compared.append("shown")
        for c in results:
            p = index.id_to_pos.get(c.get("facility_id"))
            if p is None or int(p) not in scored:
                mism.append({"gate": "shown", "facility_id": c.get("facility_id"),
                             "problem": "a card that is not a scored recommendable record"})
        n_groups = _n_distinct(index, list(scored))
        if len(results) > n_groups:
            mism.append({"gate": "shown", "ranker": len(results), "funnel": n_groups,
                         "problem": "more cards than distinct scored facilities"})
    gate_n = len([g for g in compared if g in _EXPECTED_GATES])
    matches = False if mism else (True if gate_n == len(_EXPECTED_GATES) else None)
    out.update(status=answer_dict.get("status", "ok"), steps=steps,
               consistency={"matches_ranker": matches, "mismatches": mism, "gates_compared": compared})
    return out


#: Drop reasons that are about distance or the candidate cap, not the record: items take the group's line.
_DISTANCE_CODES = frozenset({"no_usable_location", "no_mention", "beyond_reach", "beyond_radius", "not_scored"})

_GROUP_PLAIN = {
    "claim_only": "List or claim the service (a specialty or a general statement), but we found no fact naming it.",
    "fact_not_counted": "A fact uses the service's words, but our rules did not count it as evidence.",
    "fact_states_absence": "A fact names the service in a negative statement (not available, not offered, "
                           "not specified); the line quotes it.",
    "public_record_silent": "Public hospitals whose record lists or claims the service, but we found no fact naming "
                            "it: silence, not evidence either way.",
    "flag_possible_merge": "Blocked: the record may combine several facilities or branches.",
    "flag_location_conflict": "Blocked: the record's location is in doubt.",
    "flag_implausible_claim": "Blocked: the claim does not fit the kind of facility the record describes.",
    "below_threshold": "A fact names the service, but the estimated chance of independent confirmation is below the bar.",
}
_GROUP_ORDER = {"claim_only": 0, "fact_not_counted": 1, "fact_states_absence": 2, "public_record_silent": 3,
                "flag_implausible_claim": 4, "flag_possible_merge": 5, "flag_location_conflict": 6,
                "below_threshold": 7}


def funnel_counts(f: Mapping) -> list:
    """``[n]`` per step, for a one-line read-out: Lucknow emoc gives
    ``[9989, 9671, 118, 14, 9, 3]`` on the Nov 21 bundle."""
    return [int(s["n"]) for s in f.get("steps", [])]
