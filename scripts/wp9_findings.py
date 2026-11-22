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
  laqshya      LaQshya link review as shipped (bundle laqshya_links.parquet)
  sealed_eval  the sealed evaluation, both halves (labels/eval_sealed/results_*.json, outcomes, judgments):
               coverage, precision, cost of caution, judge agreement, ranker vs any-fact sign test
  spend        LLM spend per ledger and the project total (every labels/**/spend*.jsonl)
  method       declared constants the app shows: drive-time fit, windows, allowance (vf.travel, vf.services)
  outside      outside lists in the bundle, and link precision per band recomputed from silver/ext_links_*
  tagger_wp4b  the tagger mix in the frozen bundle (WP4b, D059, D064), teacher pilot, ship-rule gates
  teacher      WP3 teacher labels against regex v0.2
  v0v1         blind v0 against v1 comparison, rounds 1 and 2
  recipe_v03   recipe v0.2 against v0.3 grades
  scenarios_v1 the v1 scenario run (not judged as a whole)
  ordering     the order and abstention sensitivity, rerun (skip with --no-ordering)
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
    python scripts/wp9_findings.py --laqshya-links dbapp/bundle/laqshya_links.parquet
    python scripts/wp9_findings.py --sealed data/labels/eval_sealed/results_sealed.json
    python scripts/wp9_findings.py --extra mygroup=some.json   # numeric leaves of any JSON
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
#: which findings groups carry each report's numbers (recomputed from the files the report names, not parsed
#: from the report text); a report number with no machine-readable source is an explicit null with report_states
REPORT_COVERAGE = {
    "reports/eval_sealed_results.md": "sealed_eval.sealed.*, sealed_eval.dev.*, spend.*",
    "reports/eval_dev_rubric_check.md": "sealed_eval.dev_rubric_check.*",
    "reports/HUMAN_LABELING.md": "sealed_eval.*.human.* (null: not labeled yet)",
    "reports/outside_lists.md": "outside.*, laqshya.*",
    "reports/ext_aerb_rt.md, ext_ncg.md, ext_nhp_bloodbank.md, ext_mohfw_trauma.md, ext_nabi_burns.md":
        "outside.<source>.* (band verdicts and Clopper-Pearson bounds recomputed from silver/ext_links_*)",
    "reports/laqshya_links.md": "laqshya.* (from the bundle's laqshya_links.parquet)",
    "reports/model_a_wp4b.md": "tagger_wp4b.*",
    "reports/model_a_results.md": "tagger.* (the WP4 mix, superseded in the frozen bundle by tagger_wp4b.mix)",
    "reports/teacher_run.md": "teacher.*, tagger.teacher.*",
    "reports/ranking_v0_vs_v1.md": "v0v1.r1.*, v0v1.r2.*, v0v1.judge_noise.*",
    "reports/ranking_scenarios.md": "scenarios.* (v0 run, judged), scenarios_v1.* (v1 run, not judged as a whole)",
    "reports/recipe_v03_changes.md": "recipe_v03.*, grades.*",
    "reports/trust_checks.md": "trust.*",
    "reports/resolver_eval.md": "resolver.*",
    "reports/ordering_behaviour.md": "ordering.* (rerun now)",
    "reports/cold_open.md": "cold_open.*, query.cold_open_226001.*",
    "reports/score_methodology_research.md": "method.* (the constants the code ships, vf.travel and vf.services)",
    "not covered": "build_final.md and overnight_test_and_fix.md (process logs: hashes, test counts, issue lists); "
                   "eda_notes.md, wp2b_external_validation.md, wp6_rule_quadrants.md (retired outputs, D024); "
                   "standout_brainstorm.md, outside_data_research.md, eval_sealed_protocol.md, "
                   "eval_sealed_amendment_reach_draft.md (plans and research, no results)",
}
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
    if kind == "minutes":
        return f"{float(value):,.{0 if digits is None else digits}f} min"
    if kind == "usd":
        return f"${float(value):,.{2 if digits is None else digits}f}"
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

    def null(self, key: str, *, source, reason: str, how: str = "", kind: str = "number",
             report_states: Optional[str] = None) -> None:
        """An explicit null. ``report_states`` quotes what a report says, when a report gives a number that no
        machine-readable file backs; it is kept for the reader and never counts as a finding in --check."""
        if key in self.keys:
            raise ValueError(f"duplicate finding key {key}")
        self.keys.add(key)
        item = {
            "key": key, "group": key.split(".")[0], "value": None, "kind": kind, "display": None,
            "source_file": self.rel(source) if source else None, "how_computed": how, "reason": reason,
        }
        if report_states is not None:
            item["report_states"] = report_states
        self.items.append(item)


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
    # D048 item 7: sea-pixel post offices are dropped from coverage numbers. A bundle point is a sea point when it
    # lies within 10 m of a point in reference/sea_pixel_post_offices.csv (coordinates are rounded differently).
    sea_p = ROOT / "reference" / "sea_pixel_post_offices.csv"
    land = np.ones(len(pts), dtype=bool)
    if sea_p.exists():
        sea = pd.read_csv(sea_p)[["lat_po", "lon_po"]].dropna().drop_duplicates()
        if len(sea):
            ds, _ = BallTree(np.radians(sea.to_numpy()), metric="haversine").query(P, k=1)
            land = ds[:, 0] * 6371.0088 > 0.01
        F.add("emoc.post_office_points_sea_pixel", int((~land).sum()), source=sea_p, kind="count",
              how="bundle post-office points within 10 m of a sea-pixel post office (D048 item 7); dropped from "
                  "the coverage shares")
    F.add("emoc.post_office_points_land", int(land.sum()), source=po_src, kind="count",
          how="distinct post-office points used for the coverage shares: bundle points minus sea-pixel points")
    R = 50.0
    for name, mask, what in (
            ("specialty_label", lab & ok, f"a record listing {OBS_SPECIALTY!r}"),
            ("grade_2plus", (idx.lines["emoc"].grade >= 2) & ok, "a record graded 2+ on emoc (names a caesarean)")):
        X = np.radians(np.c_[idx.lat[mask], idx.lon[mask]])
        d, _ = BallTree(X, metric="haversine").query(P, k=1)
        within = d[:, 0] * 6371.0088 <= R
        F.add(f"emoc.coverage_50km_{name}", float(within[land].mean()), source=po_src, kind="share", digits=1,
              how=f"share of the land post-office points (sea-pixel points dropped, D048 item 7) within {R:.0f} km "
                  f"straight-line of {what} with a usable location. A fact about records, not about care")
        F.add(f"emoc.coverage_50km_{name}_with_sea", float(within.mean()), source=po_src, kind="share", digits=1,
              how=f"same share over every bundle post-office point, sea-pixel points included (before D048 item 7 "
                  "was applied)")


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
    names = {"shipped": "WP4 mix (student 1b on ob_delivery, h24, icu, ventilator; regex v0.2 on the rest), the "
                        "shipped tagger before the WP4b promotion; the frozen bundle ships tagger_wp4b.mix (D059, D064)",
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
              how="paired bootstrap 95% interval of WP4 mix (before WP4b) minus regex macro F1 on gold: lower end")
        F.add("tagger.shipped_minus_regex_ci_high", d[1], source=rep, kind="score", digits=3,
              how="paired bootstrap 95% interval of WP4 mix (before WP4b) minus regex macro F1 on gold: upper end")
    shipped = r.get("shipped_tags")
    if shipped is not None:
        F.add("tagger.student_tags_shipped", len(shipped), source=rep, kind="count",
              how=f"tags the WP4 student shipped on before WP4b ({', '.join(shipped)}); regex v0.2 on the rest. "
                  "Since D064 ob_delivery comes from WP4b 2b_r2 (tagger_wp4b)")
    pc = (r.get("population_check") or {}).get("per_tag") or {}
    for tag, d in pc.items():
        if "read" in d and "right" in d:
            hw = (f"WP4 population check of the student on {tag}: 25 random texts the student tags and regex v0.2 "
                  "does not, read by the WP4 agent (not blinded, not a clinician); the WP4 gate shipped a tag when "
                  "at least half were right")
            F.add(f"tagger.population.{tag}.read", d["read"], source=rep, kind="count", how=f"{hw}: texts read")
            F.add(f"tagger.population.{tag}.right", d["right"], source=rep, kind="count", how=f"{hw}: texts right")
    tr = ((r.get("grade_changes") or {}).get("icu") or {}).get("transitions") or {}
    if tr:
        up = sum(v for k, v in tr.items() if int(k[0]) < 2 <= int(k[-1]))
        down = sum(v for k, v in tr.items() if int(k[-1]) < 2 <= int(k[0]))
        hw = "icu line, regex v0.2 against the WP4 mix (student on icu, h24, ventilator, ob_delivery), recipe v0.2"
        F.add("tagger.icu_grade.rose_to_2plus", up, source=rep, kind="count",
              how=f"{hw}: facilities moving from grade 0 or 1 to grade 2 or 3")
        F.add("tagger.icu_grade.fell_below_2", down, source=rep, kind="count",
              how=f"{hw}: facilities moving from grade 2 or 3 to grade 0 or 1")
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
        how = ("summary of the WP5 v0 scenario run (scripts/wp5_scenarios.py, score v0, recipe v0.2, flags wp11_v2) "
               "on labels/scenarios.csv; the run the judges marked, not the frozen v1 build (see scenarios_v1, v0v1)")
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
    how = ("independent judges' marks on the WP5 v0 scenario run (labels/scenario_judgments.csv; score v0, recipe "
           "v0.2, flags wp11_v2, not the frozen v1 build), agent judges")
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
        # the filmed SQL (sql/cold_open_lucknow.sql): country IN and lower(specialties) LIKE '%obstetric%'
        m_sql = ((raw["address_countryCode"] == "IN")
                 & raw["specialties"].astype(str).str.lower().str.contains("obstetric", regex=False)
                 & lat.between(*INDIA_BBOX["lat"]) & lon.between(*INDIA_BBOX["lon"]))
        F.add("cold_open.foil.rows_passing_sql", int(m_sql.sum()), source=raw_src, kind="count",
              how="the filmed query's filters as in sql/cold_open_lucknow.sql (address_countryCode = 'IN', "
                  "lower(specialties) LIKE '%obstetric%', try_cast coordinates inside India's bounding box), "
                  "replicated on the raw CSV: rows passing. rows_passing above uses the exact label instead")
        top_sql = raw[m_sql].assign(km=km[m_sql]).sort_values("km", kind="stable").head(3)
        F.add("cold_open.foil.sql_top3_same_as_exact_label", int(list(top_sql["unique_id"]) == list(top["unique_id"])),
              source=raw_src, kind="count",
              how="1 if the filmed query's top 3 equals the exact-label top 3 (same rows, same order), else 0")
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


