"""WP13 / D055: National Health Portal blood bank directory (data.gov.in), parsed,
located and linked (candidates only) to our facility records.

Reads:
  external/nhp_bloodbank/mirror_snap24_caresync/file.csv
      the data.gov.in "Blood Bank Directory (updated till last month)" CSV, as kept
      in a public GitHub copy (data.gov.in itself refused or timed out from here on
      22 Nov 2026; see external/nhp_bloodbank/SOURCE.md). Read as data, never
      executed. Encoding cp1252.
  external/nhp_bloodbank/mirror_*/file.csv   three other copies, used only to
      check that the chosen copy is not edited (column-level comparison)
  silver/facilities.parquet, silver/pin_crosswalk.parquet,
  gold/facility_duplicates.parquet (WP11 duplicate pairs), and the gazetteer
  behind vf.locate.resolve

Writes (atomic, with .meta.json sidecars):
  silver/ext_nhp_bloodbank.parquet          one row per blood bank on the list
  silver/ext_links_nhp_bloodbank.parquet    every candidate pair (list_uid, facility_id, score, band,
                                            reader_verdict, accepted, then detail columns)
  labels/ext_link_check_nhp_bloodbank.csv   pairs for independent readers (frozen once readers' file exists)
  reports/ext_nhp_bloodbank.md              counts, method, limits

Use (D055 contract): blood bank listings are card facts only, for emoc,
cardiac_surgery and trauma. Never in the "nearby, not in this dataset" lane.

Linkage (reports/score_methodology_research.md, strict rules):
  blocking: same PIN; or within 2 km when the list row has coordinates that pass
  the position check; or same district plus a rare shared name token.
  score: IDF-weighted soft Jaccard on name tokens, bounded to [0, 1], after the
  service words ("blood bank", "blood transfusion centre", ...) are removed from
  both names, because they name the listed service, not the hospital.
  rejects: AYUSH, dental and veterinary records never enter the pool.
  one-to-one: a record links to at most one list row and a list row to at most one
  record, except records in the same WP11 duplicate cluster. A pair that loses
  that contest drops to the middle band.
  bands here are provisional sorting for readers; a band is accepted only if its
  one-sided 95% lower bound of precision, from the readers' labels, is at least
  0.90.

Accept stage: readers fill labels/ext_link_read_nhp_bloodbank.csv (item_id,
  verdict, note). Per band, precision = same / (same + different), with a
  one-sided 95% Clopper-Pearson lower bound; a band is accepted only if that bound
  is >= 0.90, and inside an accepted band only pairs a reader marked same become
  links (accepted = True). List rows get link_status (linked,
  reader_same_band_not_accepted, not_in_dataset) and clearly_not_ours; lane_eligible
  is false for every row (blood banks are card facts only).

Personal data: the list names a nodal officer per blood bank with a mobile number
and email. None of the contact or officer columns are carried into silver.

Usage:
  .venv/bin/python -I scripts/wp13_ext_nhp_bloodbank.py            # accept stage only once readers' file exists
  .venv/bin/python -I scripts/wp13_ext_nhp_bloodbank.py --rebuild  # re-parse and re-link (check sheet frozen)
  .venv/bin/python -I scripts/wp13_ext_nhp_bloodbank.py --as-of 2026-11-22   # first build
"""
from __future__ import annotations

import argparse
import hashlib
import html
import io
import json
import math
import os
import re
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from rapidfuzz import fuzz

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from vf import locate, paths  # noqa: E402
from vf.public_list import GENERIC, normalise_name  # noqa: E402

SCRIPT = "scripts/wp13_ext_nhp_bloodbank.py"
SOURCE_ID = "nhp_bloodbank"
SOURCE_NAME = "National Health Portal blood bank directory"
PUBLISHER = ("National Health Portal, Ministry of Health and Family Welfare; contributed to data.gov.in by "
             "the National Institute of Health and Family Welfare (NIHFW), New Delhi")
CATALOG_URL = "https://data.gov.in/resources/blood-bank-directory-updated-till-last-month"
CATALOG_ARCHIVE = ("https://web.archive.org/web/20211123122924/"
                   "https://data.gov.in/resources/blood-bank-directory-updated-till-last-month")
PUBLISHED_ON_PORTAL = date(2017, 8, 10)  # catalog: "Published on Data Portal: 10/08/2017"
LICENSE = "Government Open Data License - India (GODL), under NDSAP"
MIRROR_DIR = "mirror_snap24_caresync"
MIRROR_REPO = "snap24/caresync"
MIRROR_COMMIT = "b9fb624a5fb749ee1160d2e4cf08fec0bd3036a6"
MIRROR_PATH = "data/raw/bloodbanks.csv"
MIRROR_URL = f"https://raw.githubusercontent.com/{MIRROR_REPO}/{MIRROR_COMMIT}/{MIRROR_PATH}"
OTHER_MIRRORS = ("mirror_roshk01", "mirror_krishnadashtisastra", "mirror_apoorva_basingada")
RETRIEVED_UTC = "2026-11-22T05:22:24Z"
LINK_VERSION = "nhp_bloodbank_v1"
SERVICE_LINES = ("emoc", "cardiac_surgery", "trauma")

#: Provisional bands for readers (not acceptance), same values as the NCG linker.
AUTO_MIN = 0.70
MIDDLE_MIN = 0.30
SOFT_MIN = 85.0
RARE_MAX_DF = 10
NEAR_KM = 2.0
CHECK_CAP = 150
LOW_PROBE = 15
SEED = 20261122

#: A listed point is kept when it lies this close to the PIN area (or district
#: centre) the row resolves to: max(floor, factor x that position's precision).
POS_FLOOR_KM = {"PIN area": 15.0, "district centre": 30.0}
POS_FACTOR = 2.0

STOP = frozenset("""and of the a an at in for pvt ltd limited private unit by trust society
foundation india co ms""".split())
#: Service words: they name what the list lists, not which hospital it is.
SERVICE_PHRASE = re.compile(
    r"\b(?:blood\s+(?:bank|banks|centre|transfusion(?:\s+(?:centre|service|services|unit))?|"
    r"storage(?:\s+(?:centre|unit))?|components?(?:\s+separation)?(?:\s+(?:unit|centre))?)|"
    r"component\s+(?:and\s+)?apheresis(?:\s+unit)?|apheresis\s+unit|bb)\b")
SERVICE_WORDS = frozenset({"blood", "bank", "banks", "transfusion", "apheresis", "component", "components"})
REJECT_NAME = re.compile(
    r"\b(?:ayurved\w*|ayush|homoeo\w*|homeo\w*|unani|siddha|naturopath\w*|yoga|panchakarma|"
    r"dental|dentist\w*|dentofacial|orthodont\w*|endodont\w*|veterinar\w*|vet|animal|pet|pets)\b", re.I)
REJECT_TYPES = frozenset({"dentist"})
#: Columns with a person's name or contact details: never carried into silver.
PERSONAL_COLS = ("Contact No", "Mobile", "Helpline", "Fax", "Email", "Nodal Officer", "Contact Nodal Officer",
                 "Mobile Nodal Officer", "Email Nodal Officer", "Qualification Nodal Officer")
KEEP_RAW = {"Sr No": "raw_sr_no", "Blood Bank Name": "raw_name", "State": "raw_state", "District": "raw_district",
            "City": "raw_city", "Address": "raw_address", "Pincode": "raw_pincode", "Website": "raw_website",
            "Category": "raw_category", "Blood Component Available": "raw_components",
            "Apheresis": "raw_apheresis", "Service Time": "raw_service_time", "License #": "raw_licence_no",
            "Date License Obtained": "raw_licence_date", "Date of Renewal": "raw_renewal_date",
            "Latitude": "raw_latitude", "Longitude": "raw_longitude"}
_PIN = re.compile(r"(?<!\d)([1-8]\d{2})\s?(\d{3})(?!\d)")
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
                                       "nov", "dec"], start=1)}
