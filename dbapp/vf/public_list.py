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

A lapsed certificate is worded as D051 says: "certificate on the 8 Apr 2026
list, valid to <date>, not renewed on that list" (never "expired facility").

Version 2 (D055, ``annotate_answer``, at the end of this file) adds five more
outside lists (AERB radiotherapy licences, National Cancer Grid, MoHFW trauma
care facilities, NABI burn-care providers, the NHP blood bank directory) as
``outside_lines`` on cards and an ``outside_nearby`` lane on the answer.

Pure functions (normalisation, scoring, the line text) are importable without
any data. ``PublicList`` holds the link table; ``public_line(facility_id)``
uses a cached default loaded from ``silver/laqshya_links.parquet``.
"""
from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Mapping, Optional

import numpy as np
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
    return (f"Government list ({SOURCE}, {fmt_date(list_date)}, page {int(cert['page'])} row {int(cert['sno'])}): "
            f"{cert_what(cert)}{cert_validity(cert, as_of, list_date)}. Outside data, not from this record.")


def cert_what(cert: Mapping) -> str:
    """'maternity operating theatre certified' (department and status, no dates)."""
    dept = DEPARTMENT_PLAIN.get(cert.get("department") or "", "unit (department not stated)")
    status = STATUS_PLAIN.get(cert.get("certification_status"), str(cert.get("certification_status")))
    return f"{dept} {status}"


def cert_validity(cert: Mapping, as_of: date, list_date: date = LIST_DATE) -> str:
    """The validity clause after ``cert_what``. A lapsed certificate is worded as D051 says: "certificate on
    the 8 Apr 2026 list, valid to <date>, not renewed on that list" (never "expired facility")."""
    v = _d(cert.get("validity_date"))
    if v is None:
        return ", validity not stated"
    if v < as_of:
        return f"; certificate on the {fmt_date(list_date)} list, valid to {fmt_date(v)}, not renewed on that list"
    return f", valid to {fmt_date(v)}"


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


# ===========================================================================
# public_list v2: outside lists (D055)
# ===========================================================================
#
# Six outside lists, two uses, ranking unchanged:
#
# 1. ``outside_lines`` on a card: a dated, cited fact from a list row linked to
#    that record. Only links a reader marked ``same`` inside a band whose
#    one-sided 95% lower bound of precision is at least 0.90 (``accepted`` in
#    ``silver/ext_links_<source>.parquet``; for LaQshya, ``review == "correct"``).
# 2. ``outside_nearby`` on the answer: up to 3 listed facilities for this
#    service, within the service window, that are not linked to any record in
#    our dataset. A separate lane, never mixed with recommended cards. Blood bank
#    listings never appear in it (card fact only).
#
# Lists never make a facility recommendable, never change a grade, and the
# absence of a line says nothing about a facility. Every text ends with
# ``OUTSIDE_TAIL``.
#
#     from vf import public_list as pl
#     d = pl.annotate_answer(ans.to_dict(), "oncology", res.lat, res.lon, res.state)
#     d["results"][0]["outside_lines"]    # [{source_id, ..., text}]
#     d["outside_nearby"]                  # [{source_id, name, ..., minutes_label, text}]
#
# Tables: built once from ``silver/`` (``build_outside``) or read from a bundle
# folder holding ``outside_card_facts.parquet`` and ``outside_lane.parquet``
# (``write_bundle``), then cached (``default_outside``).

OUTSIDE_VERSION = "public_list_v2"
OUTSIDE_TAIL = "Outside data, not from this record."
LANE_HEADING = "Also on government or registry lists nearby (not in this dataset)"
LANE_NOT_LINKED = "Not linked to any record in this dataset."
CARD_FACTS_FILE = "outside_card_facts.parquet"
LANE_FILE = "outside_lane.parquet"
OUTSIDE_META_FILE = "outside_meta.json"
LANE_MAX = 3
#: A listed position less precise than this is shown as a drive-time range, not one number.
LOOSE_POSITION_KM = 5.0

#: Lane rule shared with the AERB and blood bank accept stages: a list row is
#: kept out of the lane when a low-band candidate it was never read against
#: scores at least this (it may still be one of our records).
NEAR_MISS_MIN = 0.20
#: Look-alike rule (from scripts/wp13_ext_aerb_rt.py): one of our records within
#: max(LOOKALIKE_KM, the row's position precision) whose distinctive name words
#: match at token set ratio LOOKALIKE_MIN or more keeps the row out of the lane.
LOOKALIKE_KM = 10.0
LOOKALIKE_MAX_KM = 40.0
LOOKALIKE_MIN = 85
#: Two lane entries this close with matching distinctive words are one place
#: listed twice (for example by AERB and NCG); the nearer citation is kept.
LANE_DEDUPE_KM = 3.0
#: Our records of these types never block the lane (a dental clinic is not a hospital).
LOOKALIKE_SKIP_TYPES = frozenset({"dentist", "pharmacy"})

#: Words that say what kind of place it is, not which place: dropped with GENERIC
#: before the look-alike and dedupe comparisons.
_LANE_GENERIC = GENERIC | frozenset("""pvt private ltd limited llp trust society unit cancer oncology
radiotherapy radiation burn burns trauma blood bank centre center centres institute hospital
healthcare clinic clinics nursing home sciences foundation charitable mission superspeciality
superspecialty multispeciality multispecialty speciality specialty and of the""".split())


