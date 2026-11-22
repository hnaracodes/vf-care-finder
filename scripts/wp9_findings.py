"""WP9: one source of truth for every on-screen number (D044 item 6).

Build mode (default) computes ``findings.json`` from a frozen app bundle
(default ``dbapp/bundle``) and the reports' data files. Every number is one
entry::

    {key, value, display, source_file, how_computed, kind, group}

``value`` is the number (int or float); ``display`` is how the number is meant
to read on screen; ``source_file`` is the file it was computed from;
``how_computed`` states the one definition used. A source that is missing
gives an explicit null (``value: null``, ``display: null``) with a ``reason``,
never an invented number.

Groups:
  facilities   records and usable locations in the bundle
  grades       grade counts per service line, recommendable counts
  emoc         specialty label vs grade 2+ (one definition), blood questions,
               national coverage within 50 km
  tagger       Model A macro F1, shipped mix and regex, with interval and gold size
  trust        WP11 flag counts, blocked pairs, hand-review precision
  resolver     hit rate on the frozen hand-written cases (rerun live)
  scenarios    WP5 scenario run summary and the judges' marks
  query        the demo queries (the brief's five from labels/scenarios.csv,
               plus the cold-open and Nashik PINs), answered live from the bundle
  cold_open    the obvious SQL foil on the Marketplace rows, and typicality
  laqshya      government-list link numbers, when a hand-labeled link file exists
  sealed_eval  the sealed evaluation, when its result file exists
  extra.*      any JSON passed with --extra GROUP=FILE (numeric leaves)

Check mode scans text files (a script, a caption list, story JSON) for
numbers and fails, listing every number that is not in findings.json. It is a
token guard, not a context check:

- a number passes when some finding has the same value; a unit in the text
  must match the finding's kind ("73 km" needs a km finding, "86%" a share);
- "X of Y" and "X/Y/Z" runs pass only when all their numbers come from one
  group of findings (one parent key, or one query's gate funnel), so "1 of 13"
  cannot borrow two unrelated findings;
- it cannot tell which finding a sentence means ("0.93" passes if any finding
  shows 0.93). Captions that matter should name their finding as
  ``{{key}}`` (see ``render``) and be checked with ``--strict``, where every
  bare number fails.

Allowed without a finding: years, PIN codes, clock times, dates, list markers,
identifiers such as WP11 or v0.2, commit hashes, real paths and URLs,
citations such as "page 35, row 1109", demo ordinals such as "card 3" (no unit
after), and the emergency numbers 102, 108 and 112. Durations, money and glued
units (45 minutes, Rs.2000, 250mg) are scanned. A line containing
``findings:skip`` is skipped (for example a verbatim quote of a facility's
own text).

    python scripts/wp9_findings.py                          # writes reports/findings.json
    python scripts/wp9_findings.py --bundle dbapp/bundle --out reports/findings.json
    python scripts/wp9_findings.py --laqshya-links data/labels/laqshya_public_links_handlabeled.csv
    python scripts/wp9_findings.py --extra sealed_eval=reports/sealed_eval.json
    python scripts/wp9_findings.py --check reports/demo_script.md [more files]
    python scripts/wp9_findings.py --check captions.json --strict   # every number must be a {{key}}
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Iterable, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

VERSION = "wp9-findings-v1"
DEFAULT_BUNDLE = ROOT / "dbapp" / "bundle"
DEFAULT_OUT = ROOT / "reports" / "findings.json"
OBS_SPECIALTY = "gynecologyAndObstetrics"
COLD_OPEN_PIN = "226001"
#: the obvious query's filters (reports/standout_brainstorm.md): try_cast, India's bounding box
INDIA_BBOX = {"lat": (6.0, 38.0), "lon": (68.0, 98.0)}
CAESAREAN_RE = r"caesar|cesar|c-section|c section|lscs|csection"
#: demo queries beyond the brief's five (scenario ids starting "brief_")
EXTRA_QUERIES = [
    {"id": "cold_open_226001", "service": "emoc", "place_text": COLD_OPEN_PIN, "state_hint": None,
     "why": "cold open: the brief's Lucknow C-section query at a PIN precise to about 2 km"},
    {"id": "highrisk_422002", "service": "emoc", "place_text": "422002", "state_hint": None,
     "why": "Maharashtra beat: Nashik PIN precise to about 2 km"},
    {"id": "remote_emoc_gadchiroli", "service": "emoc", "place_text": "Gadchiroli", "state_hint": "Maharashtra",
     "why": "the shown miss: Gadchiroli's nearest reliable option"},
]
#: candidate locations for optional sources, first existing wins (repo-relative)
LAQSHYA_CANDIDATES = [
    "data/labels/laqshya_public_links_handlabeled.csv",
    "data/labels/laqshya_links_handlabeled.csv",
    "reports/laqshya_links.json",
]
SEALED_CANDIDATES = [
    "reports/sealed_eval.json",
    "reports/eval/sealed_eval.json",
    "reports/eval/results.json",
]
MAX_LIST_FLATTEN = 20


# ---------------------------------------------------------------------------
# number formatting and tokens (shared by build and check)
# ---------------------------------------------------------------------------

def canon(x) -> str:
    """Canonical text of a number: no thousands commas, no trailing zeros ("0.90" -> "0.9")."""
    s = str(x).replace(",", "").strip()
    d = Decimal(s)
    if d == d.to_integral_value():
        return str(int(d))
    return format(d.normalize(), "f")


def _round_half_up(v: float, places: int) -> str:
    q = Decimal(1).scaleb(-places) if places > 0 else Decimal(1)
    return canon(Decimal(repr(float(v))).quantize(q, rounding=ROUND_HALF_UP))


def fmt_display(value, kind: str, digits: Optional[int] = None) -> Optional[str]:
    if value is None:
        return None
    if kind == "count":
        return f"{int(value):,}"
    if kind == "share":
        d = 0 if digits is None else digits
        return f"{float(value) * 100:.{d}f}%"
    if kind == "km":
        d = 1 if digits is None else digits
        return f"{float(value):,.{d}f} km"
    if kind == "ms":
        return f"{float(value):.{1 if digits is None else digits}f} ms"
    if kind == "seconds":
        return f"{float(value):.{1 if digits is None else digits}f} s"
    if kind == "score":
        return f"{float(value):.{2 if digits is None else digits}f}"
    if isinstance(value, int):
        return f"{value:,}"
    d = (2 if abs(float(value)) < 10 else 1) if digits is None else digits
    return f"{float(value):,.{d}f}"


#: a number: not glued to a letter or digit before it, and not the fraction of "0.2" (so "v0.2" is one
#: identifier) but scanned after a period that ends a word ("Rs.2000")
_NUM_RE = re.compile(r"(?<![\w])(?<!\d\.)(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?(?!\d)")
_PCT_AFTER = re.compile(r"\s*(%|percent\b|per cent\b)", re.I)
#: units that tie a number to a finding's kind: "73 km" must match a km finding, "12 ms" an ms finding
_UNIT_AFTER = {
    "km": re.compile(r"\s*(?:km|kms|kilomet(?:er|re)s?)\b", re.I),
    "ms": re.compile(r"\s*(?:ms|milliseconds?)\b", re.I),
}
_KIND_UNIT = {"km": "km", "ms": "ms"}
_CURRENCY_BEFORE = re.compile(r"(?:\b(?:rs|inr|usd)\.?|[₹$])\s*$", re.I)
_CURRENCY_AFTER = re.compile(r"\s*(?:rupees|rs\b|inr\b|crores?|lakhs?|dollars|usd\b)", re.I)
_MONTH = (r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|"
          r"Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)")
_NO_MEASURE_AFTER = r"(?![\d]|[.,]\d|\s*(?:%|percent|km|kms|kilomet|ms\b|minutes?|mins?|hours?|hrs?|days?|seconds?))"
_EXT = r"(?:py|md|csv|json|parquet|txt|ipynb|html?|js|ts|css|png|jpe?g|svg|gif|ya?ml|toml|npz|pdf|sh|mp4|mov|tsv|xlsx?)"
_ALLOW_SPANS = {
    "time": re.compile(r"(?<![\d.])\d{1,2}:\d{2}(?::\d{2})?(?![\d])"),
    "date": re.compile(rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTH}\b|\b{_MONTH}\.?\s+\d{{1,2}}(?:st|nd|rd|th)?\b"
                       r"|\b\d{4}-\d{2}-\d{2}(?:T[\d:.]*Z?)?(?!\d)|\b\d{1,2}/\d{1,2}/\d{4}\b"),
    "list_marker": re.compile(r"^\s*\d+[.)]\s"),
    # real paths and URLs only: a URL, a token starting with /, ./, ../ or ~/, or one ending in a file
    # extension. "1/13" and "9,989/9,671/118" are ratios and funnels, and are scanned
    "path": re.compile(rf"https?://\S+|(?:^|(?<=[\s(\[{{<\"'`]))(?:\.{{1,2}}/|~/|/)\S*"
                       rf"|(?<![\w/])[\w.\-]*[A-Za-z][\w.\-]*/\S*\.{_EXT}\b"),
    # citations: "page 35, row 1109", "table 2"; an integer that names a place, never a measured number
    "citation": re.compile(rf"\b(?:page|pages|p\.|row|rows|table|figure|fig\.|section|no\.|#)\s*\d+{_NO_MEASURE_AFTER}"
                           rf"(?:\s*(?:to|-|and)\s*\d+{_NO_MEASURE_AFTER})?", re.I),
    # ordinals of the demo: "card 3", "step 2", "beat 4", "option 1"; one or two digits, no unit after
    "ordinal": re.compile(rf"\b(?:card|step|beat|option|rank)\s+\d{{1,2}}{_NO_MEASURE_AFTER}", re.I),
    # commit hashes and other hex identifiers (letters and digits mixed, 7 or more characters)
    "hex_id": re.compile(r"\b(?=[0-9a-f]*[a-f])(?=[0-9a-f]*\d)[0-9a-f]{7,64}\b"),
}
#: a caption may name its finding: "{{laqshya.correct_with_current_maternity_ot}} of ..."
_PLACEHOLDER = re.compile(r"\{\{\s*([\w.\-]+)\s*\}\}")
#: "X of Y", "X out of Y", "X of the Y", "X/Y/Z" (plain counts, no unit): the numbers must come from one
#: group of findings (see _scope)
_RATIO_JOIN = re.compile(r"^\s*(?:(?:out\s+)?of(?:\s+the)?|/)\s*$", re.I)
#: emergency phone numbers in India (ambulance 108, maternal transport 102, police/all 112)
EMERGENCY_NUMBERS = {"102", "108", "112"}
CHECK_NOTE = ("--check is a token guard, not a context check: a number passes when some finding with the same "
              "value and unit (km, ms, %) exists, and an 'X of Y' or 'X/Y' ratio passes when both numbers come "
              "from one group. It cannot tell which finding a sentence means; name it with {{key}} and use "
              "--strict for captions that matter")


def extract_numbers(text: str) -> list[tuple[str, bool, int, str]]:
    """Every number in ``text`` as (canonical, is_percent, start, raw). No allowlist."""
    out = []
    for m in _NUM_RE.finditer(text or ""):
        raw = m.group(0)
        pct = bool(_PCT_AFTER.match(text, m.end()))
        out.append((canon(raw), pct, m.start(), raw))
    return out


def _unit_after(text: str, end: int) -> Optional[str]:
    if _PCT_AFTER.match(text, end):
        return "%"
    for u, rx in _UNIT_AFTER.items():
        if rx.match(text, end):
            return u
    return None


def _allowed_reason(raw: str, tok: str) -> Optional[str]:
    if "," in raw or "." in raw:
        return None
    n = int(tok)
    if len(raw) == 4 and 1900 <= n <= 2100:
        return "year"
    if len(raw) == 6 and 100000 <= n <= 999999:
        return "pin"
    if raw in EMERGENCY_NUMBERS:
        return "emergency_number"
    return None


_GLUED = re.compile(r"[A-Za-z]+")
#: letters glued after a number that make it an ordinal or a model name ("3rd", "student 1b"); any other
#: letters ("250mg", "45min", "50km", "3x") leave it a measure that is scanned
_IDENT_SUFFIXES = {"st", "nd", "rd", "th", "b"}


def _mask(text: str, rx: re.Pattern) -> str:
    return rx.sub(lambda m: " " * len(m.group(0)), text)


def scan_line(line: str) -> list[dict]:
    """Numbers in one line that need a finding, with the allowlist applied.

    Each item: token (canonical), pct, unit ("%", "km", "ms" or None), raw, col, and ratio (an id shared
    by the numbers of one "X of Y" or "X/Y/Z" run, else None)."""
    masked = _mask(line, _PLACEHOLDER)
    for rx in _ALLOW_SPANS.values():
        masked = _mask(masked, rx)
    out = []
    for tok, pct, start, raw in extract_numbers(masked):
        end = start + len(raw)
        money = bool(_CURRENCY_BEFORE.search(masked[:start]) or _CURRENCY_AFTER.match(masked, end))
        if not money and _allowed_reason(raw, tok):
            continue
        glued = _GLUED.match(masked, end)
        if glued and glued.group(0).lower() in _IDENT_SUFFIXES:
            continue
        unit = _unit_after(masked, end)
        out.append({"token": tok, "pct": unit == "%", "unit": unit, "raw": raw + ("%" if unit == "%" else ""),
                    "col": start, "end": end, "ratio": None})
    rid = 0
    for a, b in zip(out, out[1:]):
        if a["unit"] is None and b["unit"] is None and _RATIO_JOIN.match(masked[a["end"]:b["col"]]):
            if a["ratio"] is None:
                rid += 1
                a["ratio"] = rid
            b["ratio"] = a["ratio"]
    return out


def accepted_tokens(f: dict) -> set[tuple[str, Optional[str]]]:
    """What a finding allows a text to say, as (token, unit): every number in its display, and its value
    rounded half-up (shares also as percentages). unit is "%" for a percentage, "km" or "ms" for findings of
    that kind, else None."""
    v = f.get("value")
    if v is None or isinstance(v, bool):
        return set()
    kind = f.get("kind", "number")
    ku = _KIND_UNIT.get(kind)
    out = {(t, "%" if p else ku) for t, p, _, _ in extract_numbers(f.get("display") or "")}
    if isinstance(v, int):
        out.add((canon(v), ku))
        if kind == "share":
            out.add((canon(v * 100), "%"))
        return out
    v = float(v)
    if math.isnan(v) or math.isinf(v):
        return out
    for places in (1, 2, 3):
        out.add((_round_half_up(abs(v), places), ku))
    if abs(v) >= 10:
        out.add((_round_half_up(abs(v), 0), ku))
    if kind == "share":
        for places in (0, 1):
            out.add((_round_half_up(abs(v) * 100, places), "%"))
    return out


def _scope(key: str) -> str:
    """The group a ratio's numbers must share: the key's parent ("emoc.records_naming_csection" ->
    "emoc"), except that one query's gate counts and its results count form one funnel
    ("query.X.gate.within_reach.n", "query.X.results" -> "query.X.gate")."""
    parts = key.split(".")
    if parts[0] == "query" and len(parts) >= 3 and parts[2] in ("gate", "results"):
        return ".".join(parts[:2] + ["gate"])
    return ".".join(parts[:-1]) or key


def render(text: str, findings: dict) -> str:
    """Replace each {{key}} with that finding's display. Raises KeyError for a missing or null key."""
    d = {f["key"]: f for f in findings.get("findings", [])}

    def sub(m):
        f = d.get(m.group(1))
        if f is None or f.get("value") is None:
            raise KeyError(f"finding {m.group(1)!r} is missing or null")
        return f["display"]
    return _PLACEHOLDER.sub(sub, text)