RENEWAL_TEXT = re.compile(r"(?i)renew|pending|process|applied|awaited|submitted")


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def git_head():
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=paths.code_root(), capture_output=True,
                              text=True, check=True).stdout.strip()
    except Exception:
        return None


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def write_atomic(path: Path, write) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        write(tmp)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def write_meta(path: Path, rows: int, extra: dict) -> None:
    rel = str(path.relative_to(paths.data_dir())) if paths.data_dir() in path.parents else str(path)
    meta = {"output": rel, "rows": int(rows), "created_at_utc": utcnow(), "git_commit": git_head(),
            "script": SCRIPT, "source_id": SOURCE_ID, "link_version": LINK_VERSION, **extra}
    body = json.dumps(meta, indent=2, default=str) + "\n"
    write_atomic(path.with_name(path.name + ".meta.json"), lambda p: p.write_text(body))


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------

def read_csv(p: Path) -> pd.DataFrame:
    t = p.read_bytes().decode("cp1252")
    df = pd.read_csv(io.StringIO(t), dtype=str, keep_default_na=False)
    df.columns = [c.strip() for c in df.columns]
    return df


def clean(s) -> str:
    s = html.unescape(str(s or "")).replace("\xa0", " ")
    s = s.replace("‘", "'").replace("’", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", s).strip()


def blank(s) -> bool:
    return clean(s).upper() in ("", "N/A", "NA", "NIL", "-", "NOT AVAILABLE", "0")


def parse_date(s, max_year: int = 2032) -> tuple[date | None, str]:
    """A list date to (date, note). Day first (Indian usage). Year-only, year
    range and month-year values keep their precision in the note; a day past
    the month's end (31-11-2017) is moved to the month's last day; years
    outside 1950..max_year are rejected as typing errors."""
    import calendar

    t = clean(s)
    if not t or blank(t):
        return None, "blank"

    def ok(y: int) -> bool:
        return 1950 <= y <= max_year

    m = re.search(r"(?<!\d)(\d{1,2})[-./](\d{1,2})[-./](\d{2,4})(?!\d)", t)
    if m:
        d, mo, y = (int(x) for x in m.groups())
        if y < 100:
            y += 2000 if y < 50 else 1900
        note = "day first assumed" if d <= 12 and mo <= 12 and d != mo else "day-month-year"
        if mo > 12 and d <= 12:
            d, mo, note = mo, d, "month-day-year (month above 12 in day-first reading)"
        if not ok(y) or not 1 <= mo <= 12 or d < 1:
            return None, f"implausible: {t}"
        last = calendar.monthrange(y, mo)[1]
        if d > last:
            d, note = last, note + f"; day past month end, {last} used"
        return date(y, mo, d), note
    m = re.search(r"(?i)\b([a-z]{3})[a-z]*[-./ ](\d{2,4})\b", t)
    if m and m.group(1).lower() in MONTHS:
        y = int(m.group(2))
        y += 2000 if y < 100 else 0
        if ok(y):
            return date(y, MONTHS[m.group(1).lower()], 1), "month and year only"
    m = re.search(r"(?i)\b(\d{1,2})[-./ ]([a-z]{3})[a-z]*\b", t)
    if m and m.group(2).lower() in MONTHS:  # "12-Jan" style (spreadsheet), year in first field
        return date(int(m.group(1)) + 2000, MONTHS[m.group(2).lower()], 1), "month and two-digit year only"
    m = re.fullmatch(r"((?:19|20)\d{2})\s*[-/to ]+\s*((?:19|20)?\d{2})(?:\s*\(\w+\))?", t.strip(" .,"))
    if m:
        y2 = int(m.group(2))
        y2 += 2000 if y2 < 100 else 0
        if ok(y2):
            return date(y2, 12, 31), f"year range {t}, end year used (31 Dec)"
    m = re.fullmatch(r"((?:19|20)\d{2})(?:\s*\(\w+\))?", t.strip(" .,"))
    if m and ok(int(m.group(1))):
        return date(int(m.group(1)), 12, 31), "year only (31 Dec used)"
    return None, f"unparsed: {t}"


def fmt_date(d: date | None) -> str:
    return f"{d.day} {d.strftime('%b')} {d.year}" if d else ""


def pins_in(s: str) -> list[str]:
    return [a + b for a, b in _PIN.findall(s or "")]


def norm_state(s) -> str:
    t = re.sub(r"[^A-Z ]", " ", str(s or "").upper().replace("&", " AND "))
    t = re.sub(r"\s+", " ", t).strip()
    return locate.STATE_ALIASES.get(t, t) or t


_STATE_GROUPS = [{"ANDHRA PRADESH", "TELANGANA"}, {"JAMMU AND KASHMIR", "LADAKH"}]


def same_state(a, b) -> bool:
    x, y = norm_state(a), norm_state(b)
    if not x or not y:
        return True
    if x == y or x in y or y in x:
        return True
    if any(x in g and y in g for g in _STATE_GROUPS):
        return True
    dd = ("DADRA", "DAMAN", "DIU")
    return any(k in x for k in dd) and any(k in y for k in dd)


def haversine_km(lat1, lon1, lat2, lon2):
    if any(v is None or (isinstance(v, float) and math.isnan(v)) for v in (lat1, lon1, lat2, lon2)):
        return None
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(h))


def in_india_box(lat, lon) -> bool:
    return lat is not None and lon is not None and 6.0 <= lat <= 37.6 and 68.0 <= lon <= 97.5


def to_float(s):
    try:
        v = float(clean(s))
        return v if math.isfinite(v) else None
    except ValueError:
        return None


def locate_row(r: pd.Series) -> dict:
    """Area first: the listed PIN (or a PIN in the address) when it resolves and
    sits in the listed state; else the listed district, else the city. Then the
    listed point is kept only if it lies near that area."""
    st = r["raw_state"]
    notes = []
    area = None
    pin_cands = pins_in(r["raw_pincode"]) + [p for p in reversed(pins_in(r["raw_address"]))]
    seen = []
    for p in pin_cands:
        if p in seen:
            continue
        seen.append(p)
        res = locate.resolve(p)
        if res.status != "ok" or res.kind != "pin":
            notes.append(f"PIN {p} not in the India Post directory")
            continue
        if res.state and not same_state(res.state, st):
            notes.append(f"PIN {p} is in {locate.nice(res.state)}, list says {st}")
            continue
        area = {"pin": p, "district": res.district, "state_pin": res.state, "lat": res.lat, "lon": res.lon,
                "precision_km": res.precision_km, "note": "PIN area", "how": res.how}
        break
    if area is None:
        for q, kind in ((r["raw_district"], "district"), (r["raw_city"], "city")):
            q = clean(q)
            if blank(q):
                continue
            res = locate.resolve(f"{q} district" if kind == "district" else q, state=clean(st) or None)
            if res.status == "ok" and res.kind in ("district", "city", "post_office") and res.district:
                if res.kind != "district":
                    d = locate.resolve(f"{res.district} district", state=res.state)
                    if d.status == "ok" and d.kind == "district":
                        res = d
                if res.kind == "district" and same_state(res.state, st):
                    area = {"pin": None, "district": res.district, "state_pin": res.state, "lat": res.lat,
                            "lon": res.lon, "precision_km": res.precision_km, "note": "district centre",
                            "how": f"listed {kind} '{q}': {res.how}"}
                    break
            notes.append(f"listed {kind} '{q}' did not resolve to a district in {st}")

    lat, lon = to_float(r["raw_latitude"]), to_float(r["raw_longitude"])
    pt = None
    if lat is not None and lon is not None and not (lat == 0 and lon == 0):
        if in_india_box(lat, lon):
            pt = (lat, lon, "")
        elif in_india_box(lon, lat):
            pt = (lon, lat, "listed latitude and longitude swapped back")
    if pt is not None and area is not None:
        km = haversine_km(pt[0], pt[1], area["lat"], area["lon"])
        tol = max(POS_FLOOR_KM[area["note"]], POS_FACTOR * (area["precision_km"] or 0))
        if km is not None and km <= tol:
            return {"pin": area["pin"], "district": area["district"], "state_pin": area["state_pin"],
                    "lat": pt[0], "lon": pt[1], "position_precision_km": None,
                    "position_note": "listed coordinates", "list_point_ok": True,
                    "list_point_km_from_area": round(km, 2),
                    "position_detail": (f"listed point, {km:.1f} km from the {area['note']} "
                                        f"(tolerance {tol:.0f} km)" + (f"; {pt[2]}" if pt[2] else "")),
                    "locate_notes": "; ".join(notes) or None}
        notes.append(f"listed point {km:.0f} km from the {area['note']} (tolerance {tol:.0f} km), not used")
    elif pt is None and not blank(r["raw_latitude"]):
        notes.append("listed point outside India or zero, not used")
    if area is not None:
        return {"pin": area["pin"], "district": area["district"], "state_pin": area["state_pin"],
                "lat": area["lat"], "lon": area["lon"], "position_precision_km": area["precision_km"],
                "position_note": area["note"], "list_point_ok": False,
                "list_point_km_from_area": (round(haversine_km(pt[0], pt[1], area["lat"], area["lon"]), 2)
                                            if pt else None),
                "position_detail": area["how"], "locate_notes": "; ".join(notes) or None}
    notes.append("no PIN or district resolved; listed point not checkable, not used")
    return {"pin": None, "district": None, "state_pin": None, "lat": None, "lon": None,
            "position_precision_km": None, "position_note": None, "list_point_ok": False,
            "list_point_km_from_area": None, "position_detail": None, "locate_notes": "; ".join(notes)}