@dataclass(frozen=True)
class OutsideSource:
    """One outside list. ``kind`` opens every sentence ("Regulator list (...)");
    ``short`` is the in-sentence citation; ``card_lines`` are the service lines
    whose cards may show its facts; ``lane_lines`` those whose lane may show
    its unlinked rows (empty: card fact only)."""
    source_id: str
    source_name: str
    publisher: str
    kind: str
    short: str
    list_date: Optional[str]
    card_lines: tuple
    lane_lines: tuple
    terms: str
    note: str = ""

    def as_dict(self) -> dict:
        return {"source_id": self.source_id, "source_name": self.source_name, "publisher": self.publisher,
                "kind": self.kind, "short": self.short, "list_date": self.list_date,
                "card_lines": list(self.card_lines), "lane_lines": list(self.lane_lines), "terms": self.terms}


OUTSIDE_SOURCES: dict[str, OutsideSource] = {s.source_id: s for s in (
    OutsideSource("laqshya", "LaQshya master list", "National Health Systems Resource Centre (NHSRC), MoHFW",
                  "Government list", "LaQshya, NHSRC, 8 Apr 2026", "2026-04-08", ("emoc",), ("emoc",),
                  "Government of India publication; no open licence stated; cited, not redistributed."),
    OutsideSource("aerb_rt", "AERB list of licensed radiotherapy centres",
                  "Atomic Energy Regulatory Board (AERB), Government of India",
                  "Regulator list", "AERB, as on Apr 2025", "2025-04", ("oncology",), ("oncology",),
                  "AERB disclaimer: extracts may be freely used with acknowledgement of the source.",
                  note="the list gives no licence dates"),
    OutsideSource("ncg", "National Cancer Grid member list",
                  "National Cancer Grid (NCG), coordinated by Tata Memorial Centre",
                  "Registry list", "National Cancer Grid member list, as served 22 Nov 2026", None,
                  ("oncology",), ("oncology",),
                  "Page footer: all rights reserved; no terms page. Cited facts only.",
                  note="membership is not an accreditation of a service"),
    OutsideSource("mohfw_trauma", "MoHFW list of 196 trauma care facilities",
                  "Ministry of Health and Family Welfare (MoHFW), Government of India",
                  "Government list", "MoHFW, 8 Feb 2023", "2023-02-08", ("trauma",), ("trauma",),
                  "MoHFW copyright policy: no reproduction without permission; cite and acknowledge.",
                  note="the list records scheme funding and gives no level or operating status"),
    OutsideSource("nabi_burns", "NABI list of burn care providers", "National Academy of Burns India (NABI)",
                  "Registry list", "NABI, updated 5 Feb 2026", "2026-02-05", ("burns",), ("burns",),
                  "NABI page: all contents private properties of NABI, all rights reserved. Cited facts only."),
    OutsideSource("nhp_bloodbank", "National Health Portal blood bank directory",
                  "National Health Portal, MoHFW (data.gov.in)",
                  "Government directory", "National Health Portal, data.gov.in, published 10 Aug 2017",
                  "2017-08-10", ("emoc", "cardiac_surgery", "trauma"), (),
                  "Government Open Data License - India (GODL), attribution required."),
)}
#: The five sources with a link table of their own (LaQshya keeps laqshya_links.parquet).
LINKED_SOURCES = ("aerb_rt", "ncg", "mohfw_trauma", "nabi_burns", "nhp_bloodbank")

_CARD_COLS = ["facility_id", "source_id", "list_uid", "what", "citation", "locator", "valid_to"]
_LANE_COLS = ["source_id", "list_uid", "service_line", "name", "district", "state", "pin", "lat", "lon",
              "position_precision_km", "position_note", "what", "citation", "locator", "valid_to", "group_key"]


def _blank(v) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and math.isnan(v):
        return True
    try:
        if v is pd.NA or v is pd.NaT:
            return True
    except TypeError:
        pass
    return isinstance(v, str) and v.strip().lower() in ("", "nan", "none", "null", "nat")


def _s(v) -> Optional[str]:
    return None if _blank(v) else str(v).strip()


def _iso(v) -> Optional[str]:
    d = None if _blank(v) else _d(v)
    return d.isoformat() if d else None


def _fmt_list_date(iso: Optional[str]) -> Optional[str]:
    if not iso:
        return None
    if len(iso) == 7:  # year and month only
        return datetime.strptime(iso, "%Y-%m").strftime("%b %Y")
    return fmt_date(date.fromisoformat(iso))


def nice_place(s) -> str:
    """'GUNTUR' -> 'Guntur'; mixed case is kept as written."""
    s = _s(s)
    if not s:
        return ""
    if s.upper() != s:
        return s
    small = {"And", "Of", "The"}
    words = s.title().split()
    return " ".join(w.lower() if i and w in small else w for i, w in enumerate(words))


def nice_name(s) -> str:
    """An all-capitals list name in title case ('SRI PRAAGNA HOSPITALS' -> 'Sri Praagna Hospitals');
    short all-capital words (AIIMS, ESIC) and mixed-case names are kept."""
    s = _s(s)
    if not s:
        return ""
    letters = [c for c in s if c.isalpha()]
    if not letters or sum(c.isupper() for c in letters) / len(letters) < 0.8:
        return s
    out = []
    for w in s.split():
        core = re.sub(r"[^A-Za-z]", "", w)
        keep = core in _ACRONYMS or (2 <= len(core) <= 5 and not re.search(r"[AEIOUY]", core))
        out.append(w if keep else w.capitalize())
    return " ".join(out)