# ---------------------------------------------------------------------------
# the findings collector
# ---------------------------------------------------------------------------

class Findings:
    def __init__(self, data_dir: Path):
        self.items: list[dict] = []
        self.keys: set[str] = set()
        self.sources: dict[str, Optional[str]] = {}
        self.warnings: list[str] = []
        self.caveats: list[str] = []
        self.data_dir = data_dir

    def rel(self, p) -> str:
        p = Path(p).resolve()
        for base in (ROOT, self.data_dir.parent):
            try:
                return str(p.relative_to(base.resolve()))
            except ValueError:
                pass
        return str(p)

    def _src(self, source) -> str:
        r = self.rel(source)
        if r not in self.sources:
            p = Path(source)
            self.sources[r] = _sha256(p) if p.is_file() else None
        return r

    def add(self, key: str, value, *, source, how: str, kind: str = "number",
            display: Optional[str] = None, digits: Optional[int] = None) -> None:
        if key in self.keys:
            raise ValueError(f"duplicate finding key {key}")
        if value is not None and not isinstance(value, bool):
            if hasattr(value, "item"):
                value = value.item()  # numpy scalar
            if isinstance(value, float) and value.is_integer() and kind == "count":
                value = int(value)
            if isinstance(value, float):
                value = round(value, 6)
        self.keys.add(key)
        self.items.append({
            "key": key, "group": key.split(".")[0], "value": value, "kind": kind,
            "display": display if display is not None else fmt_display(value, kind, digits),
            "source_file": self._src(source), "how_computed": how,
        })

    def null(self, key: str, *, source, reason: str, how: str = "", kind: str = "number") -> None:
        if key in self.keys:
            raise ValueError(f"duplicate finding key {key}")
        self.keys.add(key)
        self.items.append({
            "key": key, "group": key.split(".")[0], "value": None, "kind": kind, "display": None,
            "source_file": self.rel(source) if source else None, "how_computed": how, "reason": reason,
        })


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(s).lower()).strip("_")