def status_of(renewal: date | None, renewal_raw: str, as_of: date) -> tuple[str, str]:
    rt = clean(renewal_raw)
    if renewal is not None:
        if renewal >= as_of:
            return "current", f"renewal date on the list {fmt_date(renewal)}, on or after {as_of.isoformat()}"
        extra = f" (the list also says: \"{rt}\")" if RENEWAL_TEXT.search(rt) else ""
        return "lapsed", (f"renewal date on the list {fmt_date(renewal)}, before {as_of.isoformat()}; this copy "
                          f"of the list shows no later renewal{extra}")
    if rt and not blank(rt):
        return "undated", f"no usable renewal date on the list; it says \"{rt}\""
    return "undated", "no renewal date on the list"


def mirror_check(base: pd.DataFrame, ext: Path) -> dict:
    out = {}
    for m in OTHER_MIRRORS:
        p = ext / m / "file.csv"
        if not p.exists():
            out[m] = "missing"
            continue
        x = read_csv(p)
        diffs = {}
        if len(x) != len(base):
            out[m] = {"rows": len(x), "rows_chosen": len(base)}
            continue
        for c in base.columns:
            if c in x.columns:
                n = int((x[c].str.strip() != base[c].str.strip()).sum())
                if n:
                    diffs[c] = n
        out[m] = {"rows": len(x), "differing_cells_by_column": diffs}
    return out


def build_list(src: Path, as_of: date) -> tuple[pd.DataFrame, dict]:
    raw = read_csv(src)
    missing = [c for c in KEEP_RAW if c not in raw.columns]
    if missing:
        raise SystemExit(f"unexpected columns, missing {missing}")
    df = raw[list(KEEP_RAW)].rename(columns=KEEP_RAW).copy()
    for c in df.columns:
        df[c] = df[c].map(clean)
    loc = pd.DataFrame([locate_row(r) for _, r in df.iterrows()], index=df.index)
    df = pd.concat([df, loc], axis=1)
    df["list_uid"] = "nhpbb-" + df["raw_sr_no"].str.zfill(4)
    df["list_row"] = df["raw_sr_no"].astype(int)
    df["name"] = df["raw_name"]
    df["address"] = df.apply(lambda r: ", ".join(x for x in (r.raw_address, r.raw_city) if x and x not in
                                                 r.raw_address) or r.raw_address, axis=1)
    df["state"] = df["raw_state"]
    df["district"] = df.apply(lambda r: locate.nice(r.district) if r.district else (r.raw_district.title() or None),
                              axis=1)
    lic = df["raw_licence_date"].map(parse_date)
    ren = df["raw_renewal_date"].map(parse_date)
    df["licence_date"] = [d for d, _ in lic]
    df["licence_date_note"] = [n for _, n in lic]
    df["renewal_date"] = [d for d, _ in ren]
    df["renewal_date_note"] = [n for _, n in ren]
    st = [status_of(d, raw_t, as_of) for (d, _), raw_t in zip(ren, df["raw_renewal_date"])]
    df["status"] = [s for s, _ in st]
    df["status_detail"] = [t for _, t in st]
    has_lic = ~df["raw_licence_no"].map(blank)
    df["licence_no"] = df["raw_licence_no"].where(has_lic, None)
    df["what"] = np.where(has_lic, "licensed blood bank", "blood bank (no licence number on the list)")
    yes = lambda s: {"YES": True, "NO": False}.get(clean(s).upper())  # noqa: E731
    df["components_listed"] = df["raw_components"].map(yes)
    df["apheresis_listed"] = df["raw_apheresis"].map(yes)
    df["category"] = df["raw_category"].where(df["raw_category"] != "", None)
    df["list_date"] = None
    df["list_published_on_portal"] = PUBLISHED_ON_PORTAL
    df["service_lines"] = [list(SERVICE_LINES)] * len(df)
    df["retrieved_utc"] = RETRIEVED_UTC
    df["source_url"] = CATALOG_URL
    df["copy_url"] = MIRROR_URL
    df["citation"] = df["list_row"].map(
        lambda n: (f"{SOURCE_NAME} (data.gov.in, published on the portal 10 Aug 2017), row Sr No {n}; "
                   f"{CATALOG_URL}; copy used: {MIRROR_URL}, retrieved {RETRIEVED_UTC[:10]}"))
    df["source_file"] = f"external/{SOURCE_ID}/{MIRROR_DIR}/file.csv"
    df["source_sha256"] = sha256(src)
    df["licence_date"] = pd.to_datetime(df["licence_date"])
    df["renewal_date"] = pd.to_datetime(df["renewal_date"])
    cols = ["list_uid", "name", "address", "pin", "district", "state", "lat", "lon", "position_precision_km",
            "position_note", "position_detail", "list_point_ok", "list_point_km_from_area", "locate_notes",
            "what", "status", "status_detail", "licence_no", "licence_date", "licence_date_note", "renewal_date",
            "renewal_date_note", "category", "components_listed", "apheresis_listed", "list_date",
            "list_published_on_portal", "service_lines", "citation", "source_url", "copy_url", "retrieved_utc",
            "list_row", "state_pin", "source_file", "source_sha256"] + list(KEEP_RAW.values())
    df = df[cols].reset_index(drop=True)
    lic_dates = df["licence_date"].dropna()
    stats = {"list_rows": len(df), "columns_dropped_personal": list(PERSONAL_COLS),
             "duplicate_list_uid": int(df.list_uid.duplicated().sum()),
             "position_note": df.position_note.fillna("not located").value_counts().to_dict(),
             "status": df.status.value_counts().to_dict(),
             "what": df.what.value_counts().to_dict(),
             "category": df.category.fillna("not stated").value_counts().to_dict(),
             "latest_licence_date": str(lic_dates[lic_dates <= pd.Timestamp(as_of)].max().date())
             if len(lic_dates) else None,
             "latest_renewal_date": str(df["renewal_date"].max().date()) if df["renewal_date"].notna().any() else None,
             "renewal_dates_parsed": int(df["renewal_date"].notna().sum()),
             "licence_dates_by_year_2014_on": {int(k): int(v) for k, v in
                                               lic_dates.dt.year.value_counts().sort_index().items() if k >= 2014},
             "list_points_kept": int(df.list_point_ok.sum()),
             "mirror_check": mirror_check(raw, src.parent.parent)}
    return df, stats


# ---------------------------------------------------------------------------
# Link
# ---------------------------------------------------------------------------

def tokens(name) -> list[str]:
    """Name tokens for matching: service words and stop words removed, and runs
    of single letters joined into one token ("B.N.K. Hospital" -> bnk hospital),
    so initials are not silently dropped."""
    n = SERVICE_PHRASE.sub(" ", normalise_name(name))
    out, run = [], []
    for w in n.split() + [""]:
        if len(w) == 1 and w.isalpha():
            run.append(w)
            continue
        if run:
            out.append("".join(run))
            run = []
        if w:
            out.append(w)
    return [w for w in out if w not in STOP and w not in SERVICE_WORDS and len(w) > 1]


def distinctive(matched: list[str]) -> list[str]:
    """Matched tokens that are not generic words (hospital, civil, medical ...)."""
    return [m for m in matched if m.split("~")[0] not in GENERIC]