#: Capitalised words kept as written by ``nice_name`` (words with no vowel, such as MGM, are kept anyway).
_ACRONYMS = frozenset("""AIIMS ESIC ESI PGIMER NIMHANS SGPGI SGPGIMS KGMU TMC HCG BHU CMC JIPMER ACTREC KEM GMC
RML LNJP SMS IGIMS RIMS SKIMS ICMR NHSRC DRDO KLE KMC CHC PHC DH SDH GH ECHS MCH IMS AIMS MIOT SIMS NIMS""".split())


def lane_core(name) -> str:
    """Distinctive words of a facility name for the look-alike and dedupe checks."""
    return " ".join(sorted({w for w in normalise_name(name).split() if w not in _LANE_GENERIC and len(w) > 1}))


def outside_status(valid_to: Optional[str], as_of: date) -> str:
    """``current`` (a validity or renewal date on or after ``as_of``), ``lapsed``
    (a date before it) or ``undated`` (the row carries none)."""
    valid_to = _iso(valid_to)
    if not valid_to:
        return "undated"
    return "current" if date.fromisoformat(valid_to) >= as_of else "lapsed"


def _validity_clause(source_id: str, valid_to: Optional[str], as_of: date) -> str:
    valid_to = _iso(valid_to)
    st = outside_status(valid_to, as_of)
    src = OUTSIDE_SOURCES[source_id]
    if source_id == "laqshya":
        return cert_validity({"validity_date": valid_to}, as_of)
    if source_id == "nhp_bloodbank":
        if st == "undated":
            return "; no usable licence renewal date on the list"
        when = fmt_date(date.fromisoformat(valid_to))
        if st == "lapsed":
            return f", licence renewal date on the list {when}, not renewed on that list"
        return f", licence renewal date on the list {when}"
    return f"; {src.note}" if src.note else ""


def card_text(source_id: str, locator: str, what: str, valid_to: Optional[str], as_of: date) -> str:
    """'Regulator list (AERB, as on Apr 2025, page 3 row 12): licensed by AERB for radiotherapy; the list
    gives no licence dates. Outside data, not from this record.' Template only (D030)."""
    src = OUTSIDE_SOURCES[source_id]
    return (f"{src.kind} ({src.short}, {locator}): {what}{_validity_clause(source_id, valid_to, as_of)}. "
            f"{OUTSIDE_TAIL}")


def lane_text(row: Mapping, minutes_label: str, as_of: date) -> str:
    """The lane sentence: name and place, what the list says, the citation, that it is not linked to our
    data, the estimated drive and the position quality."""
    src = OUTSIDE_SOURCES[row["source_id"]]
    name = nice_name(row["name"])
    parts = [x for x in (nice_place(row.get("district")), nice_place(row.get("state"))) if x]
    parts = [x for x in parts if x.lower() not in name.lower()]  # "District Hospital, Rewari", not "..., Rewari"
    head = name + (f", {', '.join(parts)}" if parts else "")
    pos = row.get("position_note") or "approximate"
    pos_txt = ("position as listed" if pos == "listed coordinates" else f"position approximate ({pos})")
    return (f"{head}: {row['what']}{_validity_clause(row['source_id'], row.get('valid_to'), as_of)} "
            f"({src.kind}, {src.short}, {row['locator']}). {LANE_NOT_LINKED} {minutes_label[0].upper()}"
            f"{minutes_label[1:]}; {pos_txt}. {OUTSIDE_TAIL}")


def _locator(source_id: str, r: Mapping) -> str:
    def i(v):
        return int(v) if not _blank(v) else "?"
    if source_id == "aerb_rt":
        return f"page {i(r.get('page'))} row {i(r.get('row_on_page'))}"
    if source_id == "ncg":
        return f"section '{_s(r.get('raw_section')) or '?'}' row {i(r.get('row_in_section'))}"
    if source_id == "mohfw_trauma":
        return f"page {i(r.get('page'))} S. No. {i(r.get('raw_sno'))}"
    if source_id == "nabi_burns":
        return f"sheet row {i(r.get('sheet_row'))}"
    if source_id == "nhp_bloodbank":
        return f"row Sr No {_s(r.get('raw_sr_no')) or '?'}"
    if source_id == "laqshya":
        return f"page {i(r.get('page'))} row {i(r.get('sno'))}"
    raise KeyError(source_id)


def _haversine(lat0: float, lon0: float, lat, lon) -> np.ndarray:
    la0, lo0 = math.radians(lat0), math.radians(lon0)
    la, lo = np.radians(np.asarray(lat, dtype=float)), np.radians(np.asarray(lon, dtype=float))
    h = np.sin((la - la0) / 2) ** 2 + math.cos(la0) * np.cos(la) * np.sin((lo - lo0) / 2) ** 2
    return 6371.0088 * 2 * np.arcsin(np.sqrt(np.clip(h, 0.0, 1.0)))


# ---------------------------------------------------------------------------
# Building the tables from silver
# ---------------------------------------------------------------------------

def _source_rows(source_id: str, silver: Path) -> tuple[Optional[pd.DataFrame], Optional[pd.DataFrame]]:
    lp, kp = silver / f"ext_{source_id}.parquet", silver / f"ext_links_{source_id}.parquet"
    if not lp.exists() or not kp.exists():
        return None, None
    return pd.read_parquet(lp), pd.read_parquet(kp)