def _first_existing(cands: Iterable[str]) -> Optional[Path]:
    for c in cands:
        p = ROOT / c
        if p.exists():
            return p
    return None


# ---------------------------------------------------------------------------
# sections
# ---------------------------------------------------------------------------

def facilities_section(F: Findings, b: Path, fac) -> None:
    src = b / "facilities.parquet"
    n = len(fac)
    usable = int((fac["lat"].notna() & fac["lon"].notna()).sum())
    F.add("facilities.records", n, source=src, kind="count",
          how="rows of the bundle's facilities.parquet: one per real facility after WP1 removed duplicate ids")
    F.add("facilities.usable_locations", usable, source=src, kind="count",
          how="records with both lat and lon set in the bundle (null when missing, outside India, or "
              "excluded_unresolved by the WP1 location check)")
    F.add("facilities.no_usable_location", n - usable, source=src, kind="count",
          how="records with lat or lon null in the bundle")
    op = fac["operator_type"].fillna("unknown")
    known = op[op != "unknown"]
    for v, c in op.value_counts().sort_index().items():
        F.add(f"facilities.operator.{_slug(v)}", int(c), source=src, kind="count",
              how=f"records whose operator_type is {v!r} ('unknown' = null in the record)")
    if n:
        F.add("facilities.private_share", float((op == "private").sum() / n), source=src, kind="share",
              how="private records / all bundle records (records with no operator_type count as not private)")
    if len(known):
        F.add("facilities.private_share_of_known_operator", float((known == "private").mean()), source=src,
              kind="share", how="private records / records with a known operator_type")


def grades_section(F: Findings, b: Path, grades, idx, S) -> None:
    src = b / "grades.parquet"
    for line in S.SERVICE_LINES:
        g = grades[grades["service_line"] == line]
        if not len(g):
            F.null(f"grades.{line}.grade_2plus", source=src, reason=f"no {line} rows in grades.parquet")
            continue
        label = S.label(line)
        vc = g["grade"].value_counts()
        for k, name in ((0, "none"), (1, "claimed"), (2, "supported"), (3, "corroborated")):
            F.add(f"grades.{line}.grade_{k}", int(vc.get(k, 0)), source=src, kind="count",
                  how=f"{label}: records graded {k} ({name})")
        F.add(f"grades.{line}.grade_2plus", int((g["grade"] >= 2).sum()), source=src, kind="count",
              how=f"{label}: records graded 2 or 3 (supported or corroborated by facts in the record)")
        ld = idx.lines.get(line)
        if ld is not None:
            rec = (ld.grade >= 2) & ~ld.blocked
            F.add(f"grades.{line}.recommendable", int(rec.sum()), source=src, kind="count",
                  how=f"{label}: graded 2+ and no blocking (major) WP11 flag for this line, as vf.rank.load_index "
                      "builds it from the bundle; location not required")
    g2 = grades[grades["grade"] >= 2]
    F.add("grades.pairs_grade_2plus", len(g2), source=src, kind="count",
          how="facility-line pairs graded 2 or 3, over all 12 lines")


def emoc_section(F: Findings, b: Path, grades, idx, data_dir: Path) -> None:
    import numpy as np
    import pandas as pd
    from sklearn.neighbors import BallTree

    gsrc = b / "grades.parquet"
    em = grades[grades["service_line"] == "emoc"]
    has = lambda t: em["tags_present"].map(lambda x: t in list(x) if x is not None else False)  # noqa: E731
    cs, blood = has("csection"), has("blood")
    n_cs = int(cs.sum())
    F.add("emoc.grade_2plus", int((em["grade"] >= 2).sum()), source=gsrc, kind="count",
          how="emergency C-section line graded 2+; under recipe v0.2 grade 2 needs a fact tagged csection "
              "(a caesarean named in the record)")
    F.add("emoc.records_naming_csection", n_cs, source=gsrc, kind="count",
          how="emoc rows whose tags_present includes csection: the record has a fact naming a caesarean")
    F.add("emoc.csection_records_without_blood", int((cs & ~blood).sum()), source=gsrc, kind="count",
          how="of the records naming a caesarean, those with no fact tagged blood (blood bank or transfusion)")
    if n_cs:
        F.add("emoc.csection_records_without_blood_share", float((cs & ~blood).sum() / n_cs), source=gsrc,
              kind="share", how="csection records without a blood fact / csection records")

    sil = data_dir / "silver" / "facilities.parquet"
    if not sil.exists():
        for k in ("emoc.specialty_label_records", "emoc.coverage_50km_specialty_label"):
            F.null(k, source=sil, reason="silver/facilities.parquet not found; the bundle has no specialties column")
        return
    spec = pd.read_parquet(sil, columns=["facility_id", "specialties"]).set_index("facility_id")["specialties"]
    lab = idx.fac["facility_id"].map(lambda i: OBS_SPECIALTY in list(spec.get(i, []) if spec.get(i) is not None
                                                                     else [])).to_numpy(dtype=bool)
    F.add("emoc.specialty_label_records", int(lab.sum()), source=sil, kind="count",
          how=f"bundle records whose deduped specialties list (silver/facilities.parquet) contains "
              f"{OBS_SPECIALTY!r}. One definition: a specialty label is a claim, not a service")
    lab_g2 = int((lab & (idx.lines["emoc"].grade >= 2)).sum()) if "emoc" in idx.lines else None
    if lab_g2 is not None:
        F.add("emoc.specialty_label_records_grade_2plus", lab_g2, source=sil, kind="count",
              how=f"records listing {OBS_SPECIALTY!r} that are also graded 2+ on the emoc line")

    po = idx.post_offices
    po_src = b / "post_offices.parquet"
    if po is None or not len(po) or "emoc" not in idx.lines:
        F.null("emoc.coverage_50km_specialty_label", source=po_src, reason="no post offices in the bundle")
        return
    pts = po[["lat", "lon"]].drop_duplicates()
    P = np.radians(pts.to_numpy())
    ok = np.isfinite(idx.lat) & np.isfinite(idx.lon)
    F.add("emoc.post_office_points", len(pts), source=po_src, kind="count",
          how="distinct (lat, lon) points in the bundle's post_offices.parquet (non-suspect geocoded offices, "
              "one row per placeholder point)")
    R = 50.0
    for name, mask, what in (
            ("specialty_label", lab & ok, f"a record listing {OBS_SPECIALTY!r}"),
            ("grade_2plus", (idx.lines["emoc"].grade >= 2) & ok, "a record graded 2+ on emoc (names a caesarean)")):
        X = np.radians(np.c_[idx.lat[mask], idx.lon[mask]])
        d, _ = BallTree(X, metric="haversine").query(P, k=1)
        share = float((d[:, 0] * 6371.0088 <= R).mean())
        F.add(f"emoc.coverage_50km_{name}", share, source=po_src, kind="share", digits=1,
              how=f"share of those post-office points within {R:.0f} km straight-line of {what} with a usable "
                  "location. A fact about records, not about care")