def soft_jaccard(a: list[str], b: list[str], idf: dict, default_idf: float) -> tuple[float, list]:
    """IDF-weighted soft Jaccard on token sets, in [0, 1] (as in wp13_ext_ncg)."""
    A, B = sorted(set(a)), sorted(set(b))
    if not A or not B:
        return 0.0, []
    w = lambda t: idf.get(t, default_idf)  # noqa: E731
    pairs = []
    for x in A:
        for y in B:
            s = 100.0 if x == y else (fuzz.ratio(x, y) if min(len(x), len(y)) >= 4 else 0.0)
            if s >= SOFT_MIN:
                pairs.append((s, x, y))
    pairs.sort(reverse=True)
    used_a, used_b, overlap, matched = set(), set(), 0.0, []
    for s, x, y in pairs:
        if x in used_a or y in used_b:
            continue
        used_a.add(x)
        used_b.add(y)
        overlap += (s / 100.0) * (w(x) + w(y)) / 2
        matched.append(x if x == y else f"{x}~{y}")
    wa, wb = sum(w(t) for t in A), sum(w(t) for t in B)
    den = wa + wb - overlap
    return (float(min(1.0, max(0.0, overlap / den))) if den > 0 else 0.0), matched


def duplicate_clusters(dups: pd.DataFrame) -> dict:
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in zip(dups.facility_id_a, dups.facility_id_b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)
    return {x: find(x) for x in parent}