# ---------------------------------------------------------------------------
# report sources (D063 stage: findings from the frozen bundle and every report)
# ---------------------------------------------------------------------------

def _wilson(k: int, n: int, z: float = 1.96) -> tuple[Optional[float], Optional[float]]:
    """Wilson 95% interval, the same formula as scripts/eval_sealed.py."""
    if not n:
        return None, None
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, c - h), min(1.0, c + h)


def _cp_lower(k: int, n: int, alpha: float = 0.05) -> Optional[float]:
    """One-sided Clopper-Pearson lower bound (95% by default), the D048 item 5 link rule."""
    from scipy.stats import beta
    if not n:
        return None
    return 0.0 if k == 0 else float(beta.ppf(alpha, k, n - k + 1))


def _cp_two_sided(k: int, n: int) -> tuple[Optional[float], Optional[float]]:
    from scipy.stats import beta
    if not n:
        return None, None
    lo = 0.0 if k == 0 else float(beta.ppf(0.025, k, n - k + 1))
    hi = 1.0 if k == n else float(beta.ppf(0.975, k + 1, n - k))
    return lo, hi


def _sign_test(a: int, b: int, alternative: str = "two-sided") -> Optional[float]:
    from scipy.stats import binomtest
    if a + b == 0:
        return None
    return float(binomtest(a, a + b, 0.5, alternative=alternative).pvalue)


def _cohen_kappa(a: list, b: list) -> Optional[float]:
    """Cohen's kappa for two lists of booleans, the same formula as scripts/eval_sealed.py."""
    pairs = [(bool(x), bool(y)) for x, y in zip(a, b) if x is not None and y is not None]
    n = len(pairs)
    if n == 0:
        return None
    po = sum(x == y for x, y in pairs) / n
    pa = sum(x for x, _ in pairs) / n
    pb = sum(y for _, y in pairs) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    if pe >= 1.0:
        return 1.0 if po == 1.0 else 0.0
    return (po - pe) / (1 - pe)


def _tf(x) -> Optional[bool]:
    s = str(x)
    return True if s == "True" else False if s == "False" else None


def _add_kn(F: Findings, key: str, k: int, n: int, *, source, how: str, ci: str = "wilson",
            ci_given: Optional[Iterable] = None, share_digits: int = 0) -> None:
    """k, n, rate and its 95% interval as one group of findings (so "k of n" passes --check)."""
    F.add(f"{key}.k", int(k), source=source, kind="count", how=f"{how}: numerator")
    F.add(f"{key}.n", int(n), source=source, kind="count", how=f"{how}: denominator")
    if not n:
        F.null(f"{key}.rate", source=source, reason="denominator is 0", how=how, kind="share")
        return
    F.add(f"{key}.rate", k / n, source=source, kind="share", digits=share_digits, how=f"{how}: k / n")
    if ci_given is not None:
        lo, hi = list(ci_given)[:2]
        what = "95% interval as stored in the source file"
    elif ci == "wilson":
        lo, hi = _wilson(k, n)
        what = "95% Wilson interval"
    else:
        lo, hi = _cp_two_sided(k, n)
        what = "two-sided 95% Clopper-Pearson interval"
    if lo is not None:
        F.add(f"{key}.ci_low", lo, source=source, kind="share", digits=share_digits, how=f"{how}: {what}, lower end")
        F.add(f"{key}.ci_high", hi, source=source, kind="share", digits=share_digits, how=f"{how}: {what}, upper end")


def method_section(F: Findings) -> None:
    """Declared design parameters the app shows (D048): the drive-time fit and each line's window."""
    from vf import services as S
    from vf import travel as T

    tsrc, ssrc = ROOT / "src" / "vf" / "travel.py", ROOT / "src" / "vf" / "services.py"
    F.add("method.drive_time.n_trips", T.N_TRIPS, source=tsrc, kind="count",
          how="OSRM free-flow car trips the drive-time model was fitted to (score research, Nov 21; vf.travel.N_TRIPS)")
    F.add("method.drive_time.n_anchors", T.N_ANCHORS, source=tsrc, kind="count",
          how="anchor post offices those trips start from (vf.travel.N_ANCHORS)")
    F.add("method.drive_time.coef_minutes", T.A_MIN, source=tsrc, kind="number", digits=2,
          how="minutes = state factor x 2.30 x km^0.842: the multiplier (vf.travel.A_MIN)")
    F.add("method.drive_time.exponent", T.B_EXP, source=tsrc, kind="number", digits=3,
          how="minutes = state factor x 2.30 x km^0.842: the exponent on straight-line km (vf.travel.B_EXP)")
    F.add("method.drive_time.log_sd", T.LOG_SD, source=tsrc, kind="score",
          how="leave-one-anchor-out SD of log(OSRM minutes / fitted minutes) (vf.travel.LOG_SD)")
    F.add("method.drive_time.band_coverage", T.BAND_COVERAGE, source=tsrc, kind="share", digits=1,
          how="share of held-out OSRM trips inside the shown band, minutes x e^-0.52 to x e^+0.52 (vf.travel.BAND_COVERAGE)")
    F.add("method.allowance.urgent", S.ALLOWANCE_URGENT, source=ssrc, kind="minutes",
          how="declared evidence allowance: a better-evidenced facility may come first if at most this many "
              "estimated minutes further (urgent lines; design choice, D048 item 4)")
    F.add("method.allowance.planned", S.ALLOWANCE_PLANNED, source=ssrc, kind="minutes",
          how="the same allowance for planned care (MRI, cardiac surgery, oncology)")
    F.add("method.max_candidates", S.MAX_CANDIDATES, source=ssrc, kind="count",
          how="candidate cap: the ranker orders at most this many nearest recommendable records (vf.services.MAX_CANDIDATES)")
    for line, w in S.WINDOWS.items():
        F.add(f"method.window.{line}", w.minutes, source=ssrc, kind="minutes",
              how=f"{line} drive-time window, a planning threshold not a clinical deadline ({w.kind}): {w.basis}")