def tagger_section(F: Findings, data_dir: Path) -> None:
    rep = data_dir / "gold" / "model_a_report.json"
    md = ROOT / "reports" / "model_a_results.md"
    keys = ("tagger.shipped.macro_f1", "tagger.regex.macro_f1", "tagger.gold_rows")
    if not rep.exists():
        for k in keys:
            F.null(k, source=rep, reason="gold/model_a_report.json not found (WP4 not run)")
        return
    r = json.loads(rep.read_text())
    gs = r.get("gold_scores", {})
    names = {"shipped": "shipped mix (WP4 student on 4 tags, regex v0.2 on the rest)",
             "regex": "regex v0.2", "teacher": "LLM teacher labels", "student_1b": "student 1b alone"}
    rows = None
    for m, what in names.items():
        s = (gs.get(m) or {}).get("summary")
        if not s:
            F.null(f"tagger.{m}.macro_f1", source=rep, reason=f"no gold_scores.{m} in model_a_report.json")
            continue
        mac = s["macro"]
        lo, hi = mac.get("f1_ci95", [None, None])
        F.add(f"tagger.{m}.macro_f1", mac["f1"], source=rep, kind="score",
              display=f"{mac['f1']:.2f} ({lo:.2f} to {hi:.2f})" if lo is not None else None,
              how=f"{what}: macro F1 over {s['tags']} evidence tags on {s['rows']} agent-labeled gold facts; "
                  "95% interval from a stratified bootstrap (2,000 resamples)")
        if lo is not None:
            F.add(f"tagger.{m}.macro_f1_ci_low", lo, source=rep, kind="score", how=f"{what}: lower end of the 95% interval")
            F.add(f"tagger.{m}.macro_f1_ci_high", hi, source=rep, kind="score", how=f"{what}: upper end of the 95% interval")
        F.add(f"tagger.{m}.macro_precision", mac["precision"], source=rep, kind="score", how=f"{what}: macro precision on gold")
        F.add(f"tagger.{m}.macro_recall", mac["recall"], source=rep, kind="score", how=f"{what}: macro recall on gold")
        rows = rows or (s["rows"], s["tags"])
    if rows:
        F.add("tagger.gold_rows", rows[0], source=rep, kind="count",
              how="distinct fact texts in the gold set (labels/gold_facts.csv), labeled by agents, never used for training")
        F.add("tagger.tags", rows[1], source=rep, kind="count", how="evidence tags scored (recipe v0.2)")
    d = ((r.get("paired_delta_vs_regex") or {}).get("shipped") or {}).get("macro_f1_delta_ci95")
    if d:
        F.add("tagger.shipped_minus_regex_ci_low", d[0], source=rep, kind="score", digits=3,
              how="paired bootstrap 95% interval of shipped minus regex macro F1 on gold: lower end")
        F.add("tagger.shipped_minus_regex_ci_high", d[1], source=rep, kind="score", digits=3,
              how="paired bootstrap 95% interval of shipped minus regex macro F1 on gold: upper end")
    shipped = r.get("shipped_tags")
    if shipped is not None:
        F.add("tagger.student_tags_shipped", len(shipped), source=rep, kind="count",
              how=f"tags the student model ships on ({', '.join(shipped)}); regex v0.2 on the rest")
    sp = r.get("split") or {}
    for k in ("train", "val"):
        if k in sp:
            F.add(f"tagger.teacher_texts_{k}", sp[k], source=rep, kind="count",
                  how=f"teacher-labeled texts in the student's {k} split")
    if md.exists() and "tagger.shipped.macro_f1" in F.keys:
        shown = next(x for x in F.items if x["key"] == "tagger.shipped.macro_f1")["display"]
        if shown and shown.split(" ")[0] not in md.read_text():
            F.warnings.append(f"reports/model_a_results.md does not show the shipped macro F1 {shown}")


def trust_section(F: Findings, b: Path, flags, dups, grades, idx, data_dir: Path) -> None:
    import pandas as pd

    src = b / "flags.parquet"
    F.add("trust.flag_rows", len(flags), source=src, kind="count", how="rows in the bundle's flags.parquet (WP11)")
    F.add("trust.facilities_flagged", int(flags["facility_id"].nunique()), source=src, kind="count",
          how="distinct records with at least one WP11 flag")
    if len(idx.fac):
        F.add("trust.facilities_flagged_share", float(flags["facility_id"].nunique() / len(idx.fac)), source=src,
              kind="share", how="records with a flag / bundle records")
    for (flag, sev), g in flags.groupby(["flag", "severity"]):
        F.add(f"trust.{flag}.{sev}.rows", len(g), source=src, kind="count", how=f"{flag} flags of severity {sev}")
        F.add(f"trust.{flag}.{sev}.facilities", int(g["facility_id"].nunique()), source=src, kind="count",
              how=f"distinct records with a {sev} {flag} flag")
    for sig, c in flags["signal"].value_counts().sort_index().items():
        F.add(f"trust.signal.{_slug(sig)}", int(c), source=src, kind="count", how=f"flag rows whose signal is {sig!r}")
    blocked, fac_blocked = 0, set()
    for line, ld in idx.lines.items():
        m = (ld.grade >= 2) & ld.blocked
        blocked += int(m.sum())
        fac_blocked |= set(idx.fac["facility_id"].to_numpy()[m])
    F.add("trust.blocked_pairs", blocked, source=src, kind="count",
          how="facility-line pairs graded 2+ that a major flag blocks from recommendation (the record's own "
              "major flags, or a major implausible_claim on that line), as vf.rank.load_index builds them")
    F.add("trust.blocked_facilities", len(fac_blocked), source=src, kind="count",
          how="distinct records with at least one blocked graded 2+ line")
    F.add("trust.duplicate_pairs", len(dups), source=b / "duplicates.parquet", kind="count",
          how="likely-duplicate record pairs (WP11); the app shows one card and 'also listed as'")

    rv = data_dir / "labels" / "flag_review.csv"
    if not rv.exists():
        F.null("trust.review.rows", source=rv, reason="labels/flag_review.csv not found")
        return
    r = pd.read_csv(rv, dtype=str, keep_default_na=False)
    F.add("trust.review.rows", len(r), source=rv, kind="count",
          how="rows in the WP11 hand review, read by an agent (not by Hruday or a clinician)")
    for mk, c in r["mark"].value_counts().sort_index().items():
        F.add(f"trust.review.{_slug(mk)}", int(c), source=rv, kind="count", how=f"review rows marked {mk!r}")
    for (rnd, chk, sig, smp), g in r.groupby(["round", "check", "signal", "sample"], sort=True):
        k = f"trust.review.r{rnd}.{_slug(chk)}.{_slug(sig) or 'none'}.{_slug(smp)}"
        right, wrong = int((g["mark"] == "correct").sum()), int((g["mark"] == "wrong").sum())
        what = f"round {rnd}, check {chk}, signal {sig or '(none)'}, sample {smp}"
        F.add(f"{k}.correct", right, source=rv, kind="count", how=f"{what}: rows marked correct")
        F.add(f"{k}.reviewed", right + wrong, source=rv, kind="count",
              how=f"{what}: rows marked correct or wrong (unsure left out)")
        if right + wrong:
            F.add(f"{k}.precision", right / (right + wrong), source=rv, kind="share",
                  how=f"{what}: correct / (correct + wrong)")


def resolver_section(F: Findings, b: Path, resolver, data_dir: Path) -> None:
    from vf import locate

    cases_p = data_dir / "labels" / "resolver_cases.csv"
    if not cases_p.exists() or resolver is None:
        F.null("resolver.hits", source=cases_p, reason="resolver cases or the bundle's places.parquet not found")
        return
    cases = locate.load_cases(cases_p)
    hits, by_status = 0, {}
    for _, c in cases.iterrows():
        res = resolver.resolve(c["query"], c["state"] or None)
        hit, _ = locate.score_case(res, c["expected_status"], c["expected_kind"], c["expected_districts"])
        hits += int(hit)
        s = by_status.setdefault(c["expected_status"], [0, 0])
        s[0] += int(hit)
        s[1] += 1
    how = ("rerun now: vf.locate.Resolver on the bundle's places.parquet against the frozen hand-written cases "
           "(labels/resolver_cases.csv), scored with vf.locate.score_case. The cases were written by the agent "
           "that wrote the resolver")
    F.add("resolver.cases", len(cases), source=cases_p, kind="count", how=how)
    F.add("resolver.hits", hits, source=cases_p, kind="count", how=how)
    F.add("resolver.hit_rate", hits / len(cases), source=cases_p, kind="share", how="hits / cases")
    for st, (h, n) in sorted(by_status.items()):
        F.add(f"resolver.{_slug(st)}.hits", h, source=cases_p, kind="count", how=f"hits among cases expecting {st}")
        F.add(f"resolver.{_slug(st)}.cases", n, source=cases_p, kind="count", how=f"cases expecting {st}")
    md = ROOT / "reports" / "resolver_eval.md"
    if md.exists():
        m = re.search(r"\*\*(\d+) of (\d+)\*\*", md.read_text())
        if m and (int(m.group(1)), int(m.group(2))) != (hits, len(cases)):
            F.warnings.append(f"reports/resolver_eval.md says {m.group(1)} of {m.group(2)}; rerun gives {hits} of {len(cases)}")


