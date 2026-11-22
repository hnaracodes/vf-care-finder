"""'Why not the nearer ones?' and the gate funnel (D044 item 2).

Two read-only views over a ranked answer, built only from ``vf.rank``'s public
index and answer shapes, so the ranker, server and UI need a few lines each to
use them (see the integration note in the WP5 result):

* ``why_not_nearer(index, service_line, resolution, answer_dict, k=5)``: the
  nearest records that are closer than the first card but were not
  recommended, each with the reason in the record's own terms. Claim-only
  records name what the record lists and what none of its facts names; flagged
  records quote the flag; a public hospital whose record is merely silent gets
  a neutral line. Every line is shown grey, never struck through.
* ``funnel(index, service_line, resolution)``: the ranker's gates as ordered
  steps (all records, usable location, claim the service within reach,
  recommendable, within radius, shown), each with its count and the records
  that dropped there, grouped by reason, nearest first.

Wording rules (CLAUDE.md, ``reports/standout_brainstorm.md`` "What not to do"):
no line says a named facility "cannot" or "does not have" anything; it says
what the record lists, names or does not name. A record on a shared or
approximate point gets ``distance_label = "position approximate"``, never
"0.0 km". Every string is a template filled from the record, a quoted fact,
or a WP11 flag (D030); no LLM text.

Typical use::

    from vf import rank, locate, why_not
    idx = rank.load_index(bundle)
    res = locate.resolve("226001")
    ans = rank.rank(idx, "emoc", res).to_dict()
    why_not.why_not_nearer(idx, "emoc", res, ans)
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

WHY_NOT_VERSION = "why_not_v1"

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

#: The location note ``vf.services`` gives a record moved to its PIN's centre.
_MOVED_NOTE = "centre of its PIN code area"
#: When ``operator_type`` is missing, a name that says government care.
_PUBLIC_NAME = re.compile(
    r"\b(govt|government|district hospital|civil hospital|sub[- ]?district hospital|primary health|"
    r"community health|phc|chc|esic|railway hospital|zila|jila)\b", re.I)

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


# ---------------------------------------------------------------------------
# Per-index lookups, computed once and kept on the index object
# ---------------------------------------------------------------------------

class _Lookups:
    """Specialties, fact counts and fact texts per facility, and which records
    sit on a shared or approximate point. Works for a WP12 bundle index (facts
    come from the keyword index postings) and a local index (silver facts)."""

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
        self._fact_rows: Optional[dict] = None
        self._n_facts: Optional[np.ndarray] = None

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
        n = len(self.idx.fac)
        self._n_facts = np.full(n, -1, dtype=np.int64)  # -1: unknown (no facts loaded)
        self._fact_rows = {}
        facts = self.idx.facts_for_keyword
        kw = self.idx.keyword
        if facts is not None:
            f = facts[facts["field"].isin(("capability", "procedure", "equipment"))]
            self._fact_src = ("facts", f["text"].to_numpy(object))
            groups = f.groupby("facility_id").indices
        elif kw is not None:
            post = kw.postings
            fmask = post["field"].isin(("capability", "procedure", "equipment")).to_numpy()
            f = post[fmask]
            texts = kw.docs["text"].to_numpy(object)
            self._fact_src = ("kw", texts[f["doc_id"].to_numpy(np.int64)])
            groups = f.groupby("facility_id").indices
        else:
            self._fact_src = ("none", np.array([], dtype=object))
            return
        self._n_facts[:] = 0
        for fid, rows in groups.items():
            p = self.idx.id_to_pos.get(fid)
            if p is not None:
                self._n_facts[p] = len(rows)
                self._fact_rows[p] = rows

    def n_facts(self, pos: int) -> Optional[int]:
        if self._n_facts is None:
            self._build_facts()
        v = int(self._n_facts[pos])
        return None if v < 0 else v

    def fact_texts(self, pos: int) -> list:
        if self._n_facts is None:
            self._build_facts()
        rows = self._fact_rows.get(pos)
        if rows is None:
            return []
        return [str(t) for t in self._fact_src[1][rows]]


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


def _absence(text: str, n_facts: Optional[int]) -> dict:
    return {"kind": "absence", "text": text, "n_facts": n_facts}


def _facts_phrase(n: Optional[int], req: str, verb: str = "names") -> str:
    """"none of its 141 facts names caesarean section"."""
    base = {"names": "name", "mentions": "mention"}.get(verb, verb)
    if n is None:
        return f"no fact in the record {verb} {req}"
    if n == 0:
        return f"the record has no facts beyond its listing, so nothing {verb} {req}"
    if n == 1:
        return f"its one fact does not {base} {req}"
    return f"none of its {n} facts {verb} {req}"


def _supported_req(line: str) -> str:
    rule = recipes.GRADE_RULES.get(line)
    if rule is not None and rule.supported_names:
        return rule.supported_names[0]
    return S.label(line).lower()


# ---------------------------------------------------------------------------
# Reasons: why one record was not recommended
# ---------------------------------------------------------------------------

def record_reasons(idx, line: str, pos: int, *, shown_ids: frozenset = frozenset(),
                   shown_names: Optional[Mapping[int, str]] = None) -> list:
    """Every reason this record is not among the cards, most basic first:
    ``[{reason_code, reason_plain, quote_or_absence, tone}]``. ``tone`` is
    ``"neutral"`` for a public hospital whose record is only silent, else
    ``"grey"``. Empty when the record was shown or does not concern the line."""
    ld = idx.lines[line]
    g = int(ld.grade[pos])
    fid = idx.fac["facility_id"].iat[pos]
    if fid in shown_ids:
        return []
    lk = _lookups(idx)
    req = _supported_req(line)
    public = _is_public(idx, pos)
    major = list(ld.major_flags.get(pos, []))
    minor = list(ld.minor_flags.get(pos, []))
    prob = ld.p is not None
    rec = g >= 2 and not bool(ld.blocked[pos]) and (not prob or float(ld.p[pos]) >= S.p_threshold(line))
    out = []

    # 1. the record names nothing for this service beyond a claim, or nothing at all
    if g <= 1:
        n = lk.n_facts(pos)
        absence = _facts_phrase(n, req)
        if g == 1:
            basis = None
            ev = ld.evidence[pos]
            if ev and ev[0].get("quote") and ev[0].get("field") != "specialties":
                basis = ("mentions " + (ev[0].get("requirement") or "the service"),
                         _quote(ev[0]["quote"], ev[0].get("field"), ev[0].get("fact_id")))
            if basis is None:
                rule = recipes.GRADE_RULES.get(line)
                listed = [s for s in lk.specialties(pos) if rule is not None and s in rule.claim_specialties]
                if listed:
                    plain = [specialty_plain(s) for s in listed]
                    names = (", ".join(plain[:-1]) + ", and " + plain[-1]) if len(plain) > 1 and any(
                        " and " in x for x in plain) else _join(plain)
                    basis = (f"lists {names} as a specialty" if len(listed) == 1 else f"lists {names} as specialties",
                             {"kind": "quote", "text": listed[0], "field": "specialties", "fact_id": None})
            if basis is None:
                basis = ("claims the service", None)
            claim_text, claim_quote = basis
            if public and not major:
                out.append({"reason_code": "public_record_silent",
                            "reason_plain": (f"Public hospital: its record {claim_text}, but {absence}. "
                                             "The record is silent on it, which is not evidence either way; ask the hospital."),
                            "quote_or_absence": _absence(f"No fact in the record names {req}.", n),
                            "claim_quote": claim_quote, "tone": "neutral"})
            else:
                out.append({"reason_code": "claim_only",
                            "reason_plain": f"{claim_text[0].upper()}{claim_text[1:]}, but {absence}.",
                            "quote_or_absence": _absence(f"No fact in the record names {req}.", n),
                            "claim_quote": claim_quote, "tone": "grey"})
        elif not public:  # grade 0 with a probability only (probability mode)
            out.append({"reason_code": "no_mention",
                        "reason_plain": f"The record lists no specialty and no fact for it: {absence}.",
                        "quote_or_absence": _absence(f"No fact in the record names {req}.", n),
                        "claim_quote": None, "tone": "grey"})
        else:  # grade 0 public hospital (see _concerns)
            kind = "hospital" if _is_hospital(idx, pos) else "facility"
            what = label_lc(line)
            out.append({"reason_code": "public_record_silent",
                        "reason_plain": (f"Public {kind}: {_facts_phrase(n, what, verb='mentions')}. Public hospitals "
                                         "are often thinly described online, so this silence is not evidence either way."),
                        "quote_or_absence": _absence(f"No fact in the record mentions {what}.", n),
                        "claim_quote": None, "tone": "neutral"})

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

    # 4. recommendable but not on the cards: another listing of a card, or ranked lower
    if rec:
        canon = idx.dup_canon.get(pos)
        group = [canon] + idx.dup_of.get(canon, []) if canon is not None else []
        shown_in_group = [o for o in group if o != pos and idx.fac["facility_id"].iat[o] in shown_ids]
        ev = ld.evidence[pos]
        q = _quote(ev[0]["quote"], ev[0].get("field"), ev[0].get("fact_id")) if ev else None
        if shown_in_group:
            o = shown_in_group[0]
            nm = (shown_names or {}).get(o) or str(idx.fac["name"].iat[o])
            out.append({"reason_code": "duplicate_listing",
                        "reason_plain": f"Another listing of {nm}, which is shown once, as its card.",
                        "quote_or_absence": q, "tone": "grey"})
        elif canon is not None and canon != pos:
            nm = str(idx.fac["name"].iat[canon])
            out.append({"reason_code": "duplicate_listing",
                        "reason_plain": f"Likely another listing of {nm}; the ranker keeps one record per facility.",
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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def why_not_nearer(index, service_line: str, resolution, answer_dict: Optional[Mapping], k: int = 5) -> list:
    """The ``k`` nearest records closer than the first card that were not
    recommended, nearest first. Each item::

        {facility_id, name, operator, type, lat, lon,
         distance_km, distance_label, position_approximate,
         reason_code, reason_plain, quote_or_absence, tone,
         also: [{reason_code, reason_plain, quote_or_absence}]}

    ``distance_km`` is None when ``position_approximate`` (the label then reads
    "position approximate"). ``tone`` is "neutral" for a public hospital whose
    record is only silent, "grey" otherwise; neither is ever struck through.
    The first card is ``answer_dict["results"][0]``, else its beyond-radius
    card. With no card at all, records within the service radius are listed."""
    return why_not_strip(index, service_line, resolution, answer_dict, k=k)["items"]


def why_not_strip(index, service_line: str, resolution, answer_dict: Optional[Mapping], k: int = 5) -> dict:
    """``why_not_nearer`` plus what the strip's heading needs:
    ``{items, n_nearer, first_card: {facility_id, name, distance_label} | None,
    cutoff_km, heading, version}``."""
    if service_line not in S.SERVICES:
        raise ValueError(f"unknown service line {service_line!r}")
    empty = {"items": [], "n_nearer": 0, "first_card": None, "cutoff_km": None, "heading": "", "version": WHY_NOT_VERSION}
    if service_line not in index.lines:
        return empty
    lat, lon, oprec, _ = _origin(resolution)
    if lat is None:
        return empty
    d = haversine_km(lat, lon, index.lat, index.lon)
    cards, first = _shown(answer_dict)
    shown_ids = frozenset(str(c.get("facility_id")) for c in cards if c.get("facility_id"))
    shown_names = {index.id_to_pos[c["facility_id"]]: c.get("name") for c in cards
                   if c.get("facility_id") in index.id_to_pos}
    R = float(S.SERVICES[service_line].radius_km)
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
    items = []
    n_nearer = 0
    for p in near:
        p = int(p)
        if not _concerns(index, service_line, p):
            continue
        reasons = record_reasons(index, service_line, p, shown_ids=shown_ids, shown_names=shown_names)
        if not reasons:
            continue
        n_nearer += 1
        if len(items) >= k:
            continue
        top, rest = reasons[0], reasons[1:]
        r = index.fac.iloc[p]
        items.append({
            "facility_id": r["facility_id"],
            "name": _rank._str_or_none(r.get("name")) or "(no name)",
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
        })
    label = label_lc(service_line)
    if first_view is not None:
        heading = (f"Why not the nearer ones? {n_nearer} record{'s' if n_nearer != 1 else ''} closer than "
                   f"{first_view['name']} mention {label} or are public hospitals, but were not recommended."
                   if n_nearer else f"No record closer than {first_view['name']} mentions {label}.")
    else:
        heading = (f"Within {R:.0f} km, {n_nearer} record{'s' if n_nearer != 1 else ''} mention {label} or are "
                   "public hospitals, but none was recommended." if n_nearer else "")
    return {"items": items, "n_nearer": n_nearer, "first_card": first_view, "cutoff_km": round(float(cutoff), 2),
            "heading": heading, "version": WHY_NOT_VERSION}


def _n_distinct(idx, pos) -> int:
    """Distinct facilities among ``pos``: one per WP11 duplicate group."""
    return len({idx.dup_canon.get(int(p), -1 - int(p)) for p in pos})


def _id_list(idx, d: np.ndarray, pos: np.ndarray, oprec, max_ids: Optional[int]) -> dict:
    pos = pos[np.argsort(np.nan_to_num(d[pos], nan=np.inf), kind="stable")]
    take = pos if max_ids is None else pos[:max_ids]
    return {"n": int(len(pos)), "ids": [str(idx.fac["facility_id"].iat[int(p)]) for p in take],
            "n_more": int(len(pos) - len(take))}


def funnel(index, service_line: str, resolution, *, answer_dict: Optional[Mapping] = None,
           max_ids: Optional[int] = FUNNEL_MAX_IDS, detail_steps: tuple = ("recommendable", "within_radius", "shown")) -> dict:
    """The ranker's gates as ordered steps::

        {service, service_label, radius_km, reach_km, location, status, steps: [
           {step, label, n, n_dropped, dropped: [{reason_code, reason_plain, n, ids, n_more,
                                                  items?: [{facility_id, name, distance_label, reason_plain}]}],
            added?: [...]}],
         consistency: {matches_ranker, mismatches}, version}

    Steps: ``all_records`` -> ``usable_location`` -> ``claim_within_reach`` ->
    ``recommendable`` -> ``within_radius`` -> ``shown``. Counts are complete;
    ``ids`` are nearest first, at most ``max_ids`` per reason (``None``: all).
    Steps in ``detail_steps`` also carry ``items`` with each record's reason.
    ``answer_dict`` is the ``rank`` answer for the same query; without it,
    ``rank`` is called. ``consistency`` compares the counts with the answer's
    ``how_we_decided.gates``."""
    if service_line not in S.SERVICES:
        raise ValueError(f"unknown service line {service_line!r}")
    svc = S.SERVICES[service_line]
    R = float(svc.radius_km)
    reach = float(S.REACH_FACTOR) * R
    n_all = int(len(index.fac))
    out: dict[str, Any] = {"service": service_line, "service_label": svc.label, "radius_km": R, "reach_km": reach,
                           "location": _rank._location_dict(resolution), "version": WHY_NOT_VERSION}
    first_step = {"step": "all_records", "label": "All records in this dataset", "n": n_all, "n_dropped": 0, "dropped": []}
    lat, lon, oprec, status = _origin(resolution)
    if lat is None:
        out.update(status="no_location", steps=[first_step],
                   note="No searchable point: pick a place first.", consistency={"matches_ranker": True, "mismatches": []})
        return out
    if service_line not in index.lines:
        out.update(status="no_grades", steps=[first_step],
                   note="No recipe grades are loaded for this service; the answer is a keyword search.",
                   consistency={"matches_ranker": True, "mismatches": []})
        return out
    if answer_dict is None:
        answer_dict = _rank.rank(index, service_line, resolution).to_dict()
    ld = index.lines[service_line]
    d = haversine_km(lat, lon, index.lat, index.lon)
    usable = ~np.isnan(d)
    prob = ld.p is not None
    claim = (ld.grade >= 1) | (ld.p > 0) if prob else (ld.grade >= 1)
    rec_mask = (ld.grade >= 2) & ~ld.blocked
    if prob:
        rec_mask &= ld.p >= S.p_threshold(service_line)
    in_reach = usable & (d <= reach) & claim
    rec = in_reach & rec_mask
    rec_all = np.flatnonzero(rec)
    rec_all = rec_all[np.argsort(d[rec_all], kind="stable")]
    scored = set(int(x) for x in rec_all[: S.MAX_CANDIDATES])  # the ranker scores only the nearest ones
    in_R = rec_all[d[rec_all] <= R]  # uncapped, as the ranker's "within radius" gate counts it
    cards, _ = _shown(answer_dict)
    results = [c for c in (answer_dict.get("results") or [])]
    shown_ids = [str(c["facility_id"]) for c in cards if c.get("facility_id") in index.id_to_pos]
    shown_set = frozenset(shown_ids)
    shown_pos = np.array([index.id_to_pos[f] for f in shown_ids], dtype=np.int64)
    shown_names = {index.id_to_pos[c["facility_id"]]: c.get("name") for c in cards
                   if c.get("facility_id") in index.id_to_pos}
    label = label_lc(service_line)

    def items_for(pos: np.ndarray, step: str, override: Optional[tuple] = None) -> Optional[list]:
        if step not in detail_steps:
            return None
        pos = pos[np.argsort(d[pos], kind="stable")]
        take = pos if max_ids is None else pos[:max_ids]
        rows = []
        for p in take:
            p = int(p)
            rs = [{"reason_code": override[0], "reason_plain": override[1], "tone": "grey"}] if override else \
                record_reasons(index, service_line, p, shown_ids=shown_set, shown_names=shown_names)
            rows.append({"facility_id": str(index.fac["facility_id"].iat[p]), "name": str(index.fac["name"].iat[p]),
                         **distance_view(index, p, float(d[p]), oprec),
                         "reason_code": rs[0]["reason_code"] if rs else None,
                         "reason_plain": rs[0]["reason_plain"] if rs else None,
                         "tone": rs[0]["tone"] if rs else "grey"})
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
    steps.append({"step": "claim_within_reach", "label": f"Mention {label} and are within {reach:.0f} km",
                  "n": int(in_reach.sum()), "n_dropped": int(len(no_claim) + len(far)),
                  "dropped": [x for x in [
                      group("no_mention", f"The record does not mention {label}.", no_claim, "claim_within_reach"),
                      group("beyond_reach", f"Mentions {label}, but more than {reach:.0f} km away "
                                            f"(3 x the {R:.0f} km radius).", far, "claim_within_reach")] if x]})
    # recommendable
    nr = np.flatnonzero(in_reach & ~rec_mask)
    by_code: dict[str, list] = {}
    plain_of = {}
    for p in nr:
        rs = record_reasons(index, service_line, int(p), shown_ids=shown_set, shown_names=shown_names)
        code = rs[0]["reason_code"] if rs else "not_recommendable"
        by_code.setdefault(code, []).append(int(p))
        plain_of.setdefault(code, _GROUP_PLAIN.get(code, "Not recommendable."))
    rec_groups = [group(c, plain_of[c], np.array(v, dtype=np.int64), "recommendable")
                  for c, v in sorted(by_code.items(), key=lambda kv: (_GROUP_ORDER.get(kv[0], 9), kv[0]))]
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
    steps.append({"step": "within_radius", "label": f"Within the {R:.0f} km radius", "n": int(len(in_R)),
                  "n_distinct": _n_distinct(index, in_R), "n_dropped": int(len(beyond)),
                  "dropped": [x for x in [
                      group("beyond_radius", f"Recommendable, but more than {R:.0f} km away.", beyond,
                            "within_radius")] if x]})
    # shown
    in_R_set = set(int(p) for p in in_R)
    shown_in_R = [int(p) for p in shown_pos if int(p) in in_R_set]
    not_shown = [int(p) for p in in_R if index.fac["facility_id"].iat[int(p)] not in shown_set]
    dup, lower, unscored = [], [], []
    for p in not_shown:
        if p not in scored:
            unscored.append(p)
            continue
        rs = record_reasons(index, service_line, p, shown_ids=shown_set, shown_names=shown_names)
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

    # consistency with the ranker's own gate counts
    mism = []
    gates = {g.get("gate"): g for g in ((answer_dict.get("how_we_decided") or {}).get("gates") or [])}
    expect = {"usable location": ("usable_location", int(usable.sum())), "within reach": ("claim_within_reach", int(in_reach.sum())),
              "recommendable": ("recommendable", int(rec.sum())), "within radius": ("within_radius", int(len(in_R)))}
    for gate, (step, n) in expect.items():
        if gate in gates and gates[gate].get("n") is not None and int(gates[gate]["n"]) != n:
            mism.append({"gate": gate, "ranker": int(gates[gate]["n"]), "funnel": n})
    out.update(status=answer_dict.get("status", "ok"), steps=steps,
               consistency={"matches_ranker": not mism, "mismatches": mism})
    return out


#: Drop reasons that are about distance or the candidate cap, not the record: items take the group's line.
_DISTANCE_CODES = frozenset({"no_usable_location", "no_mention", "beyond_reach", "beyond_radius", "not_scored"})

_GROUP_PLAIN = {
    "claim_only": "Mention the service (a specialty or a general statement), but no fact names it.",
    "public_record_silent": "Public hospitals whose record mentions the service but no fact names it: silent, not evidence either way.",
    "flag_possible_merge": "Blocked: the record may combine several facilities or branches.",
    "flag_location_conflict": "Blocked: the record's location is in doubt.",
    "flag_implausible_claim": "Blocked: the claim does not fit the kind of facility the record describes.",
    "below_threshold": "A fact names the service, but the estimated chance of independent confirmation is below the bar.",
}
_GROUP_ORDER = {"claim_only": 0, "public_record_silent": 1, "flag_implausible_claim": 2, "flag_possible_merge": 3,
                "flag_location_conflict": 4, "below_threshold": 5}


def funnel_counts(f: Mapping) -> list:
    """``[n]`` per step, for a one-line read-out: Lucknow emoc gives
    ``[9989, 9671, 118, 14, 9, 3]`` on the Nov 21 bundle."""
    return [int(s["n"]) for s in f.get("steps", [])]