def _eval_split(F: Findings, split: str, res_p: Path, b: Path) -> None:
    import pandas as pd

    g = f"sealed_eval.{split}"
    ev = res_p.parent
    if not res_p.exists():
        F.null(f"{g}.queries", source=res_p, reason=f"{res_p.name} not found (eval_sealed.py score not run for {split})")
        return
    r = json.loads(res_p.read_text())
    base = (f"{split} half of the sealed evaluation (scripts/eval_sealed.py, protocol reports/eval_sealed_protocol.md "
            "with Amendment 3: every tool on the same drive-time window, same right to abstain). Judges are AI models "
            "(A claude-haiku-5-5, B claude-sonnet-5-5, C claude-opus-5-5 on A/B disagreements), blind to the tool, "
            "reading only this dataset's text")
    if split == "dev":
        base += ". The dev half was used for design decisions (D053), so it is not clean evidence"
    F.add(f"{g}.queries", r["n_queries"], source=res_p, kind="count", how=f"{base}: queries")
    metrics = {
        "coverage": "queries the tool answered (gave a top pick) / all queries",
        "precision": "answered queries whose top pick's record the judges found clearly shows the service (Q1 yes) "
                     "/ answered queries",
        "good_pick": "answered queries whose top pick is Q1 yes and judged sensible for the place (Q2 yes) / answered",
        "abstained_with_support": "abstentions where the judges found a facility inside the same window whose "
                                  "record clearly shows the service / abstentions (a lower bound: at most 10 "
                                  "facilities with some signal read per query)",
        "abstained_with_support_of_all": "abstentions with a judge-supported option / all queries",
        "skipped_nearer": "answers whose top pick is beaten by a different record at least 1 km nearer that the "
                          "judges found clearly shows the service / answers",
        "wrong_picks_of_all": "answered queries whose top pick is Q1 no / all queries",
        "precision_excluding_listing_only": "precision with Q1 yes votes that rest only on the specialties listing "
                                            "counted as no (strict)",
        "precision_excluding_listing_any": "precision with Q1 yes votes that cite the specialties listing at all "
                                           "counted as no (loose)",
    }
    names = {"specialty": "specialty filter (obvious query)", "any_fact": "any-fact baseline (any fact names it)",
             "ranker": "our ranker (v1-final, frozen)"}
    for tool, t in (r.get("tools") or {}).items():
        for m, what in metrics.items():
            d = t.get(m)
            if isinstance(d, dict) and "k" in d:
                _add_kn(F, f"{g}.{tool}.{m}", d["k"], d["n"], source=res_p, ci_given=d.get("ci95"),
                        how=f"{base}. {names.get(tool, tool)}: {what}")
        al = t.get("skipped_nearer_allowance") or {}
        for part in ("within_allowance", "beyond_allowance"):
            d = al.get(part)
            if isinstance(d, dict) and "k" in d:
                _add_kn(F, f"{g}.{tool}.skipped_nearer_{part}", d["k"], d["n"], source=res_p, ci_given=d.get("ci95"),
                        how=f"{base}. {names.get(tool, tool)}: skipped a nearer supported option with the pick "
                            f"{'at most' if part == 'within_allowance' else 'more than'} the declared allowance "
                            "(15 minutes, 30 for MRI, cardiac surgery and oncology) further / answers")
    for svc, by in (r.get("by_service") or {}).items():
        for tool, d in by.items():
            for f_ in ("queries", "answered", "correct"):
                if f_ in d:
                    F.add(f"{g}.by_service.{svc}.{tool}.{f_}", d[f_], source=res_p, kind="count",
                          how=f"{base}. {svc}, {names.get(tool, tool)}: {f_} ('correct' = top pick Q1 yes)")
    third = {}
    for svc, by in (r.get("by_service") or {}).items():
        if svc in ("emoc", "dialysis"):
            continue
        for tool, d in by.items():
            t3 = third.setdefault(tool, {"queries": 0, "answered": 0, "correct": 0})
            for f_ in t3:
                t3[f_] += int(d.get(f_, 0))
    for tool, d in third.items():
        for f_, v in d.items():
            F.add(f"{g}.by_service.third_service.{tool}.{f_}", v, source=res_p, kind="count",
                  how=f"{base}. Third service (one per point, every service except emoc and dialysis, summed from "
                      f"by_service), {names.get(tool, tool)}: {f_}")
    wsp = r.get("without_sea_pixel") or {}
    if "n_points" in wsp:
        F.add(f"{g}.without_sea_pixel.points_dropped", wsp["n_points"], source=res_p, kind="count",
              how=f"{base}: sea-pixel post offices among the points (D048 item 7), dropped in this sensitivity line")
        F.add(f"{g}.without_sea_pixel.queries_dropped", wsp.get("n_queries_dropped"), source=res_p, kind="count",
              how=f"{base}: queries dropped with them")
        for tool, t in (wsp.get("tools") or {}).items():
            for m in ("coverage", "precision"):
                d = t.get(m)
                if isinstance(d, dict) and "k" in d:
                    _add_kn(F, f"{g}.without_sea_pixel.{tool}.{m}", d["k"], d["n"], source=res_p,
                            ci_given=d.get("ci95"), how=f"{base}. Without sea-pixel points, {names.get(tool, tool)}: "
                                                        f"{metrics[m]}")
    pk0 = ev / f"picks_{split}.json"
    if pk0.exists():
        cr = [c for q in json.loads(pk0.read_text()).get("queries", [])
              for c in (((q.get("tools") or {}).get("ranker") or {}).get("cards_reach") or [])]
        ok = sum(1 for c in cr if c.get("inside") and c.get("card_minutes") is not None
                 and abs(float(c["card_minutes"]) - float(c["harness_minutes"])) <= 0.05 + 1e-9)
        _add_kn(F, f"{g}.integrity.card_minutes_match", ok, len(cr), source=pk0,
                how=f"integrity check ({split}): ranker cards whose shown minutes are within 0.05 of the harness's "
                    "minutes and inside the pinned window")
    ce = ev / f"cost_estimate_{split}.json"
    if ce.exists():
        c = json.loads(ce.read_text())
        for nm, lab in (("expected", "expected"), ("high", "high (every walk to the cap)"),
                        ("low", "low (picks only)")):
            if lab in c and "usd" in c[lab]:
                F.add(f"{g}.cost_estimate.{nm}_usd", c[lab]["usd"], source=ce, kind="usd",
                      how=f"judge cost estimate before the {split} run, {lab}")
    for st, n in (r.get("walk_status") or {}).items():
        F.add(f"{g}.walk_status.{_slug(st)}", n, source=res_p, kind="count",
              how=f"{base}: queries whose judge walk (nearest facilities with any signal, up to 10) ended {st!r}")
    for ck, d in (r.get("integrity_checks") or {}).items():
        if isinstance(d, dict) and "k" in d:
            _add_kn(F, f"{g}.integrity.{ck}", d["k"], d["n"], source=res_p, ci_given=d.get("ci95"),
                    how=f"integrity check, not a judged result ({split}): {ck.replace('_', ' ')}; holds by construction")
    meta = r.get("meta") or {}
    led = meta.get("ledger") or {}
    if "usd" in led:
        F.add(f"{g}.judge_spend_ledger_usd", led["usd"], source=res_p, kind="usd",
              how=f"cumulative judge spend on ledger purpose {led.get('purpose')!r} over every run of this half "
                  f"({led.get('first_at')} to {led.get('last_at')})")
    sp_p, t0 = ev / "spend.jsonl", meta.get("picks_created_at_utc")
    if sp_p.exists() and t0:
        usd, calls = 0.0, 0
        for line in sp_p.read_text().splitlines():
            if line.strip():
                x = json.loads(line)
                if x.get("purpose") == f"judge_{split}" and str(x.get("at")) >= t0:
                    usd += float(x.get("cost_usd") or 0.0)
                    calls += 1
        F.add(f"{g}.judge_spend_usd", usd, source=sp_p, kind="usd",
              how=f"spend of the judging run behind these results: cost_usd of judge_{split} ledger lines at or after "
                  f"the picks ({t0})")
        F.add(f"{g}.judge_api_calls", calls, source=sp_p, kind="count", how="API calls of that run")

    # derived from the per-query outcomes and per-unit judgments
    oc_p, jd_p, jj_p, pk_p = (ev / f"outcomes_{split}.csv", ev / f"judgments_{split}.csv",
                              ev / f"judged_{split}.json", ev / f"picks_{split}.json")
    if not (oc_p.exists() and jd_p.exists()):
        F.null(f"{g}.judges.q1.kappa", source=jd_p, reason="outcomes or judgments csv not found")
        return
    o = pd.read_csv(oc_p, dtype={"pick_id": str, "nearest_yes_id": str})
    F.add(f"{g}.points", int(o["po_id"].nunique()), source=oc_p, kind="count",
          how=f"{base}: distinct random post offices (po_id) the queries were asked at; emoc and dialysis at every "
              "point, a third service at some")
    j = pd.read_csv(jd_p)
    for q in ("q1", "q2"):
        jq = j[j["kind"] == q]
        A, B = [_tf(x) for x in jq["vote_A"]], [_tf(x) for x in jq["vote_B"]]
        pairs = [(a, b_) for a, b_ in zip(A, B) if a is not None and b_ is not None]
        agree = sum(a == b_ for a, b_ in pairs)
        what = ("Q1, record clearly shows the service" if q == "q1" else "Q2, sensible for the place")
        hw = f"{base}. Judge units for {what}, from {jd_p.name}"
        _add_kn(F, f"{g}.judges.{q}.agree", agree, len(pairs), source=jd_p, share_digits=1,
                how=f"{hw}: units where judges A and B gave the same vote / units with both votes")
        F.add(f"{g}.judges.{q}.units", len(jq), source=jd_p, kind="count", how=f"{hw}: judge units")
        F.add(f"{g}.judges.{q}.kappa", _cohen_kappa([a for a, _ in pairs], [b_ for _, b_ in pairs]), source=jd_p,
              kind="score", how=f"{hw}: Cohen's kappa, judge A vs judge B")
        F.add(f"{g}.judges.{q}.sent_to_c", int(jq["vote_C"].notna().sum()), source=jd_p, kind="count",
              how=f"{hw}: units sent to judge C (A and B disagreed)")
        F.add(f"{g}.judges.{q}.a_yes", sum(1 for a in A if a), source=jd_p, kind="count", how=f"{hw}: judge A yes votes")
        F.add(f"{g}.judges.{q}.b_yes", sum(1 for b_ in B if b_), source=jd_p, kind="count", how=f"{hw}: judge B yes votes")
        F.add(f"{g}.judges.{q}.unresolved", int(jq["final"].isna().sum()), source=jd_p, kind="count",
              how=f"{hw}: units with no final verdict")

    rk = o[o["tool"] == "ranker"].set_index("query_id")
    af = o[o["tool"] == "any_fact"].set_index("query_id").reindex(rk.index)
    both = rk["answered"].astype(bool) & af["answered"].astype(bool)
    hw = f"{base}. Ranker against the any-fact baseline on queries both answered, from {oc_p.name}"
    F.add(f"{g}.ranker_vs_any_fact.both_answered", int(both.sum()), source=oc_p, kind="count", how=f"{hw}: queries")
    F.add(f"{g}.ranker_vs_any_fact.same_top_pick", int((rk["pick_id"][both] == af["pick_id"][both]).sum()),
          source=oc_p, kind="count", how=f"{hw}: same top pick (facility_id)")
    rc = rk["correct"][both].map(_tf) == True  # noqa: E712
    ac = af["correct"][both].map(_tf) == True  # noqa: E712
    n10, n01 = int((rc & ~ac).sum()), int((~rc & ac).sum())
    F.add(f"{g}.ranker_vs_any_fact.ranker_only_supported", n10, source=oc_p, kind="count",
          how=f"{hw}: ranker's pick Q1 yes and the baseline's Q1 no (discordant)")
    F.add(f"{g}.ranker_vs_any_fact.any_fact_only_supported", n01, source=oc_p, kind="count",
          how=f"{hw}: baseline's pick Q1 yes and the ranker's Q1 no (discordant)")
    F.add(f"{g}.ranker_vs_any_fact.sign_test_p", _sign_test(n10, n01), source=oc_p, kind="score", digits=3,
          how=f"{hw}: exact two-sided binomial sign test on the discordant queries, p = 0.5")

    ra = rk[rk["answered"].astype(bool)]
    wrong = ra[ra["correct"].map(_tf) == False]  # noqa: E712
    for svc, n in wrong["service"].value_counts().sort_index().items():
        F.add(f"{g}.ranker.wrong_picks_by_service.{svc}", int(n), source=oc_p, kind="count",
              how=f"{base}. Ranker answers whose top pick is Q1 no, service {svc}")
    F.add(f"{g}.ranker.not_sensible", int((ra["q2"].map(_tf) == False).sum()), source=oc_p, kind="count",  # noqa: E712
          how=f"{base}. Ranker answers whose top pick was judged not sensible for the place (Q2 no). Q2 is the "
              "least reliable judge question (see judges.q2.kappa)")
    sk = ra[ra["skipped_nearer"].map(_tf) == True]  # noqa: E712
    beyond = sk[sk["skip_beyond_allowance"].map(_tf) == True]  # noqa: E712
    if len(beyond):
        F.add(f"{g}.ranker.skipped_beyond_extra_min_min", float(beyond["skip_extra_min"].min()), source=oc_p,
              kind="minutes", how=f"{base}. Skips beyond the allowance: fewest extra estimated minutes of the pick "
                                  "over the nearer supported option")
        F.add(f"{g}.ranker.skipped_beyond_extra_min_max", float(beyond["skip_extra_min"].max()), source=oc_p,
              kind="minutes", how=f"{base}. Skips beyond the allowance: most extra estimated minutes")
    grades = pd.read_parquet(b / "grades.parquet", columns=["facility_id", "service_line", "grade"])
    gm = grades.set_index(["facility_id", "service_line"])["grade"]
    g1 = [fid for fid, svc in zip(sk["nearest_yes_id"], sk["service"]) if gm.get((fid, svc)) == 1]
    F.add(f"{g}.ranker.skipped_nearer.graded_1", len(g1), source=b / "grades.parquet", kind="count",
          how=f"{base}. Of the ranker's skips, those whose nearer judge-supported facility is graded 1 (claimed) by "
              "recipe v0.3 in the frozen bundle, so it never passed the grade 2 gate: grading misses, not ordering")
    if pk_p.exists():
        pk = json.loads(pk_p.read_text())
        cards = {q["query_id"]: set(((q.get("tools") or {}).get("ranker") or {}).get("result_ids") or [])
                 for q in pk.get("queries", [])}
        win = sk[sk["skip_within_allowance"].map(_tf) == True]  # noqa: E712
        on_cards = sum(1 for qid, fid in zip(win.index, win["nearest_yes_id"]) if fid in cards.get(qid, set()))
        F.add(f"{g}.ranker.skipped_nearer_within_allowance.nearer_on_own_cards", on_cards, source=pk_p, kind="count",
              how=f"{base}. Of the skips inside the allowance, those where the nearer supported facility is on the "
                  "ranker's own answer (card 2 or 3): the allowance put a better-evidenced record first, as designed")
    if jj_p.exists():
        jq = json.loads(jj_p.read_text()).get("queries", {})
        ab = rk[~rk["answered"].astype(bool)].index
        walked = [int((jq.get(q) or {}).get("walked") or 0) for q in ab]
        _add_kn(F, f"{g}.ranker.abstentions", len(ab), len(rk), source=oc_p,
                how=f"{base}. Ranker abstentions ('no reliable option') / all queries")
        F.add(f"{g}.ranker.abstentions.no_signal_in_window", sum(1 for w in walked if w == 0), source=jj_p,
              kind="count", how=f"{base}. Ranker abstentions where no facility with any signal for the service lay "
                                 "inside the window (the judges had nothing to read): nothing to miss in this dataset")
        F.add(f"{g}.ranker.abstentions.facilities_read", sum(walked), source=jj_p, kind="count",
              how=f"{base}. Facilities the judges read across the other ranker abstentions")

    # human check of the judges
    hl_p, hk_p = ev / f"human_labels_{split}.csv", ev / f"human_key_{split}.csv"
    if hl_p.exists():
        hl = pd.read_csv(hl_p, dtype=str, keep_default_na=False)
        F.add(f"{g}.human.items", len(hl), source=hl_p, kind="count",
              how="items in the human label file for Hruday (stratified across tools, outcomes and judge disagreements)")
        filled = int((hl.get("human_clearly_offers", pd.Series(dtype=str)).str.strip() != "").sum())
        if filled == 0:
            F.null(f"{g}.human.agreement_q1", source=hl_p, kind="share",
                   reason="not labeled yet: human_clearly_offers is empty on every item, so no human agreement "
                          "figure exists (reports/HUMAN_LABELING.md)")
        else:
            F.null(f"{g}.human.agreement_q1", source=hl_p, kind="share",
                   reason=f"{filled} items labeled; run scripts/eval_sealed.py agreement to score them against "
                          f"{hk_p.name}, then rebuild")