def link(lst: pd.DataFrame, fac: pd.DataFrame, pinx: pd.DataFrame, dups: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    f = fac.copy()
    rej = f["facility_type"].isin(REJECT_TYPES) | f["name"].fillna("").str.contains(REJECT_NAME)
    pool = f[~rej].copy().reset_index(drop=True)
    stats = {"our_records": len(f), "rejected_ayush_dental_vet": int(rej.sum()), "pool": len(pool)}

    docs = [set(tokens(n)) for n in f["name"]] + [set(tokens(n)) for n in lst["name"]]
    df_all, df_ours = {}, {}
    for i, d in enumerate(docs):
        for t in d:
            df_all[t] = df_all.get(t, 0) + 1
            if i < len(f):
                df_ours[t] = df_ours.get(t, 0) + 1
    N = len(docs)
    idf = {t: math.log((N + 1) / (c + 1)) + 1.0 for t, c in df_all.items()}
    default_idf = math.log(N + 1) + 1.0

    pd_map = {str(p): set(zip(g.state_pin, g.district_pin)) for p, g in pinx.groupby("pin")}
    pool["districts"] = pool["pin"].map(lambda p: pd_map.get(str(p), set()) if pd.notna(p) else set())
    pool["toks"] = pool["name"].map(tokens)
    by_pin = pool.groupby("pin").groups
    by_dist = {}
    for i, ds in zip(pool.index, pool["districts"]):
        for d in ds:
            by_dist.setdefault(d, []).append(i)
    ok_pt = pool["coord_valid"].fillna(False).astype(bool).to_numpy()
    plat = np.radians(pool["lat"].to_numpy(dtype=float))
    plon = np.radians(pool["lon"].to_numpy(dtype=float))
    clusters = duplicate_clusters(dups)

    rows = []
    for r in lst.itertuples():
        ltoks = tokens(r.name)
        dist_tokens = set(normalise_name(r.district or "").split()) | set(normalise_name(r.raw_city or "").split())
        rare = {t for t in ltoks if df_ours.get(t, 0) <= RARE_MAX_DF and len(t) >= 4 and t not in GENERIC
                and t not in dist_tokens and not t.isdigit()}
        cand = {}
        if r.pin and r.pin in by_pin:
            for i in by_pin[r.pin]:
                cand.setdefault(i, set()).add("same_pin")
        if r.list_point_ok:
            la, lo = math.radians(r.lat), math.radians(r.lon)
            h = (np.sin((plat - la) / 2) ** 2 + np.cos(la) * np.cos(plat) * np.sin((plon - lo) / 2) ** 2)
            km = 2 * 6371.0088 * np.arcsin(np.sqrt(np.clip(h, 0, 1)))
            for i in np.nonzero(ok_pt & (km <= NEAR_KM))[0]:
                cand.setdefault(int(i), set()).add("within_2km")
        if r.district and r.state_pin and rare:
            for k in [k for k in by_dist if k[0] == r.state_pin and k[1].upper() == str(r.district).upper()]:
                for i in by_dist[k]:
                    shared = rare & set(pool.at[i, "toks"])
                    if not shared:
                        shared = {t for t in rare for u in pool.at[i, "toks"]
                                  if len(u) >= 4 and fuzz.ratio(t, u) >= SOFT_MIN}
                    if shared:
                        cand.setdefault(i, set()).add("same_district_rare_token:" + ",".join(sorted(shared)))
        for i, blocks in cand.items():
            o = pool.loc[i]
            score, matched = soft_jaccard(ltoks, o["toks"], idf, default_idf)
            km = haversine_km(r.lat, r.lon, o["lat"], o["lon"]) if bool(o["coord_valid"]) else None
            rows.append({
                "list_uid": r.list_uid, "facility_id": o["facility_id"],
                "dup_cluster": clusters.get(o["facility_id"], o["facility_id"]),
                "block": " | ".join(sorted(blocks)), "same_pin": "same_pin" in blocks,
                "within_2km": "within_2km" in blocks,
                "score": round(score, 4), "matched_tokens": ", ".join(matched),
                "distinctive_match": bool(distinctive(matched)),
                "list_tokens": " ".join(ltoks), "our_tokens": " ".join(o["toks"]),
                "km_apart": None if km is None else round(km, 2),
                "list_position_note": r.position_note, "list_position_precision_km": r.position_precision_km,
                "list_name": r.name, "list_address": r.address, "list_pin": r.pin, "list_district": r.district,
                "list_state": r.state, "list_category": r.category, "list_status": r.status,
                "our_name": o["name"], "our_city": o["city_raw"], "our_state": o["state_raw"],
                "our_pin": o["pin"], "our_type": o["facility_type"], "our_operator": o["operator_type"],
                "our_coord_check": o["coord_check"], "our_row_status": o["row_status"],
            })
    links = pd.DataFrame(rows)
    if links.empty:
        return links, stats
    links = links.sort_values(["score", "same_pin", "within_2km"], ascending=[False, False, False]).reset_index(drop=True)
    links["rank_in_list_row"] = links.groupby("list_uid")["score"].rank(method="first", ascending=False).astype(int)
    taken_list, taken_rec, winner = {}, {}, []
    for x in links.itertuples():
        lc = taken_list.get(x.list_uid)
        rc = taken_rec.get(x.dup_cluster)
        ok = (lc is None or lc == x.dup_cluster) and (rc is None or rc == x.list_uid)
        if ok and x.score >= AUTO_MIN and x.distinctive_match:
            taken_list[x.list_uid] = x.dup_cluster
            taken_rec[x.dup_cluster] = x.list_uid
        winner.append(ok)
    links["one_to_one_ok"] = winner
    links["band"] = np.where((links.score >= AUTO_MIN) & links.one_to_one_ok & links.distinctive_match, "auto",
                             np.where(links.score >= MIDDLE_MIN, "middle", "low"))
    links["band_note"] = None
    links.loc[(links.score >= AUTO_MIN) & ~links.distinctive_match, "band_note"] = "only generic words shared"
    links.loc[(links.score >= AUTO_MIN) & links.distinctive_match & ~links.one_to_one_ok,
              "band_note"] = "lost one-to-one contest"
    links["link_version"] = LINK_VERSION
    stats.update({"candidates": len(links), "bands": links.band.value_counts().to_dict(),
                  "list_rows_with_candidate": int(links.list_uid.nunique()),
                  "list_rows_with_auto": int(links[links.band == "auto"].list_uid.nunique()),
                  "records_with_auto": int(links[links.band == "auto"].facility_id.nunique()),
                  "blocks": {"same_pin": int(links.same_pin.sum()), "within_2km": int(links.within_2km.sum()),
                             "district_rare_token_only": int((~links.same_pin & ~links.within_2km).sum())},
                  "lost_one_to_one": int(links.band_note.eq("lost one-to-one contest").sum()),
                  "high_score_generic_only": int(links.band_note.eq("only generic words shared").sum()),
                  "idf_docs": N, "rare_max_df": RARE_MAX_DF})
    return links, stats


def check_sheet(links: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Pairs for readers: every auto and middle pair, sampled per band (in
    proportion, at least 60 auto) if together over the cap; then the best low
    pairs as a boundary probe if room remains. Shuffled so order does not
    reveal the band."""
    rng = np.random.default_rng(SEED)
    a, m = links[links.band == "auto"], links[links.band == "middle"]
    sampling = {"cap": CHECK_CAP, "auto_total": len(a), "middle_total": len(m), "seed": SEED}
    if len(a) + len(m) > CHECK_CAP:
        share = len(a) / (len(a) + len(m))
        na = min(len(a), max(int(round(CHECK_CAP * share)), min(len(a), 60)))
        nm = min(len(m), CHECK_CAP - na)
        a = a.sample(n=na, random_state=SEED) if len(a) > na else a
        m = m.sample(n=nm, random_state=SEED) if len(m) > nm else m
        sampling["sampled"] = True
        sampling["method"] = ("simple random sample within each band (pandas sample, random_state=SEED); "
                              "auto gets its share of the cap, at least 60, and middle the rest")
    else:
        sampling["sampled"] = False
    room = CHECK_CAP - len(a) - len(m)
    lo = links[links.band == "low"].sort_values("score", ascending=False).head(max(0, min(LOW_PROBE, room)))
    sampling.update({"auto_in_sheet": len(a), "middle_in_sheet": len(m), "low_probe_in_sheet": len(lo),
                     "low_total": int((links.band == "low").sum())})
    s = pd.concat([a, m, lo])
    s = s.iloc[rng.permutation(len(s))]
    out = pd.DataFrame({
        "item_id": s.list_uid + "|" + s.facility_id,
        "list_name": s.list_name, "list_address": s.list_address, "list_pin": s.list_pin,
        "list_district": s.list_district, "our_facility_id": s.facility_id, "our_name": s.our_name,
        "our_address_or_city": s.our_city.fillna("") + ", " + s.our_state.fillna(""),
        "our_pin": s.our_pin, "km_apart": s.km_apart, "score": s.score, "band": s.band,
    })
    return out.reset_index(drop=True), sampling


# ---------------------------------------------------------------------------
# Accept: join readers' verdicts, accept bands, classify list rows
# (same rule and layout as scripts/wp13_ext_aerb_rt.py)
# ---------------------------------------------------------------------------

VERDICTS = {"same", "different", "unsure"}
P0, ALPHA = 0.90, 0.05
#: A low-band mutual-best pair at or above this may still be the same place.
NEAR_MISS_MIN = 0.20
LANE_RULE = ("blood bank listings are card facts only (D055 and the public_list v2 contract): lane_eligible is "
             "false for every row of this source")


def cp_lower(k: int, n: int, alpha: float = ALPHA) -> float:
    """One-sided Clopper-Pearson lower bound for a binomial proportion k/n."""
    from scipy.stats import beta
    if n == 0 or k == 0:
        return 0.0
    return float(beta.ppf(alpha, k, n - k + 1))


def read_verdicts(path: Path) -> pd.DataFrame:
    """Readers' file: item_id (list_uid|facility_id), verdict, note."""
    r = pd.read_csv(path, dtype=str, keep_default_na=False)
    need = {"item_id", "verdict"}
    if not need <= set(r.columns):
        raise SystemExit(f"{path}: missing columns {sorted(need - set(r.columns))}")
    r["verdict"] = r["verdict"].str.strip().str.lower()
    bad = r[~r.verdict.isin(VERDICTS | {""})]
    if len(bad):
        raise SystemExit(f"{path}: unknown verdicts {sorted(bad.verdict.unique())}")
    if r.item_id.duplicated().any():
        raise SystemExit(f"{path}: duplicate item_id rows")
    r = r.rename(columns={"verdict": "reader_verdict", "note": "reader_note"})
    if "reader_note" not in r:
        r["reader_note"] = ""
    return r[["item_id", "reader_verdict", "reader_note"]]


def accept(links: pd.DataFrame, sheet: pd.DataFrame, reads: pd.DataFrame, lst: pd.DataFrame):
    """Returns (links with reader_verdict and accepted, list rows with link status, band table, info)."""
    c = links.drop(columns=[x for x in ("item_id", "reader_verdict", "reader_note", "accepted", "band_accepted",
                                        "in_check_sheet", "mutual_best") if x in links.columns]).copy()
    c["item_id"] = c.list_uid + "|" + c.facility_id
    k_sheet, k_read, k_cand = set(sheet.item_id), set(reads.item_id), set(c.item_id)
    info = {"check_rows": len(sheet), "reader_rows": len(reads),
            "reader_rows_not_in_check_file": len(k_read - k_sheet),
            "reader_rows_not_in_candidates": len(k_read - k_cand),
            "check_rows_without_reader_row": len(k_sheet - k_read)}
    # the sheet's band is the one readers were given; it must match the candidate file
    sb = sheet.set_index("item_id")["band"]
    cb = c.set_index("item_id")["band"]
    common = sorted(set(sb.index) & set(cb.index))
    info["sheet_band_mismatches"] = int((sb.loc[common] != cb.loc[common]).sum())
    if info["reader_rows_not_in_candidates"] or info["sheet_band_mismatches"]:
        raise SystemExit(f"reader file does not match the candidate file: {info}")
    c = c.merge(reads, on="item_id", how="left")
    c["reader_verdict"] = c["reader_verdict"].replace("", np.nan).fillna("unread")
    c["reader_note"] = c["reader_note"].fillna("")
    c["in_check_sheet"] = c.item_id.isin(k_sheet)

    bands = []
    for b in ("auto", "middle", "low"):
        s = c[c.band == b]
        same, diff, uns = (int((s.reader_verdict == v).sum()) for v in ("same", "different", "unsure"))
        n = same + diff
        lb = cp_lower(same, n)
        bands.append({"band": b, "pairs": len(s), "read": same + diff + uns, "same": same, "different": diff,
                      "unsure": uns, "precision": (same / n) if n else None, "lower_95": lb,
                      "lower_95_if_unsure_counted_wrong": cp_lower(same, n + uns),
                      "accepted": bool(n and lb >= P0)})
    bt = pd.DataFrame(bands)
    ok = set(bt.loc[bt.accepted, "band"])
    c["band_accepted"] = c.band.isin(ok)
    c["accepted"] = c.band_accepted & c.reader_verdict.eq("same")
    best_l = c.groupby("list_uid")["score"].transform("max")
    best_f = c.groupby("facility_id")["score"].transform("max")
    c["mutual_best"] = c.score.eq(best_l) & c.score.eq(best_f)

    g = c.groupby("list_uid")
    linked = g["accepted"].any()
    any_same = g["reader_verdict"].agg(lambda v: (v == "same").any())
    any_unsure = g["reader_verdict"].agg(lambda v: (v == "unsure").any())
    unread_am = set(c[c.band.isin(["auto", "middle"]) & c.reader_verdict.eq("unread")].list_uid)
    near = set(c[c.band.eq("low") & c.mutual_best & (c.score >= NEAR_MISS_MIN)].list_uid)
    acc_ids = c[c.accepted].groupby("list_uid")["facility_id"].agg(lambda v: sorted(set(v)))

    out = lst.drop(columns=[x for x in ("link_status", "linked_facility_ids", "clearly_not_ours",
                                        "not_ours_exclusion", "lane_eligible", "lane_exclusion")
                            if x in lst.columns]).copy()
    uid = out.list_uid
    is_linked = uid.map(linked).fillna(False).astype(bool)
    is_same = uid.map(any_same).fillna(False).astype(bool)
    out["link_status"] = np.where(is_linked, "linked",
                                  np.where(is_same, "reader_same_band_not_accepted", "not_in_dataset"))
    out["linked_facility_ids"] = [acc_ids.get(u, []) for u in uid]
    nid = out.link_status.eq("not_in_dataset").to_numpy()
    excl = np.full(len(out), "", dtype=object)
    not_loc = out.position_note.isna() | out.position_note.eq("not located")
    # first reason wins, strictest first
    for mask, why in ((uid.map(any_unsure).fillna(False).astype(bool).to_numpy(), "reader_unsure"),
                      (uid.isin(unread_am).to_numpy(), "unread_auto_or_middle_candidate"),
                      (uid.isin(near).to_numpy(), "near_miss_low_candidate"),
                      (not_loc.to_numpy(), "not_located")):
        excl = np.where(nid & (excl == "") & mask, why, excl)
    out["clearly_not_ours"] = nid & (excl == "")
    out["not_ours_exclusion"] = np.where(nid, excl, out.link_status)
    out["lane_eligible"] = False
    out["lane_exclusion"] = "card_fact_only_source"

    multi = c[c.accepted].groupby("facility_id")["list_uid"].nunique()
    acc = c[c.accepted]
    info.update({
        "bands_accepted": sorted(ok),
        "accepted_pairs": int(c.accepted.sum()),
        "list_rows_linked": int(is_linked.sum()),
        "facilities_linked": int(acc.facility_id.nunique()),
        "facilities_linked_to_more_than_one_list_row": {k: int(v) for k, v in multi[multi > 1].items()},
        "linked_rows_by_status": out.loc[is_linked.to_numpy(), "status"].value_counts().to_dict(),
        "list_rows_reader_same_band_not_accepted": int((out.link_status == "reader_same_band_not_accepted").sum()),
        "list_rows_not_in_dataset": int(nid.sum()),
        "list_rows_not_in_dataset_without_candidate": int((nid & ~uid.isin(set(c.list_uid)).to_numpy()).sum()),
        "list_rows_clearly_not_ours": int(out.clearly_not_ours.sum()),
        "not_ours_exclusions": out.loc[nid, "not_ours_exclusion"].replace("", "clearly_not_ours")
                                  .value_counts().to_dict(),
        "list_rows_lane_eligible": 0,
        "lane_rule": LANE_RULE,
        "unread_pairs_in_accepted_bands": int((c.band_accepted & c.reader_verdict.eq("unread")).sum()),
    })
    c = c.drop(columns=["item_id"])
    return c, out, bt, info


def loc_quality(df: pd.DataFrame) -> dict:
    note = df.position_note.fillna("not located")
    p = pd.to_numeric(df.position_precision_km, errors="coerce")
    area = note.isin(["PIN area", "district centre"])
    return {"rows": len(df), "listed point": int((note == "listed coordinates").sum()),
            "PIN area": int((note == "PIN area").sum()), "district centre": int((note == "district centre").sum()),
            "not located": int((note == "not located").sum()),
            "median_km": float(p[area].median()) if area.any() else None,
            "le_5km": int((p[area] <= 5).sum()), "gt_10km": int((p[area] > 10).sum())}


def acceptance_report(c, out, bt, info) -> str:
    L = ["\n## Acceptance (reader verdicts)\n\n"]
    L.append(f"Readers' file `data/labels/ext_link_read_{SOURCE_ID}.csv`: {info['reader_rows']} rows "
             f"({info['reader_rows_not_in_check_file']} not in the check sheet, "
             f"{info['check_rows_without_reader_row']} check rows unread). Precision per band = same / (same + "
             "different); unsure verdicts are left out of both counts. Lower bound: one-sided 95% Clopper-Pearson. "
             f"A band is accepted only if its lower bound is at least {P0:.2f}; inside an accepted band only pairs "
             "the reader marked same become links.\n\n")
    L.append("| band | pairs | read | same | different | unsure | precision | 95% lower bound | "
             "lower bound if unsure counted wrong | accepted |\n|---|---|---|---|---|---|---|---|---|---|\n")
    for r in bt.itertuples():
        prec = "n/a" if r.precision is None or (isinstance(r.precision, float) and math.isnan(r.precision)) \
            else f"{r.precision:.3f}"
        lb = f"{r.lower_95:.3f}" if r.read else "n/a"
        lb2 = f"{r.lower_95_if_unsure_counted_wrong:.3f}" if r.read else "n/a"
        L.append(f"| {r.band} | {r.pairs} | {r.read} | {r.same} | {r.different} | {r.unsure} | {prec} | {lb} | "
                 f"{lb2} | {'yes' if r.accepted else 'no'} |\n")
    acc = ", ".join(info["bands_accepted"]) or "none"
    L.append(f"\nAccepted bands: **{acc}**. Accepted links: {info['accepted_pairs']} pairs, "
             f"{info['list_rows_linked']} list rows, {info['facilities_linked']} of our records "
             f"(list status of the linked rows: {info['linked_rows_by_status']}).")
    if info["facilities_linked_to_more_than_one_list_row"]:
        L.append(f" Records linked to more than one list row: "
                 f"{len(info['facilities_linked_to_more_than_one_list_row'])}.")
    L.append(f" {info['unread_pairs_in_accepted_bands']} pairs in accepted bands were not in the reader sample, so "
             "they are not links: the rule keeps only pairs a reader marked same.\n\n")
    if "auto" in info["bands_accepted"]:
        a = bt.set_index("band").loc["auto"]
        L.append(f"The auto band passes narrowly. With {int(a.same)} same and {int(a.different)} different its lower "
                 f"bound is {a.lower_95:.3f}; had the {int(a.unsure)} unsure pairs been wrong it would be "
                 f"{a.lower_95_if_unsure_counted_wrong:.3f} and fail. The rule leaves unsure out, so the band is "
                 "accepted, and the unsure pairs themselves are not links.\n\n")
    for lab, mask in (("Pairs marked different inside an accepted band (the band's error, kept out)",
                       c.band_accepted & c.reader_verdict.eq("different")),
                      ("Pairs marked unsure inside an accepted band (not links)",
                       c.band_accepted & c.reader_verdict.eq("unsure"))):
        rows = c[mask]
        if len(rows):
            L.append(f"{lab}:\n\n")
            for r in rows.itertuples():
                L.append(f"- {r.list_name} ({r.list_pin}) vs {r.our_name} ({r.our_pin}): {r.reader_note}\n")
            L.append("\n")

    nid = out[out.link_status == "not_in_dataset"]
    clear = out[out.clearly_not_ours]
    st = out.link_status.value_counts()
    L.append("## List rows: in our dataset or not\n\n| status | list rows |\n|---|---|\n")
    L.append(f"| linked (accepted link, gives a card fact) | {int(st.get('linked', 0))} |\n")
    L.append(f"| reader marked same, band not accepted (in our dataset, no card fact) | "
             f"{int(st.get('reader_same_band_not_accepted', 0))} |\n")
    L.append(f"| not in our dataset (no accepted link, no candidate marked same) | {len(nid)} |\n")
    L.append(f"| of which had no candidate at all | {info['list_rows_not_in_dataset_without_candidate']} |\n")
    L.append(f"| of which clearly not any of our records | {len(clear)} |\n")
    L.append(f"| eligible for the nearby lane | 0 |\n")
    L.append(f"| total | {len(out)} |\n\n")
    L.append("\"Not in our dataset\" is the task's definition. It is not proof of absence: the linker's recall is not "
             "measured, and only a sample of the auto and middle pairs was read. A not-in-dataset row is counted as "
             "clearly not any of our records only when none of these holds (first reason counted):\n\n")
    ex = info["not_ours_exclusions"]
    L.append(f"- a reader marked a candidate unsure: {ex.get('reader_unsure', 0)}\n")
    L.append(f"- it has an auto or middle candidate no reader saw: {ex.get('unread_auto_or_middle_candidate', 0)}\n")
    L.append(f"- it has a low-band mutual-best candidate scoring {NEAR_MISS_MIN:.2f} or more (a possible match the "
             f"score missed): {ex.get('near_miss_low_candidate', 0)}\n")
    L.append(f"- it could not be located: {ex.get('not_located', 0)}\n")
    L.append(f"- clearly not ours: {ex.get('clearly_not_ours', 0)}\n\n")
    L.append("None of them feed the \"nearby, not in this dataset\" lane: the public_list v2 contract (D055) makes a "
             "blood bank listing a card fact only. `clearly_not_ours` is recorded for completeness, and "
             "`lane_eligible` is false on every row.\n\n")
    L.append("Location quality. A listed point is the list's own latitude and longitude, kept only when it lies "
             "near the row's PIN area or district centre; it is self-reported and has no stated precision. "
             "Precision figures below are for the PIN-area and district-centre rows only.\n\n"
             "| rows | count | listed point | PIN area | district centre | not located | median precision km "
             "| precision <= 5 km | precision > 10 km |\n|---|---|---|---|---|---|---|---|---|\n")
    for lab, d in (("all list rows", out), ("linked", out[out.link_status == "linked"]),
                   ("reader same, band not accepted", out[out.link_status == "reader_same_band_not_accepted"]),
                   ("not in our dataset", nid), ("clearly not ours", clear)):
        q = loc_quality(d)
        med = f"{q['median_km']:.1f}" if q["median_km"] is not None and not math.isnan(q["median_km"]) else "n/a"
        L.append(f"| {lab} | {q['rows']} | {q['listed point']} | {q['PIN area']} | {q['district centre']} | "
                 f"{q['not located']} | {med} | {q['le_5km']} | {q['gt_10km']} |\n")
    L.append("\nVerdicts come from agent readers (D055: agent-checked precision), not from Hruday, and the "
             "readers saw the band and score. The 95% bound covers sampling error in the band, not reader error.\n")
    L.append(f"\n`silver/ext_{SOURCE_ID}.parquet` carries `link_status`, `linked_facility_ids`, `clearly_not_ours`, "
             f"`not_ours_exclusion`, `lane_eligible` and `lane_exclusion` per list row; "
             f"`silver/ext_links_{SOURCE_ID}.parquet` carries `reader_verdict` (same, different, unsure, unread), "
             "`reader_note`, `band_accepted` and `accepted` per candidate pair.\n")
    return "".join(L)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def report(lst, lstats, links, kstats, sheet, sampling, src_sha, as_of) -> str:
    pn = lstats["position_note"]
    st = lstats["status"]
    L = ["# NHP blood bank directory: parsed, located, candidate links\n\n",
         f"Built by `{SCRIPT}` on {utcnow()}. Source: {SOURCE_NAME}, data.gov.in catalog \"Blood Bank Directory "
         f"(updated till last month)\", {CATALOG_URL} (published on the portal 10 Aug 2017; licence {LICENSE}). "
         f"Copy used: {MIRROR_URL} (sha256 `{src_sha}`), retrieved {RETRIEVED_UTC}. See "
         f"`data/external/{SOURCE_ID}/SOURCE.md`.\n\n",
         "Use under D055: a linked blood bank listing is a card fact only, for emoc, cardiac surgery and trauma. "
         "It never appears in the \"nearby, not in this dataset\" lane. "
         + ("Links are final only where `accepted` is true: readers labelled "
            f"`data/labels/ext_link_check_{SOURCE_ID}.csv`, and a band is accepted only if the one-sided 95% lower "
            "bound of its precision is at least 0.90 (see Acceptance).\n\n" if "accepted" in links.columns else
            "No link here is final: readers label "
            f"`data/labels/ext_link_check_{SOURCE_ID}.csv`, and a band is accepted only if the one-sided 95% lower "
            "bound of its precision is at least 0.90.\n\n"),
         "## Findings\n\n",
         f"- **The list:** {lstats['list_rows']} blood banks ({', '.join(f'{v} {k}' for k, v in lstats['category'].items())}). "
         f"{lstats['what'].get('licensed blood bank', 0)} carry a licence number.\n",
         f"- **Dates:** the copy is old, a list of about 2017 to 2018. Licence dates by year from 2014: "
         f"{lstats['licence_dates_by_year_2014_on']} (latest {lstats['latest_licence_date']}); the latest renewal date is "
         f"{lstats['latest_renewal_date']}. Against {as_of.isoformat()}: {st.get('current', 0)} current, "
         f"{st.get('lapsed', 0)} lapsed (renewal date passed, no later renewal on this copy), "
         f"{st.get('undated', 0)} undated. \"Lapsed\" describes this copy of the list, not the blood bank today.\n",
         f"- **Positions:** {pn.get('listed coordinates', 0)} rows keep their listed point (within tolerance of their "
         f"PIN area or district centre), {pn.get('PIN area', 0)} use the PIN area, {pn.get('district centre', 0)} the "
         f"district centre, and {pn.get('not located', 0)} could not be located.\n",
         f"- **Candidates:** {kstats.get('candidates', 0)} pairs for {kstats.get('list_rows_with_candidate', 0)} list rows. "
         f"Bands: {kstats.get('bands', {})}. Auto band: {kstats.get('list_rows_with_auto', 0)} list rows, "
         f"{kstats.get('records_with_auto', 0)} of our records.\n",
         f"- **Check sheet:** {len(sheet)} pairs ({sampling['auto_in_sheet']} auto of {sampling['auto_total']}, "
         f"{sampling['middle_in_sheet']} middle of {sampling['middle_total']}, {sampling['low_probe_in_sheet']} best "
         f"low as a boundary probe); sampled: {sampling['sampled']}.\n",
         "\n## Method\n\n",
         "1. Parse: cp1252 CSV, HTML entities unescaped, whitespace collapsed. One row per `Sr No` "
         "(`list_uid` = `nhpbb-` + zero-padded Sr No). Officer names, phone numbers and emails are dropped.\n",
         "2. Dates: day first (Indian usage); month-year and year-only values kept with a note. `Date of Renewal` is "
         "read as the date the licence fell due for renewal (for example obtained 29-4-2013, renewal 28-4-18). "
         "Status: `current` if that date is on or after the as-of date, `lapsed` if before, `undated` if absent "
         "or text only.\n",
         "3. Position: the listed PIN (or a PIN in the address) through `vf.locate.resolve`, accepted only in the "
         "listed state (Andhra Pradesh and Telangana treated as one); else the listed district, then the city, as a "
         "district centre. The listed point is kept only if it lies within max(15 km, 2 x precision) of the PIN area, "
         "or max(30 km, 2 x precision) of the district centre; otherwise the area position is used.\n",
         "4. Pool: all of `silver/facilities.parquet` except dentists and AYUSH, dental or veterinary names "
         f"({kstats.get('rejected_ayush_dental_vet', 0)} rejected, pool {kstats.get('pool', 0)}).\n",
         "5. Blocks: same PIN; within 2 km (only for rows whose listed point was kept, against our valid "
         f"coordinates); same district plus a shared name token that at most {RARE_MAX_DF} of our records carry. "
         f"Pairs by block: {kstats.get('blocks', {})}.\n",
         "6. Score: IDF-weighted soft Jaccard on name tokens in [0, 1], after removing the service words (blood bank, "
         "blood transfusion centre, blood storage, component and apheresis unit, BB) from both names. Tokens pair at "
         f"rapidfuzz ratio {SOFT_MIN:.0f}+. Bands: auto at {AUTO_MIN}+ and the one-to-one winner, middle at "
         f"{MIDDLE_MIN}+ (or a high score that lost the contest or shares only generic words such as hospital or "
         f"civil: {kstats.get('high_score_generic_only', 0)} pairs), else low. Runs of initials are joined into one "
         "token (B.N.K. to bnk).\n",
         f"7. One-to-one, greedy by score; records in one WP11 duplicate cluster may share a list row "
         f"({kstats.get('lost_one_to_one', 0)} pairs lost the contest).\n",
         "\n## Copy check\n\n",
         "Four public copies of the same CSV were found. The chosen copy has the raw, mixed date formats; the others "
         "differ from it only where a spreadsheet reformatted dates or phone numbers:\n\n"]
    for k, v in lstats["mirror_check"].items():
        L.append(f"- `{k}`: {v}\n")
    L.append("\n## Suggested card wording (for the public_list v2 owner)\n\n"
             "A linked row would read, for a lapsed row: \"Listed as a licensed blood bank in the National Health "
             "Portal blood bank directory (data.gov.in, a list of about 2017 to 2018), licence renewal date on that "
             "list 28 Apr 2018, not renewed on that list. Outside data, not from this record.\" For an undated row, "
             "drop the renewal clause. Never \"has a blood bank\" or \"blood available\".\n")
    L.append("\n## Status counts\n\n| status | rows |\n|---|---|\n")
    for k, v in st.items():
        L.append(f"| {k} | {v} |\n")
    if len(links):
        top = links[links.band == "auto"].head(8)
        L.append("\n## Highest-scoring auto pairs (for orientation, not decisions)\n\n"
                 "| list name | our name | block | score | km |\n|---|---|---|---|---|\n")
        for x in top.itertuples():
            L.append(f"| {x.list_name} | {x.our_name} | {x.block} | {x.score:.2f} | {x.km_apart} |\n")
    L.append("\n## Limits\n\n")
    L.append("- The official file could not be fetched from data.gov.in tonight (HTTP 503 or no connection). The copy "
             "used is a third-party GitHub copy whose header and rows match the data.gov.in schema and three other "
             "copies; its exact snapshot date is not known. The catalog said \"updated till last month\" but showed "
             "\"Published on Data Portal: 10/08/2017\" as late as Nov 2021.\n")
    L.append("- A licence on an old list says the blood bank was licensed then. It is not evidence that it runs, or "
             "has blood, today. Card text must say so.\n")
    L.append("- Many listed blood banks are standalone (Red Cross, Rotary, charity and private banks). They have no "
             "hospital record to link to, and missing links are expected. Recall is not measured.\n")
    L.append("- Listed points are self-reported and many are rough; the position check keeps only points near the "
             "row's PIN area or district centre.\n")
    L.append("- Dates are ambiguous where day and month are both 12 or less; day first is assumed and noted.\n")
    return "".join(L)


LINK_COLS_FIRST = ["list_uid", "facility_id", "score", "band", "reader_verdict", "accepted"]


def list_meta(lst, lstats, src_sha, as_of, acc_info) -> dict:
    m = {"grain": "one row per blood bank on the list (Sr No)", "columns": list(lst.columns),
         "inputs": [f"external/{SOURCE_ID}/{MIRROR_DIR}/file.csv"], "source_sha256": src_sha,
         "source_url": CATALOG_URL, "copy_url": MIRROR_URL, "license": LICENSE,
         "published_on_portal": PUBLISHED_ON_PORTAL.isoformat(), "retrieved_utc": RETRIEVED_UTC,
         "as_of": as_of.isoformat(), "service_lines": list(SERVICE_LINES), "use": "card fact only (D055)",
         "summary": lstats}
    if acc_info:
        m["link_status"] = lst.link_status.value_counts().to_dict()
        m["clearly_not_ours_rule"] = ("not_in_dataset (no accepted link, no candidate a reader marked same) and "
                                      "none of: a reader-unsure candidate, an unread auto or middle candidate, a "
                                      f"low-band mutual-best candidate scoring >= {NEAR_MISS_MIN}, no position")
        m["lane_rule"] = LANE_RULE
        m["inputs"].append(f"labels/ext_link_read_{SOURCE_ID}.csv")
    return m


def links_meta(links, kstats, acc_info, bt) -> dict:
    m = {"grain": ("one row per candidate pair (list_uid x our facility_id); a link is final only where accepted "
                   "is true" if acc_info else
                   "one row per candidate pair (list_uid x our facility_id); bands provisional, no final links"),
         "columns": list(links.columns),
         "inputs": [f"silver/ext_{SOURCE_ID}.parquet", "silver/facilities.parquet", "silver/pin_crosswalk.parquet",
                    "gold/facility_duplicates.parquet"],
         "params": {"auto_min": AUTO_MIN, "middle_min": MIDDLE_MIN, "soft_min": SOFT_MIN, "rare_max_df": RARE_MAX_DF,
                    "near_km": NEAR_KM}, "summary": kstats,
         "acceptance_rule": ("a band is accepted only if the one-sided 95% Clopper-Pearson lower bound of reader "
                             "precision same/(same+different) >= 0.90; accepted = band accepted and reader marked "
                             "same; unread and unsure pairs are never accepted")}
    if acc_info:
        m["reader_file"] = f"labels/ext_link_read_{SOURCE_ID}.csv"
        m["bands"] = json.loads(bt.to_json(orient="records"))
        m["acceptance"] = acc_info
        m["inputs"].append(f"labels/ext_link_read_{SOURCE_ID}.csv")
    return m


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--as-of", default=None, help="date for current/lapsed (default today; accept stage keeps "
                                                  "the as-of date of the build)")
    ap.add_argument("--rebuild", action="store_true",
                    help="re-parse and re-link even when readers' verdicts exist (the check sheet stays frozen)")
    args = ap.parse_args(argv)
    silver, labels = paths.silver_dir(), paths.labels_dir()
    out = silver / f"ext_{SOURCE_ID}.parquet"
    lout = silver / f"ext_links_{SOURCE_ID}.parquet"
    cpath = labels / f"ext_link_check_{SOURCE_ID}.csv"
    read_path = labels / f"ext_link_read_{SOURCE_ID}.csv"

    if read_path.exists() and not args.rebuild:
        print(f"accept stage only (readers' file {read_path.name} exists; pass --rebuild to re-link)")
        lst = pd.read_parquet(out)
        lm = json.loads(out.with_name(out.name + ".meta.json").read_text())
        lstats, src_sha, as_of = lm["summary"], lm["source_sha256"], date.fromisoformat(lm["as_of"])
        links = pd.read_parquet(lout)
        kstats = json.loads(lout.with_name(lout.name + ".meta.json").read_text())["summary"]
    else:
        as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
        ext = paths.data_dir() / "external" / SOURCE_ID
        src = ext / MIRROR_DIR / "file.csv"
        src_sha = sha256(src)
        lst, lstats = build_list(src, as_of)
        fac = pd.read_parquet(silver / "facilities.parquet")
        pinx = pd.read_parquet(silver / "pin_crosswalk.parquet")
        dups = pd.read_parquet(paths.gold_dir() / "facility_duplicates.parquet")
        links, kstats = link(lst, fac, pinx, dups)

    if read_path.exists():
        sheet = pd.read_csv(cpath, dtype=str, keep_default_na=False)
        sampling = json.loads(cpath.with_name(cpath.name + ".meta.json").read_text())["sampling"]
        print(f"check sheet {cpath.name} kept as is (frozen: readers' verdicts exist)")
    else:
        sheet, sampling = check_sheet(links)
        write_atomic(cpath, lambda p: sheet.to_csv(p, index=False))
        write_meta(cpath, len(sheet), {
            "grain": "one row per candidate pair for independent readers; no decisions",
            "sampling": sampling, "readers_write": f"labels/ext_link_read_{SOURCE_ID}.csv"})
        print(f"check sheet {len(sheet)} rows; sampling {sampling}")

    acc_info, bt = None, None
    if read_path.exists():
        links, lst, bt, acc_info = accept(links, sheet, read_verdicts(read_path), lst)
        first = [x for x in LINK_COLS_FIRST if x in links.columns]
        links = links[first + [x for x in links.columns if x not in first]]

    write_atomic(out, lambda p: lst.to_parquet(p, index=False))
    write_meta(out, len(lst), list_meta(lst, lstats, src_sha, as_of, acc_info))
    write_atomic(lout, lambda p: links.to_parquet(p, index=False))
    write_meta(lout, len(links), links_meta(links, kstats, acc_info, bt))

    rep = report(lst, lstats, links, kstats, sheet, sampling, src_sha, as_of)
    if acc_info:
        rep = rep.replace("\n## Method\n", acceptance_report(links, lst, bt, acc_info) + "\n## Method\n", 1)
    rpath = paths.code_root() / "reports" / f"ext_{SOURCE_ID}.md"
    write_atomic(rpath, lambda p: p.write_text(rep))

    print(f"list rows {len(lst)}; positions {lstats['position_note']}; status {lstats['status']}")
    print(f"candidates {kstats.get('candidates')}; bands {kstats.get('bands')}")
    if acc_info:
        for r in bt.itertuples():
            lb = f"{r.lower_95:.3f}" if r.read else "n/a"
            print(f"band {r.band}: pairs {r.pairs}, read {r.read} (same {r.same}, different {r.different}, "
                  f"unsure {r.unsure}), lower 95% {lb}, accepted {r.accepted}")
        print(f"accepted links {acc_info['accepted_pairs']} pairs, {acc_info['list_rows_linked']} list rows, "
              f"{acc_info['facilities_linked']} records; reader-same not accepted "
              f"{acc_info['list_rows_reader_same_band_not_accepted']}; not in dataset "
              f"{acc_info['list_rows_not_in_dataset']} (clearly not ours {acc_info['list_rows_clearly_not_ours']}); "
              f"lane eligible 0 (card fact only)")
    print(f"wrote {out}\nwrote {lout}\nwrote {rpath}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
