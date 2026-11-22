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

Two sources, one set of frames (WP12):

* ``csv`` (default): the exported files in ``data/raw``.
* ``databricks``: the same tables read from Unity Catalog with Spark. Turn it on
  with ``use_source("databricks", ...)`` or the env var ``VF_SOURCE=databricks``.
  The frames come back in the CSV path's shape: every column a string, SQL NULL
  as the string "null", NUL bytes removed (``normalize_table_frame``), so every
  later step runs unchanged.
"""
from __future__ import annotations

import io as _io
import json
import os
import re
from pathlib import Path
from typing import Callable, Optional

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
# source switch: data/raw CSVs or Databricks tables (WP12)
# --------------------------------------------------------------------------

#: Env var that picks the source: "csv" (default) or "databricks".
SOURCE_ENV = "VF_SOURCE"
#: Env var with "<catalog>.<schema>" of the Marketplace tables.
DATABRICKS_SCHEMA_ENV = "VF_DATABRICKS_SCHEMA"
#: Where the Marketplace listing landed in Hruday's workspace (data/raw/MANIFEST.json).
DEFAULT_DATABRICKS_SCHEMA = "databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset"
#: Table name for each raw kind, as listed in data/raw/MANIFEST.json.
DATABRICKS_TABLES = {
    "facilities": "facilities",
    "pincodes": "india_post_pincode_directory",
    "nfhs": "nfhs_5_district_health_indicators",
}
SOURCES = ("csv", "databricks")

#: A reader takes a fully qualified table name and returns a pandas frame.
TableReader = Callable[[str], pd.DataFrame]

_source: Optional[str] = None
_schema: Optional[str] = None
_reader: Optional[TableReader] = None
_table_cache: dict = {}


def use_source(source: Optional[str] = None, *, schema: Optional[str] = None,
               reader: Optional[TableReader] = None) -> None:
    """Pick where the raw readers load from, for this process.

    ``source="databricks"`` reads ``<schema>.<table>`` through ``reader``
    (default: ``spark_table_reader()``, the active Spark session). ``schema``
    defaults to ``VF_DATABRICKS_SCHEMA``, then ``DEFAULT_DATABRICKS_SCHEMA``.
    Tables are read once and cached; each call to a raw reader gets a copy.
    ``use_source("csv")`` forces the files; ``use_source()`` clears the choice,
    so ``VF_SOURCE`` (default "csv") decides again.
    """
    global _source, _schema, _reader
    if source is not None and source not in SOURCES:
        raise ValueError(f"unknown source {source!r}; expected one of {SOURCES}")
    _source, _schema, _reader = source, schema, reader
    _table_cache.clear()


def current_source() -> str:
    """The active source: set by ``use_source``, else ``VF_SOURCE``, else "csv"."""
    if _source is not None:
        return _source
    env = os.environ.get(SOURCE_ENV, "").strip().lower()
    if env and env not in SOURCES:
        raise ValueError(f"{SOURCE_ENV}={env!r}; expected one of {SOURCES}")
    return env or "csv"


def table_name(kind: str) -> str:
    """Fully qualified Unity Catalog name of a raw table."""
    schema = _schema or os.environ.get(DATABRICKS_SCHEMA_ENV) or DEFAULT_DATABRICKS_SCHEMA
    return f"{schema}.{DATABRICKS_TABLES[kind]}"


def _cell_to_str(v) -> str:
    """One table cell as the CSV export would spell it."""
    if isinstance(v, str):
        return v.replace("\x00", "")
    if v is None or (pd.api.types.is_scalar(v) and pd.isna(v)):  # None, NaN, NA, NaT
        return "null"
    if isinstance(v, bool) or type(v).__name__ == "bool_":
        return "true" if v else "false"
    if isinstance(v, bytes):
        v = v.decode("utf-8", errors="replace")
    return str(v).replace("\x00", "")


def normalize_table_frame(df: pd.DataFrame) -> pd.DataFrame:
    """A frame read from a table, in the CSV path's shape.

    Every column becomes a string column; SQL NULL (None, NaN, NA) becomes the
    string "null", which is how the SQL editor's CSV export wrote it
    (data/raw/MANIFEST.json); booleans become "true" and "false"; NUL bytes are
    removed, as ``_read_text`` does for the files. Column order is kept and the
    index reset. Read tables with every column cast to string in Spark
    (``spark_table_reader`` does) so numbers are spelled by Spark, not here.
    """
    out = {c: [_cell_to_str(v) for v in df[c].tolist()] for c in df.columns}
    return pd.DataFrame(out, columns=list(df.columns)).astype(str).reset_index(drop=True)


def spark_table_reader(spark=None) -> TableReader:
    """A reader that loads a table with Spark and returns it as pandas.

    Every column is cast to string inside Spark (arrays, structs and maps as
    JSON), so numbers keep Spark's spelling and nothing is coerced by pandas.
    ``spark`` defaults to the active session (the notebook's ``spark``).
    """
    def read(fqn: str) -> pd.DataFrame:
        from pyspark.sql import SparkSession
        from pyspark.sql import functions as F

        s = spark or SparkSession.getActiveSession() or SparkSession.builder.getOrCreate()
        sdf = s.table(fqn)
        cols = []
        for field in sdf.schema.fields:
            c = F.col(f"`{field.name}`")
            if field.dataType.typeName() in ("array", "struct", "map"):
                c = F.to_json(c)
            cols.append(c.cast("string").alias(field.name))
        return sdf.select(cols).toPandas()

    return read


def read_table(kind: str) -> pd.DataFrame:
    """A raw table from Databricks, normalized to the CSV path's shape."""
    fqn = table_name(kind)
    if fqn not in _table_cache:
        reader = _reader or spark_table_reader()
        _table_cache[fqn] = normalize_table_frame(reader(fqn))
    return _table_cache[fqn].copy()


def _read_raw(kind: str) -> pd.DataFrame:
    if current_source() == "databricks":
        return read_table(kind)
    return _read_csv(kind)


# --------------------------------------------------------------------------
# facilities
# --------------------------------------------------------------------------

def read_facilities_raw() -> pd.DataFrame:
    """All 10,088 rows and 51 columns, as strings, junk included."""
    return _read_raw("facilities")


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
    return _read_raw("pincodes")


def read_nfhs_raw() -> pd.DataFrame:
    """706 district rows, 109 columns, as strings.

    Indicator cells are numeric strings (sometimes with trailing spaces),
    '*' (suppressed), or '(29.5)' (low-sample estimate). Use parse_nfhs_value.
    """
    return _read_raw("nfhs")


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