def sealed_section(F: Findings, b: Path, data_dir: Path, path: Optional[Path]) -> None:
    ev = data_dir / "labels" / "eval_sealed"
    old = ev / "archive_v0_radius" / "results_dev.json"
    if old.exists():
        r0 = json.loads(old.read_text())
        hw0 = ("archived dev-half run under the old km radii and the v0 ranker (D053), before the window rule and "
               "v1-final; superseded by sealed_eval.dev, kept for the D053 comparison. D053 and the reports quote "
               "precision_excluding_listing_only (listing-only yes votes counted as no)")
        for tool, t0 in (r0.get("tools") or {}).items():
            for m in ("precision", "precision_excluding_listing_only", "skipped_nearer", "coverage"):
                d = t0.get(m)
                if isinstance(d, dict) and "k" in d:
                    _add_kn(F, f"sealed_eval.dev_v0_radius.{tool}.{m}", d["k"], d["n"], source=old,
                            ci_given=d.get("ci95"), how=f"{hw0}: {tool} {m.replace('_', ' ')}")
        oc0, rc0 = old.with_name("outcomes_dev.csv"), ev / "dev_rubric_check_q1_yes.csv"
        if oc0.exists() and rc0.exists():
            import pandas as pd
            o0 = pd.read_csv(oc0, dtype={"pick_id": str})
            rcd = pd.read_csv(rc0, dtype=str, keep_default_na=False)
            lo_units = set(rcd[rcd["class"] == "listing_only"]["unit_id"])
            for tool in ("specialty", "any_fact", "ranker"):
                a0 = o0[(o0["tool"] == tool) & o0["answered"].astype(bool)]
                yes = a0["correct"].map(_tf) == True  # noqa: E712
                k0 = int((yes & ~(a0["pick_id"] + "|" + a0["service"]).isin(lo_units)).sum())
                _add_kn(F, f"sealed_eval.dev_v0_radius.{tool}.precision_without_hand_listing_only", k0, len(a0),
                        source=oc0, how=f"{hw0}: {tool} top picks with Q1 yes, leaving out the units the dev rubric "
                                        "check hand-classified as listing-only / answered (the D053 figures)")
    sealed_p = Path(path) if path else ev / "results_sealed.json"
    _eval_split(F, "sealed", sealed_p, b)
    _eval_split(F, "dev", sealed_p.with_name("results_dev.json"), b)
    F.null("sealed_eval.sealed.ranker.not_sensible_reasons_merge", source=ROOT / "reports" / "eval_sealed_results.md",
           kind="count", reason="a crude keyword read of the judges' Q2 reasons in the report; the keyword rule was not "
                                "saved, so it cannot be recomputed exactly",
           report_states="of the 34 ranker picks judged not sensible, 33 mention a record that merges branches or "
                         "several facilities and 20 a listed city inconsistent with the distance")
    rc = ev / "dev_rubric_check_q1_yes.csv"
    if rc.exists():
        import pandas as pd
        d = pd.read_csv(rc, dtype=str, keep_default_na=False)
        _add_kn(F, "sealed_eval.dev_rubric_check.listing_only", int((d["class"] == "listing_only").sum()), len(d),
                source=rc, share_digits=1,
                how="dev half, before sealing (D053): final Q1 yes verdicts that rest only on a specialty or "
                    "department name / all final Q1 yes verdicts, hand-classified; threshold fixed in advance at 10%")
    else:
        F.null("sealed_eval.dev_rubric_check.listing_only.k", source=rc, reason="dev rubric check file not found")