def scenarios_section(F: Findings, data_dir: Path) -> None:
    import pandas as pd

    raw = ROOT / "reports" / "ranking_scenarios_raw.json"
    if raw.exists():
        s = json.loads(raw.read_text()).get("summary", {})
        how = "summary of the WP5 scenario run (scripts/wp5_scenarios.py) on labels/scenarios.csv"
        for k in ("scenarios", "resolved", "abstentions", "recipe_resolved", "recipe_abstentions", "cards_shown",
                  "cards_with_a_flag", "status_as_expected", "expected_abstentions",
                  "expected_abstentions_that_abstained"):
            if k in s:
                F.add(f"scenarios.{k}", s[k], source=raw, kind="count", how=f"{how}: {k.replace('_', ' ')}")
        for k in ("abstention_rate", "recipe_abstention_rate"):
            if k in s:
                F.add(f"scenarios.{k}", s[k], source=raw, kind="share", how=f"{how}: {k.replace('_', ' ')}")
        for k, v in (s.get("status_counts") or {}).items():
            F.add(f"scenarios.status.{k}", v, source=raw, kind="count", how=f"{how}: answers with status {k}")
        for k, v in (s.get("cards_by_confidence") or {}).items():
            F.add(f"scenarios.cards_confidence.{k}", v, source=raw, kind="count", how=f"{how}: cards with {k} confidence")
        lc = s.get("latency_check") or {}
        for k in ("ms_median", "ms_p95", "ms_max"):
            if k in lc:
                F.add(f"scenarios.latency.{k}", lc[k], source=raw, kind="ms",
                      how=f"rank() time after loading over {lc.get('n_queries')} queries, {k[3:]} (one machine, one run)")
    else:
        F.null("scenarios.scenarios", source=raw, reason="reports/ranking_scenarios_raw.json not found")

    jp = data_dir / "labels" / "scenario_judgments.csv"
    if not jp.exists():
        F.null("scenarios.judged.cards", source=jp, reason="labels/scenario_judgments.csv not found (not judged yet)")
        return
    j = pd.read_csv(jp, dtype=str, keep_default_na=False)
    cards = j[j["rank"] != ""]
    noc = j[j["rank"] == ""]
    how = "independent judges' marks on the WP5 scenario run (labels/scenario_judgments.csv), agent judges"
    F.add("scenarios.judged.total", len(j), source=jp, kind="count", how=f"{how}: all judgments")
    F.add("scenarios.judged.cards", len(cards), source=jp, kind="count", how=f"{how}: recommendation cards judged")
    F.add("scenarios.judged.no_card_answers", len(noc), source=jp, kind="count",
          how=f"{how}: answers that showed no cards (abstain, pick a location, not found)")
    for mk in ("right", "defensible", "wrong"):
        n = int((cards["mark"] == mk).sum())
        F.add(f"scenarios.judged.{mk}", n, source=jp, kind="count", how=f"{how}: cards marked {mk}")
        if len(cards):
            F.add(f"scenarios.judged.{mk}_share", n / len(cards), source=jp, kind="share", how=f"cards marked {mk} / cards")
    for mk in ("abstention_right", "abstention_wrong"):
        F.add(f"scenarios.judged.{mk}", int((noc["mark"] == mk).sum()), source=jp, kind="count",
              how=f"{how}: no-card answers marked {mk}")
    order = {"wrong": 0, "defensible": 1, "right": 2}
    worst = cards.groupby("scenario_id")["mark"].agg(lambda m: min(m, key=lambda x: order.get(x, 3)))
    for mk in ("right", "defensible", "wrong"):
        F.add(f"scenarios.judged.weakest_card_{mk}", int((worst == mk).sum()), source=jp, kind="count",
              how=f"scenarios with cards whose weakest card is marked {mk}")


def _km_display(card, loc) -> Optional[str]:
    from vf import rank
    try:
        return rank._fmt_km(card.distance_km, rank._eff_precision(card.location_precision_km, loc))
    except Exception:  # private helpers may change; fall back to the plain number
        return None if card.distance_km is None else f"{card.distance_km} km"


def queries_section(F: Findings, b: Path, idx, resolver, data_dir: Path) -> list[dict]:
    import pandas as pd
    from vf import rank
    from vf import services as S

    sc = data_dir / "labels" / "scenarios.csv"
    qs = []
    if sc.exists():
        s = pd.read_csv(sc, dtype=str, keep_default_na=False)
        for _, r in s[s["id"].str.startswith("brief_")].iterrows():
            qs.append({"id": r["id"], "service": r["service"], "place_text": r["place_text"],
                       "state_hint": r["state_hint"] or None, "why": r["why"], "src": sc})
    qs += [dict(q, src=Path(__file__)) for q in EXTRA_QUERIES if q["id"] not in {x["id"] for x in qs}]
    src = b / "grades.parquet"
    summary = []
    for q in qs:
        k = f"query.{q['id']}"
        what = f"{q['service']} at {q['place_text']!r}" + (f" ({q['state_hint']})" if q["state_hint"] else "")
        if q["service"] not in S.SERVICES:
            F.null(f"{k}.results", source=q["src"], reason=f"service {q['service']!r} is not a recipe line")
            continue
        res = resolver.resolve(q["place_text"], q["state_hint"])
        ans = rank.rank(idx, q["service"], res)
        loc = ans.location or {}
        base = f"live vf.rank.rank on the bundle for {what}, resolved by vf.locate on the bundle's places"
        prec = loc.get("precision_km")
        below = []

        def cav(c, label):
            """Caveat for a distance smaller than the searched location's own precision."""
            if prec is None or c.distance_km is None or c.distance_km >= prec:
                return ""
            below.append(label)
            F.caveats.append(f"{k}: {label} distance {c.distance_km} km is smaller than the location's precision "
                             f"(about {_round_half_up(prec, 0)} km)")
            return (f". CAVEAT: the distance is smaller than the location's own precision (about "
                    f"{_round_half_up(prec, 0)} km), so it is not a measured gap; do not caption it as one")
        summary.append({"id": q["id"], "status": ans.status, "cards": [c.name for c in ans.results]})
        if loc.get("precision_km") is not None:
            F.add(f"{k}.location_precision_km", float(loc["precision_km"]), source=src, kind="km",
                  display=f"about {_round_half_up(loc['precision_km'], 0)} km", how=f"{base}: the location's precision radius")
        F.add(f"{k}.radius_km", float(ans.radius_km), source=src, kind="km", digits=0,
              how=f"{base}: the service radius (vf.services). Status {ans.status}")
        F.add(f"{k}.results", len(ans.results), source=src, kind="count", how=f"{base}: cards shown. Status {ans.status}")
        for g in (ans.how_we_decided or {}).get("gates", []):
            gk = f"{k}.gate.{_slug(g.get('gate', 'gate'))}"
            for f_ in ("n", "n_distinct", "of", "n_blocked"):
                if isinstance(g.get(f_), (int, float)) and not isinstance(g.get(f_), bool):
                    F.add(f"{gk}.{f_}", g[f_], source=src, kind="count",
                          how=f"{base}: gate '{g.get('gate')}' ({g.get('rule', '')}), field {f_}")
        for i, c in enumerate(ans.results, 1):
            ck = f"{k}.card{i}"
            F.add(f"{ck}.distance_km", c.distance_km, source=src, kind="km", display=_km_display(c, loc),
                  how=f"{base}: card {i}, {c.name}, {c.confidence} confidence, {c.grade_label}; straight-line km "
                      "rounded to the location precision" + cav(c, f"card {i}"))
            nd = (c.sources or {}).get("n_domains")
            if nd is not None:
                F.add(f"{ck}.n_source_domains", nd, source=b / "facilities.parquet", kind="count",
                      how=f"{base}: card {i}, {c.name}: distinct web domains its record was compiled from")
            F.add(f"{ck}.n_quotes", len(c.evidence or []), source=src, kind="count",
                  how=f"{base}: card {i}, {c.name}: quoted facts on the card")
        if ans.beyond_radius is not None:
            c = ans.beyond_radius
            F.add(f"{k}.beyond_radius.distance_km", c.distance_km, source=src, kind="km", display=_km_display(c, loc),
                  how=f"{base}: nearest recommendable record beyond the radius, {c.name}, {c.confidence} confidence"
                      + cav(c, "beyond_radius"))
        unv = ans.unverified_nearby or []
        F.add(f"{k}.unverified_nearby", len(unv), source=src, kind="count",
              how=f"{base}: claim-only or blocked records inside the radius shown as 'also nearby, not verified'")
        for i, c in enumerate(unv, 1):
            F.add(f"{k}.unverified{i}.distance_km", c.distance_km, source=src, kind="km", display=_km_display(c, loc),
                  how=f"{base}: unverified record {i}, record {c.facility_id} (name left out of this file), "
                      f"{c.grade_label}" + cav(c, f"unverified {i}"))
        if prec is not None:
            F.add(f"{k}.distances_below_precision", len(below), source=src, kind="count",
                  how=f"{base}: distances on this answer (cards, further-away line, unverified) that are smaller "
                      f"than the location's own precision" + (f" ({', '.join(below)})" if below else ""))
    return summary


def typicality_counts(tied: list, low) -> dict:
    """Tie rule for "the nearest listing names no caesarean". ``tied[i]`` holds the record positions tied at
    point i's nearest distance; ``low[pos]`` is True when that record is graded below 2 on emoc.

    points_all counts points where every tied record is low (the stated rule); records_all is the set of all
    tied records at those points. points_any and records_any are the upper bound (at least one tied record
    is low; only the low records counted). points_tied counts points with two or more tied records."""
    out = {"points_all": 0, "points_any": 0, "points_tied": 0, "records_all": set(), "records_any": set()}
    for recs in tied:
        recs = list(recs)
        lo = [r for r in recs if low[r]]
        out["points_tied"] += int(len(recs) > 1)
        if recs and len(lo) == len(recs):
            out["points_all"] += 1
            out["records_all"] |= set(recs)
        if lo:
            out["points_any"] += 1
            out["records_any"] |= set(lo)
    return out


