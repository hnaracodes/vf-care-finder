"""Cell-level cleaning helpers shared by the silver steps and the fact labelers.

WP1 uses all of them to build ``silver/facilities.parquet`` and
``silver/facts.parquet``. WP3 and WP4 should use ``normalize_text`` for any
``text_norm`` they compute (gold labels, teacher labels), so the dedup key is
identical everywhere, and ``fact_id`` if they ever need to rebuild an id.

Every function takes one raw string cell (as returned by ``vf.io``) and returns
a typed Python value, or None for missing.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from typing import Iterable, Optional
from urllib.parse import urlsplit

from .io import is_null, to_float

#: Placeholder strings that mean "no value" inside a fact or specialty list.
#: Compared after strip and lowercase. Covers "N/A" and "Null", which occur as
#: whole facts in the raw arrays.
NULL_ITEM_TOKENS = frozenset({"", "null", "none", "nan", "n/a", "na"})

_WS_RE = re.compile(r"\s+")
_PIN_SEP_RE = re.compile(r"[\s-]+")
_PIN_RE = re.compile(r"[1-9]\d{5}")
_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def clean_str(v) -> Optional[str]:
    """Strip whitespace; None for null tokens ("null", "", "None", NaN, ...)."""
    if is_null(v):
        return None
    return str(v).strip()


def normalize_text(s: str) -> str:
    """The fact dedup key: whitespace runs collapsed to one space, stripped, lowercased."""
    return _WS_RE.sub(" ", s).strip().lower()


def is_null_item(s) -> bool:
    """True for a list item that carries no value (None, "", "N/A", "Null", ...)."""
    if s is None:
        return True
    return str(s).strip().lower() in NULL_ITEM_TOKENS


def parse_items(v) -> list[tuple[int, str]]:
    """Parse a JSON-array cell into (position, text) pairs.

    Same parsing as ``vf.io.parse_list`` (items stripped, non-arrays give []),
    but keeps each item's 0-based position in the raw array and also drops
    placeholder items such as "N/A". The position is what makes ``fact_id``
    stable: dropping an empty item never shifts the ids of the others.
    """
    if is_null(v):
        return []
    try:
        parsed = json.loads(v)
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    out = []
    for i, x in enumerate(parsed):
        if x is None:
            continue
        s = str(x).strip()
        if is_null_item(s):
            continue
        out.append((i, s))
    return out


def dedupe(items: Iterable) -> list:
    """Distinct items, first occurrence kept, order preserved."""
    seen = set()
    out = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def parse_pin(v) -> Optional[str]:
    """A 6-digit Indian PIN as a string, else None.

    Spaces and hyphens between digits are removed first, so "613 007" and
    "641-602" become valid PINs. A PIN never starts with 0, so "000000" (a
    placeholder in the raw data) is None.
    """
    if is_null(v):
        return None
    s = _PIN_SEP_RE.sub("", str(v).strip())
    return s if _PIN_RE.fullmatch(s) else None


def parse_int(v, lo: Optional[int] = None, hi: Optional[int] = None) -> Optional[int]:
    """Integer for a whole-number string, else None. Values outside [lo, hi] are None."""
    f = to_float(v)
    if f is None or not f.is_integer():
        return None
    n = int(f)
    if lo is not None and n < lo:
        return None
    if hi is not None and n > hi:
        return None
    return n


def parse_bool(v) -> Optional[bool]:
    """True or False for "true" or "false" (any case), else None."""
    if is_null(v):
        return None
    s = str(v).strip().lower()
    if s == "true":
        return True
    if s == "false":
        return False
    return None


def parse_iso_date(v, max_date: Optional[date] = None) -> Optional[date]:
    """A date for a YYYY-MM-DD string, else None.

    Relative or partial strings ("2 months ago", "April 2025", "2024") are None
    rather than guessed. Dates after ``max_date`` are None.
    """
    if is_null(v):
        return None
    s = str(v).strip()
    if not _ISO_DATE_RE.fullmatch(s):
        return None
    try:
        d = date.fromisoformat(s)
    except ValueError:
        return None
    if max_date is not None and d > max_date:
        return None
    return d


def url_domain(url) -> Optional[str]:
    """Lowercased host of a URL with a leading "www." removed, else None."""
    if is_null(url):
        return None
    s = str(url).strip()
    if "://" not in s and not s.startswith("//"):
        s = "//" + s  # let urlsplit find the host in "example.com/page"
    try:
        host = urlsplit(s).hostname
    except ValueError:
        return None
    if not host:
        return None
    host = host.lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    return host or None


def distinct_domains(urls: Iterable) -> list[str]:
    """Distinct domains of a list of URLs, in first-seen order, empties dropped."""
    return dedupe(d for d in (url_domain(u) for u in urls) if d)


def fact_id(facility_id: str, field: str, position: int) -> str:
    """Stable 16-hex-digit id for one fact: sha1 of "facility_id|field|position".

    ``position`` is the item's index in the raw JSON array (see ``parse_items``).
    """
    key = f"{facility_id}|{field}|{position}".encode("utf-8")
    return hashlib.sha1(key).hexdigest()[:16]