def _card_facts_for(source_id: str, lst: pd.DataFrame, links: pd.DataFrame) -> pd.DataFrame:
    acc = links[links["accepted"].fillna(False).astype(bool)]
    if not len(acc):
        return pd.DataFrame(columns=_CARD_COLS)
    by_uid = lst.set_index("list_uid")
    rows = []
    for a in acc.itertuples():
        r = by_uid.loc[a.list_uid].to_dict()
        valid = _iso(r.get("renewal_date")) if source_id == "nhp_bloodbank" else None
        rows.append({"facility_id": a.facility_id, "source_id": source_id, "list_uid": a.list_uid,
                     "what": _s(r.get("what")) or "listed", "citation": _s(r.get("citation")) or "",
                     "locator": _locator(source_id, r), "valid_to": valid})
    return pd.DataFrame(rows, columns=_CARD_COLS)


def _lane_rows_for(source_id: str, lst: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Rows that may enter the lane before the look-alike check, and why the others did not."""
    why: dict[str, int] = {}
    if "lane_eligible" in lst.columns:
        ok = lst["lane_eligible"].fillna(False).astype(bool)
        for k, v in lst.loc[~ok, "lane_exclusion" if "lane_exclusion" in lst else "link_status"].fillna(
                "not eligible").value_counts().items():
            why[f"source rule: {k}"] = int(v)
    else:
        status = lst["link_status"].fillna("")
        ok = status.eq("not_in_dataset")
        for k, v in status[~ok].value_counts().items():
            why[f"source rule: {k}"] = int(v)
        if "best_unread_score" in lst:
            near = ok & lst["best_unread_score"].fillna(0.0).ge(NEAR_MISS_MIN)
            why["near_miss_low_candidate"] = int(near.sum())
            ok = ok & ~near
    located = lst["lat"].notna() & lst["lon"].notna()
    why["not_located"] = int((ok & ~located).sum())
    sel = lst[ok & located]
    line = OUTSIDE_SOURCES[source_id].lane_lines
    rows = []
    for r in sel.to_dict("records"):
        name = _s(r.get("name"))
        if not name:
            why["unnamed"] = why.get("unnamed", 0) + 1
            continue
        for sl in line:
            rows.append({"source_id": source_id, "list_uid": r["list_uid"], "service_line": sl, "name": name,
                         "district": _s(r.get("district")), "state": _s(r.get("state")), "pin": _s(r.get("pin")),
                         "lat": float(r["lat"]), "lon": float(r["lon"]),
                         "position_precision_km": None if _blank(r.get("position_precision_km"))
                         else float(r["position_precision_km"]),
                         "position_note": _s(r.get("position_note")), "what": _s(r.get("what")) or "listed",
                         "citation": _s(r.get("citation")) or "", "locator": _locator(source_id, r),
                         "valid_to": None, "group_key": _s(r.get("list_group")) or r["list_uid"]})
    return pd.DataFrame(rows, columns=_LANE_COLS), why


def _district_centres(silver: Path) -> dict:
    """NFHS district_key -> (lat, lon, precision_km, district, state) of the PIN directory's district
    centre (``places`` kind ``district``). A district_key that several PIN districts map to takes the one
    with the most post offices."""
    pl_path, dc_path = silver / "places.parquet", silver / "district_crosswalk.parquet"
    if not pl_path.exists() or not dc_path.exists():
        return {}
    places = pd.read_parquet(pl_path, columns=["kind", "state", "district", "lat", "lon", "precision_km",
                                                "n_offices"])
    d = places[places["kind"].eq("district")]
    dc = pd.read_parquet(dc_path, columns=["state_pin", "district_pin", "district_key"]).dropna()
    m = d.merge(dc, left_on=["state", "district"], right_on=["state_pin", "district_pin"], how="inner")
    m = m.sort_values("n_offices", ascending=False).drop_duplicates("district_key")
    return {r.district_key: (float(r.lat), float(r.lon), None if pd.isna(r.precision_km) else float(r.precision_km),
                             r.district, r.state) for r in m.itertuples()}


def _laqshya_lane(silver: Path, links: pd.DataFrame, as_of: date) -> tuple[pd.DataFrame, dict]:
    """LaQshya units with a maternity operating theatre row and no link to our records: no candidate at
    all, or only candidates a reviewer marked ``wrong``. Placed at their district centre: the list's OSM
    point matches came from place-name tokens and some are wrong."""
    p = silver / "ext_laqshya.parquet"
    why: dict[str, int] = {}
    if not p.exists():
        return pd.DataFrame(columns=_LANE_COLS), why
    lq = pd.read_parquet(p)
    units = lq.groupby("facility_uid")
    reviewed = links.groupby("facility_uid")["review"].agg(set).to_dict() if len(links) else {}
    centres = _district_centres(silver)
    rows = []
    for uid, g in units:
        ot = g[g["department"].eq("Maternity OT")]
        if not len(ot):
            why["no maternity OT row"] = why.get("no maternity OT row", 0) + 1
            continue
        rv = reviewed.get(uid, set())
        if rv - {"wrong"}:
            k = "linked or under review (" + ", ".join(sorted(rv - {"wrong"})) + ")"
            why[k] = why.get(k, 0) + 1
            continue
        c = centres.get(g["district_key"].iloc[0]) if isinstance(g["district_key"].iloc[0], str) else None
        if c is None:
            why["not_located"] = why.get("not_located", 0) + 1
            continue
        certs = [{"sno": int(r.sno), "page": int(r.page), "department": r.department,
                  "certification_status": r.certification_status,
                  "validity_date": None if pd.isna(r.validity_date) else pd.Timestamp(r.validity_date).date()}
                 for r in ot.itertuples()]
        best = sorted(certs, key=lambda x: _cert_order(x, as_of))[0]
        first = g.iloc[0]
        rows.append({"source_id": "laqshya", "list_uid": f"laqshya:{uid}", "service_line": "emoc",
                     "name": str(first.facility_name), "district": _s(first.district_raw),
                     "state": _s(c[4]) or _s(first.state_ut), "pin": None, "lat": c[0], "lon": c[1],
                     "position_precision_km": c[2], "position_note": "district centre",
                     "what": cert_what(best),
                     "citation": (f"LaQshya master list (NHSRC, 8 Apr 2026), page {best['page']}, S.No. "
                                  f"{best['sno']}; {SOURCE_URL}, retrieved {RETRIEVED.isoformat()}"),
                     "locator": f"page {best['page']} row {best['sno']}",
                     "valid_to": best["validity_date"].isoformat() if best["validity_date"] else None,
                     "group_key": f"laqshya:{uid}"})
    return pd.DataFrame(rows, columns=_LANE_COLS), why


def lookalike_ratio(core: str, ours: str, place_words: frozenset = frozenset()) -> float:
    """How well our record's distinctive words match a list row's. Token set ratio (one name's words all
    in the other's scores 100), except when the row's only distinctive word is a place name ("DH
    Gwalior" -> "gwalior"): then token sort ratio, so "Paridhi Hospital, Gwalior" does not count but
    "District Hospital, Gwalior" does."""
    from rapidfuzz import fuzz

    if not core or not ours:
        return 0.0
    if len(core.split()) == 1 and core in place_words:
        return float(fuzz.token_sort_ratio(core, ours))
    return float(fuzz.token_set_ratio(core, ours))


def _place_words(silver: Path) -> frozenset:
    """Single-word place names (districts, cities, post offices), lower case."""
    p = silver / "places.parquet"
    if not p.exists():
        return frozenset()
    n = pd.read_parquet(p, columns=["name_norm"])["name_norm"].dropna().astype(str).str.lower()
    return frozenset(w for w in n.unique() if w.isalpha() and len(w) > 2)


def _lookalike_filter(lane: pd.DataFrame, fac: Optional[pd.DataFrame],
                      place_words: frozenset = frozenset()) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split lane rows into (kept, dropped): a row is dropped when one of our records lies within
    max(LOOKALIKE_KM, its position precision, capped at LOOKALIKE_MAX_KM) and ``lookalike_ratio`` of the
    distinctive name words is LOOKALIKE_MIN or more."""

    if fac is None or not len(lane):
        return lane, lane.iloc[0:0].assign(lookalike_facility_id=None, lookalike_name=None, lookalike_km=None)
    f = fac[fac["lat"].notna() & fac["lon"].notna()]
    if "facility_type" in f:
        f = f[~f["facility_type"].isin(LOOKALIKE_SKIP_TYPES)]
    cores = [lane_core(n) for n in f["name"]]
    flat, flon = f["lat"].astype(float).to_numpy(), f["lon"].astype(float).to_numpy()
    keep, drop = [], []
    cache: dict[str, Optional[tuple]] = {}
    for r in lane.itertuples(index=False):
        key = f"{r.list_uid}"
        if key not in cache:
            core = lane_core(r.name)
            best = None
            if core:
                radius = min(max(LOOKALIKE_KM, r.position_precision_km or 0.0), LOOKALIKE_MAX_KM)
                km = _haversine(r.lat, r.lon, flat, flon)
                for i in np.flatnonzero(km <= radius):
                    if not cores[i]:
                        continue
                    q = lookalike_ratio(core, cores[i], place_words)
                    if q >= LOOKALIKE_MIN and (best is None or q > best[3]):
                        best = (f["facility_id"].iat[i], f["name"].iat[i], round(float(km[i]), 1), q)
            cache[key] = best
        (drop if cache[key] else keep).append(r._asdict())
    kept = pd.DataFrame(keep, columns=lane.columns)
    dropped = pd.DataFrame(drop, columns=lane.columns)
    if len(dropped):
        dropped["lookalike_facility_id"] = [cache[u][0] for u in dropped["list_uid"]]
        dropped["lookalike_name"] = [cache[u][1] for u in dropped["list_uid"]]
        dropped["lookalike_km"] = [cache[u][2] for u in dropped["list_uid"]]
    return kept, dropped


@dataclass
class OutsideLists:
    """The outside-list tables for ``annotate_answer``. ``card_facts``: one row
    per accepted link (five linked sources); ``laqshya``: the reviewed LaQshya
    links (v1 ``PublicList``); ``lane``: list rows not linked to our data, one
    row per (row, service line); ``meta``: counts and sources."""
    card_facts: pd.DataFrame
    lane: pd.DataFrame
    laqshya: PublicList
    meta: dict

    def __post_init__(self):
        self._facts: dict[str, list[dict]] = {}
        for r in self.card_facts.to_dict("records"):
            self._facts.setdefault(r["facility_id"], []).append(r)
        self._lane_by_line: dict[str, tuple[pd.DataFrame, np.ndarray, np.ndarray]] = {}
        for line, g in self.lane.groupby("service_line"):
            g = g.reset_index(drop=True)
            self._lane_by_line[line] = (g, g["lat"].to_numpy(float), g["lon"].to_numpy(float))

    # -- card facts --------------------------------------------------------
    def lines_for(self, facility_id: str, service_line: Optional[str], as_of: Optional[date] = None) -> list[dict]:
        """Outside lines for one record and service: LaQshya first (its best certificate), then the other
        sources in ``OUTSIDE_SOURCES`` order. An unknown or missing service line shows every source's
        card facts except blood banks."""
        as_of = as_of or date.today()

        def wanted(sid: str) -> bool:
            src = OUTSIDE_SOURCES[sid]
            if service_line in SERVICE_LINES_ALL:
                return service_line in src.card_lines
            return bool(src.card_lines) and sid != "nhp_bloodbank"

        out = []
        if wanted("laqshya"):
            ln = self.laqshya.line(facility_id, as_of=as_of)
            if ln is not None:
                cert = {"validity_date": ln["validity"], "department": ln["department"],
                        "certification_status": ln["status"]}
                src = OUTSIDE_SOURCES["laqshya"]
                out.append({"source_id": "laqshya", "source_name": src.source_name, "publisher": src.publisher,
                            "list_date": src.list_date, "what": cert_what(cert),
                            "status": outside_status(ln["validity"], as_of),
                            "citation": (f"LaQshya master list (NHSRC, 8 Apr 2026), page {ln['page']}, S.No. "
                                         f"{ln['row']}; {SOURCE_URL}, retrieved {ln['retrieved']}"),
                            "text": ln["text"]})
        order = {sid: i for i, sid in enumerate(OUTSIDE_SOURCES)}
        seen = set()
        for r in sorted(self._facts.get(facility_id, []), key=lambda x: order.get(x["source_id"], 99)):
            sid = r["source_id"]
            if not wanted(sid) or (sid, r["list_uid"]) in seen:
                continue
            seen.add((sid, r["list_uid"]))
            src = OUTSIDE_SOURCES[sid]
            vt = _s(r.get("valid_to"))
            out.append({"source_id": sid, "source_name": src.source_name, "publisher": src.publisher,
                        "list_date": src.list_date, "what": r["what"], "status": outside_status(vt, as_of),
                        "citation": r["citation"], "text": card_text(sid, r["locator"], r["what"], vt, as_of)})
        return out

    # -- the lane ------------------------------------------------------------
    def nearby(self, service_line: Optional[str], lat: Optional[float], lon: Optional[float],
               state: Optional[str] = None, k: int = LANE_MAX, as_of: Optional[date] = None) -> list[dict]:
        """Up to ``k`` listed facilities for ``service_line`` within its window from (lat, lon), nearest
        first, that are not linked to our data. Drive time from ``vf.travel`` when it is available and the
        origin is not an island; otherwise the line's straight-line km radius from ``vf.services``."""
        if service_line not in self._lane_by_line or lat is None or lon is None:
            return []
        try:
            lat, lon = float(lat), float(lon)
        except (TypeError, ValueError):
            return []
        if math.isnan(lat) or math.isnan(lon):
            return []
        as_of = as_of or date.today()
        g, glat, glon = self._lane_by_line[service_line]
        km = _haversine(lat, lon, glat, glon)
        reach = _reach(service_line, state)
        inside = km <= reach["km_limit"] + 1e-9
        if not inside.any():
            return []
        out, kept_cores = [], []
        # nearest first; ties (rows placed on one district centre) put a current certificate first, then
        # the more precise position, then list order
        cur = np.array([0 if outside_status(v, as_of) == "current" else 1 for v in g["valid_to"]])
        prec = g["position_precision_km"].astype(float).fillna(0.0).to_numpy()
        order = np.lexsort((np.arange(len(g)), prec, cur, np.round(km, 1)))
        for i in order:
            if not inside[i]:
                continue
            r = g.iloc[int(i)].to_dict()
            d = float(km[i])
            core = lane_core(r["name"])
            # one place listed twice: the same list group, or another source's row close by with the
            # same distinctive words (rows of one source are distinct entries of that list)
            dup = any(gk == r["group_key"]
                      or (sid != r["source_id"] and core and c and _same_place(core, c)
                          and _haversine(r["lat"], r["lon"], [la], [lo])[0] <= LANE_DEDUPE_KM)
                      for c, la, lo, gk, sid in kept_cores)
            if dup:
                continue
            label = _minutes_label(d, r.get("position_precision_km"), reach)
            src = OUTSIDE_SOURCES[r["source_id"]]
            vt = _s(r.get("valid_to"))
            out.append({
                "source_id": r["source_id"], "source_name": src.source_name, "name": nice_name(r["name"]),
                "district": nice_place(r.get("district")) or None, "state": nice_place(r.get("state")) or None,
                "pin": _s(r.get("pin")), "lat": round(float(r["lat"]), 6), "lon": round(float(r["lon"]), 6),
                "position_precision_km": None if _blank(r.get("position_precision_km"))
                else round(float(r["position_precision_km"]), 1),
                "position_note": r.get("position_note"), "minutes_label": label, "km": round(d, 1),
                "what": r["what"] + (_validity_clause(r["source_id"], vt, as_of) if r["source_id"] == "laqshya"
                                     else ""),
                "citation": r["citation"], "text": lane_text(r, label, as_of),
            })
            kept_cores.append((core, r["lat"], r["lon"], r["group_key"], r["source_id"]))
            if len(out) >= k:
                break
        return out


def _same_place(a: str, b: str) -> bool:
    from rapidfuzz import fuzz
    return fuzz.token_set_ratio(a, b) >= LOOKALIKE_MIN


def _service_lines_all() -> tuple:
    try:
        from .services import SERVICE_LINES
        return tuple(SERVICE_LINES)
    except Exception:  # services not importable: the twelve lines as shipped
        return ("emoc", "nicu", "dialysis", "icu", "cardiac_surgery", "cath_lab", "mri", "ct", "trauma",
                "burns", "neurosurgery", "oncology")


SERVICE_LINES_ALL = _service_lines_all()


def _reach(service_line: str, state: Optional[str]) -> dict:
    """The lane's reach for one origin: the service window in estimated drive minutes (``vf.travel``),
    or, where minutes cannot be estimated (islands) or ``vf.travel`` is missing, the line's km radius."""
    try:
        from . import services as S
        radius = float(S.RADII_KM.get(service_line, 50.0))
        win = S.WINDOWS.get(service_line) if hasattr(S, "WINDOWS") else None
    except Exception:
        S, radius, win = None, 50.0, None
    try:
        from . import travel
    except Exception:
        travel = None
    if travel is not None and win is not None:
        f = travel.state_factor(state)
        if f.value is not None:
            km_limit = travel.window_km(win.minutes, factor=f.value)
            return {"mode": "minutes", "minutes": float(win.minutes), "factor": f.value, "km_limit": km_limit,
                    "travel": travel}
        return {"mode": "km", "km_limit": radius, "why": travel.SEA_NOTE, "travel": travel}
    return {"mode": "km", "km_limit": radius, "why": "drive time not estimated", "travel": travel}


def _minutes_label(km: float, precision: Optional[float], reach: Mapping) -> str:
    """'about 25 min estimated drive, free-flow; real trips take longer', a range when the listed position
    is loose (precision above LOOSE_POSITION_KM: the range spans km minus to km plus the precision and the
    95% band of the drive-time fit), or the km fallback."""
    if reach["mode"] == "minutes":
        tr, f = reach["travel"], reach["factor"]
        loose = precision is not None and not _blank(precision) and float(precision) > LOOSE_POSITION_KM
        t, lo, hi = tr.minutes_range(km, f, None if not loose else float(precision), loose=loose)
        if loose:
            return (f"about {tr.fmt_minutes(lo)} to {tr.fmt_minutes(hi)} estimated drive, free-flow (the listed "
                    f"position is approximate); real trips take longer")
        return f"about {tr.fmt_minutes(t)} estimated drive, free-flow; real trips take longer"
    return (f"{km:.0f} km straight line, within the {reach['km_limit']:.0f} km radius for this service "
            f"({reach['why']})")


def _empty_outside(note: str) -> OutsideLists:
    return OutsideLists(pd.DataFrame(columns=_CARD_COLS), pd.DataFrame(columns=_LANE_COLS),
                        PublicList(pd.DataFrame(columns=["facility_id", "lq_name", "certs", "review"])),
                        {"version": OUTSIDE_VERSION, "note": note, "sources": {}})


def build_outside(data_dir: Optional[Path] = None, as_of: Optional[date] = None) -> OutsideLists:
    """Build the tables from ``silver/`` (``data_dir`` defaults to ``vf.paths.data_dir()``). A missing
    source is skipped and recorded in ``meta['sources'][id]['status']``. ``as_of`` only chooses which
    LaQshya certificate a lane row cites; status and wording are computed when an answer is served."""
    from . import paths

    root = Path(data_dir) if data_dir is not None else paths.data_dir()
    silver = root / "silver"
    as_of = as_of or date.today()
    meta = {"version": OUTSIDE_VERSION, "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "as_of": as_of.isoformat(), "lane_rules": {
                "near_miss_min": NEAR_MISS_MIN, "lookalike_km": LOOKALIKE_KM, "lookalike_max_km": LOOKALIKE_MAX_KM,
                "lookalike_min": LOOKALIKE_MIN, "dedupe_km": LANE_DEDUPE_KM}, "sources": {}}
    facts, lanes = [], []
    lq_links_path = silver / LINKS_FILE
    lq_links = pd.read_parquet(lq_links_path) if lq_links_path.exists() else pd.DataFrame(
        columns=["facility_uid", "facility_id", "lq_name", "certs", "review"])
    laq = PublicList(lq_links)
    lq_lane, lq_why = _laqshya_lane(silver, lq_links, as_of)
    meta["sources"]["laqshya"] = {"status": "ok" if lq_links_path.exists() else "missing",
                                  "card_links": int(lq_links["review"].eq("correct").sum()) if len(lq_links) else 0,
                                  "card_records": len(laq.facility_ids()), "lane_candidates": int(len(lq_lane)),
                                  "lane_excluded": lq_why}
    lanes.append(lq_lane)
    for sid in LINKED_SOURCES:
        lst, links = _source_rows(sid, silver)
        if lst is None:
            meta["sources"][sid] = {"status": "missing"}
            continue
        cf = _card_facts_for(sid, lst, links)
        facts.append(cf)
        info = {"status": "ok", "list_rows": int(len(lst)), "card_links": int(len(cf)),
                "card_records": int(cf["facility_id"].nunique()) if len(cf) else 0}
        if OUTSIDE_SOURCES[sid].lane_lines:
            ln, why = _lane_rows_for(sid, lst)
            lanes.append(ln)
            info.update({"lane_candidates": int(ln["list_uid"].nunique()), "lane_excluded": why})
        else:
            info.update({"lane_candidates": 0, "lane_excluded": {"card fact only source": int(len(lst))}})
        meta["sources"][sid] = info
    lane = pd.concat([x for x in lanes if len(x)], ignore_index=True) if any(len(x) for x in lanes) else \
        pd.DataFrame(columns=_LANE_COLS)
    fac_path = silver / "facilities.parquet"
    fac = pd.read_parquet(fac_path, columns=["facility_id", "name", "facility_type", "lat", "lon"]) \
        if fac_path.exists() else None
    lane, dropped = _lookalike_filter(lane, fac, _place_words(silver))
    for sid, n in dropped.groupby("source_id")["list_uid"].nunique().items():
        meta["sources"][sid].setdefault("lane_excluded", {})["nearby_lookalike_record"] = int(n)
    for sid in meta["sources"]:
        if "lane_candidates" in meta["sources"][sid]:
            meta["sources"][sid]["lane_rows"] = int(lane.loc[lane["source_id"].eq(sid), "list_uid"].nunique())
    meta["lookalike_examples"] = dropped.head(40)[["source_id", "list_uid", "name", "lookalike_name",
                                                   "lookalike_km"]].to_dict("records") if len(dropped) else []
    card = pd.concat([x for x in facts if len(x)], ignore_index=True) if any(len(x) for x in facts) else \
        pd.DataFrame(columns=_CARD_COLS)
    meta["card_facts"] = int(len(card))
    meta["lane_rows"] = int(len(lane))
    meta["sources_info"] = {k: v.as_dict() for k, v in OUTSIDE_SOURCES.items()}
    return OutsideLists(card.reset_index(drop=True), lane.reset_index(drop=True), laq, meta)


def write_bundle(out_dir: Path, outside: Optional[OutsideLists] = None) -> dict:
    """Write ``outside_card_facts.parquet``, ``outside_lane.parquet`` and ``outside_meta.json`` into a
    bundle folder (WP12), atomically. ``laqshya_links.parquet`` is shipped separately, as before.
    Returns {file name: bytes}."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    o = outside or build_outside()
    sizes = {}
    for name, df in ((CARD_FACTS_FILE, o.card_facts), (LANE_FILE, o.lane)):
        tmp = out_dir / (name + ".tmp")
        df.to_parquet(tmp, index=False)
        os.replace(tmp, out_dir / name)
        sizes[name] = (out_dir / name).stat().st_size
    tmp = out_dir / (OUTSIDE_META_FILE + ".tmp")
    tmp.write_text(json.dumps(o.meta, indent=1, default=str))
    os.replace(tmp, out_dir / OUTSIDE_META_FILE)
    sizes[OUTSIDE_META_FILE] = (out_dir / OUTSIDE_META_FILE).stat().st_size
    return sizes


def _bundle_candidates() -> list[Path]:
    here = Path(__file__).resolve()
    c = []
    if os.environ.get("VF_BUNDLE_DIR"):
        c.append(Path(os.environ["VF_BUNDLE_DIR"]))
    c.append(here.parents[1] / "bundle")  # dbapp/vf -> dbapp/bundle
    return c


def load_outside(path: Optional[Path] = None) -> OutsideLists:
    """The outside tables from ``path`` (a bundle folder holding ``outside_card_facts.parquet`` and
    ``outside_lane.parquet``, or a data folder holding ``silver/``). With no path: $VF_BUNDLE_DIR or the
    app's bundle folder when they hold the files, else built from ``silver/``. Nothing found gives empty
    tables (no lines, no lane), never an error."""
    cands = [Path(path)] if path is not None else _bundle_candidates()
    for d in cands:
        if (d / CARD_FACTS_FILE).exists() and (d / LANE_FILE).exists():
            meta = {}
            if (d / OUTSIDE_META_FILE).exists():
                try:
                    meta = json.loads((d / OUTSIDE_META_FILE).read_text())
                except ValueError:
                    meta = {}
            return OutsideLists(pd.read_parquet(d / CARD_FACTS_FILE), pd.read_parquet(d / LANE_FILE), load(d),
                                meta or {"version": OUTSIDE_VERSION})
        if (d / "silver").is_dir():
            return build_outside(d)
    if path is not None:
        return _empty_outside(f"no outside tables at {path}")
    try:
        from . import paths
        if (paths.data_dir() / "silver").is_dir():
            return build_outside()
    except Exception:
        pass
    return _empty_outside("no outside tables found")


@lru_cache(maxsize=1)
def default_outside() -> OutsideLists:
    return load_outside()


CARD_SECTIONS = ("results", "beyond_radius", "unverified_nearby", "further_away")


def annotate_answer(answer: dict, service_line: Optional[str], lat: Optional[float], lon: Optional[float],
                    state: Optional[str] = None, *, outside: Optional[OutsideLists] = None,
                    as_of: Optional[date] = None, k: int = LANE_MAX) -> dict:
    """Add ``outside_lines`` (a list, possibly empty) to every card dict in ``answer`` (``results``,
    ``beyond_radius``, ``unverified_nearby``, ``further_away``) and ``outside_nearby`` (up to ``k`` lane
    entries) to the answer. Nothing existing is changed or removed. Returns the same dict."""
    o = outside if outside is not None else default_outside()
    as_of = as_of or date.today()

    def tag(card):
        if isinstance(card, dict) and card.get("facility_id"):
            card["outside_lines"] = o.lines_for(str(card["facility_id"]), service_line, as_of=as_of)

    for sec in CARD_SECTIONS:
        v = answer.get(sec)
        if isinstance(v, list):
            for c in v:
                tag(c)
        else:
            tag(v)
    answer["outside_nearby"] = o.nearby(service_line, lat, lon, state, k=k, as_of=as_of)
    return answer
