"""The government list beside the records it certifies (D044 item 5).

LaQshya is the national quality certification of labour rooms and maternity
operating theatres in public hospitals, published by NHSRC as a master list
(``silver/ext_laqshya.parquet``, list of 8 April 2026). This module links
LaQshya units to our facility records and turns a reviewed link into one cited
line for a result card:

    Government list (LaQshya, NHSRC, 8 Apr 2026, page 35 row 1109): maternity
    operating theatre certified, valid to 31 Dec 2026. Outside data, not from
    this record.

Rules this module keeps:

* A line is produced only for links a reviewer marked ``correct``
  (``data/labels/laqshya_link_review.csv``). Unreviewed, ``wrong`` and
  ``unsure`` links never produce a line.
* Linking uses state and district (our record's PIN through
  ``silver/pin_crosswalk`` and ``silver/district_crosswalk``), normalised name
  similarity (rapidfuzz), unit type and public operator type. It never uses the
  OSM point matches in ``ext_laqshya`` (715 of 717 came from place-name tokens,
  and some are wrong).
* The line is outside data and says so. It certifies a unit of a hospital on a
  date; it is not a statement about the record, and the absence of a line says
  nothing about a hospital.

Pure functions (normalisation, scoring, the line text) are importable without
any data. ``PublicList`` holds the link table; ``public_line(facility_id)``
uses a cached default loaded from ``silver/laqshya_links.parquet``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Mapping, Optional

import pandas as pd

SOURCE = "LaQshya, NHSRC"
SOURCE_URL = "https://qps.nhsrcindia.org/sites/default/files/2026-04/LaQshya_Master_data-08-04-2026.pdf"
LIST_DATE = date(2026, 4, 8)
#: When the PDF was downloaded (data/external/SOURCES.md).
RETRIEVED = date(2026, 11, 21)
LINKS_FILE = "laqshya_links.parquet"
REVIEW_FILE = "laqshya_link_review.csv"
LINK_VERSION = "v1"
REVIEW_VALUES = ("correct", "wrong", "unsure")

#: Department as printed in the list, to the words on the card.
DEPARTMENT_PLAIN = {
    "Maternity OT": "maternity operating theatre",
    "Labour Room": "labour room",
}
STATUS_PLAIN = {
    "certified": "certified",
    "conditionally_certified": "conditionally certified",
}

# ---------------------------------------------------------------------------
# Name normalisation
# ---------------------------------------------------------------------------

#: Abbreviations in the LaQshya list (and some of our names), expanded before
#: matching. Applied token by token, so "DH" and "dh" expand but "Dhar" does not.
ABBREV = {
    "dh": "district hospital",
    "sdh": "sub district hospital",
    "dwh": "district women hospital",
    "dch": "district civil hospital",
    "chc": "community health centre",
    "uchc": "urban community health centre",
    "phc": "primary health centre",
    "mch": "mother child hospital",
    "mcmh": "mother child hospital",
    "mc": "medical college",
    "gmc": "government medical college",
    "gmch": "government medical college hospital",
    "gh": "general hospital",
    "th": "taluk hospital",
    "ah": "area hospital",
    "fru": "first referral unit",
    "govt": "government",
    "gov": "government",
    "hosp": "hospital",
    "hopital": "hospital",
    "hospitals": "hospital",
    "center": "centre",
    "dist": "district",
    "distt": "district",
    "subdivisional": "sub divisional",
    "subdistrict": "sub district",
    "womens": "women",
    "woman": "women",
    "matri": "mother",
    "shishu": "child",
    "hq": "headquarters",
}

#: Words that describe what kind of unit a place is, not which one. Removed to
#: get the distinctive core ("District Hospital, Nashik" -> "nashik").
GENERIC = frozenset("""
hospital district sub divisional division community health centre medical college general civil
government area taluk taluka tehsil referral first unit rural urban and of the at in research
institute women mother child maternity sadar upgraded primary block headquarters model mg combined
state new old memorial cum level super speciality specialty multi multispeciality multispecialty
care maternal neonatal sciences science teaching main city town hq head quarter quarters fw
""".split())

#: Public-system name markers, used only to admit a record whose operator type
#: is missing (or, with STRONG_PUBLIC, labelled private) into the candidate pool.
PUBLIC_MARKER = re.compile(
    r"\b(?:government|district hospital|civil hospital|general hospital|sub divisional|sub district|"
    r"community health centre|taluk hospital|taluka hospital|sadar hospital|area hospital|"
    r"medical college|esi|esic|aiims|referral hospital|women hospital|primary health centre|"
    r"mother child hospital|district women hospital)\b"
)
STRONG_PUBLIC = re.compile(
    r"\b(?:government|medical college|district hospital|civil hospital|sub divisional|sub district|"
    r"community health centre|taluk hospital|taluka hospital|sadar hospital|area hospital|"
    r"district women hospital)\b"
)

_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")
_SPACES = re.compile(r"\s+")
_PIN6 = re.compile(r"\b\d{6}\b")


def normalise_name(s) -> str:
    """Lowercase, punctuation to spaces, PIN codes dropped, abbreviations expanded."""
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return ""
    t = str(s).lower().replace("&", " and ")
    t = t.replace("sub-divisional", "sub divisional").replace("sub-district", "sub district")
    t = _PIN6.sub(" ", t)
    t = t.replace(".", " ")
    t = _NON_ALNUM.sub(" ", t)
    words = []
    for w in t.split():
        words.extend(ABBREV.get(w, w).split())
    return _SPACES.sub(" ", " ".join(words)).strip()


def core_name(s) -> str:
    """The distinctive words of a name: ``normalise_name`` minus GENERIC words."""
    return " ".join(w for w in normalise_name(s).split() if w not in GENERIC and len(w) > 1)


def unit_type(name) -> Optional[str]:
    """The kind of public unit a name describes, in LaQshya's vocabulary:
    ``MC``, ``DH``, ``SDH``, ``CHC``, or None when the name does not say."""
    n = normalise_name(name)
    if not n:
        return None
    if "medical college" in n or "aiims" in n.split():
        return "MC"
    if re.search(r"\b(sub district|sub divisional|taluk|taluka|tehsil|area hospital|first referral unit)\b", n):
        return "SDH"
    if re.search(r"\b(community health centre|primary health centre)\b", n):
        return "CHC"
    if re.search(r"\b(district|civil hospital|general hospital|sadar|headquarters|women hospital|"
                 r"mother child hospital)\b", n):
        return "DH"
    return None


def type_compatible(lq_type: Optional[str], ours: Optional[str]) -> Optional[bool]:
    """True when the two kinds agree, False when they clearly differ, None when
    our name does not say. DH and SDH are allowed together (list spellings mix
    "district" and "sub-district" for the same building), MC and DH too (many
    district hospitals are the teaching hospital of a college)."""
    def blank(v) -> bool:
        return v is None or (isinstance(v, float) and pd.isna(v)) or v == ""

    if blank(lq_type) or blank(ours):
        return None
    if lq_type == ours:
        return True
    ok = {frozenset({"DH", "SDH"}), frozenset({"DH", "MC"})}
    return frozenset({lq_type, ours}) in ok


def name_scores(lq_name, our_name) -> dict:
    """Similarity of two names on their cores and in full, 0 to 100.

    ``core_set`` is rapidfuzz token_set_ratio on the cores (100 when one core's
    words are all in the other); ``core_sort`` is token_sort_ratio on the cores,
    which drops when one name carries extra distinctive words ("Sullia" against
    "KVG Sullia"); ``full`` is token_set_ratio on the normalised full names."""
    from rapidfuzz import fuzz

    a, b = core_name(lq_name), core_name(our_name)
    na, nb = normalise_name(lq_name), normalise_name(our_name)
    if not a or not b:
        cs = cso = 0.0
    else:
        cs, cso = float(fuzz.token_set_ratio(a, b)), float(fuzz.token_sort_ratio(a, b))
    return {"core_lq": a, "core_ours": b, "core_set": cs, "core_sort": cso,
            "full": float(fuzz.token_set_ratio(na, nb)) if na and nb else 0.0}


def link_score(s: Mapping, compat: Optional[bool]) -> float:
    """One number to order candidates: mostly the core match, with the strict
    core score and type agreement as tie breakers. Not a probability."""
    base = 0.6 * s["core_set"] + 0.3 * s["core_sort"] + 0.1 * s["full"]
    if compat is True:
        base += 5
    elif compat is False:
        base -= 15
    return round(base, 1)


# ---------------------------------------------------------------------------
# Candidate links
# ---------------------------------------------------------------------------

def pin_district_keys(pin_crosswalk: pd.DataFrame, district_crosswalk: pd.DataFrame) -> dict:
    """PIN -> the set of NFHS ``district_key`` values its post offices fall in
    (every rank, so a PIN that straddles two districts admits both)."""
    m = pin_crosswalk[["pin", "state_pin", "district_pin"]].merge(
        district_crosswalk[["state_pin", "district_pin", "district_key"]],
        on=["state_pin", "district_pin"], how="inner")
    m = m.dropna(subset=["district_key"])
    return {str(p): set(g.district_key) for p, g in m.groupby("pin")}


def public_pool(facilities: pd.DataFrame) -> pd.DataFrame:
    """Records that may be public units: operator type ``public``; or missing
    operator type with a public-system name; or labelled ``private`` but named
    as a government unit (STRONG_PUBLIC), kept so a mislabelled district hospital
    can still be reviewed. Private-labelled medical colleges are let in too: some
    state-society colleges (GMERS in Gujarat) are labelled private in the data.
    ``pool_reason`` says which."""
    f = facilities.copy()
    norm = f["name"].map(normalise_name)
    op = f["operator_type"]
    reason = pd.Series(None, index=f.index, dtype=object)
    reason[op.eq("public")] = "operator public"
    reason[op.isna() & norm.str.contains(PUBLIC_MARKER)] = "operator missing, public name"
    reason[op.eq("private") & norm.str.contains(STRONG_PUBLIC)] = "operator private, government name"
    f["pool_reason"] = reason
    return f[f["pool_reason"].notna()].copy()


def laqshya_units(lq: pd.DataFrame) -> pd.DataFrame:
    """One row per LaQshya unit (``facility_uid``) with its list rows as
    ``certs``: [{sno, page, department, certification_status, result_date,
    validity_date}]."""
    rows = []
    for uid, g in lq.sort_values("sno").groupby("facility_uid", sort=False):
        first = g.iloc[0]
        certs = [{
            "sno": int(r.sno), "page": int(r.page),
            "department": None if pd.isna(r.department) else str(r.department),
            "certification_status": str(r.certification_status),
            "result_date": None if pd.isna(r.result_date) else pd.Timestamp(r.result_date).date().isoformat(),
            "validity_date": None if pd.isna(r.validity_date) else pd.Timestamp(r.validity_date).date().isoformat(),
        } for r in g.itertuples()]
        rows.append({"facility_uid": uid, "lq_name": first.facility_name, "lq_type": first.facility_type,
                     "state_ut": first.state_ut, "district_key": first.district_key,
                     "district_raw": first.district_raw, "certs": certs})
    return pd.DataFrame(rows)


def candidate_links(lq: pd.DataFrame, facilities: pd.DataFrame, pin_districts: Mapping[str, set],
                    min_core_set: float = 70.0, per_unit: int = 2,
                    state_min_core_sort: float = 95.0) -> pd.DataFrame:
    """Candidate (LaQshya unit, our record) pairs.

    District pass (``match_scope = "district"``): our record's PIN falls in the
    unit's district, and the cores agree (``core_set`` at or above
    ``min_core_set``) or, for a unit whose name has no core, the full names agree
    at 90 or more.

    State pass (``match_scope = "state"``): same state, a different (or unknown)
    district, and the cores agree strictly (``core_sort`` at or above
    ``state_min_core_sort``, a core word of 4 or more letters, types not in
    conflict). This catches records whose PIN sits in a neighbouring or renamed
    district; a reviewer checks every one.

    At most ``per_unit`` candidates per unit, district pass first, then best
    ``link_score``. Every candidate still needs a review."""
    units = laqshya_units(lq)
    pool = public_pool(facilities)
    pool = pool.assign(pin=pool["pin"].astype("string"))
    pool["district_keys"] = pool["pin"].map(lambda p: pin_districts.get(str(p), set()) if pd.notna(p) else set())
    pool["states"] = pool["district_keys"].map(lambda ks: {k.split("|", 1)[0] for k in ks})
    by_dk: dict[str, list] = {}
    by_state: dict[str, list] = {}
    for i, dks, sts in zip(pool.index, pool["district_keys"], pool["states"]):
        for dk in dks:
            by_dk.setdefault(dk, []).append(i)
        for st in sts:
            by_state.setdefault(st, []).append(i)
    pool_type = pool["name"].map(unit_type)

    out = []
    for u in units.itertuples():
        in_d = by_dk.get(u.district_key, []) if isinstance(u.district_key, str) else []
        in_d_set = set(in_d)
        cands = []
        for i in in_d:
            s = name_scores(u.lq_name, pool.at[i, "name"])
            if not (s["core_set"] >= min_core_set or (not s["core_lq"] and s["full"] >= 90)):
                continue
            compat = type_compatible(u.lq_type, pool_type[i])
            cands.append((0, -link_score(s, compat), i, s, compat, "district"))
        for i in by_state.get(u.state_ut, []) if isinstance(u.state_ut, str) else []:
            if i in in_d_set:
                continue
            s = name_scores(u.lq_name, pool.at[i, "name"])
            long_word = any(len(w) >= 4 for w in s["core_lq"].split())
            compat = type_compatible(u.lq_type, pool_type[i])
            if s["core_sort"] >= state_min_core_sort and long_word and compat is not False:
                cands.append((1, -link_score(s, compat), i, s, compat, "state"))
        cands.sort(key=lambda x: (x[0], x[1]))
        for rank, (_, neg, i, s, compat, scope) in enumerate(cands[:per_unit], start=1):
            r = pool.loc[i]
            out.append({
                "facility_uid": u.facility_uid, "lq_name": u.lq_name, "lq_type": u.lq_type,
                "state_ut": u.state_ut, "district_key": u.district_key,
                "lq_district_raw": u.district_raw,
                "facility_id": r["facility_id"], "our_name": r["name"],
                "our_operator": r["operator_type"] if pd.notna(r["operator_type"]) else None,
                "pool_reason": r["pool_reason"], "our_type": pool_type[i],
                "our_pin": None if pd.isna(r["pin"]) else str(r["pin"]),
                "our_city": r["city_raw"] if "city_raw" in r and pd.notna(r["city_raw"]) else None,
                "our_district_keys": sorted(r["district_keys"]),
                "match_scope": scope,
                "core_lq": s["core_lq"], "core_ours": s["core_ours"],
                "core_set": s["core_set"], "core_sort": s["core_sort"], "full": s["full"],
                "type_compatible": compat, "link_score": -neg, "rank_in_unit": rank,
                "n_candidates": len(cands), "n_pool_in_district": len(in_d),
                "certs": u.certs,
            })
    df = pd.DataFrame(out)
    if len(df):
        # keep "our name does not say" distinct from "types conflict"
        df["type_compatible"] = pd.array([x for x in (o["type_compatible"] for o in out)], dtype="boolean")
    return df


# ---------------------------------------------------------------------------
# The card line
# ---------------------------------------------------------------------------

def _d(v) -> Optional[date]:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return pd.Timestamp(v).date()


def fmt_date(d: date) -> str:
    """8 Apr 2026 (no leading zero, short month)."""
    return f"{d.day} {d.strftime('%b')} {d.year}"


def is_current(cert: Mapping, as_of: date) -> bool:
    v = _d(cert.get("validity_date"))
    return v is not None and v >= as_of


def _cert_order(cert: Mapping, as_of: date):
    """Current before expired; maternity OT before labour room; full before
    conditional; later validity first."""
    v = _d(cert.get("validity_date")) or date.min
    return (not is_current(cert, as_of), cert.get("department") != "Maternity OT",
            cert.get("certification_status") != "certified", -v.toordinal())


def line_text(cert: Mapping, as_of: date, list_date: date = LIST_DATE) -> str:
    """The card sentence for one list row. Template only (D030)."""
    dept = DEPARTMENT_PLAIN.get(cert.get("department") or "", "unit (department not stated)")
    status = STATUS_PLAIN.get(cert.get("certification_status"), str(cert.get("certification_status")))
    v = _d(cert.get("validity_date"))
    tail = ""
    if v is None:
        valid = "validity not stated"
    else:
        valid = f"valid to {fmt_date(v)}"
        if v < as_of:
            tail = f" (expired by {fmt_date(as_of)})"
    return (f"Government list ({SOURCE}, {fmt_date(list_date)}, page {int(cert['page'])} row {int(cert['sno'])}): "
            f"{dept} {status}, {valid}{tail}. Outside data, not from this record.")


def line_dict(cert: Mapping, as_of: date, listed_as: Optional[str] = None) -> dict:
    v = _d(cert.get("validity_date"))
    return {
        "text": line_text(cert, as_of),
        "source": f"{SOURCE}, master list of {fmt_date(LIST_DATE)}",
        "source_url": SOURCE_URL,
        "page": int(cert["page"]),
        "row": int(cert["sno"]),
        "department": cert.get("department"),
        "status": cert.get("certification_status"),
        "validity": v.isoformat() if v else None,
        "current": bool(v is not None and v >= as_of),
        "retrieved": RETRIEVED.isoformat(),
        "listed_as": listed_as,
        "outside_data": True,
    }


@dataclass
class PublicList:
    """Reviewed links, one row per (LaQshya unit, our record). Only rows with
    ``review == "correct"`` produce lines."""
    links: pd.DataFrame

    def __post_init__(self):
        ok = self.links[self.links["review"].eq("correct")] if len(self.links) else self.links
        self._by_fid: dict[str, list[tuple[str, list]]] = {}
        for r in ok.itertuples():
            self._by_fid.setdefault(r.facility_id, []).append((r.lq_name, list(r.certs)))

    def facility_ids(self) -> set:
        return set(self._by_fid)

    def lines(self, facility_id: str, as_of: Optional[date] = None, current_only: bool = False) -> list[dict]:
        """Every list row behind a correct link for this record, best first."""
        as_of = as_of or date.today()
        rows = [(c, name) for name, certs in self._by_fid.get(facility_id, []) for c in certs]
        rows.sort(key=lambda x: _cert_order(x[0], as_of))
        out, seen = [], set()
        for c, name in rows:
            if current_only and not is_current(c, as_of):
                continue
            if c["sno"] in seen:
                continue
            seen.add(c["sno"])
            out.append(line_dict(c, as_of, listed_as=name))
        return out

    def line(self, facility_id: str, as_of: Optional[date] = None, current_only: bool = False) -> Optional[dict]:
        """The one line for a card: a current maternity OT certificate if there
        is one, else a current labour room one, else the latest expired row
        (worded as expired) unless ``current_only``. None when the record has
        no correct link."""
        ls = self.lines(facility_id, as_of=as_of, current_only=current_only)
        return ls[0] if ls else None

    def annotate(self, answer: dict, as_of: Optional[date] = None, current_only: bool = False) -> dict:
        """Add ``public_list`` (a line dict or None) to every card dict in an
        ``Answer.to_dict()``: ``results``, ``beyond_radius``, ``unverified_nearby``.
        Returns the same dict."""
        def tag(card):
            if isinstance(card, dict) and card.get("facility_id"):
                card["public_list"] = self.line(card["facility_id"], as_of=as_of, current_only=current_only)
        for c in answer.get("results") or []:
            tag(c)
        tag(answer.get("beyond_radius"))
        for c in answer.get("unverified_nearby") or []:
            tag(c)
        return answer


def load(path: Optional[Path] = None) -> PublicList:
    """Read the link table: ``path`` (a file, or a folder holding
    ``laqshya_links.parquet``, such as a WP12 bundle), else ``silver/``.
    A missing file gives an empty list (no lines), never an error."""
    from . import paths

    p = Path(path) if path is not None else paths.silver_dir() / LINKS_FILE
    if p.is_dir():
        p = p / LINKS_FILE
    if not p.exists():
        return PublicList(pd.DataFrame(columns=["facility_id", "lq_name", "certs", "review"]))
    return PublicList(pd.read_parquet(p))


@lru_cache(maxsize=1)
def default() -> PublicList:
    return load()


def public_line(facility_id: str, as_of: Optional[date] = None, current_only: bool = False) -> Optional[dict]:
    """``{text, source, page, row, department, validity, retrieved, ...}`` for a
    record with a reviewed-correct LaQshya link, else None."""
    return default().line(facility_id, as_of=as_of, current_only=current_only)


def apply_review(cands: pd.DataFrame, review: Optional[pd.DataFrame]) -> pd.DataFrame:
    """Attach ``review`` and ``review_reason`` from the review CSV, keyed on
    (``facility_uid``, ``facility_id``). Pairs absent from the CSV are
    ``unreviewed``."""
    c = cands.copy()
    if review is None or not len(review):
        c["review"], c["review_reason"] = "unreviewed", None
        return c
    r = review[["facility_uid", "facility_id", "review", "review_reason"]].copy()
    r["review"] = r["review"].fillna("").astype(str).str.strip().str.lower()
    r.loc[~r["review"].isin(REVIEW_VALUES), "review"] = "unreviewed"
    c = c.merge(r, on=["facility_uid", "facility_id"], how="left")
    c["review"] = c["review"].fillna("unreviewed")
    return c


def summarise(links: pd.DataFrame, as_of: date) -> dict:
    """Counts for the report, from a reviewed link table."""
    rev = links["review"].value_counts().to_dict()
    decided = rev.get("correct", 0) + rev.get("wrong", 0)
    ok = links[links["review"].eq("correct")]

    def has_current_ot(certs: Iterable) -> bool:
        return any(c.get("department") == "Maternity OT" and is_current(c, as_of) for c in certs)

    mask = ok["certs"].map(has_current_ot).astype(bool) if len(ok) else pd.Series(False, index=ok.index)
    cur_ot = ok[mask]
    return {
        "candidates": int(len(links)),
        "review_counts": {k: int(v) for k, v in rev.items()},
        "precision_correct_over_decided": (rev.get("correct", 0) / decided) if decided else None,
        "precision_correct_over_all": (rev.get("correct", 0) / len(links)) if len(links) else None,
        "correct_links": int(len(ok)),
        "correct_records": int(ok["facility_id"].nunique()),
        "correct_units": int(ok["facility_uid"].nunique()),
        "records_with_current_maternity_ot": int(cur_ot["facility_id"].nunique()),
        "current_ot_facility_ids": sorted(cur_ot["facility_id"].unique().tolist()),
    }