def spend_section(F: Findings, data_dir: Path) -> None:
    led = sorted((data_dir / "labels").rglob("spend*.jsonl"))
    if not led:
        F.null("spend.project_usd", source=data_dir / "labels", reason="no spend*.jsonl ledger under labels/")
        return
    total = 0.0
    for p in led:
        s = 0.0
        n = 0
        for line in p.read_text().splitlines():
            if line.strip():
                s += float(json.loads(line).get("cost_usd") or 0.0)
                n += 1
        total += s
        name = _slug(str(p.parent.relative_to(data_dir / "labels")) + "_" + p.stem)
        F.add(f"spend.ledger.{name}.usd", s, source=p, kind="usd", how=f"sum of cost_usd over the {n} lines of this ledger")
        F.add(f"spend.ledger.{name}.calls", n, source=p, kind="count", how="lines (API responses) in this ledger")
    F.add("spend.project_usd", total, source=data_dir / "labels", kind="usd",
          how="project LLM spend: sum of cost_usd over every spend*.jsonl ledger under labels/ ("
              + ", ".join(F.rel(p) for p in led) + "); the project cap is 28 USD")


def outside_section(F: Findings, b: Path, data_dir: Path) -> None:
    import pandas as pd

    mp = b / "outside_meta.json"
    if not mp.exists():
        F.null("outside.card_facts", source=mp, reason="bundle has no outside_meta.json")
        return
    m = json.loads(mp.read_text())
    hw = "outside lists in the frozen bundle (vf.public_list v2, D055, D057 terms gates)"
    F.add("outside.card_facts_bundle", m.get("card_facts"), source=mp, kind="count",
          how=f"{hw}: card facts in outside_card_facts.parquet (AERB and NCG; LaQshya lines come from "
              "laqshya_links.parquet, see laqshya.*)")
    F.add("outside.lane_rows", m.get("lane_rows"), source=mp, kind="count",
          how=f"{hw}: lane rows 'also on government or registry lists nearby', reuse-permitted lists only")
    F.add("outside.lane_cross_source_merged_rows", m.get("lane_cross_source_merged_rows"), source=mp, kind="count",
          how=f"{hw}: rows re-keyed into one place across sources at build time")
    srcs = m.get("sources") or {}
    F.add("outside.built.card_links_all_sources", sum(int(v.get("card_links", 0)) for v in srcs.values()), source=mp,
          kind="count", how=f"{hw}: accepted card links summed over every source as built, including those withheld "
                            "from the app by the D057 terms gate")
    F.add("outside.built.lane_rows_all_sources", sum(int(v.get("lane_rows", 0)) for v in srcs.values()), source=mp,
          kind="count", how=f"{hw}: lane rows summed over every source as built, including withheld sources")
    info = m.get("sources_info") or {}
    lane_ok = set(m.get("lane_sources_shown") or [])
    card_ok = set(m.get("card_sources_shown") or [])
    for sid, s in (m.get("sources") or {}).items():
        k = f"outside.{sid}"
        for f_ in ("list_rows", "card_links", "card_records", "lane_candidates", "lane_rows"):
            if f_ in s:
                shown = (sid in lane_ok) if f_.startswith("lane") else (sid in card_ok)
                F.add(f"{k}.{f_}", s[f_], source=mp, kind="count",
                      how=f"{hw}: {sid} {f_.replace('_', ' ')} as built"
                          + ("" if shown else " (withheld from the app by the D057 terms gate)"))
        for why, n in (s.get("lane_excluded") or {}).items():
            F.add(f"{k}.lane_excluded.{_slug(why)}", n, source=mp, kind="count",
                  how=f"{hw}: {sid} lane candidates dropped by rule {why!r}")
    cf, ln = b / "outside_card_facts.parquet", b / "outside_lane.parquet"
    if cf.exists():
        c = pd.read_parquet(cf)
        for sid, n in c["source_id"].value_counts().sort_index().items():
            F.add(f"outside.shown.card_facts.{sid}", int(n), source=cf, kind="count",
                  how=f"rows of the bundle's outside_card_facts.parquet from {sid}: card facts the app can show")
    if ln.exists():
        lr = pd.read_parquet(ln)
        for sid, n in lr["source_id"].value_counts().sort_index().items():
            F.add(f"outside.shown.lane_rows.{sid}", int(n), source=ln, kind="count",
                  how=f"rows of the bundle's outside_lane.parquet from {sid}: lane entries the app can show")
    # link precision per band, recomputed from the reader verdicts
    for sid in ("aerb_rt", "ncg", "nhp_bloodbank", "mohfw_trauma", "nabi_burns"):
        lp = data_dir / "silver" / f"ext_links_{sid}.parquet"
        if not lp.exists():
            F.null(f"outside.{sid}.band.auto.precision", source=lp, reason=f"silver/ext_links_{sid}.parquet not found")
            continue
        d = pd.read_parquet(lp, columns=["band", "reader_verdict", "accepted", "facility_id"])
        v = d["reader_verdict"].astype(str)
        F.add(f"outside.{sid}.accepted_links", int(d["accepted"].fillna(False).astype(bool).sum()), source=lp,
              kind="count", how=f"{sid}: reader-marked 'same' pairs inside a band that passed the 0.90 rule")
        for band in ("auto", "middle", "low"):
            sel = d["band"] == band
            same, diff, uns = int((sel & (v == "same")).sum()), int((sel & (v == "different")).sum()), \
                int((sel & (v == "unsure")).sum())
            if same + diff + uns == 0:
                continue
            k = f"outside.{sid}.band.{band}"
            hw2 = (f"{sid} link candidates in the {band} band, agent reader verdicts (silver/ext_links_{sid}.parquet); "
                   "precision = same / (same + different), unsure left out as the rule says")
            F.add(f"{k}.same", same, source=lp, kind="count", how=f"{hw2}: same")
            F.add(f"{k}.different", diff, source=lp, kind="count", how=f"{hw2}: different")
            F.add(f"{k}.unsure", uns, source=lp, kind="count", how=f"{hw2}: unsure")
            if same + diff:
                F.add(f"{k}.precision", same / (same + diff), source=lp, kind="score", digits=3, how=hw2)
                lo = _cp_lower(same, same + diff)
                l2, h2 = _cp_two_sided(same, same + diff)
                F.add(f"{k}.ci_low", l2, source=lp, kind="score", digits=3,
                      how=f"{hw2}: two-sided 95% Clopper-Pearson interval, lower end")
                F.add(f"{k}.ci_high", h2, source=lp, kind="score", digits=3,
                      how=f"{hw2}: two-sided 95% Clopper-Pearson interval, upper end")
                F.add(f"{k}.lower95_one_sided", lo, source=lp, kind="score", digits=3,
                      how=f"{hw2}: one-sided 95% Clopper-Pearson lower bound; the band is accepted only if >= 0.90 "
                          f"(here {'passes' if lo >= 0.9 else 'fails'})")
                if uns:
                    F.add(f"{k}.lower95_unsure_as_wrong", _cp_lower(same, same + diff + uns), source=lp, kind="score",
                          digits=3, how=f"{hw2}: the same bound with unsure counted as wrong (sensitivity)")
    F.null("outside.aerb_rt.lane_residual_in_dataset", source=ROOT / "reports" / "ext_aerb_rt.md", kind="share",
           reason="AERB's 40-row lane audit is described in reports/ext_aerb_rt.md and reports/outside_lists.md, "
                  "with no machine-readable audit file to recompute it from; it also predates the v2 lane fixes",
           report_states="about 5 to 8% of AERB lane rows may be in our data (one-sided 95% upper bound 18%)")


def laqshya_section(F: Findings, b: Path, grades, data_dir: Path, path: Optional[Path]) -> None:
    """LaQshya links as shipped in the frozen bundle (laqshya_links.parquet, every candidate agent-reviewed)."""
    import pandas as pd

    p = Path(path) if path else b / "laqshya_links.parquet"
    if not p.exists():
        F.null("laqshya.link_candidates", source=p, reason=f"{p} not found; pass --laqshya-links FILE")
        return
    d = pd.read_parquet(p) if p.suffix == ".parquet" else pd.read_csv(p, dtype=str, keep_default_na=False)
    if "review" not in d.columns:
        F.null("laqshya.link_candidates", source=p, reason="link file has no review column")
        return
    how = ("LaQshya units (NHSRC list, 8 Apr 2026) linked by name to our public records in the PIN's district or "
           "state; every candidate read by an agent (Claude), not by Hruday (D051)")
    F.add("laqshya.link_candidates", len(d), source=p, kind="count", how=how + ": candidate links")
    rv = d["review"].astype(str)
    for mk, c in rv.value_counts().sort_index().items():
        F.add(f"laqshya.links_{_slug(mk)}", int(c), source=p, kind="count", how=how + f": links marked {mk!r}")
    ok, bad = int((rv == "correct").sum()), int((rv == "wrong").sum())
    _add_kn(F, "laqshya.matcher_precision", ok, ok + bad, source=p, ci="cp",
            how=how + ": matcher precision, correct / (correct + wrong), unsure left out")
    c = d[rv == "correct"]
    F.add("laqshya.records_with_correct_link", int(c["facility_id"].nunique()), source=p, kind="count",
          how="our records with at least one correct LaQshya link (each gets a cited card line)")
    F.add("laqshya.units_with_correct_link", int(c["facility_uid"].nunique()), source=p, kind="count",
          how="LaQshya units with at least one correct link")
    if "has_current_maternity_ot" in c.columns:
        cur = c[c["has_current_maternity_ot"].astype(str) == "True"]
        F.add("laqshya.records_current_maternity_ot", int(cur["facility_id"].nunique()), source=p, kind="count",
              how="of those records, ones whose linked unit holds a maternity OT certificate current at the link "
                  "build (has_current_maternity_ot)")
        if "caesarean_named" in c.columns:
            F.add("laqshya.records_current_maternity_ot_naming_caesarean",
                  int(cur[cur["caesarean_named"].astype(str) == "True"]["facility_id"].nunique()), source=p,
                  kind="count", how="of those, records with a fact naming a caesarean: the rest are a gap in the "
                                    "record, not evidence about the hospital")
    if "emoc_grade" in c.columns:
        for gr, n in c.drop_duplicates("facility_id")["emoc_grade"].astype(str).value_counts().sort_index().items():
            F.add(f"laqshya.records_correct_link_emoc_grade_{_slug(gr)}", int(n), source=p, kind="count",
                  how=f"records with a correct link whose emoc grade (as stored in the link file) is {gr}")