def cold_open_section(F: Findings, b: Path, idx, resolver, data_dir: Path) -> None:
    """The obvious query on the Marketplace rows, replicated, and how typical its failure is."""
    import numpy as np
    import pandas as pd
    from sklearn.neighbors import BallTree
    from vf import geo, io

    res = resolver.resolve(COLD_OPEN_PIN)
    if res.status != "ok":
        F.null("cold_open.foil.rows_passing", source=b / "places.parquet", reason=f"PIN {COLD_OPEN_PIN} did not resolve")
        return
    try:
        raw = io.read_facilities_raw()
        raw_src = io.raw_path("facilities")
    except Exception as e:  # raw data absent (for example on Databricks)
        F.null("cold_open.foil.rows_passing", source=data_dir / "raw", reason=f"raw facilities not readable: {e}")
        raw = None
    if raw is not None:
        def try_cast(s):
            return pd.to_numeric(s.where(~s.isin(["null", "NULL", ""])), errors="coerce")
        lat, lon = try_cast(raw["latitude"]), try_cast(raw["longitude"])
        m = (raw["specialties"].astype(str).str.contains(OBS_SPECIALTY, regex=False)
             & lat.between(*INDIA_BBOX["lat"]) & lon.between(*INDIA_BBOX["lon"]))
        km = pd.Series(geo.haversine_km(res.lat, res.lon, lat.to_numpy(), lon.to_numpy()), index=raw.index)
        top = raw[m].assign(km=km[m]).sort_values("km", kind="stable").head(3)
        sql = (f"all raw Marketplace rows (vf.io.read_facilities_raw, junk included, like the table), "
               f"specialties containing {OBS_SPECIALTY!r}, try_cast coordinates inside India's bounding box, "
               f"ordered by straight-line km from the resolver's point for PIN {COLD_OPEN_PIN}")
        F.add("cold_open.foil.rows_scanned", len(raw), source=raw_src, kind="count", how=sql + ": rows scanned")
        F.add("cold_open.foil.rows_passing", int(m.sum()), source=raw_src, kind="count", how=sql + ": rows passing")
        F.add("cold_open.foil.junk_rows_passing", int((m & (raw["address_countryCode"] != "IN")).sum()),
              source=raw_src, kind="count", how=sql + ": junk rows (country code not IN) passing")
        facts_p = data_dir / "silver" / "facts.parquet"
        facts = pd.read_parquet(facts_p, columns=["facility_id", "text"]) if facts_p.exists() else None
        flags = pd.read_parquet(b / "flags.parquet")
        for i, (_, r) in enumerate(top.iterrows(), 1):
            k = f"cold_open.foil.row{i}"
            F.add(f"{k}.distance_km", float(r["km"]), source=raw_src, kind="km", digits=2,
                  how=f"{sql}: row {i}, record {r['unique_id']} (record city {r.get('address_city')!r}); name left "
                      "out of this file" + ("; blur it on screen" if i == 1 else ""))
            if facts is not None:
                ff = facts[facts["facility_id"] == r["unique_id"]]
                F.add(f"{k}.facts", len(ff), source=facts_p, kind="count",
                      how=f"row {i}: capability, procedure and equipment facts in its record (silver/facts.parquet)")
                F.add(f"{k}.facts_mentioning_caesarean", int(ff["text"].str.contains(CAESAREAN_RE, case=False,
                                                                                     regex=True).sum()),
                      source=facts_p, kind="count",
                      how=f"row {i}: facts whose text matches /{CAESAREAN_RE}/ (case-insensitive)")
            F.add(f"{k}.major_flags", int(((flags["facility_id"] == r["unique_id"]) & (flags["severity"] == "major")).sum()),
                  source=b / "flags.parquet", kind="count", how=f"row {i}: major WP11 flags on its record")

    # typicality: around the same point, how often the nearest specialty listing names no caesarean
    sil = data_dir / "silver" / "facilities.parquet"
    po = idx.post_offices
    if po is None or not sil.exists() or "emoc" not in idx.lines:
        F.null("cold_open.typicality.points_within_50km", source=b / "post_offices.parquet",
               reason="post offices, silver specialties or emoc grades missing")
        return
    spec = pd.read_parquet(sil, columns=["facility_id", "specialties"]).set_index("facility_id")["specialties"]
    lab = idx.fac["facility_id"].map(lambda i: OBS_SPECIALTY in list(spec.get(i, []) if spec.get(i) is not None
                                                                     else [])).to_numpy(dtype=bool)
    ok = np.isfinite(idx.lat) & np.isfinite(idx.lon) & lab
    pos = np.flatnonzero(ok)
    bt = BallTree(np.radians(np.c_[idx.lat[ok], idx.lon[ok]]), metric="haversine")
    pts = po[["lat", "lon"]].drop_duplicates()
    near = pts[geo.haversine_km(res.lat, res.lon, pts["lat"].to_numpy(), pts["lon"].to_numpy()) <= 50]
    P = np.radians(near.to_numpy())
    d, _ = bt.query(P, k=1)
    # every listing as near as the nearest one: records that share a coordinate tie exactly, and BallTree's
    # pick among them is arbitrary, so the rule below looks at all of them
    tied = bt.query_radius(P, r=d[:, 0] + 1e-9)  # 1e-9 rad is about 6 mm
    tc = typicality_counts([pos[t] for t in tied], idx.lines["emoc"].grade < 2)
    all_bad, any_bad, n_tied = tc["points_all"], tc["points_any"], tc["points_tied"]
    rec_all, rec_any = tc["records_all"], tc["records_any"]
    src = b / "post_offices.parquet"
    how = (f"distinct post-office points in the bundle within 50 km of PIN {COLD_OPEN_PIN}'s point; for each, "
           f"the nearest records listing {OBS_SPECIALTY!r} (all records tied at the nearest distance, about 6 mm "
           "tolerance); 'no caesarean' = graded below 2 on emoc (no fact names a caesarean). Tie rule: a point "
           "counts only if every tied nearest record names no caesarean")
    F.add("cold_open.typicality.points_within_50km", len(near), source=src, kind="count", how=how)
    F.add("cold_open.typicality.points_nearest_listing_no_caesarean", all_bad, source=src, kind="count", how=how)
    F.add("cold_open.typicality.distinct_records", len(rec_all), source=src, kind="count",
          how=how + "; distinct records among the tied nearest records of the counted points")
    F.add("cold_open.typicality.points_with_tied_nearest", n_tied, source=src, kind="count",
          how=how + "; points whose nearest distance is shared by two or more records")
    F.add("cold_open.typicality.points_upper_bound", any_bad, source=src, kind="count",
          how=how + "; upper bound under the other tie rule: at least one tied nearest record names no caesarean")
    F.add("cold_open.typicality.distinct_records_upper_bound", len(rec_any), source=src, kind="count",
          how=how + "; distinct records graded below 2 among the tied nearest records under the upper-bound rule")


def laqshya_section(F: Findings, b: Path, grades, data_dir: Path, path: Optional[Path]) -> None:
    import pandas as pd

    p = path or _first_existing(LAQSHYA_CANDIDATES)
    if p is None or not Path(p).exists():
        F.null("laqshya.link_candidates", source=None,
               reason="no hand-labeled LaQshya link file found (looked for " + ", ".join(LAQSHYA_CANDIDATES) +
                      "); pass --laqshya-links FILE")
        return
    p = Path(p)
    if p.suffix == ".json":
        flatten_section(F, "laqshya", p)
        return
    d = pd.read_csv(p, dtype=str, keep_default_na=False)
    need = {"uid", "fid", "hand"}
    if not need <= set(d.columns):
        F.null("laqshya.link_candidates", source=p, reason=f"link file lacks columns {sorted(need - set(d.columns))}")
        return
    how = ("LaQshya units linked by name to public records within the PIN's district, each link checked by an "
           "agent (column hand); Hruday's spot-check is pending (D044), so keep ratios off screen until then")
    F.add("laqshya.link_candidates", len(d), source=p, kind="count", how=how + ": candidate links")
    for mk, c in d["hand"].value_counts().sort_index().items():
        F.add(f"laqshya.links_{_slug(mk)}", int(c), source=p, kind="count", how=how + f": links marked {mk!r}")
    lq_p = data_dir / "silver" / "ext_laqshya.parquet"
    if not lq_p.exists():
        F.null("laqshya.correct_with_current_maternity_ot", source=lq_p, reason="silver/ext_laqshya.parquet not found")
        return
    lq = pd.read_parquet(lq_p)
    c = d[d["hand"] == "correct"]
    ot = lq[lq["facility_uid"].isin(c["uid"]) & lq["department"].astype(str).str.contains("Maternity OT")]
    cur = ot[~ot["expired_as_of_retrieval"].fillna(True).astype(bool)]
    cur_uids = set(cur["facility_uid"])
    F.add("laqshya.correct_with_current_maternity_ot", len(cur_uids), source=lq_p, kind="count",
          how="correct links whose LaQshya unit holds a Maternity OT certificate not expired at retrieval "
              "(21 Nov 2026, D019)")
    fids = set(c[c["uid"].isin(cur_uids)]["fid"])
    em = grades[(grades["service_line"] == "emoc") & grades["facility_id"].isin(fids)]
    n_cs = int(em["tags_present"].map(lambda t: "csection" in list(t)).sum())
    F.add("laqshya.current_maternity_ot_record_names_csection", n_cs, source=b / "grades.parquet", kind="count",
          how="of those, records with a fact naming a caesarean (emoc tags_present has csection)")


