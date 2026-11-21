"""Raw loaders for the three source tables.

Everything is returned as strings, exactly as exported, so no information is
lost before the silver step (WP1, WP2) decides how to clean it.

Quirks handled here (all verified against the files in data/raw):

* facilities.csv contains 2 NUL bytes and 1 invalid UTF-8 byte. Python's csv
  module refuses NUL, so the text is scrubbed before parsing.
* Missing values are the literal string "null" (never an empty cell).
* 88 of the 10,088 rows are junk: fragments of multi-line descriptions that the
  upstream ingest split into their own rows. They have no country code.
* 30 of the 10,000 real rows are truncated: the same ingest problem cut them
  off partway through `description`, so every later column is "null".

See docs/02_data_dictionary.md and docs/03_data_profile.md for the full list.
"""
from __future__ import annotations

import io as _io
import json
import re
from pathlib import Path
from typing import Optional

import pandas as pd

from . import paths

RAW_FILES = {
    "facilities": "facilities.csv",
    "pincodes": "india_post_pincode_directory.csv",
    "nfhs": "nfhs5_district_health_indicators.csv",
}

#: Strings that mean "no value" in the facilities table.
NULL_TOKENS = frozenset({"", "null", "NULL", "None", "nan", "NaN"})

#: Loose bounding box for India, used to reject garbage coordinates.
INDIA_BBOX = {"lat_min": 6.0, "lat_max": 38.0, "lon_min": 68.0, "lon_max": 98.0}

_NUM_RE = re.compile(r"^-?\d+(\.\d+)?$")


def is_null(v) -> bool:
    """True for None, NaN, and the null tokens used in the raw files."""
    if v is None:
        return True
    if isinstance(v, float) and v != v:  # NaN
        return True
    return isinstance(v, str) and v.strip() in NULL_TOKENS


def raw_path(kind: str) -> Path:
    return paths.raw_dir() / RAW_FILES[kind]


def _read_text(path: Path) -> str:
    data = path.read_bytes().replace(b"\x00", b"")
    return data.decode("utf-8", errors="replace")


def _read_csv(kind: str) -> pd.DataFrame:
    path = raw_path(kind)
    if not path.exists():
        raise FileNotFoundError(
            f"{path} is missing. See data/README.md for how to re-export it."
        )
    return pd.read_csv(
        _io.StringIO(_read_text(path)),
        dtype=str,
        keep_default_na=False,
        na_filter=False,
    )


# --------------------------------------------------------------------------
# facilities
# --------------------------------------------------------------------------

def read_facilities_raw() -> pd.DataFrame:
    """All 10,088 rows and 51 columns, as strings, junk included."""
    return _read_csv("facilities")


def facility_row_status(df: pd.DataFrame) -> pd.Series:
    """Classify each raw row as 'ok', 'truncated', or 'junk'.

    junk      : not a facility row at all (country code is not 'IN')
    truncated : a real facility whose tail columns were lost (source is null),
                so it has no specialties, capability, coordinates, or URLs
    ok        : a complete facility row
    """
    status = pd.Series("ok", index=df.index, dtype="object")
    is_junk = df["address_countryCode"] != "IN"
    is_trunc = (~is_junk) & df["source"].map(is_null)
    status[is_trunc] = "truncated"
    status[is_junk] = "junk"
    return status


def read_facilities(include_truncated: bool = True) -> pd.DataFrame:
    """Real facility rows only, with a `row_status` column added.

    10,000 rows with include_truncated=True, 9,970 without.
    Values are still raw strings; "null" has NOT been converted.
    """
    df = read_facilities_raw()
    df["row_status"] = facility_row_status(df)
    keep = df["row_status"] != "junk"
    if not include_truncated:
        keep &= df["row_status"] == "ok"
    return df.loc[keep].reset_index(drop=True)


# --------------------------------------------------------------------------
# pincodes and NFHS
# --------------------------------------------------------------------------

def read_pincodes_raw() -> pd.DataFrame:
    """165,627 post office rows, 11 columns, as strings.

    Row grain is post office, NOT PIN code. latitude/longitude are strings:
    most are numeric, about 12,000 are 'NA', and a handful are malformed.
    """
    return _read_csv("pincodes")


def read_nfhs_raw() -> pd.DataFrame:
    """706 district rows, 109 columns, as strings.

    Indicator cells are numeric strings (sometimes with trailing spaces),
    '*' (suppressed), or '(29.5)' (low-sample estimate). Use parse_nfhs_value.
    """
    return _read_csv("nfhs")


# --------------------------------------------------------------------------
# cell parsers
# --------------------------------------------------------------------------

def parse_list(v, dedupe: bool = False) -> list:
    """Parse a JSON-array cell such as specialties or capability.

    Returns [] for null tokens and for anything that is not a JSON array.
    Empty-string items are dropped. With dedupe=True the first occurrence of
    each item is kept (specialties repeat heavily inside a single row).
    """
    if is_null(v):
        return []
    try:
        parsed = json.loads(v)
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    items = [str(x).strip() for x in parsed if x is not None and str(x).strip()]
    if dedupe:
        seen = set()
        out = []
        for x in items:
            if x not in seen:
                seen.add(x)
                out.append(x)
        return out
    return items


def to_float(v) -> Optional[float]:
    """Float for a plain numeric string, else None."""
    if is_null(v):
        return None
    s = str(v).strip()
    return float(s) if _NUM_RE.match(s) else None


def parse_nfhs_value(v):
    """Parse one NFHS indicator cell into (value, flag).

    flag is one of:
      'ok'         plain number
      'low_sample' value in parentheses, based on 25 to 49 unweighted cases
      'suppressed' '*', treat as missing and never as zero
      'missing'    empty or null token
      'unparsed'   anything else (should not occur; investigate if it does)
    """
    if v is None:
        return None, "missing"
    s = str(v).strip()
    if s in NULL_TOKENS:
        return None, "missing"
    if s == "*":
        return None, "suppressed"
    if s.startswith("(") and s.endswith(")"):
        inner = to_float(s[1:-1])
        return (inner, "low_sample") if inner is not None else (None, "unparsed")
    val = to_float(s)
    return (val, "ok") if val is not None else (None, "unparsed")


def in_india(lat: Optional[float], lon: Optional[float]) -> bool:
    if lat is None or lon is None:
        return False
    b = INDIA_BBOX
    return b["lat_min"] <= lat <= b["lat_max"] and b["lon_min"] <= lon <= b["lon_max"]