def tagger_wp4b_section(F: Findings, b: Path, data_dir: Path) -> None:
    dp, ip, pp = (data_dir / "labels" / "wp4b" / f for f in ("decision.json", "impact.json", "pilot_result.json"))
    if not (dp.exists() and ip.exists()):
        F.null("tagger_wp4b.mix.macro_f1", source=dp, reason="labels/wp4b decision.json or impact.json not found")
        return
    dec, imp = json.loads(dp.read_text()), json.loads(ip.read_text())
    man = json.loads((b / "manifest.json").read_text()) if (b / "manifest.json").exists() else {}
    shipped = ((man.get("versions") or {}).get("fact_tags_model"))
    if shipped and shipped != dec.get("model_version"):
        F.warnings.append(f"bundle fact_tags_model {shipped!r} differs from WP4b decision model {dec.get('model_version')!r}")
    gold = imp.get("gold") or {}
    names = {"candidate_mix": ("mix", "the tagger mix in the frozen bundle (WP4b 2b_r2 on ob_delivery, student 1b on "
                                      "h24, icu, ventilator, regex v0.2 on the rest; promoted D059, D064)"),
             "current": ("pre_wp4b", "the WP4 mix it replaced (student 1b on 4 tags, regex v0.2 on the rest)"),
             "regex_v0.2": ("regex", "regex v0.2 alone")}
    for src_k, (k, what) in names.items():
        s = gold.get(src_k)
        if not s:
            continue
        hw = (f"{what}: on the {s.get('rows')} agent-labeled gold texts over {s.get('tags')} tags; 95% interval from a "
              "stratified bootstrap (2,000 resamples). Gold also decided the per-tag ship gate, so the mix is slightly "
              "optimistic (D050)")
        lo, hi = s.get("macro_f1_ci95", [None, None])
        F.add(f"tagger_wp4b.{k}.macro_f1", s["macro_f1"], source=ip, kind="score",
              display=f"{s['macro_f1']:.3f} ({lo:.3f} to {hi:.3f})" if lo is not None else None, how=f"{hw}: macro F1")
        if lo is not None:
            F.add(f"tagger_wp4b.{k}.macro_f1_ci_low", lo, source=ip, kind="score", digits=3, how=f"{hw}: lower end")
            F.add(f"tagger_wp4b.{k}.macro_f1_ci_high", hi, source=ip, kind="score", digits=3, how=f"{hw}: upper end")
        F.add(f"tagger_wp4b.{k}.macro_precision", s["macro_precision"], source=ip, kind="score", digits=3,
              how=f"{hw}: macro precision")
        F.add(f"tagger_wp4b.{k}.macro_recall", s["macro_recall"], source=ip, kind="score", digits=3,
              how=f"{hw}: macro recall")
    for nm, key in (("mix_minus_current", "paired_macro_f1_delta_mix_minus_current_ci95"),
                    ("mix_minus_regex", "paired_macro_f1_delta_mix_minus_regex_ci95")):
        d = gold.get(key)
        if d:
            F.add(f"tagger_wp4b.paired.{nm}.ci_low", d[0], source=ip, kind="score", digits=4,
                  how=f"paired bootstrap 95% interval of gold macro F1, {nm.replace('_', ' ')}: lower end")
            F.add(f"tagger_wp4b.paired.{nm}.ci_high", d[1], source=ip, kind="score", digits=4,
                  how=f"paired bootstrap 95% interval of gold macro F1, {nm.replace('_', ' ')}: upper end")
    pt = dec.get("per_tag") or {}
    for tag in ("ob_delivery", "burns"):
        t = pt.get(tag) or {}
        c = (t.get("candidates") or {}).get("2b_r2") or {}
        if not c:
            continue
        hw = f"WP4b, tag {tag}, candidate 2b_r2 (fine-tuned bge-small encoder) under the D050 ship rule"
        F.add(f"tagger_wp4b.{tag}.current_gold_f1", t.get("current_gold_f1"), source=dp, kind="score",
              how=f"{hw}: the method it would replace ({t.get('current_method')}), gold F1")
        F.add(f"tagger_wp4b.{tag}.candidate_gold_f1", c.get("gold_f1"), source=dp, kind="score", how=f"{hw}: gold F1")
        d = c.get("paired_delta_ci95") or [None, None]
        F.add(f"tagger_wp4b.{tag}.paired_ci_low", d[0], source=dp, kind="score", digits=3,
              how=f"{hw}: paired 95% interval of the gold F1 gain, lower end (gate 1 needs > 0)")
        F.add(f"tagger_wp4b.{tag}.paired_ci_high", d[1], source=dp, kind="score", digits=3,
              how=f"{hw}: paired 95% interval of the gold F1 gain, upper end")
        hr = c.get("hand_read") or {}
        if hr:
            _add_kn(F, f"tagger_wp4b.{tag}.hand_read_favours_candidate", hr["favours_candidate"], hr["n"], source=dp,
                    how=f"{hw}: blinded hand read of disagreements by an agent that did not build it, texts in the "
                        f"candidate's favour (gate 2 needs at least {hr.get('needed')})")
        F.add(f"tagger_wp4b.{tag}.ships", 1 if c.get("passes_both") else 0, source=dp, kind="count",
              how=f"{hw}: 1 if it passed both gates and ships, else 0")
    sens = (((dec.get("gold_gate_sensitivity") or {}).get("per_candidate_tag") or {}).get("2b_r2|ob_delivery") or {})
    ext, oth = sens.get("declared_seed_extended") or {}, sens.get("other_seeds") or {}
    if ext:
        ci = ext.get("ci95") or [None, None]
        F.add("tagger_wp4b.ob_delivery.extended_ci_low", ci[0], source=dp, kind="score", digits=4,
              how=f"disclosure: paired interval of the ob_delivery gold gain at {ext.get('resamples'):,} resamples "
                  "(declared seed): lower end")
        F.add("tagger_wp4b.ob_delivery.extended_resamples", ext.get("resamples"), source=dp, kind="count",
              how="disclosure: resamples in that extended run")
        F.add("tagger_wp4b.ob_delivery.p_gain_le_0", ext.get("p_delta_le_0"), source=dp, kind="share", digits=1,
              how="disclosure: share of 50,000 paired resamples (declared seed) where the ob_delivery gold gain is <= 0")
    if oth:
        _add_kn(F, "tagger_wp4b.ob_delivery.other_seeds_lower_above_0", oth.get("runs_with_lower_bound_above_0"),
                oth.get("runs"), source=dp, how="disclosure: of 100 other bootstrap seeds (2,000 resamples each), runs "
                                                "whose paired interval's lower bound is above 0")
    for cand, s in ((dec.get("summary") or {}).get("candidates_as_whole_taggers") or {}).items():
        F.add(f"tagger_wp4b.whole.{cand}.gold_macro_f1", s.get("gold_macro_f1"), source=dp, kind="score", digits=3,
              how=f"candidate {cand} used as a whole tagger: gold macro F1 (it scores below the shipped mix)")
        d = s.get("paired_macro_delta_vs_current_ci95") or [None, None]
        F.add(f"tagger_wp4b.whole.{cand}.paired_ci_low", d[0], source=dp, kind="score", digits=3,
              how=f"candidate {cand} as a whole tagger: paired 95% interval of gold macro F1 minus the WP4 mix, lower end")
        F.add(f"tagger_wp4b.whole.{cand}.paired_ci_high", d[1], source=dp, kind="score", digits=3,
              how=f"candidate {cand} as a whole tagger: paired 95% interval, upper end")
    gi = (imp.get("grades") or {}).get("v0.3") or (imp.get("grades") or {}).get("v0.2") or {}
    em = gi.get("emoc") or {}
    ver = "v0.3" if (imp.get("grades") or {}).get("v0.3") else "v0.2"
    if em:
        F.add("tagger_wp4b.grade_impact.emoc_facilities_changed", em.get("facilities_changed"), source=ip, kind="count",
              how=f"emoc facility grades changed by the WP4b promotion (recipe {ver})")
        for tr, n in (em.get("transitions") or {}).items():
            F.add(f"tagger_wp4b.grade_impact.emoc_{_slug(tr)}", n, source=ip, kind="count",
                  how=f"emoc facility grades moving {tr} (recipe {ver})")
    g2 = imp.get("grade2_gate") or {}
    if "facility_lines_crossing_v0.3" in g2:
        F.add("tagger_wp4b.grade_impact.lines_crossing_grade2_gate", g2["facility_lines_crossing_v0.3"], source=ip,
              kind="count", how="facility-lines crossing the grade 2 gate under recipe v0.3 because of the promotion "
                                "(emoc, dialysis, icu, trauma, neurosurgery): the promotion changes no recommendation")
    fl = imp.get("fact_level") or {}
    for k_ in ("fact_rows_whose_tags_change", "facilities_with_a_changed_fact", "distinct_texts_whose_tags_change"):
        if k_ in fl:
            F.add(f"tagger_wp4b.fact_level.{k_}", fl[k_], source=ip, kind="count",
                  how=f"WP4b promotion at fact level: {k_.replace('_', ' ')}")
    if pp.exists():
        pr = json.loads(pp.read_text())
        ov = pr.get("overall") or {}
        for tch, s in ov.items():
            _add_kn(F, f"tagger_wp4b.pilot.{tch}", s["correct"], s["n"], source=pp, ci_given=s.get("wilson95"),
                    how=f"teacher pilot: {tch} correct on the blinded disagreement items read by two independent agent "
                        "readers (accuracy only where the three teachers disagree)")
    mlp = data_dir / "labels" / "wp4b" / "merged_labels.parquet.meta.json"
    if mlp.exists():
        ml = json.loads(mlp.read_text())
        if ml.get("rows") is not None:
            F.add("tagger_wp4b.merged_label_texts", ml["rows"], source=mlp, kind="count",
                  how="texts in the merged teacher labels the WP4b students trained on (Opus 5.5 relabels plus Haiku)")