def _flatten(obj, prefix: str = "") -> Iterable[tuple[str, Any]]:
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _flatten(v, f"{prefix}.{_slug(k)}" if prefix else _slug(k))
    elif isinstance(obj, list):
        if len(obj) <= MAX_LIST_FLATTEN:
            for i, v in enumerate(obj):
                yield from _flatten(v, f"{prefix}.{i}")
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        yield prefix, obj


def flatten_section(F: Findings, group: str, path: Path) -> int:
    """Every numeric leaf of a JSON file as a finding (lists over 20 items are skipped)."""
    obj = json.loads(Path(path).read_text())
    n = 0
    for k, v in _flatten(obj):
        name = k.rsplit(".", 1)[-1]
        if isinstance(v, float) and 0 <= v <= 1 and re.search(r"share|rate|pct|percent|coverage|precision|recall", k):
            kind = "share"
        elif isinstance(v, int):
            kind = "count"
        else:
            kind = "number"
        F.add(f"{group}.{k}", v, source=path, kind=kind, how=f"read from {F.rel(path)} at JSON path {k} ({name})")
        n += 1
    return n


def sealed_section(F: Findings, path: Optional[Path]) -> None:
    p = path or _first_existing(SEALED_CANDIDATES)
    if p is None or not Path(p).exists():
        F.null("sealed_eval.results", source=None,
               reason="sealed evaluation not run yet (looked for " + ", ".join(SEALED_CANDIDATES) +
                      "); pass --sealed FILE")
        return
    if flatten_section(F, "sealed_eval", Path(p)) == 0:
        F.null("sealed_eval.results", source=p, reason="the sealed evaluation file has no numbers")


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------

def _git_commit() -> Optional[str]:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              check=True).stdout.strip()
    except Exception:
        return None


#: the code a findings file depends on: this script and the shared package
CODE_PATHS = ("scripts/wp9_findings.py", "src/vf")


def _code_uncommitted() -> Optional[bool]:
    """True when this script or src/vf differs from HEAD (or is untracked); None when git is unavailable."""
    try:
        out = subprocess.run(["git", "status", "--porcelain", "--", *CODE_PATHS], cwd=ROOT, capture_output=True,
                             text=True, check=True).stdout
    except Exception:
        return None
    return bool(out.strip())


def public_view(obj: dict) -> dict:
    """What an app may serve (for example /api/findings): key, value, display and kind only. how_computed
    names facilities and source_file names local paths, so both stay in the report file."""
    return {"version": obj.get("meta", {}).get("version"),
            "created_at_utc": obj.get("meta", {}).get("created_at_utc"),
            "findings": {f["key"]: {"value": f["value"], "display": f["display"], "kind": f["kind"]}
                         for f in obj.get("findings", [])}}


def build(bundle: Path = DEFAULT_BUNDLE, *, data_dir: Optional[Path] = None, laqshya: Optional[Path] = None,
          sealed: Optional[Path] = None, extras: Optional[dict] = None, cold_open: bool = True,
          queries: bool = True) -> dict:
    import pandas as pd
    from vf import locate, paths, rank
    from vf import services as S

    t0 = time.time()
    bundle = Path(bundle)
    data_dir = Path(data_dir) if data_dir else paths.data_dir()
    if not (bundle / "facilities.parquet").exists():
        raise FileNotFoundError(f"{bundle} has no facilities.parquet; build it with scripts/wp12_bundle.py")
    F = Findings(data_dir)
    manifest = json.loads((bundle / "manifest.json").read_text()) if (bundle / "manifest.json").exists() else {}
    fac = pd.read_parquet(bundle / "facilities.parquet")
    grades = pd.read_parquet(bundle / "grades.parquet")
    flags = pd.read_parquet(bundle / "flags.parquet")
    dups = pd.read_parquet(bundle / "duplicates.parquet")
    idx = rank.load_index(bundle)
    places_p = bundle / "places.parquet"
    resolver = locate.Resolver(places=pd.read_parquet(places_p)) if places_p.exists() else None

    facilities_section(F, bundle, fac)
    grades_section(F, bundle, grades, idx, S)
    emoc_section(F, bundle, grades, idx, data_dir)
    tagger_section(F, data_dir)
    trust_section(F, bundle, flags, dups, grades, idx, data_dir)
    resolver_section(F, bundle, resolver, data_dir)
    scenarios_section(F, data_dir)
    summary = queries_section(F, bundle, idx, resolver, data_dir) if (queries and resolver is not None) else []
    if cold_open and resolver is not None:
        cold_open_section(F, bundle, idx, resolver, data_dir)
    laqshya_section(F, bundle, grades, data_dir, laqshya)
    sealed_section(F, sealed)
    for group, p in (extras or {}).items():
        if Path(p).exists():
            flatten_section(F, f"extra.{_slug(group)}", Path(p))
        else:
            F.null(f"extra.{_slug(group)}", source=p, reason=f"{p} not found")

    # consistency with the bundle's own manifest
    rc = manifest.get("row_counts", {})
    if rc.get("facilities") not in (None, len(fac)):
        F.warnings.append(f"manifest says {rc['facilities']} facilities; facilities.parquet has {len(fac)}")
    for line, counts in (manifest.get("grade_counts") or {}).items():
        for g, n in counts.items():
            k = f"grades.{line}.grade_{g}"
            got = next((x["value"] for x in F.items if x["key"] == k), None)
            if got is not None and got != n:
                F.warnings.append(f"manifest grade_counts {line} {g} = {n}; grades.parquet gives {got}")

    nulls = [x for x in F.items if x["value"] is None]
    return {
        "meta": {
            "version": VERSION,
            "script": "scripts/wp9_findings.py",
            "created_at_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "git_commit": _git_commit(),
            "code_uncommitted": _code_uncommitted(),
            "script_sha256": _sha256(Path(__file__)),
            "bundle_dir": F.rel(bundle),
            "bundle_created_at_utc": manifest.get("created_at_utc"),
            "bundle_git_commit": manifest.get("git_commit"),
            "bundle_versions": manifest.get("versions"),
            "score_status": "provisional (v0 score, D039)" if not any(idx.is_probability(l) for l in idx.lines)
            else "probability mode",
            "n_findings": len(F.items),
            "n_null": len(nulls),
            "null_keys": [x["key"] for x in nulls],
            "sources": F.sources,
            "warnings": F.warnings,
            "caveats": F.caveats,
            "queries": summary,
            "check_allowlist": "years 1900 to 2100 and 6-digit PIN codes (not after Rs, INR or a currency "
                               "sign), clock times (H:MM), dates (next to a month name, 2026-11-21, 21/11/2026), "
                               "list markers at a line start, identifiers (a letter before the number: WP11, "
                               "v0.2, D044; ordinal or model suffix: 3rd, 1b), commit hashes, real paths and URLs "
                               "(http(s)://, /, ./, ../, ~/, or a file extension), citations (page, row, table, "
                               "figure, section, no. or # with an integer and no unit), demo ordinals (card, "
                               "step, beat, option or rank with one or two digits and no unit), emergency numbers "
                               "102, 108 and 112; lines with findings:skip. Durations, money and glued units "
                               "(45 minutes, Rs.2000, 250mg) are scanned",
            "check_note": CHECK_NOTE,
            "build_s": round(time.time() - t0, 1),
        },
        "findings": F.items,
    }


def write_atomic(obj: dict, out: Path) -> None:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, indent=1, ensure_ascii=False, default=str) + "\n"
    with tempfile.NamedTemporaryFile("w", dir=out.parent, prefix=out.name + ".", suffix=".tmp", delete=False,
                                     encoding="utf-8") as fh:
        fh.write(text)
        tmp = fh.name
    os.chmod(tmp, 0o644)
    os.replace(tmp, out)
    m = obj.get("meta", {})
    meta = {
        "script": m.get("script"), "version": m.get("version"), "created_at_utc": m.get("created_at_utc"),
        "git_commit": m.get("git_commit"), "code_uncommitted": m.get("code_uncommitted"),
        "script_sha256": m.get("script_sha256"), "output": str(out.name),
        "rows": len(obj.get("findings", [])), "null_rows": m.get("n_null"), "bundle_dir": m.get("bundle_dir"),
        "bundle_created_at_utc": m.get("bundle_created_at_utc"), "inputs": m.get("sources"),
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }
    mp = out.with_name(out.name + ".meta.json")
    with tempfile.NamedTemporaryFile("w", dir=out.parent, prefix=mp.name + ".", suffix=".tmp", delete=False,
                                     encoding="utf-8") as fh:
        fh.write(json.dumps(meta, indent=2) + "\n")
        tmp = fh.name
    os.chmod(tmp, 0o644)
    os.replace(tmp, mp)


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------

def known_tokens(findings: dict) -> dict[tuple[str, Optional[str]], list[str]]:
    out: dict[tuple[str, Optional[str]], list[str]] = {}
    for f in findings.get("findings", []):
        for t in accepted_tokens(f):
            out.setdefault(t, []).append(f["key"])
    return out


def _matches(known: dict, tok: str, unit: Optional[str]) -> list[str]:
    """Finding keys a scanned number can stand for: same value, and the same unit when the text gives one
    (a bare number may match a km or ms finding, never a percentage)."""
    if unit is not None:
        return known.get((tok, unit), [])
    return [k for u in (None, "km", "ms") for k in known.get((tok, u), [])]


def _ratio_ok(member_keys: list[Optional[list[str]]]) -> bool:
    """One group of findings holds every number of the ratio. A facilities.* finding (a whole-dataset
    denominator such as 9,989 records) and an --allow number pair with anything."""
    scopes = []
    for keys in member_keys:
        if keys is None or any(k.split(".")[0] == "facilities" for k in keys):
            continue
        scopes.append({_scope(k) for k in keys})
    return not scopes or bool(set.intersection(*scopes))


def _json_strings(obj, path: str = "$") -> Iterable[tuple[str, str]]:
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _json_strings(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _json_strings(v, f"{path}[{i}]")
    elif isinstance(obj, str):
        for j, line in enumerate(obj.splitlines() or [""]):
            yield (path if j == 0 else f"{path} line {j + 1}"), line


def check_text(lines: Iterable[tuple[str, str]], findings: dict, allow: Iterable[str] = (),
               strict: bool = False) -> dict:
    """Numbers in (where, line) pairs that no finding covers (see CHECK_NOTE for what this can and cannot
    catch). A {{key}} placeholder passes when the key exists and is not null. With strict, every bare
    number fails unless it is in allow: captions must name their findings."""
    known = known_tokens(findings)
    by_key = {f["key"]: f for f in findings.get("findings", [])}
    extra = {canon(a.rstrip("%")) for a in allow if a.strip()}
    unknown, n_checked, n_ok = [], 0, 0

    def miss(where, number, line, reason):
        unknown.append({"where": where, "number": number, "line": line.strip()[:160], "reason": reason})

    for where, line in lines:
        if "findings:skip" in line:
            continue
        for m in _PLACEHOLDER.finditer(line):
            n_checked += 1
            f = by_key.get(m.group(1))
            if f is not None and f.get("value") is not None:
                n_ok += 1
            else:
                miss(where, m.group(0), line, "no such finding" if f is None else "finding is null")
        toks = scan_line(line)
        keys: list[Optional[list[str]]] = []
        for t in toks:
            n_checked += 1
            if t["token"] in extra:
                keys.append(None)
                n_ok += 1
                continue
            ks = _matches(known, t["token"], t["unit"])
            keys.append(ks)
            if strict:
                miss(where, t["raw"], line, "strict: bare number; name its finding with {{key}}")
            elif ks:
                n_ok += 1
            else:
                miss(where, t["raw"] + (f" {t['unit']}" if t["unit"] not in (None, "%") else ""), line,
                     "no finding with this value" + (f" and unit {t['unit']}" if t["unit"] else ""))
        if strict:
            continue
        groups: dict[int, list[int]] = {}
        for i, t in enumerate(toks):
            if t["ratio"] is not None:
                groups.setdefault(t["ratio"], []).append(i)
        for ids in groups.values():
            if all(keys[i] != [] for i in ids) and not _ratio_ok([keys[i] for i in ids]):
                n_ok -= len(ids)
                sep = line[toks[ids[0]]["col"]:toks[ids[-1]]["end"]]
                miss(where, sep, line, "ratio: its numbers exist, but not in one group of findings; "
                                       "name them with {{key}}")
    return {"checked": n_checked, "found": n_ok, "unknown": unknown}


def check_file(path: Path, findings: dict, allow: Iterable[str] = (), strict: bool = False) -> dict:
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        lines = list(_json_strings(json.loads(text)))
    else:
        lines = [(f"line {i}", ln) for i, ln in enumerate(text.splitlines(), 1)]
    return check_text(lines, findings, allow, strict=strict)


# ---------------------------------------------------------------------------
# cli
# ---------------------------------------------------------------------------

def _print_summary(obj: dict, out: Path) -> None:
    m = obj["meta"]
    by = {}
    for f in obj["findings"]:
        by.setdefault(f["group"], [0, 0])
        by[f["group"]][0 if f["value"] is not None else 1] += 1
    print(f"wrote {out} ({m['n_findings']} findings, {m['n_null']} null) in {m['build_s']} s from {m['bundle_dir']} "
          f"(bundle built {m['bundle_created_at_utc']})")
    for g, (n, z) in sorted(by.items()):
        print(f"  {g:12s} {n:5d} numbers" + (f", {z} null" if z else ""))
    for f in obj["findings"]:
        if f["value"] is None:
            print(f"  null {f['key']}: {f['reason']}")
    pick = ["facilities.records", "facilities.usable_locations", "emoc.specialty_label_records", "emoc.grade_2plus",
            "tagger.shipped.macro_f1", "tagger.regex.macro_f1", "tagger.gold_rows", "trust.facilities_flagged",
            "trust.blocked_pairs", "resolver.hits", "resolver.cases", "scenarios.judged.right",
            "scenarios.judged.defensible", "scenarios.judged.wrong", "query.cold_open_226001.card1.distance_km"]
    d = {f["key"]: f for f in obj["findings"]}
    for k in pick:
        if k in d and d[k]["value"] is not None:
            print(f"  {k} = {d[k]['display']}")
    for w in m["warnings"]:
        print(f"  WARNING {w}")
    if m.get("caveats"):
        print(f"  {len(m['caveats'])} distances are smaller than their location's precision (meta.caveats), e.g.")
        for c in [c for c in m["caveats"] if "beyond_radius" in c][:3] or m["caveats"][:1]:
            print(f"    {c}")
    print(f"  code_uncommitted={m.get('code_uncommitted')} script_sha256={str(m.get('script_sha256'))[:12]}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--findings", type=Path, default=DEFAULT_OUT, help="findings.json to check against")
    ap.add_argument("--laqshya-links", type=Path, default=None)
    ap.add_argument("--sealed", type=Path, default=None)
    ap.add_argument("--extra", action="append", default=[], metavar="GROUP=FILE")
    ap.add_argument("--no-cold-open", action="store_true", help="skip the raw-data foil and typicality")
    ap.add_argument("--check", nargs="+", type=Path, metavar="FILE")
    ap.add_argument("--allow", default="", help="comma-separated extra numbers to allow in --check")
    ap.add_argument("--strict", action="store_true",
                    help="with --check: every bare number fails; captions must name findings as {{key}}")
    a = ap.parse_args(argv)

    if a.check:
        if not a.findings.exists():
            print(f"{a.findings} not found; build it first", file=sys.stderr)
            return 2
        fnd = json.loads(a.findings.read_text())
        allow = [x for x in a.allow.split(",") if x.strip()]
        bad = 0
        for p in a.check:
            r = check_file(p, fnd, allow, strict=a.strict)
            print(f"{p}: {r['checked']} numbers, {r['found']} in findings, {len(r['unknown'])} not")
            for u in r["unknown"]:
                print(f"  NOT IN FINDINGS {u['number']!r} at {u['where']} ({u['reason']}): {u['line']}")
            bad += len(r["unknown"])
        print(f"note: {CHECK_NOTE}")
        return 1 if bad else 0

    extras = {}
    for e in a.extra:
        g, _, p = e.partition("=")
        if not p:
            ap.error(f"--extra wants GROUP=FILE, got {e!r}")
        extras[g] = Path(p)
    obj = build(a.bundle, laqshya=a.laqshya_links, sealed=a.sealed, extras=extras, cold_open=not a.no_cold_open)
    write_atomic(obj, a.out)
    _print_summary(obj, a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