def v0v1_section(F: Findings, data_dir: Path) -> None:
    import pandas as pd

    marks_by_round = {}
    for rnd, sub in (("r1", "v0v1_blind"), ("r2", "v0v1_blind_r2")):
        jp = data_dir / "labels" / sub / "judgments.csv"
        if not jp.exists():
            F.null(f"v0v1.{rnd}.prefer.v1", source=jp, reason=f"labels/{sub}/judgments.csv not found")
            continue
        j = pd.read_csv(jp, dtype=str, keep_default_na=False)
        hw = (f"blind side-by-side, round {rnd[1]}, of the v0 and v1 ranker answers on the 26 WP5 scenarios "
              f"(labels/{sub}/judgments.csv, unblinded); agent judges, not clinicians")
        if rnd == "r1":
            hw += ". Round 1 judged v1 before the D058 fix round"
        else:
            hw += ". Round 2 judged v1 after the D058 fix round (v1 file of 06:34 UTC, before the final assembly)"
        for ver in ("v0", "v1"):
            mk = j[j["version"] == ver]["mark"]
            marks_by_round.setdefault(rnd, {})[ver] = dict(zip(j[j["version"] == ver]["scenario_id"], mk))
            for m in ("right", "defensible", "wrong", "abstention_right", "abstention_wrong"):
                F.add(f"v0v1.{rnd}.marks.{ver}.{m}", int((mk == m).sum()), source=jp, kind="count",
                      how=f"{hw}: {ver} answers marked {m}")
            F.add(f"v0v1.{rnd}.marks.{ver}.unmarked", int((mk == "").sum()), source=jp, kind="count",
                  how=f"{hw}: {ver} answers left unmarked")
        pref = j.drop_duplicates("scenario_id")["prefer_version"]
        a, b_, t = int((pref == "v1").sum()), int((pref == "v0").sum()), int((pref == "tie").sum())
        F.add(f"v0v1.{rnd}.prefer.v1", a, source=jp, kind="count", how=f"{hw}: scenarios where v1 was preferred")
        F.add(f"v0v1.{rnd}.prefer.v0", b_, source=jp, kind="count", how=f"{hw}: scenarios where v0 was preferred")
        F.add(f"v0v1.{rnd}.prefer.tie", t, source=jp, kind="count", how=f"{hw}: ties")
        F.add(f"v0v1.{rnd}.prefer.sign_test_p", _sign_test(a, b_), source=jp, kind="score", digits=2,
              how=f"{hw}: exact two-sided binomial sign test on the untied preferences, p = 0.5")
        F.add(f"v0v1.{rnd}.prefer.sign_test_p_one_sided", _sign_test(a, b_, "greater"), source=jp, kind="score",
              digits=2, how=f"{hw}: the same test, one-sided (v1 better)")
    if {"r1", "r2"} <= set(marks_by_round):
        a, b_ = marks_by_round["r1"]["v0"], marks_by_round["r2"]["v0"]
        conv = {"right", "abstention_right"}
        # comparable: marked in both rounds, leaving out the non-answers that round 1 marked abstention_right and
        # round 2 marked right (a labeling convention, not a change of judgment)
        both = [s for s in a if s in b_ and a[s] and b_[s] and not ({a[s], b_[s]} == conv)]
        ch = sum(1 for s in both if a[s] != b_[s])
        jp2 = data_dir / "labels" / "v0v1_blind_r2" / "judgments.csv"
        hw = ("v0's answers are identical in both blind rounds (labels/v0v1_blind and v0v1_blind_r2); comparable "
              "scenarios have a v0 mark in both rounds, leaving out the non-answers marked abstention_right in round 1 "
              "and right in round 2 (a labeling convention change)")
        F.add("v0v1.judge_noise.v0_marks_changed", ch, source=jp2, kind="count",
              how=f"{hw}: comparable scenarios whose v0 mark changed (judge noise)")
        F.add("v0v1.judge_noise.v0_marks_comparable", len(both), source=jp2, kind="count", how=f"{hw}: comparable scenarios")


def recipe_v03_section(F: Findings, data_dir: Path) -> None:
    import pandas as pd

    p2, p3 = data_dir / "gold" / "capability_grades.parquet", data_dir / "gold" / "capability_grades_v03.parquet"
    if not (p2.exists() and p3.exists()):
        F.null("recipe_v03.changed_lines", source=p3, reason="gold v0.2 or v0.3 grade file not found")
        return
    a = pd.read_parquet(p2, columns=["facility_id", "service_line", "grade"])
    c = pd.read_parquet(p3, columns=["facility_id", "service_line", "grade"])
    m = a.merge(c, on=["facility_id", "service_line"], suffixes=("_v02", "_v03"))
    hw = ("recipe v0.2 (gold/capability_grades.parquet) against recipe v0.3 (gold/capability_grades_v03.parquet), "
          "both regraded from the promoted WP4b tags in the final assembly (D064); a rule-level change only (D052)")
    ch = m[m["grade_v02"] != m["grade_v03"]]
    F.add("recipe_v03.changed_lines", len(ch), source=p3, kind="count", how=f"{hw}: facility-lines whose grade changed")
    for line, g in ch.groupby("service_line"):
        F.add(f"recipe_v03.changed.{line}", len(g), source=p3, kind="count", how=f"{hw}: {line} grades changed")
        up2 = int(((g["grade_v02"] < 2) & (g["grade_v03"] >= 2)).sum())
        dn2 = int(((g["grade_v02"] >= 2) & (g["grade_v03"] < 2)).sum())
        F.add(f"recipe_v03.gate.{line}.up", up2, source=p3, kind="count", how=f"{hw}: {line} lines crossing up into grade 2+")
        F.add(f"recipe_v03.gate.{line}.down", dn2, source=p3, kind="count", how=f"{hw}: {line} lines dropping below grade 2")
        for ver, col in (("v02", "grade_v02"), ("v03", "grade_v03")):
            mm = m[m["service_line"] == line]
            F.add(f"recipe_v03.{line}.{ver}.grade_2plus", int((mm[col] >= 2).sum()), source=p3 if ver == "v03" else p2,
                  kind="count", how=f"{hw}: {line} grade 2 or 3 under {ver.replace('v0', 'v0.')}")
            F.add(f"recipe_v03.{line}.{ver}.grade_3", int((mm[col] == 3).sum()), source=p3 if ver == "v03" else p2,
                  kind="count", how=f"{hw}: {line} grade 3 (corroborated) under {ver.replace('v0', 'v0.')}")
    md = ROOT / "reports" / "recipe_v03_changes.md"
    F.null("recipe_v03.read.debatable", source=md, kind="count",
           reason="the read verdicts of changed records are in the report's tables only; no machine-readable "
                  "verdict file exists to recompute them from",
           report_states="no wrong change under the final rules; 15 debatable (D052 says 13, before the fix round); "
                         "every changed record read except cath lab demotions (a random 30 of 271 and all 15 added "
                         "by the fix round)")


def trust_extra_section(F: Findings, data_dir: Path) -> None:
    import pandas as pd

    rv = data_dir / "labels" / "flag_review.csv"
    if not rv.exists():
        return
    r = pd.read_csv(rv, dtype=str, keep_default_na=False)
    for rnd, what in (("5a", "first version"), ("5b", "second version"), ("5c", "final version (wp11_v3)")):
        g = r[(r["round"] == rnd) & (r["sample"] == "newly_blocked")]
        if not len(g):
            continue
        right = int((g["mark"] == "correct").sum())
        _add_kn(F, f"trust.per_line_rule.r{rnd}.correct_of_read", right, len(g), source=rv,
                how=f"WP11 per-line merge rule, {what}: hand-read sample of newly blocked facility-lines, rows marked "
                    "correct / rows read (unsure and wrong both count against); agent reader")
        F.add(f"trust.per_line_rule.r{rnd}.wrong", int((g["mark"] == "wrong").sum()), source=rv, kind="count",
              how=f"WP11 per-line merge rule, {what}: rows marked wrong")
        F.add(f"trust.per_line_rule.r{rnd}.unsure", int((g["mark"] == "unsure").sum()), source=rv, kind="count",
              how=f"WP11 per-line merge rule, {what}: rows marked unsure")


def resolver_extra_section(F: Findings, b: Path) -> None:
    import numpy as np
    import pandas as pd

    pp = b / "places.parquet"
    if not pp.exists():
        return
    p = pd.read_parquet(pp, columns=["kind", "name", "precision_km"])
    pin = p[p["kind"] == "pin"]["precision_km"].astype(float)
    hw = "PIN places in the frozen bundle's places.parquet (places-v3, outlier offices left out, D061)"
    F.add("resolver.places.pins", len(pin), source=pp, kind="count", how=f"{hw}: PINs")
    for q, nm in ((0.5, "median"), (0.9, "p90"), (0.99, "p99")):
        F.add(f"resolver.places.pin_precision_{nm}_km", float(np.quantile(pin, q)), source=pp, kind="km",
              how=f"{hw}: {nm} location precision (km)")
    F.add("resolver.places.pin_precision_max_km", float(pin.max()), source=pp, kind="km", how=f"{hw}: largest precision")
    for t in (50, 100):
        F.add(f"resolver.places.pins_over_{t}km", int((pin > t).sum()), source=pp, kind="count",
              how=f"{hw}: PINs whose precision is over {t} km")
    k = p[(p["kind"] == "pin") & (p["name"].astype(str) == "303108")]
    if len(k):
        F.add("resolver.places.pin_303108_precision_km", float(k["precision_km"].iloc[0]), source=pp, kind="km",
              how="precision of PIN 303108 (Kotputli, the brief's highway trauma scenario) after places-v3")
    md = ROOT / "reports" / "resolver_eval.md"
    F.null("resolver.places.pin_precision_median_km_before_v3", source=md, kind="km",
           reason="the places-v2 file was replaced by places-v3; the before figures are in the report only",
           report_states="all PINs, precision km before and after: median 6.4 to 4.9, p90 46.1 to 20.8, p99 122.9 to "
                         "84.9; Kotputli 303108 163.4 to 11.5")


def scenarios_v1_section(F: Findings) -> None:
    raw = ROOT / "reports" / "ranking_scenarios_v1_raw.json"
    if not raw.exists():
        F.null("scenarios_v1.scenarios", source=raw, reason="reports/ranking_scenarios_v1_raw.json not found")
        return
    d = json.loads(raw.read_text())
    s, m = d.get("summary") or {}, d.get("meta") or {}
    how = (f"v1 ranker scenario run (scripts/wp5_scenarios.py, {m.get('created_at_utc')}, git "
           f"{str(m.get('git_commit'))[:7]}, recipe {m.get('recipe_version')}, flags {m.get('flags_source')}); "
           "before the final assembly (D064) and not judged as a whole (see v0v1.r2 for the blind comparison)")
    for k in ("scenarios", "resolved", "abstentions", "recipe_resolved", "recipe_abstentions", "cards_shown",
              "cards_with_a_flag", "cards_nearest_reserved", "status_as_expected", "expected_abstentions",
              "expected_abstentions_that_abstained"):
        if k in s:
            F.add(f"scenarios_v1.{k}", s[k], source=raw, kind="count", how=f"{how}: {k.replace('_', ' ')}")
    for k in ("abstention_rate", "recipe_abstention_rate"):
        if k in s:
            F.add(f"scenarios_v1.{k}", s[k], source=raw, kind="share", digits=1, how=f"{how}: {k.replace('_', ' ')}")
    for k, v in (s.get("status_counts") or {}).items():
        F.add(f"scenarios_v1.status.{k}", v, source=raw, kind="count", how=f"{how}: answers with status {k}")
    lc = s.get("latency_check") or {}
    for k in ("ms_median", "ms_p95", "ms_max"):
        if k in lc:
            F.add(f"scenarios_v1.latency.{k}", lc[k], source=raw, kind="ms",
                  how=f"v1 rank() time after loading over {lc.get('n_queries')} queries, {k[3:]} (one machine, one run)")


def teacher_section(F: Findings, data_dir: Path) -> None:
    tp = data_dir / "labels" / "teacher_scores.json"
    if not tp.exists():
        F.null("teacher.macro_f1", source=tp, reason="labels/teacher_scores.json not found")
        return
    t = json.loads(tp.read_text())
    mac = ((t.get("summary") or {}).get("macro") or {})
    hw = (f"WP3 teacher labels ({t.get('model')}, prompt {t.get('prompt_version')}) on the "
          f"{(t.get('gold') or {}).get('rows')} agent-labeled gold texts, recipe {t.get('recipe_version')}")
    if "f1" in mac:
        F.add("teacher.macro_f1", mac["f1"], source=tp, kind="score", how=f"{hw}: macro F1")
    d = (t.get("teacher_minus_regex") or {}).get("macro_f1_delta_ci95")
    if d:
        F.add("teacher.minus_regex_ci_low", d[0], source=tp, kind="score", digits=3,
              how=f"{hw}: paired 95% interval of teacher minus regex v0.2 macro F1, lower end (spans 0: not better)")
        F.add("teacher.minus_regex_ci_high", d[1], source=tp, kind="score", digits=3, how=f"{hw}: upper end")
    sp = t.get("spend") or {}
    for k_ in ("usd", "total_usd"):
        if k_ in sp:
            F.add("teacher.spend_usd", sp[k_], source=tp, kind="usd", how=f"{hw}: teacher labeling spend")
            break


def ordering_section(F: Findings) -> None:
    """Rerun the v1 order and abstention sensitivity (scripts/wp5_scenarios.py --sensitivity) into a temp file."""
    import importlib.util

    sp = ROOT / "scripts" / "wp5_scenarios.py"
    try:
        spec = importlib.util.spec_from_file_location("wp5_scenarios_for_findings", sp)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as td:
            s = mod.run_sensitivity(out=Path(td) / "ordering_behaviour.md")
    except Exception as e:  # noqa: BLE001
        F.null("ordering.abstention.base", source=sp, kind="share", reason=f"sensitivity rerun failed: {e!r}")
        return
    how = (f"rerun now: wp5_scenarios.run_sensitivity on {s['origins']:,} random geocoded post offices (seed 7, "
           f"{s['sea_excluded']:,} sea-pixel offices excluded), v1 ranker with grades and flags from the gold folder "
           "(frozen with the bundle); origins are post offices, not people; mean over the 12 lines")
    for name, v in (s.get("mean_abstention") or {}).items():
        F.add(f"ordering.abstention.{_slug(name)}", v, source=sp, kind="share", digits=1,
              how=f"{how}: share of origins with no recommendable facility inside the window, setting {name!r}")
    for name, v in (s.get("mean_top1_change") or {}).items():
        F.add(f"ordering.top1_change.{_slug(name)}", v, source=sp, kind="share", digits=1,
              how=f"{how}: share of origins answered under both whose first card changes against the shipped "
                  f"settings, setting {name!r}")
    F.add("ordering.cap_binds", s.get("cap_binds"), source=sp, kind="count",
          how=f"{how}: line-origin queries where the 50-candidate cap hid a corroborated record the allowance would "
              "have put first (out of origins x 12 lines)")
    F.add("ordering.origins", s.get("origins"), source=sp, kind="count", how=f"{how}: origins")
    md = ROOT / "reports" / "ordering_behaviour.md"
    F.null("ordering.top_pick_not_nearest", source=md, kind="share",
           reason="run_sensitivity returns no per-line table, only the means above; these are in the report only",
           report_states="top pick not the nearest recommendable facility in 27.3% of answered origins; nearest "
                         "reserved to the last card in 9.4%")


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
          queries: bool = True, ordering: bool = True) -> dict:
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
    sealed_section(F, bundle, data_dir, sealed)
    method_section(F)
    spend_section(F, data_dir)
    outside_section(F, bundle, data_dir)
    tagger_wp4b_section(F, bundle, data_dir)
    v0v1_section(F, data_dir)
    recipe_v03_section(F, data_dir)
    trust_extra_section(F, data_dir)
    resolver_extra_section(F, bundle)
    scenarios_v1_section(F)
    teacher_section(F, data_dir)
    if ordering:
        ordering_section(F)
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
            "score_status": (f"ranker {idx.rank_version} (v1-final: gates and an order rule, no score or "
                             "probability, D048)") if not any(idx.is_probability(l) for l in idx.lines)
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
            "report_coverage": REPORT_COVERAGE,
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
            "tagger.shipped.macro_f1", "tagger_wp4b.mix.macro_f1", "tagger.regex.macro_f1", "tagger.gold_rows",
            "trust.facilities_flagged", "sealed_eval.sealed.ranker.precision.rate",
            "sealed_eval.sealed.specialty.precision.rate", "sealed_eval.sealed.any_fact.precision.rate",
            "sealed_eval.sealed.ranker.skipped_nearer.rate", "sealed_eval.sealed.judges.q1.kappa",
            "sealed_eval.sealed.ranker_vs_any_fact.sign_test_p", "spend.project_usd", "v0v1.r2.prefer.sign_test_p",
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
    ap.add_argument("--no-ordering", action="store_true", help="skip the 15 s ordering sensitivity rerun")
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
    obj = build(a.bundle, laqshya=a.laqshya_links, sealed=a.sealed, extras=extras, cold_open=not a.no_cold_open,
                ordering=not a.no_ordering)
    write_atomic(obj, a.out)
    _print_summary(obj, a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
