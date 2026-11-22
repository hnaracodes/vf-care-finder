#!/usr/bin/env python
"""WP2b: outside public-facility data, used to check the rule quadrants.

Decision D016: external public-facility data is used two ways, kept apart.
(1) An out-of-sample check on the rule quadrants (never an input to them).
(2) An "augmented access" layer reported next to the original distances.

Sources (raw downloads cached under data/external/, see SOURCES.md there):

  laqshya/   NHSRC LaQshya master list (PDF): public facilities whose labour
             room and/or maternity operating theatre are quality certified.
  osm/       OpenStreetMap health facilities. Preferred: one national Overpass
             query (overpass_india_health.json). If Overpass is unavailable the
             HOT country export on HDX (same OSM data, fewer tags) is used.
  census2011/ Census 2011 district totals (Hugging Face indiaset/census-2011).

Outputs (each with a .meta.json sidecar, written atomically):

  data/silver/ext_laqshya.parquet            one row per PDF row (facility x department)
  data/silver/ext_osm_health.parquet         one row per OSM feature inside India
  data/silver/ext_district_population.parquet one row per NFHS district_key
  data/external/SOURCES.md
  reports/wp2b_external_validation.md
  reports/figures/wp2b_*.png

Rerun:  .venv/bin/python scripts/wp2b_external.py            (uses cached downloads)
        .venv/bin/python scripts/wp2b_external.py --refresh  (downloads again)
        --osm-source overpass|hotosm|auto  (auto: cached Overpass, else HOT export)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from rapidfuzz import fuzz
from scipy import stats
from sklearn.neighbors import BallTree

from vf import geo, io, paths

SCRIPT = "scripts/wp2b_external.py"
UA = "vf-datathon-research/0.1 (academic one-off analysis; python urllib)"
EARTH_KM = 6371.0088
SEED = 20261121
N_BOOT = 2000
N_PERM = 10000

#: Reference date for "expired": the day the files were retrieved.
AS_OF = date(2026, 11, 21)
#: Date printed in the LaQshya file name (08-04-2026).
LIST_DATE = date(2026, 4, 8)

LAQSHYA_URL = "https://qps.nhsrcindia.org/sites/default/files/2026-04/LaQshya_Master_data-08-04-2026.pdf"
LAQSHYA_URL_OLD = "https://qps.nhsrcindia.org/sites/default/files/2026-03/LaQsha-master12march.pdf"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
HOTOSM_URL = "https://production-raw-data-api.s3.amazonaws.com/ISO3/IND/_layers/osm/health_facilities.parquet"
HOTOSM_META_URL = ("https://production-raw-data-api.s3.amazonaws.com/ISO3/IND/health_facilities/"
                   "hotosm_ind_health_facilities_osm_metadata.json")
HOTOSM_PAGE = "https://data.humdata.org/dataset/hotosm_ind_health_facilities"
CENSUS_REV = "64293c4d0de858e64cc95a5589735b1de553484e"
CENSUS_URL = (f"https://huggingface.co/datasets/indiaset/census-2011/resolve/{CENSUS_REV}/"
              "census_2011_districts_final.parquet")
CENSUS_README_URL = f"https://huggingface.co/datasets/indiaset/census-2011/resolve/{CENSUS_REV}/README.md"

OVERPASS_QUERY = """[out:json][timeout:900][maxsize:1073741824];
area(id:3600304716)->.in;
(
  nwr["amenity"~"^(hospital|clinic|doctors)$"](area.in);
  nwr["healthcare"~"^(hospital|clinic|centre)$"](area.in);
);
out center tags;
"""

LICENSES = {
    "laqshya": "Government of India publication (NHSRC, MoHFW). No explicit open licence on the file; "
               "public document, cited and used for research only, not redistributed by this project.",
    "osm": "Open Database License (ODbL) 1.0. (c) OpenStreetMap contributors.",
    "census": "CC-BY-4.0 (dataset card). Underlying data: Census of India 2011, Office of the Registrar "
              "General and Census Commissioner, India.",
}


# ==========================================================================
# small utilities
# ==========================================================================

def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git_head() -> Optional[str]:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=paths.code_root(), capture_output=True,
                              text=True, check=True).stdout.strip()
    except Exception:
        return None


def write_atomic(path: Path, write) -> None:
    paths.ensure(path.parent)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        write(tmp)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _clean(o):
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, (np.floating, float)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    return o


def write_json(path: Path, obj) -> None:
    body = json.dumps(_clean(obj), indent=2, default=str) + "\n"
    write_atomic(path, lambda p: p.write_text(body))


def write_parquet(df: pd.DataFrame, path: Path, meta: dict) -> None:
    write_atomic(path, lambda p: df.to_parquet(p, index=False))
    meta = dict(output=str(path.relative_to(paths.data_dir())), rows=int(len(df)),
                columns=list(df.columns), created_at_utc=utcnow(), git_commit=git_head(),
                script=SCRIPT, **meta)
    write_json(path.with_name(path.name + ".meta.json"), meta)


def rel_data(p: Path) -> str:
    try:
        return str(p.relative_to(paths.data_dir()))
    except ValueError:
        return str(p)


# ==========================================================================
# downloads (cached; every file gets a <file>.source.json sidecar)
# ==========================================================================

def _sidecar(dest: Path) -> Path:
    return dest.with_name(dest.name + ".source.json")


def record_source(dest: Path, url: str, license_key: str, retrieved: Optional[str] = None,
                  extra: Optional[dict] = None) -> dict:
    rec = {
        "file": rel_data(dest),
        "url": url,
        "license": LICENSES[license_key],
        "retrieved_utc": retrieved or utcnow(),
        "bytes": dest.stat().st_size,
        "sha256": sha256(dest),
    }
    if extra:
        rec.update(extra)
    write_json(_sidecar(dest), rec)
    return rec


def fetch(url: str, dest: Path, license_key: str, refresh: bool = False, data: Optional[bytes] = None,
          timeout: int = 300, extra: Optional[dict] = None) -> dict:
    """Download url to dest unless cached. Returns the source record."""
    side = _sidecar(dest)
    if dest.exists() and not refresh:
        if side.exists():
            return json.loads(side.read_text())
        # downloaded earlier without a sidecar: the file time is the retrieval time
        mtime = datetime.fromtimestamp(dest.stat().st_mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return record_source(dest, url, license_key, retrieved=mtime,
                             extra=dict(extra or {}, retrieved_note="time taken from the file's modification time"))
    paths.ensure(dest.parent)
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA})
    tmp = dest.with_name(f".{dest.name}.part")
    started = utcnow()
    with urllib.request.urlopen(req, timeout=timeout) as r, open(tmp, "wb") as fh:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            fh.write(chunk)
    os.replace(tmp, dest)
    return record_source(dest, url, license_key, retrieved=started, extra=extra)


def overpass_ok(path: Path) -> bool:
    """A complete Overpass JSON answer: parses, has elements, no runtime remark.
    (The server can close a 200 response mid-stream; such files do not parse.)"""
    if not path.exists() or path.stat().st_size < 200:
        return False
    try:
        with open(path) as fh:
            d = json.load(fh)
    except ValueError:
        return False
    return isinstance(d.get("elements"), list) and "remark" not in d


#: tiles (south, west, north, east) used when the national query cannot be served
OVERPASS_TILES = [(6, 68, 16, 80), (6, 80, 16, 98), (16, 68, 22, 80), (16, 80, 22, 98),
                  (22, 68, 26, 80), (22, 80, 26, 98), (26, 68, 38, 80), (26, 80, 38, 98)]


def tile_query(t) -> str:
    s_, w_, n_, e_ = t
    return OVERPASS_QUERY.replace("[timeout:900][maxsize:1073741824];",
                                  f"[timeout:600][maxsize:536870912][bbox:{s_},{w_},{n_},{e_}];")


def fetch_overpass_tiles(tdir: Path, refresh: bool, pause: int = 30, attempts: int = 3) -> Optional[list[dict]]:
    """Eight bounding-box queries (each still limited to India's area), one at a
    time with pauses; every tile is cached. None if any tile cannot be fetched."""
    recs = []
    for i, t in enumerate(OVERPASS_TILES):
        dest = tdir / f"tile_{i}_{t[0]}_{t[1]}_{t[2]}_{t[3]}.json"
        if overpass_ok(dest) and not refresh:
            recs.append(fetch(OVERPASS_URL, dest, "osm", extra={"query": tile_query(t)}))
            continue
        body = urllib.parse.urlencode({"data": tile_query(t)}).encode()
        wait = 60
        for k in range(attempts):
            try:
                rec = fetch(OVERPASS_URL, dest, "osm", refresh=True, data=body, timeout=700,
                            extra={"query": tile_query(t)})
                if overpass_ok(dest):
                    recs.append(rec)
                    print(f"  tile {i}: {dest.stat().st_size:,} bytes", flush=True)
                    break
                print(f"  tile {i} attempt {k + 1}: incomplete answer", flush=True)
            except Exception as e:  # noqa: BLE001
                print(f"  tile {i} attempt {k + 1}: {e}", flush=True)
            if dest.exists() and not overpass_ok(dest):
                dest.unlink()
                if _sidecar(dest).exists():
                    _sidecar(dest).unlink()
            time.sleep(wait)
            wait *= 2
        else:
            return None
        time.sleep(pause)
    return recs


def fetch_overpass(dest: Path, refresh: bool, attempts: int = 3) -> Optional[dict]:
    """One national query with exponential backoff. None when the server refuses."""
    if overpass_ok(dest) and not refresh:
        return fetch(OVERPASS_URL, dest, "osm", extra={"query": OVERPASS_QUERY})
    body = urllib.parse.urlencode({"data": OVERPASS_QUERY}).encode()
    wait = 60
    for k in range(attempts):
        try:
            rec = fetch(OVERPASS_URL, dest, "osm", refresh=True, data=body, timeout=1100,
                        extra={"query": OVERPASS_QUERY})
            if overpass_ok(dest):
                return rec
            print(f"  overpass attempt {k + 1}: incomplete answer", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"  overpass attempt {k + 1}: {e}", flush=True)
        if dest.exists() and not overpass_ok(dest):
            dest.unlink()
            if _sidecar(dest).exists():
                _sidecar(dest).unlink()
        if k + 1 < attempts:
            time.sleep(wait)
            wait *= 2
    return None


# ==========================================================================
# shared geography
# ==========================================================================

PO_SUFFIX = re.compile(r"\s+(B\s?O|S\s?O|H\s?O|G\s?P\s?O|H\s?Q|MDG|B\s?O\s?BO)$")


def po_place(name) -> str:
    n = geo.norm_name(re.sub(r"\(.*?\)", " ", str(name)))
    for _ in range(2):
        n = PO_SUFFIX.sub("", n).strip()
    return n


class Geography:
    """NFHS districts, PIN-directory districts and post offices, loaded once."""

    def __init__(self):
        self.nfhs = pd.read_parquet(paths.silver_dir() / "nfhs.parquet",
                                    columns=["district_key", "state_ut", "district_name"])
        self.cw = pd.read_parquet(paths.silver_dir() / "district_crosswalk.parquet")
        self.po = pd.read_parquet(paths.silver_dir() / "post_offices.parquet")
        self.state_of = dict(zip(self.nfhs.district_key, self.nfhs.state_ut))
        self.nfhs_states = sorted(self.nfhs.state_ut.unique())
        self.state_by_norm = {geo.norm_state(s): s for s in self.nfhs_states}
        # candidate names per NFHS state: norm name -> {district_key: source}
        self.names: dict[str, dict[str, dict[str, str]]] = defaultdict(lambda: defaultdict(dict))
        self.trunc: dict[str, dict[str, bool]] = defaultdict(dict)
        for k, s, d in self.nfhs[["district_key", "state_ut", "district_name"]].itertuples(index=False):
            n = geo.norm_name(d)
            self.names[s][n][k] = "nfhs"
            self.trunc[s][n] = geo.looks_truncated(d)
        for sp, dp, k, m in self.cw[["state_pin", "district_pin", "district_key", "match_method"]].itertuples(index=False):
            if not isinstance(k, str) or m == "unmatched":
                continue
            s = self.state_of[k]
            n = geo.norm_name(dp)
            self.names[s][n].setdefault(k, f"pin_directory:{m}")
            self.trunc[s].setdefault(n, False)
        # post office place names per state -> Counter(district_key)
        po = self.po[self.po.district_key.notna()].copy()
        po["state_ut"] = po.district_key.map(self.state_of)
        po["place"] = po.office_name.map(po_place)
        po = po[po.office_type.isin(["HO", "PO"])]   # head and sub offices; branch-office villages share names
        self.places: dict[str, dict[str, Counter]] = defaultdict(lambda: defaultdict(Counter))
        for s, p, k in po[["state_ut", "place", "district_key"]].itertuples(index=False):
            if p:
                self.places[s][p][k] += 1
        # post offices per district (all rows, geocoded or not)
        self.po_count = self.po.groupby("district_key").size()
        # geocoded offices for nearest-office lookups (same set as district_access)
        acc = pd.read_parquet(paths.gold_dir() / "post_office_access.parquet")
        if "coord_placeholder" not in acc:
            acc["coord_placeholder"] = False
        acc = acc[["po_id", "district_key", "lat", "lon", "coord_suspect", "coord_placeholder", "dist_km_emoc_g2"]]
        self.po_acc = acc
        keyed = acc[(~acc.coord_suspect) & acc.district_key.notna()].reset_index(drop=True)
        # nearest-office lookups use every non-suspect keyed office
        self.po_near = keyed
        self.po_tree = BallTree(np.radians(keyed[["lat", "lon"]].to_numpy()), metric="haversine")
        # district medians use WP5's rollup: offices stacked on one placeholder point count once per district
        repeat = keyed.coord_placeholder & keyed[["district_key", "lat", "lon"]].duplicated()
        self.po_geo = keyed[~repeat].reset_index(drop=True)

    def nearest_district(self, lat: np.ndarray, lon: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        d, i = self.po_tree.query(np.radians(np.c_[lat, lon]), k=1)
        return self.po_near.district_key.to_numpy()[i[:, 0]], d[:, 0] * EARTH_KM


# ==========================================================================
# 1. LaQshya
# ==========================================================================

LAQ_COLS = ["sno", "state_raw", "district_raw", "facility_type", "facility_name", "department",
            "certified_col", "conditional_col", "result_date_raw", "validity_date_raw"]
LAQ_TYPES = ["SDH", "CHC", "PHC", "FRU", "DH", "MC"]          # longest first where they nest
LAQ_DEPTS = ["Labour Room", "Maternity OT"]
#: state spellings used in the LaQshya file (to undo state-into-district overflow)
LAQ_STATE_NAMES = [
    "Andaman & Nicobar Islands", "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chandigarh",
    "Chhattisgarh", "DNH, D&D", "Delhi", "Goa", "Gujarat", "Haryana", "Himachal Pradesh", "Jammu & Kashmir",
    "Jharkhand", "Karnataka", "Kerala", "Ladakh", "Lakshadweep", "Madhya Pradesh", "Maharashtra", "Manipur",
    "Meghalaya", "Mizoram", "Nagaland", "Odisha", "Puducherry", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu",
    "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal",
]


def _char_runs(chars, bounds):
    """Split a row's characters (in content-stream order) into text runs.

    A run is what the PDF drew as one string. Long district names overflow
    into the next column, so assigning characters by position interleaves two
    cells ("Vishakhapatna" + "mCHC"). Assigning whole runs by where they start
    keeps them apart."""
    runs, cur, prev = [], [], None
    for c in chars:
        if prev is not None:
            gap = c["x0"] - prev["x1"]
            crosses = gap > 1.5 and any(prev["x1"] - 0.5 <= b <= c["x0"] + 0.5 for b in bounds)
            if abs(c["top"] - prev["top"]) > 2 or c["x0"] < prev["x1"] - 2 or gap > 6 or crosses:
                runs.append(cur)
                cur = []
        cur.append(c)
        prev = c
    if cur:
        runs.append(cur)
    return runs


def parse_laqshya_pdf(path: Path) -> pd.DataFrame:
    import pdfplumber

    out = []
    with pdfplumber.open(path) as pdf:
        for pi, pg in enumerate(pdf.pages):
            for tb in pg.find_tables():
                for row in tb.rows:
                    cells = row.cells
                    if len(cells) != len(LAQ_COLS):
                        raise ValueError(f"page {pi + 1}: {len(cells)} columns, expected {len(LAQ_COLS)}")
                    y0 = min(c[1] for c in cells if c)
                    y1 = max(c[3] for c in cells if c)
                    chars = [c for c in pg.chars if y0 <= (c["top"] + c["bottom"]) / 2 <= y1]
                    bounds = sorted({c[0] for c in cells if c} | {c[2] for c in cells if c})
                    texts = [[] for _ in cells]
                    for run in _char_runs(chars, bounds):
                        x = run[0]["x0"] + 0.5
                        for j, c in enumerate(cells):
                            if c and c[0] - 0.5 <= x < c[2]:
                                texts[j].append((round(run[0]["top"]), run[0]["x0"],
                                                 "".join(ch["text"] for ch in run)))
                                break
                    vals = [" ".join(s.strip() for _, _, s in sorted(t)).strip() for t in texts]
                    out.append([pi + 1] + vals)
    df = pd.DataFrame(out, columns=["page"] + LAQ_COLS)
    return df[df.sno.str.fullmatch(r"\d+")].reset_index(drop=True)


def _parse_date(s) -> Optional[date]:
    m = re.fullmatch(r"\s*(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})\s*", str(s))
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def repair_laqshya(df: pd.DataFrame) -> pd.DataFrame:
    """Undo the remaining cell overflows with closed vocabularies."""
    df = df.copy()
    df["parse_repair"] = ""
    known_states = set(LAQ_STATE_NAMES)
    for i, r in df.iterrows():
        rep = []
        st, dist, typ, name, dept = r.state_raw, r.district_raw, r.facility_type, r.facility_name, r.department
        if st not in known_states:
            for k in sorted(known_states, key=len, reverse=True):
                if st.startswith(k) and len(st) > len(k):
                    dist = (st[len(k):] + " " + dist).strip()
                    st = k
                    rep.append("state_overflow")
                    break
        if typ not in LAQ_TYPES:
            for t in LAQ_TYPES:
                if dist.endswith(t) and len(dist) > len(t) and (typ == "" or typ.endswith(t)):
                    dist, typ = dist[: -len(t)].strip(), t
                    rep.append("type_overflow")
                    break
        if dept == "":
            for d in LAQ_DEPTS:
                if name.endswith(d) and len(name) > len(d):
                    name, dept = name[: -len(d)].strip(), d
                    rep.append("department_overflow")
                    break
        df.loc[i, ["state_raw", "district_raw", "facility_type", "facility_name", "department", "parse_repair"]] = [
            st, dist, typ, name, dept, "+".join(rep)]
    return df


def clean_laqshya(raw: pd.DataFrame) -> pd.DataFrame:
    df = repair_laqshya(raw)
    yes = lambda s: s.str.strip().str.lower().eq("yes")  # noqa: E731
    cert, cond = yes(df.certified_col), yes(df.conditional_col)
    df["certification_status"] = np.select([cert & ~cond, cond & ~cert, cert & cond],
                                           ["certified", "conditionally_certified", "both_marked"], "not_marked")
    df["department"] = df.department.replace("", None)
    df["result_date"] = df.result_date_raw.map(_parse_date)
    df["validity_date"] = df.validity_date_raw.map(_parse_date)
    vd = pd.to_datetime(df.validity_date)
    df["expired_on_list_date"] = (vd < pd.Timestamp(LIST_DATE)).astype("boolean").mask(vd.isna())
    df["expired_as_of_retrieval"] = (vd < pd.Timestamp(AS_OF)).astype("boolean").mask(vd.isna())
    df["sno"] = df.sno.astype(int)
    for c in ["state_raw", "district_raw", "facility_type", "facility_name"]:
        df[c] = df[c].str.replace(r"\s+", " ", regex=True).str.strip()
    return df


LAQ_STATE_ALIASES = {
    "DNH D AND D": "Dadra and Nagar Haveli & Daman and Diu",
    "DNH AND DD": "Dadra and Nagar Haveli & Daman and Diu",
    "DADRA AND NAGAR HAVELI": "Dadra and Nagar Haveli & Daman and Diu",
    "DAMAN AND DIU": "Dadra and Nagar Haveli & Daman and Diu",
    "ODISHA": "Odisha",
    "ORISSA": "Odisha",
}
#: extra states searched when a district is not found in its own state
NEIGHBOUR_STATES = {
    "Jammu & Kashmir": ["Ladakh"],
    "Ladakh": ["Jammu & Kashmir"],
    "Andhra Pradesh": ["Telangana"],
    "Telangana": ["Andhra Pradesh"],
}
#: LaQshya district spellings that none of the automatic steps resolve.
#: Each is the district's own name or a city whose district is not in doubt.
LAQ_MANUAL = {
    ("Uttarakhand", "HALDWANI"): ("Uttarakhand|Nainital", "Haldwani is a city in Nainital district"),
    ("Chhattisgarh", "JAGDALPUR"): ("Chhattisgarh|Bastar", "Jagdalpur is the Bastar headquarters; the PIN "
                                    "directory files its head office under Raipur"),
    ("Uttar Pradesh", "KANPUR"): ("Uttar Pradesh|Kanpur Nagar", "Kanpur city is Kanpur Nagar district"),
    ("Uttar Pradesh", "MAUNATH BHANJAN"): ("Uttar Pradesh|Mau", "Maunath Bhanjan is the Mau headquarters"),
    ("Punjab", "MOHALI"): ("Punjab|Sahibzada Ajit Singh Nagar", "Mohali is SAS Nagar"),
    ("Punjab", "NAWASHAHAR"): ("Punjab|Shahid Bhagat Singh Nagar", "Nawanshahr is the SBS Nagar headquarters"),
    ("Karnataka", "MANGALORE"): ("Karnataka|Dakshina Kannada", "Mangaluru is the Dakshina Kannada headquarters"),
    ("Karnataka", "HUNGUNDA"): ("Karnataka|Bagalkot", "Hungund taluk, Bagalkot district"),
    ("Andhra Pradesh", "PADERU"): ("Andhra Pradesh|Visakhapatnam", "Paderu was in Visakhapatnam district at NFHS-5"),
    ("Andhra Pradesh", "VIJAYANAGARAM"): ("Andhra Pradesh|Vizianagaram", "spelling"),
    ("Andhra Pradesh", "KADAPA"): ("Andhra Pradesh|Y.S.R.", "YSR Kadapa"),
    ("Tamil Nadu", "TRICHY"): ("Tamil Nadu|Tiruchirappalli", "Trichy is Tiruchirappalli"),
    ("Tamil Nadu", "UDHAGAMANDALAM"): ("Tamil Nadu|The Nilgiris", "Udhagamandalam (Ooty) is The Nilgiris headquarters"),
    ("Telangana", "BHUPALPALLY"): ("Telangana|Jayashankar Bhupalapally", "spelling"),
    ("Telangana", "JANAGAON"): ("Telangana|Jangoan", "spelling"),
    ("Rajasthan", "PLAI"): ("Rajasthan|Pali", "typo for Pali (Bangur Hospital is in Pali)"),
    ("Sikkim", "GYALSHING"): ("Sikkim|West District", "Gyalshing is the West Sikkim headquarters"),
    ("Mizoram", "AIZWAL"): ("Mizoram|Aizawl", "spelling"),
    ("Assam", "SIBSAGAR"): ("Assam|Sivasagar", "old spelling"),
    ("Gujarat", "MEHSANA"): ("Gujarat|Mahesana", "spelling"),
    ("Gujarat", "VADNAGAR"): ("Gujarat|Mahesana", "Vadnagar is in Mahesana district"),
    ("Goa", "BAMBOLIM"): ("Goa|North Goa", "Bambolim is in North Goa"),
    ("West Bengal", "DURGAPUR"): ("West Bengal|Paschim Barddhaman", "Durgapur is in Paschim Bardhaman"),
    ("Dadra and Nagar Haveli & Daman and Diu", "DNH"): ("Dadra and Nagar Haveli & Daman and Diu|Dadra & Nagar Haveli",
                                                        "DNH abbreviates Dadra and Nagar Haveli"),
    ("Haryana", "MANDIKHERA"): ("Haryana|Mewat", "Mandikhera is in Nuh (Mewat) district"),
    # districts with no NFHS row in this project's NFHS file: left unmatched on purpose
    ("West Bengal", "PURBA BARDHMAN"): (None, "Purba Bardhaman has no row in the project's NFHS file"),
    ("West Bengal", "BARDHAMAN"): (None, "Katwa is in Purba Bardhaman, which has no row in the project's NFHS file"),
    ("Mizoram", "SAIHA"): (None, "Saiha (Siaha) has no row in the project's NFHS file"),
}


def map_state(s: str, g: Geography) -> Optional[str]:
    n = geo.norm_state(s)
    if n in LAQ_STATE_ALIASES:
        return LAQ_STATE_ALIASES[n]
    if n in g.state_by_norm:
        return g.state_by_norm[n]
    best = max(g.state_by_norm, key=lambda k: fuzz.ratio(n, k))
    return g.state_by_norm[best] if fuzz.ratio(n, best) >= 90 else None


#: words that describe the kind of facility, not where it is
GENERIC = frozenset("""
    HOSPITAL HOSPITALS GOVT GOVERNMENT GOVERNMNET DISTRICT DIST GENERAL CHC PHC SDH DH MC FRU MCH
    COMMUNITY HEALTH CENTRE CENTER PRIMARY MEDICAL COLLEGE AND SUB DIVISIONAL DIVISION SUBDIVISIONAL
    AREA TALUK TALUKA CIVIL SADAR REFERRAL RURAL URBAN WOMEN WOMENS WOMAN MATERNITY MOTHER CHILD
    CARE HQ HEADQUARTERS H Q QUARTERS INSTITUTE OF THE SCIENCES SCIENCE RESEARCH TEACHING
    JOINT COMBINED MAHILA ZILLA ZILA JILLA JILA STATE NEW OLD CH CHILDREN HOSPIATL HOSP HOPITAL
    MEMORIAL SUPER SPECIALITY SPECIALTY MULTI GH SDCH UPGRADED UPHC CMCH ESI ESIC CIVIL
    S D H D H C H C M C H
""".split())


def core_tokens(name: str) -> list[str]:
    return [t for t in geo.norm_name(name).split() if t not in GENERIC and not t.isdigit() and len(t) > 1]


def match_place(state: str, text: str, g: Geography, min_share: float = 0.7) -> Optional[tuple[str, float]]:
    n = geo.norm_name(text)
    if not n:
        return None
    for s in [state] + NEIGHBOUR_STATES.get(state, []):
        c = g.places.get(s, {}).get(n)
        if c:
            k, m = c.most_common(1)[0]
            share = m / sum(c.values())
            if share >= min_share:
                return k, share
    return None


def match_district(state: Optional[str], district_raw: str, facility_name: str, g: Geography) -> dict:
    """Map one LaQshya (state, district) to an NFHS district_key."""
    res = {"district_key": None, "district_match": "unmatched", "district_match_score": None,
           "district_match_detail": None}
    if state is None:
        res["district_match_detail"] = "state not recognised"
        return res
    n = geo.norm_name(district_raw)
    if state == "NCT of Delhi" and n.endswith(" DELHI") and n != "NEW DELHI":
        n = n[: -len(" DELHI")].strip()
    states = [state] + NEIGHBOUR_STATES.get(state, [])
    # 1. exact on NFHS or PIN-directory district names
    for s in states:
        hit = g.names.get(s, {}).get(n)
        if hit:
            keys = sorted(hit)
            if len(keys) == 1:
                src = hit[keys[0]]
                res.update(district_key=keys[0], district_match="exact", district_match_score=100.0,
                           district_match_detail=f"name equals {src} district name" +
                           ("" if s == state else f" in {s}"))
                return res
    # 2. manual spellings
    man = LAQ_MANUAL.get((state, n))
    if man:
        res.update(district_key=man[0], district_match="manual" if man[0] else "unmatched",
                   district_match_score=None, district_match_detail=man[1])
        return res
    # 3. fuzzy within the state (NFHS and PIN-directory names)
    if n:
        scored = []
        for s in states:
            for cand, keys in g.names.get(s, {}).items():
                sc, how = geo.name_score(n, cand, g.trunc[s].get(cand, False))
                if cand[:1] != n[:1] and how not in ("token_sort", "contained"):
                    continue   # "MANGALORE" is not "BANGALORE"
                for k in keys:
                    scored.append((sc, k, cand, how))
        scored.sort(reverse=True)
        if scored:
            best = scored[0]
            runner = max((x[0] for x in scored if x[1] != best[1]), default=0.0)
            if best[0] >= 85 and best[0] - runner >= 5:
                res.update(district_key=best[1], district_match="fuzzy", district_match_score=round(best[0], 1),
                           district_match_detail=f"{best[3]} vs '{best[2]}', runner-up {runner:.0f}")
                return res
    # 4. the district text is a town: majority district of post offices named like it
    p = match_place(state, district_raw, g)
    if p:
        res.update(district_key=p[0], district_match="place", district_match_score=round(100 * p[1], 1),
                   district_match_detail=f"district text is a post-office place name ({p[1]:.0%} of its offices)")
        return res
    # 5. the facility name holds a town
    toks = core_tokens(facility_name)
    for cand in ([" ".join(toks)] if len(toks) > 1 else []) + toks[::-1]:
        if len(cand) < 4 or cand in geo.GENERIC_TOKENS:
            continue
        p = match_place(state, cand, g)
        if p:
            res.update(district_key=p[0], district_match="place", district_match_score=round(100 * p[1], 1),
                       district_match_detail=f"facility-name token '{cand}' is a post-office place name "
                                             f"({p[1]:.0%} of its offices)")
            return res
    res["district_match_detail"] = "no exact, fuzzy or place match"
    return res


def facility_uids(df: pd.DataFrame) -> pd.Series:
    """One id per physical facility: rows of one district whose core names
    match (labour room and OT rows of a facility, spelling variants)."""
    uid = pd.Series(index=df.index, dtype=object)
    grp = df.district_key.fillna("?" + df.state_raw + "|" + df.district_raw)
    for gk, sub in df.groupby(grp, sort=False):
        reps: list[tuple[str, str, str]] = []  # (core, type, uid)
        for i, r in sub.iterrows():
            core = " ".join(core_tokens(r.facility_name)) or ("GENERIC " + r.facility_type)
            found = None
            for c, t, u in reps:
                same_type = t == r.facility_type or {t, r.facility_type} <= {"DH", "SDH"}
                if same_type and (c == core or fuzz.ratio(c, core) >= 85):
                    found = u
                    break
            if found is None:
                found = hashlib.md5(f"{gk}|{core}|{r.facility_type}".encode()).hexdigest()[:12]
                reps.append((core, r.facility_type, found))
            uid[i] = found
    return uid


# ==========================================================================
# 2. OpenStreetMap
# ==========================================================================

#: name markers for likely-public facilities, in the order they are tested
PUBLIC_NAME_RULES = [
    ("Government", r"\bgovernment\b|\bgovernmnet\b|\bgovt\b\.?|\bgov\.? hospital\b"),
    ("District Hospital", r"\bdistrict (?:general |headquarters? |hq )?hospital\b|\bdistt?\.? hospital\b"),
    ("Sub District Hospital", r"\bsub[- ]?district hospital\b"),
    ("Sub Divisional Hospital", r"\bsub[- ]?divisional hospital\b"),
    ("Community Health Centre", r"\bcommunity health cent(?:re|er)\b"),
    ("CHC", r"\bc\.?\s?h\.?\s?c\b"),
    ("Primary Health Centre", r"\bprimary health cent(?:re|er)\b"),
    ("PHC", r"\bu?p\.?\s?h\.?\s?c\b"),
    ("Civil Hospital", r"\bcivil hospital\b"),
    ("Medical College", r"\bmedical college\b"),
    # additions: common Indian names for public hospitals at FRU level
    ("Taluk Hospital", r"\btaluka? (?:general )?hospital\b"),
    ("Area Hospital", r"\barea hospital\b"),
    ("Sadar Hospital", r"\bsadar hospital\b"),
    ("Rural Hospital", r"\brural hospital\b"),
    ("Referral Hospital", r"\breferral hospital\b"),
]
PUBLIC_NAME_RE = [(k, re.compile(p, re.I)) for k, p in PUBLIC_NAME_RULES]
PUBLIC_OPERATOR_TYPES = {"government", "public", "state", "military", "municipal", "national"}
NONPUBLIC_OPERATOR_TYPES = {"private", "ngo", "religious", "charitable", "private_non_profit", "community",
                            "cooperative", "consortium", "corporate"}
#: names that look private even when they contain a public marker
PRIVATE_NAME_RE = re.compile(r"\bpvt\b|\bprivate\b|\bltd\b|\blimited\b|\bnursing home\b", re.I)
#: names at district-hospital / FRU level (used for the referral-level subset)
FRU_NAME_RULES = {"District Hospital", "Sub District Hospital", "Sub Divisional Hospital", "Community Health Centre",
                  "CHC", "Civil Hospital", "Medical College", "Taluk Hospital", "Area Hospital", "Sadar Hospital",
                  "Rural Hospital", "Referral Hospital"}


def public_rule(name: str, name_en: str, operator: str, operator_type: str) -> tuple[bool, str]:
    """(likely_public, rule) with the exact rule that decided it."""
    ot = (operator_type or "").strip().lower()
    toks = set(re.split(r"[;,\s_-]+", ot)) - {""}
    if toks & PUBLIC_OPERATOR_TYPES:
        return True, f"operator:type={ot}"
    if toks & NONPUBLIC_OPERATOR_TYPES or ot in NONPUBLIC_OPERATOR_TYPES:
        return False, f"operator:type={ot}"
    for field, text in (("name", name), ("name:en", name_en), ("operator", operator)):
        if not text:
            continue
        for k, rx in PUBLIC_NAME_RE:
            if rx.search(text):
                if PRIVATE_NAME_RE.search(text):
                    return False, f"{field} has '{k}' but also a private marker"
                return True, f"{field} contains '{k}'"
    return False, "no public marker"


def load_overpass(path) -> pd.DataFrame:
    """One Overpass JSON file, or a list of tile files (deduplicated by OSM id)."""
    files = path if isinstance(path, (list, tuple)) else [path]
    elements = {}
    for f in files:
        with open(f) as fh:
            for e in json.load(fh)["elements"]:
                elements[(e["type"], e["id"])] = e
    rows = []
    for e in elements.values():
        t = e.get("tags", {})
        if e["type"] == "node":
            lat, lon = e.get("lat"), e.get("lon")
        else:
            c = e.get("center") or {}
            lat, lon = c.get("lat"), c.get("lon")
        rows.append({
            "osm_id": f"{e['type']}/{e['id']}",
            "name": t.get("name"), "name_en": t.get("name:en"),
            "amenity": t.get("amenity"), "healthcare": t.get("healthcare"),
            "healthcare_speciality": t.get("healthcare:speciality"),
            "operator": t.get("operator"), "operator_type": t.get("operator:type"),
            "emergency": t.get("emergency"), "beds": t.get("beds"),
            "source_tag": t.get("source"),
            "lat": lat, "lon": lon,
        })
    df = pd.DataFrame(rows)
    df["osm_source"] = "overpass"
    return df


def _wkb_centre(b: bytes) -> tuple[float, float]:
    """Centre of a WKB point; other geometries use the bbox centre instead."""
    import struct
    bo = "<" if b[0] == 1 else ">"
    gtype = struct.unpack(bo + "I", b[1:5])[0]
    if gtype == 1:
        x, y = struct.unpack(bo + "dd", b[5:21])
        return y, x
    return np.nan, np.nan


def load_hotosm(path: Path) -> pd.DataFrame:
    t = pd.read_parquet(path)
    lat = (t.bbox.map(lambda b: (b["ymin"] + b["ymax"]) / 2)).astype(float)
    lon = (t.bbox.map(lambda b: (b["xmin"] + b["xmax"]) / 2)).astype(float)
    df = pd.DataFrame({
        "osm_id": t["id"], "name": t["name"], "name_en": t["name_en"],
        "amenity": t["amenity"], "healthcare": t["healthcare"],
        "healthcare_speciality": t["healthcare_speciality"],
        "operator": None, "operator_type": t["operator_type"], "emergency": None, "beds": None,
        "source_tag": t["source"], "lat": lat, "lon": lon,
    })
    keep = df.amenity.isin(["hospital", "clinic", "doctors"]) | df.healthcare.isin(["hospital", "clinic", "centre"])
    df = df[keep].reset_index(drop=True)
    df["osm_source"] = "hotosm_export"
    return df


def build_osm(raw: pd.DataFrame, g: Geography) -> tuple[pd.DataFrame, dict]:
    df = raw.copy()
    df = df.replace({np.nan: None})
    n0 = len(df)
    ok = [io.in_india(a, b) if a is not None and b is not None else False for a, b in zip(df.lat, df.lon)]
    df = df[ok].reset_index(drop=True)
    df["lat"] = df.lat.astype(float)
    df["lon"] = df.lon.astype(float)
    pub = [public_rule(*x) for x in df[["name", "name_en", "operator", "operator_type"]].itertuples(index=False)]
    df["likely_public"] = [p[0] for p in pub]
    df["public_rule"] = [p[1] for p in pub]
    df["is_hospital"] = df.amenity.eq("hospital") | df.healthcare.eq("hospital")
    fru_res = [rx for k, rx in PUBLIC_NAME_RE if k in FRU_NAME_RULES]
    nm = (df.name.fillna("") + " | " + df.name_en.fillna("")).astype(str)
    named_fru = nm.map(lambda t: any(rx.search(t) for rx in fru_res))
    named_phc = nm.str.contains(r"\bu?p\.?\s?h\.?\s?c\b|primary health", case=False, regex=True)
    df["public_fru_level"] = df.likely_public & named_fru & ~named_phc
    df["obstetric_tag"] = (df.healthcare_speciality.fillna("").str.contains(r"gyn|obstet|maternity", case=False))
    k, dist = g.nearest_district(df.lat.to_numpy(), df.lon.to_numpy())
    df["district_key"] = k
    df["nearest_po_km"] = dist
    df["state_ut"] = df.district_key.map(g.state_of)
    for c in ["name", "name_en", "amenity", "healthcare", "healthcare_speciality", "operator", "operator_type",
              "emergency", "beds", "source_tag"]:
        df[c] = df[c].astype("string")
    info = {"features_from_source": n0, "outside_india_bbox_or_no_coords": n0 - len(df), "kept": len(df)}
    cols = ["osm_id", "name", "name_en", "amenity", "healthcare", "healthcare_speciality", "operator",
            "operator_type", "emergency", "beds", "source_tag", "lat", "lon", "is_hospital", "likely_public",
            "public_rule", "public_fru_level", "obstetric_tag", "district_key", "state_ut", "nearest_po_km",
            "osm_source"]
    return df[cols], info


# ==========================================================================
# 1b. LaQshya facilities located through OSM
# ==========================================================================

TYPE_WORDS = {
    "DH": {"DISTRICT", "DH", "GENERAL", "SADAR", "HQ", "HEADQUARTERS", "ZILLA", "CIVIL", "WOMEN", "MCH"},
    "SDH": {"SUB", "AREA", "TALUK", "TALUKA", "CIVIL", "DIVISIONAL", "SDH", "GENERAL", "SUBDIVISIONAL", "RURAL"},
    "CHC": {"CHC", "COMMUNITY", "RURAL", "FRU"},
    "MC": {"MEDICAL", "COLLEGE", "MCH", "INSTITUTE", "GMC"},
    "PHC": {"PHC", "PRIMARY"},
}


#: OSM names that are never the certified allopathic facility
NOT_LAQ_RE = re.compile(r"ayurved|ayush|homoeo|homeo|unani|siddha|dispensary|veterinary|animal|pharmacy|"
                        r"diagnostic|dental|eye|blood bank", re.I)


def locate_laqshya(fac: pd.DataFrame, osm: pd.DataFrame) -> pd.DataFrame:
    """Match each LaQshya facility (one row per facility_uid) to an OSM feature
    in the same district by place-name tokens."""
    osm = osm[osm.district_key.notna() & ~osm.name.fillna("").astype(str).str.contains(NOT_LAQ_RE)].copy()
    names = osm.name.fillna("").astype(str).tolist()
    osm["core"] = [" ".join(core_tokens(n)) for n in names]
    osm["words"] = pd.Series([frozenset(geo.norm_name(n).split()) for n in names], index=osm.index, dtype=object)
    by_d = {k: v for k, v in osm.groupby("district_key")}
    out = []
    for r in fac.itertuples(index=False):
        rec = {"facility_uid": r.facility_uid, "osm_match_id": None, "osm_match_name": None, "osm_lat": np.nan,
               "osm_lon": np.nan, "osm_match_score": np.nan, "osm_match_rule": None}
        cand = by_d.get(r.district_key)
        if cand is None or not isinstance(r.district_key, str):
            out.append(rec)
            continue
        core = " ".join(core_tokens(r.facility_name))
        tw = TYPE_WORDS.get(r.facility_type, set())
        best = None
        for o in cand.itertuples(index=False):
            type_ok = bool(tw & o.words)
            if core:
                if not o.core:
                    continue
                exact = fuzz.ratio(core.replace(" ", ""), o.core.replace(" ", ""))
                sc = max(fuzz.token_set_ratio(core, o.core), exact)
                if sc < 90 or not (o.likely_public or type_ok):
                    continue
                rule = "place-name tokens"
            else:
                # generic names ("District Hospital"): unique public district hospital in the district
                if r.facility_type != "DH" or "DISTRICT" not in o.words or not o.likely_public or not o.is_hospital:
                    continue
                sc, rule, exact = 90.0, "generic district hospital", 0.0
            key = (sc, type_ok, o.likely_public, o.is_hospital, exact)
            if best is None or key > best[0]:
                best = (key, o, rule)
        if best is not None:
            if best[2] == "generic district hospital":
                n_dh = int((cand.likely_public & cand.is_hospital & cand.words.map(lambda w: "DISTRICT" in w)).sum())
                if n_dh != 1:
                    out.append(rec)
                    continue
            o = best[1]
            rec.update(osm_match_id=o.osm_id, osm_match_name=o.name, osm_lat=o.lat, osm_lon=o.lon,
                       osm_match_score=best[0][0], osm_match_rule=best[2])
        out.append(rec)
    return pd.DataFrame(out)


# ==========================================================================
# 3. Census 2011 population
# ==========================================================================

CENSUS_STATE = {
    "Dadra & Nagar Haveli": "Dadra and Nagar Haveli & Daman and Diu",
    "Daman & Diu": "Dadra and Nagar Haveli & Daman and Diu",
    "Maharashtra": "Maharastra",
}
#: Census 2011 names renamed by NFHS-5 time (name change only, same territory
#: unless a split is also handled through post offices below).
CENSUS_RENAMES = {
    ("Chhattisgarh", "DAKSHIN BASTAR DANTEWADA"): "DANTEWADA",
}


def build_population(g: Geography) -> tuple[pd.DataFrame, dict]:
    c = pd.read_parquet(paths.external_dir() / "census2011" / "census_2011_districts_final.parquet")
    c["state_ut"] = c.state_name.map(lambda s: CENSUS_STATE.get(s, s))
    c.loc[c.district_name.str.contains("Leh|Kargil", case=False) & c.state_name.eq("Jammu & Kashmir"), "state_ut"] = "Ladakh"
    unknown_states = sorted(set(c.state_ut) - set(g.nfhs_states))
    # 1. name match each census district to an NFHS district (or PIN-directory name)
    recs = []
    for r in c.itertuples(index=False):
        states = [r.state_ut] + (["Telangana"] if r.state_ut == "Andhra Pradesh" else [])
        found = None
        for nm in (r.district_name, r.district_name_lgd):
            n = geo.norm_name(nm)
            n = CENSUS_RENAMES.get((r.state_ut, n), n)
            for s in states:
                hit = g.names.get(s, {}).get(n)
                if hit and len(hit) == 1:
                    found = (next(iter(hit)), "exact", 100.0)
                    break
            if found:
                break
        if not found:
            scored = []
            for s in states:
                for cand, keys in g.names.get(s, {}).items():
                    for nm in (r.district_name, r.district_name_lgd):
                        n = CENSUS_RENAMES.get((r.state_ut, geo.norm_name(nm)), geo.norm_name(nm))
                        sc, _ = geo.name_score(n, cand, g.trunc[s].get(cand, False))
                        for k in keys:
                            scored.append((sc, k))
            scored.sort(reverse=True)
            if scored:
                runner = max((x[0] for x in scored if x[1] != scored[0][1]), default=0)
                if scored[0][0] >= 85 and scored[0][0] - runner >= 5:
                    found = (scored[0][1], "fuzzy", round(scored[0][0], 1))
        recs.append({"census_code": r.district_code, "census_state": r.state_name, "census_district": r.district_name,
                     "state_ut": r.state_ut, "pop_total": r.pop_total,
                     "district_key": found[0] if found else None,
                     "method": found[1] if found else "unmatched", "score": found[2] if found else None})
    cm = pd.DataFrame(recs)
    # 2. NFHS districts with no census district of their own: new since 2011.
    #    Parent = the census-matched district whose post offices are nearest
    #    (vote over the new district's offices, nearest office of a matched district).
    matched_keys = set(cm.district_key.dropna())
    po = g.po_geo
    parent_of: dict[str, tuple[str, float]] = {}
    for s in g.nfhs_states:
        keys = set(g.nfhs.district_key[g.nfhs.state_ut == s])
        new = sorted(keys - matched_keys)
        old = sorted(keys & matched_keys)
        if not new or not old:
            continue
        ref = po[po.district_key.isin(old)]
        if ref.empty:
            continue
        tree = BallTree(np.radians(ref[["lat", "lon"]].to_numpy()), metric="haversine")
        for k in new:
            mine = po[po.district_key == k]
            if mine.empty:
                continue
            _, idx = tree.query(np.radians(mine[["lat", "lon"]].to_numpy()), k=1)
            votes = Counter(ref.district_key.to_numpy()[idx[:, 0]])
            pk, m = votes.most_common(1)[0]
            parent_of[k] = (pk, m / sum(votes.values()))
    # 3. split each census district over its NFHS group by post-office counts
    groups: dict[str, list[str]] = defaultdict(list)
    for k, (pk, _) in parent_of.items():
        groups[pk].append(k)
    rows = []
    agg = cm.dropna(subset=["district_key"]).groupby("district_key").agg(
        pop=("pop_total", "sum"), n_census=("census_code", "size"),
        census_names=("census_district", lambda s: "; ".join(s)), method=("method", "first"))
    for k, a in agg.iterrows():
        members = [k] + sorted(groups.get(k, []))
        w = np.array([g.po_count.get(m, 0) for m in members], dtype=float)
        if w.sum() == 0:
            w = np.ones(len(w))
        share = w / w.sum()
        for m, sh in zip(members, share):
            est = len(members) > 1
            rows.append({
                "district_key": m,
                "pop_2011": float(a["pop"] * sh),
                "is_estimate": bool(est),
                "method": ("census_name_" + a["method"]) if m == k and not est else
                          ("parent_split_by_post_offices" if est else "census_name_" + a["method"]),
                "census_districts": a["census_names"],
                "parent_district_key": k if m != k else None,
                "parent_vote_share": parent_of[m][1] if m in parent_of else None,
                "split_share": float(sh),
                "n_post_offices": int(g.po_count.get(m, 0)),
            })
    pop = pd.DataFrame(rows)
    # NFHS districts left without any population
    missing = sorted(set(g.nfhs.district_key) - set(pop.district_key))
    pop = pd.concat([pop, pd.DataFrame({"district_key": missing, "pop_2011": np.nan, "is_estimate": True,
                                        "method": "no_census_match", "census_districts": None,
                                        "parent_district_key": None, "parent_vote_share": None,
                                        "split_share": None, "n_post_offices": [int(g.po_count.get(m, 0)) for m in missing]})],
                    ignore_index=True)
    pop["state_ut"] = pop.district_key.map(g.state_of)
    pop = pop.sort_values("district_key").reset_index(drop=True)
    info = {
        "census_districts": int(len(cm)),
        "census_match_counts": cm.method.value_counts().to_dict(),
        "census_unmatched": cm[cm.method == "unmatched"][["census_state", "census_district", "pop_total"]].to_dict("records"),
        "census_unmatched_population": int(cm.loc[cm.method == "unmatched", "pop_total"].sum()),
        "census_total_population": int(cm.pop_total.sum()),
        "population_assigned": float(pop.pop_2011.sum()),
        "nfhs_districts_new_since_2011_with_parent": len(parent_of),
        "nfhs_districts_without_population": missing,
        "method_counts": pop.method.value_counts().to_dict(),
        "estimates": int(pop.is_estimate.sum()),
        "unknown_states": unknown_states,
        "parents_low_vote_share_lt_0_6": sorted(k for k, (p, v) in parent_of.items() if v < 0.6),
        "census_name_unsplit": sorted(pop.loc[~pop.is_estimate, "district_key"]),
    }
    return pop, info


# ==========================================================================
# 4. validation
# ==========================================================================

QUADS = ["care_desert", "data_desert", "cost_or_quality", "served"]
QLABEL = {"care_desert": "Care desert", "data_desert": "Data desert",
          "cost_or_quality": "Cost or quality", "served": "Served"}


def stability_flags(q: pd.DataFrame, meta: dict) -> Optional[pd.Series]:
    """Recompute 'same label under all 16 threshold pairs' from the cut values
    stored in the quadrants meta. Returns None if it does not reproduce the
    published counts."""
    try:
        av = meta["thresholds"]["access"]["variants_km"]
        ov = meta["thresholds"]["outcome"]["variants_pct"]
        want = meta["stable_under_all_16_pairs"]
    except KeyError:
        return None
    a = q.access_value.to_numpy(float)
    o = q.outcome_value.to_numpy(float)
    prim = q.quadrant_rule.to_numpy(object)
    same = np.ones(len(q), bool)
    for ac in av.values():
        for oc in ov.values():
            pa, po_ = a >= ac, o < oc
            lab = np.select([pa & po_, pa & ~po_, ~pa & po_], ["care_desert", "data_desert", "cost_or_quality"], "served")
            same &= lab == prim
    got = {k: int(((prim == k) & same).sum()) for k in QUADS}
    if any(got[k] != want.get(k) for k in QUADS):
        print(f"  stability recompute {got} does not match meta {want}; skipping stable subset")
        return None
    return pd.Series(same, index=q.index)


def state_bootstrap_median(v: np.ndarray, grp: np.ndarray, state: np.ndarray, groups: list[str],
                           n_boot: int = N_BOOT, seed: int = SEED, stat=np.median) -> np.ndarray:
    rng = np.random.default_rng(seed)
    ok = ~np.isnan(v)
    v, grp, state = v[ok], grp[ok], state[ok]
    states = pd.unique(state)
    idx = [np.flatnonzero(state == s) for s in states]
    out = np.full((n_boot, len(groups)), np.nan)
    for b in range(n_boot):
        ii = np.concatenate([idx[k] for k in rng.integers(0, len(states), len(states))])
        vv, gg = v[ii], grp[ii]
        for j, gname in enumerate(groups):
            m = vv[gg == gname]
            if len(m):
                out[b, j] = stat(m)
    return out


def within_state(x, is_a, is_b, state, n_perm=N_PERM, seed=SEED):
    """Weighted mean over states of mean(a) - mean(b), weights na*nb/(na+nb),
    with a permutation p that shuffles labels inside each state."""
    x = np.asarray(x, float)
    ok = ~np.isnan(x)
    x, is_a, is_b, state = x[ok], np.asarray(is_a)[ok], np.asarray(is_b)[ok], np.asarray(state)[ok]
    rng = np.random.default_rng(seed)
    num = den = 0.0
    perm = np.zeros(n_perm)
    used = 0
    for s in pd.unique(state):
        m = state == s
        a, b = x[m & is_a], x[m & is_b]
        if not (len(a) and len(b)):
            continue
        used += 1
        w = len(a) * len(b) / (len(a) + len(b))
        num += w * (a.mean() - b.mean())
        den += w
        v = np.r_[a, b]
        order = rng.random((n_perm, len(v))).argsort(axis=1)
        pv = v[order]
        perm += w * (pv[:, :len(a)].mean(axis=1) - pv[:, len(a):].mean(axis=1))
    if not den:
        return np.nan, np.nan, 0
    obs = num / den
    perm /= den
    p = (1 + np.sum(np.abs(perm) >= abs(obs) - 1e-12)) / (1 + n_perm)
    return obs, float(p), used


def compare(d: pd.DataFrame, col: str, a: str = "data_desert", b: str = "care_desert",
            label_col: str = "quadrant_rule") -> dict:
    v = d[col].to_numpy(float)
    grp = d[label_col].to_numpy(object)
    st = d.state_ut.to_numpy(object)
    binary = set(np.unique(v[~np.isnan(v)])) <= {0.0, 1.0}
    stat = np.mean if binary else np.median   # a median of 0/1 values says nothing; use the share
    boot = state_bootstrap_median(v, grp, st, QUADS, stat=stat)
    res = {"by_quadrant": {}, "center": "mean (share)" if binary else "median"}
    for j, q in enumerate(QUADS):
        vv = v[(grp == q) & ~np.isnan(v)]
        res["by_quadrant"][q] = {"n": int(len(vv)), "median": float(stat(vv)) if len(vv) else np.nan,
                                 "mean": float(vv.mean()) if len(vv) else np.nan,
                                 "ci95": [float(np.nanpercentile(boot[:, j], 2.5)), float(np.nanpercentile(boot[:, j], 97.5))]}
    ja, jb = QUADS.index(a), QUADS.index(b)
    diff = boot[:, ja] - boot[:, jb]
    va, vb = v[(grp == a) & ~np.isnan(v)], v[(grp == b) & ~np.isnan(v)]
    mw = stats.mannwhitneyu(va, vb, alternative="two-sided") if len(va) and len(vb) else None
    ws, wp, used = within_state(v, grp == a, grp == b, st)
    res["pair"] = {"a": a, "b": b, "median_diff": float(stat(va) - stat(vb)) if len(va) and len(vb) else np.nan,
                   "median_diff_ci95": [float(np.nanpercentile(diff, 2.5)), float(np.nanpercentile(diff, 97.5))],
                   "mann_whitney_p": float(mw.pvalue) if mw else np.nan,
                   "within_state_mean_diff": ws, "within_state_perm_p": wp, "within_state_states": used}
    return res


def nearest_km(points_lat, points_lon, targets: pd.DataFrame) -> np.ndarray:
    if targets.empty:
        return np.full(len(points_lat), np.nan)
    tree = BallTree(np.radians(targets[["lat", "lon"]].to_numpy(float)), metric="haversine")
    d, _ = tree.query(np.radians(np.c_[points_lat, points_lon]), k=1)
    return d[:, 0] * EARTH_KM


def district_median(po: pd.DataFrame, col: str) -> pd.Series:
    return po.groupby("district_key")[col].median()


def validation(g: Geography, laq: pd.DataFrame, laq_fac: pd.DataFrame, osm: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    qpath = paths.gold_dir() / "district_quadrants.parquet"
    q = pd.read_parquet(qpath)
    q = q[q.service_line == "emoc"].reset_index(drop=True)
    qmeta = json.loads(qpath.with_name(qpath.name + ".meta.json").read_text())
    cut = float(qmeta["thresholds"]["access"]["cut_km"])
    q["state_ut"] = q.district_key.map(g.state_of)
    stable = stability_flags(q, qmeta)
    q["stable_all_16"] = stable if stable is not None else np.nan
    q["n_post_offices_all"] = q.district_key.map(g.po_count).fillna(0).astype(int)
    da = pd.read_parquet(paths.gold_dir() / "district_access.parquet")
    da = da[(da.service_line == "emoc") & (da.min_grade == 2)].set_index("district_key")
    q["emoc_g2_dist_km_p50"] = q.district_key.map(da.dist_km_p50)
    q["n_post_offices_geocoded"] = q.district_key.map(da.n_post_offices)

    # LaQshya counts (facilities, not certificate rows)
    f = laq_fac[laq_fac.district_key.notna()]
    q["laq_facilities"] = q.district_key.map(f.groupby("district_key").size()).fillna(0).astype(int)
    q["laq_facilities_valid"] = q.district_key.map(f[f.any_valid_as_of_retrieval].groupby("district_key").size()).fillna(0).astype(int)
    q["laq_facilities_ot"] = q.district_key.map(f[f.has_maternity_ot].groupby("district_key").size()).fillna(0).astype(int)
    q["laq_per_1k_po"] = 1000 * q.laq_facilities / q.n_post_offices_all.replace(0, np.nan)
    q["laq_valid_per_1k_po"] = 1000 * q.laq_facilities_valid / q.n_post_offices_all.replace(0, np.nan)
    q["laq_any"] = (q.laq_facilities > 0).astype(float)
    q["laq_valid_any"] = (q.laq_facilities_valid > 0).astype(float)

    # OSM likely-public hospitals
    ph = osm[osm.likely_public & osm.is_hospital]
    fru = osm[osm.public_fru_level & osm.is_hospital]
    q["osm_public_hospitals"] = q.district_key.map(ph.groupby("district_key").size()).fillna(0).astype(int)
    q["osm_public_hospitals_per_1k_po"] = 1000 * q.osm_public_hospitals / q.n_post_offices_all.replace(0, np.nan)
    q["osm_all_features"] = q.district_key.map(osm.groupby("district_key").size()).fillna(0).astype(int)

    po = g.po_geo.copy()
    po["d_osm_public_hosp"] = nearest_km(po.lat, po.lon, ph)
    po["d_osm_public_fru"] = nearest_km(po.lat, po.lon, fru)
    loc = laq_fac[laq_fac.osm_lat.notna()].rename(columns={"osm_lat": "lat", "osm_lon": "lon"})
    po["d_laq_all"] = nearest_km(po.lat, po.lon, loc)
    po["d_laq_valid"] = nearest_km(po.lat, po.lon, loc[loc.any_valid_as_of_retrieval])
    po["d_laq_ot"] = nearest_km(po.lat, po.lon, loc[loc.has_maternity_ot])
    po["aug_all"] = np.fmin(po.dist_km_emoc_g2, po.d_laq_all)
    po["aug_valid"] = np.fmin(po.dist_km_emoc_g2, po.d_laq_valid)
    po["aug_ot"] = np.fmin(po.dist_km_emoc_g2, po.d_laq_ot)
    po["aug_osm_public"] = np.fmin(po.dist_km_emoc_g2, po.d_osm_public_hosp)
    for col, out in [("d_osm_public_hosp", "osm_public_hosp_dist_km_p50"), ("d_osm_public_fru", "osm_public_fru_dist_km_p50"),
                     ("dist_km_emoc_g2", "emoc_g2_dist_km_p50_check"), ("aug_all", "aug_dist_km_p50"),
                     ("aug_valid", "aug_valid_dist_km_p50"), ("aug_ot", "aug_ot_dist_km_p50"),
                     ("aug_osm_public", "aug_osm_public_dist_km_p50")]:
        q[out] = q.district_key.map(district_median(po, col))
    chk = (q.emoc_g2_dist_km_p50 - q.emoc_g2_dist_km_p50_check).abs().max()

    res = {"access_cut_km": cut, "emoc_g2_recompute_max_abs_diff_km": float(chk), "qmeta": qmeta,
           "stable_subset_available": stable is not None}
    metrics = ["laq_facilities", "laq_per_1k_po", "laq_valid_per_1k_po", "laq_any", "laq_valid_any",
               "osm_public_hospitals", "osm_public_hospitals_per_1k_po", "osm_public_hosp_dist_km_p50",
               "osm_public_fru_dist_km_p50", "emoc_g2_dist_km_p50"]
    res["all"] = {m: compare(q, m) for m in metrics}
    if stable is not None:
        qs = q[q.stable_all_16.astype(bool)]
        res["stable"] = {m: compare(qs, m) for m in metrics}
        res["stable_counts"] = qs.quadrant_rule.value_counts().to_dict()
    # share of districts with at least one, plainly
    res["share_with_laq"] = q.groupby("quadrant_rule").laq_any.mean().to_dict()
    res["share_with_laq_valid"] = q.groupby("quadrant_rule").laq_valid_any.mean().to_dict()
    # augmented access: who leaves the far third
    aug = {}
    for col in ["aug_dist_km_p50", "aug_valid_dist_km_p50", "aug_ot_dist_km_p50", "aug_osm_public_dist_km_p50"]:
        row = {}
        for qd in QUADS:
            m = q.quadrant_rule == qd
            far0 = m & (q.access_value >= cut)
            row[qd] = {"n": int(m.sum()), "far_original": int(far0.sum()),
                       "leave_far_third": int((far0 & (q[col] < cut)).sum()),
                       "median_original": float(q.loc[m, "access_value"].median()),
                       "median_augmented": float(q.loc[m, col].median())}
            if stable is not None:
                ms = m & q.stable_all_16.astype(bool)
                row[qd]["stable_n"] = int(ms.sum())
                row[qd]["stable_leave_far_third"] = int((ms & (q[col] < cut)).sum())
        aug[col] = row
    res["augmented"] = aug
    # within-state version of the leave-far-third difference
    q["left_far_third"] = ((q.access_value >= cut) & (q.aug_dist_km_p50 < cut)).astype(float)
    res["left_far_compare"] = compare(q[q.quadrant_rule.isin(["data_desert", "care_desert"])], "left_far_third")
    # LaQshya in the far third at all (is there a located LaQshya facility in poor-access districts?)
    res["laq_located_in_far"] = {qd: int(((q.quadrant_rule == qd) & (q.laq_facilities > 0)).sum()) for qd in QUADS}
    # correlations across all districts
    rho, p = stats.spearmanr(q.emoc_g2_dist_km_p50, q.osm_public_hosp_dist_km_p50, nan_policy="omit")
    res["spearman_emoc_vs_osm_public"] = {"rho": float(rho), "p": float(p)}
    # OSM coverage by state (completeness proxy)
    st = q.groupby("state_ut").agg(districts=("district_key", "size"), pos=("n_post_offices_all", "sum"),
                                   osm_public_hosp=("osm_public_hospitals", "sum"), osm_all=("osm_all_features", "sum"),
                                   laq=("laq_facilities", "sum"))
    st["osm_public_per_1k_po"] = 1000 * st.osm_public_hosp / st.pos
    st["osm_all_per_1k_po"] = 1000 * st.osm_all / st.pos
    res["by_state"] = st.reset_index().to_dict("records")
    return q, res


# ==========================================================================
# reporting
# ==========================================================================

def fmt(x, nd=1):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "n/a"
    return f"{x:,.{nd}f}"


def fmt_p(p):
    if p is None or not np.isfinite(p):
        return "n/a"
    return f"{p:.2g}" if p >= 0.001 else f"{p:.1e}"


def write_sources(recs: list[dict], notes: dict) -> None:
    lines = ["# External data sources", "",
             "Written by `scripts/wp2b_external.py` (WP2b). One entry per downloaded file. Times are UTC. "
             "Checksums are SHA-256 of the file as stored here. Raw downloads are not committed and not republished.",
             "", "Decisions D015 and D016 govern use: outside data checks the rule quadrants and adds an "
             "augmented-access layer; it is never an input to the quadrants.", ""]
    for r in recs:
        lines += [f"## {r['file']}", "",
                  f"- URL: {r['url']}",
                  f"- License: {r['license']}",
                  f"- Retrieved: {r['retrieved_utc']}" + (f" ({r['retrieved_note']})" if r.get("retrieved_note") else ""),
                  f"- Bytes: {r['bytes']:,}",
                  f"- SHA-256: `{r['sha256']}`"]
        if r.get("note"):
            lines.append(f"- Note: {r['note']}")
        if r.get("query"):
            lines += ["- Overpass query:", "", "```", r["query"].strip(), "```"]
        lines.append("")
    for k, v in notes.items():
        lines += [f"## {k}", "", v, ""]
    body = "\n".join(lines)
    write_atomic(paths.external_dir() / "SOURCES.md", lambda p: p.write_text(body))


# ==========================================================================
# main
# ==========================================================================

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refresh", action="store_true", help="download again even when cached")
    ap.add_argument("--osm-source", choices=["auto", "overpass", "hotosm"], default="auto")
    ap.add_argument("--no-population", action="store_true")
    ap.add_argument("--no-figures", action="store_true")
    args = ap.parse_args(argv)
    t0 = time.time()
    ext = paths.ensure(paths.external_dir())
    sources: list[dict] = []
    notes: dict[str, str] = {}

    # ---------------- downloads ----------------
    print("[1/6] downloads")
    laq_pdf = ext / "laqshya" / Path(LAQSHYA_URL).name
    sources.append(fetch(LAQSHYA_URL, laq_pdf, "laqshya", args.refresh,
                         extra={"note": "Latest LaQshya master list found (dated 08-04-2026). Parsed."}))
    old_pdf = ext / "laqshya" / Path(LAQSHYA_URL_OLD).name
    sources.append(fetch(LAQSHYA_URL_OLD, old_pdf, "laqshya", args.refresh,
                         extra={"note": "Earlier master list (12 March 2026). Kept for reference; not parsed."}))
    osm_raw, osm_src, ov_files = None, None, None
    ov_path = ext / "osm" / "overpass_india_health.json"
    tdir = ext / "osm" / "overpass_tiles"
    tiles_cached = all(overpass_ok(tdir / f"tile_{i}_{t[0]}_{t[1]}_{t[2]}_{t[3]}.json")
                       for i, t in enumerate(OVERPASS_TILES))
    if args.osm_source in ("auto", "overpass"):
        if overpass_ok(ov_path) and not args.refresh:
            sources.append(fetch(OVERPASS_URL, ov_path, "osm", extra={"query": OVERPASS_QUERY}))
            osm_src, ov_files = "overpass", ov_path
        elif tiles_cached and not args.refresh:
            recs = fetch_overpass_tiles(tdir, False)
            sources.extend(recs)
            osm_src = "overpass"
            ov_files = [tdir / f"tile_{i}_{t[0]}_{t[1]}_{t[2]}_{t[3]}.json" for i, t in enumerate(OVERPASS_TILES)]
        elif args.osm_source == "overpass":
            recs = fetch_overpass_tiles(tdir, args.refresh)
            if recs:
                sources.extend(recs)
                osm_src = "overpass"
                ov_files = [tdir / f"tile_{i}_{t[0]}_{t[1]}_{t[2]}_{t[3]}.json" for i, t in enumerate(OVERPASS_TILES)]
    if osm_src is None:
        hp = ext / "osm" / "hotosm" / "health_facilities.parquet"
        sources.append(fetch(HOTOSM_URL, hp, "osm", args.refresh,
                             extra={"note": f"HOT OpenStreetMap country export (HDX page {HOTOSM_PAGE}). Used because "
                                            "the national Overpass query could not be served."}))
        sources.append(fetch(HOTOSM_META_URL, ext / "osm" / "hotosm" / Path(HOTOSM_META_URL).name, "osm", args.refresh))
        osm_src = "hotosm_export"
    if not args.no_population:
        cp = ext / "census2011" / "census_2011_districts_final.parquet"
        sources.append(fetch(CENSUS_URL, cp, "census", args.refresh,
                             extra={"note": "Hugging Face dataset indiaset/census-2011, revision " + CENSUS_REV}))
        sources.append(fetch(CENSUS_README_URL, ext / "census2011" / "README.md", "census", args.refresh))

    g = Geography()

    # ---------------- LaQshya ----------------
    print("[2/6] LaQshya")
    raw = parse_laqshya_pdf(laq_pdf)
    laq = clean_laqshya(raw)
    laq["state_ut"] = laq.state_raw.map(lambda s: map_state(s, g))
    m = [match_district(s, d, n, g) for s, d, n in laq[["state_ut", "district_raw", "facility_name"]].itertuples(index=False)]
    laq = pd.concat([laq, pd.DataFrame(m)], axis=1)
    laq["facility_uid"] = facility_uids(laq)

    # ---------------- OSM ----------------
    print(f"[3/6] OpenStreetMap ({osm_src})")
    osm_raw = load_overpass(ov_files) if osm_src == "overpass" else load_hotosm(ext / "osm" / "hotosm" / "health_facilities.parquet")
    osm, osm_info = build_osm(osm_raw, g)

    # facility table for LaQshya, located through OSM
    fac = (laq.sort_values(["facility_uid", "sno"])
              .groupby("facility_uid", sort=False)
              .agg(facility_name=("facility_name", "first"), facility_type=("facility_type", "first"),
                   state_ut=("state_ut", "first"), district_key=("district_key", "first"),
                   n_rows=("sno", "size"),
                   has_labour_room=("department", lambda s: (s == "Labour Room").any()),
                   has_maternity_ot=("department", lambda s: (s == "Maternity OT").any()),
                   any_valid_as_of_retrieval=("expired_as_of_retrieval", lambda s: (s == False).any()),  # noqa: E712
                   any_valid_on_list_date=("expired_on_list_date", lambda s: (s == False).any()))  # noqa: E712
              .reset_index())
    loc = locate_laqshya(fac, osm)
    fac = fac.merge(loc, on="facility_uid", how="left")
    fac["location_level"] = np.where(fac.osm_lat.notna(), "osm_point",
                                     np.where(fac.district_key.notna(), "district_only", "none"))
    laq = laq.merge(fac[["facility_uid", "osm_match_id", "osm_match_name", "osm_lat", "osm_lon", "osm_match_score",
                         "osm_match_rule", "location_level"]], on="facility_uid", how="left")

    laq_cols = ["sno", "page", "state_raw", "district_raw", "facility_type", "facility_name", "department",
                "certification_status", "result_date", "validity_date", "expired_on_list_date",
                "expired_as_of_retrieval", "state_ut", "district_key", "district_match", "district_match_score",
                "district_match_detail", "facility_uid", "location_level", "osm_match_id", "osm_match_name",
                "osm_lat", "osm_lon", "osm_match_score", "osm_match_rule", "parse_repair",
                "certified_col", "conditional_col", "result_date_raw", "validity_date_raw"]
    laq_out = laq[laq_cols].copy()
    for c in ["expired_on_list_date", "expired_as_of_retrieval"]:
        laq_out[c] = laq_out[c].astype("boolean")
    laq_out["result_date"] = pd.to_datetime(laq_out.result_date)
    laq_out["validity_date"] = pd.to_datetime(laq_out.validity_date)
    laq_stats = {
        "pdf_rows": int(len(laq)),
        "facilities": int(fac.facility_uid.nunique()),
        "row_match_counts": laq.district_match.value_counts().to_dict(),
        "facility_match_counts": fac.assign(m=fac.facility_uid.map(laq.groupby("facility_uid").district_match.first()))
                                    .m.value_counts().to_dict(),
        "unmatched_rows": laq[laq.district_match == "unmatched"][["sno", "state_raw", "district_raw", "facility_name",
                                                                   "district_match_detail"]]
                          .rename(columns={"district_match_detail": "reason"}).to_dict("records"),
        "top3_states": fac.state_ut.value_counts().head(3).index.tolist(),
        "top3_state_share": float(fac.state_ut.value_counts().head(3).sum() / len(fac)),
        "certification_status": laq.certification_status.value_counts().to_dict(),
        "department": laq.department.fillna("not stated").value_counts().to_dict(),
        "facility_type": laq.facility_type.value_counts().to_dict(),
        "expired_rows_as_of_retrieval": int(laq.expired_as_of_retrieval.eq(True).sum()),
        "expired_rows_on_list_date": int(laq.expired_on_list_date.eq(True).sum()),
        "validity_unparsed": int(laq.validity_date.isna().sum()),
        "facilities_with_valid_certificate_as_of_retrieval": int(fac.any_valid_as_of_retrieval.sum()),
        "facilities_with_valid_certificate_on_list_date": int(fac.any_valid_on_list_date.sum()),
        "facilities_located_osm": int((fac.location_level == "osm_point").sum()),
        "facilities_district_only": int((fac.location_level == "district_only").sum()),
        "facilities_no_district": int((fac.location_level == "none").sum()),
        "parse_repairs": laq.parse_repair.replace("", None).dropna().value_counts().to_dict(),
        "districts_with_any": int(fac.district_key.nunique()),
    }
    write_parquet(laq_out, paths.silver_dir() / "ext_laqshya.parquet", {
        "source_url": LAQSHYA_URL, "source_file": rel_data(laq_pdf), "source_sha256": sha256(laq_pdf),
        "grain": "one row per row of the LaQshya master list PDF (facility x department)",
        "as_of_for_expiry": AS_OF, "list_date": LIST_DATE,
        "use": "D016: out-of-sample check and augmented-access layer only; never an input to the quadrants",
        "stats": laq_stats,
    })

    osm_meta_src = OVERPASS_URL if osm_src == "overpass" else HOTOSM_URL
    osm_stats = {
        **osm_info,
        "osm_source": osm_src,
        "amenity_counts": osm.amenity.fillna("none").value_counts().to_dict(),
        "healthcare_counts": osm.healthcare.fillna("none").value_counts().head(15).to_dict(),
        "likely_public": int(osm.likely_public.sum()),
        "likely_public_hospitals": int((osm.likely_public & osm.is_hospital).sum()),
        "public_fru_level_hospitals": int((osm.public_fru_level & osm.is_hospital).sum()),
        "public_rule_counts": osm[osm.likely_public].public_rule.value_counts().head(40).to_dict(),
        "nearest_po_km_quantiles": osm.nearest_po_km.quantile([0.5, 0.9, 0.99, 1.0]).round(2).to_dict(),
        "with_operator_type": int(osm.operator_type.notna().sum()),
        "with_emergency_tag": int(osm.emergency.notna().sum()),
        "with_obstetric_speciality": int(osm.obstetric_tag.sum()),
    }
    write_parquet(osm, paths.silver_dir() / "ext_osm_health.parquet", {
        "source_url": osm_meta_src, "license": LICENSES["osm"], "attribution": "(c) OpenStreetMap contributors",
        "grain": "one row per OSM node, way or relation tagged amenity=hospital|clinic|doctors or "
                 "healthcare=hospital|clinic|centre, inside the India bounding box",
        "district_rule": "district_key of the nearest geocoded, non-suspect post office (BallTree, haversine); "
                         "nearest_po_km is that distance",
        "public_rule": "operator:type first (government/public/... true, private/ngo/... false); else the first name "
                       "marker found in name, name:en, operator (see PUBLIC_NAME_RULES); a private marker (Pvt, Ltd, "
                       "Nursing Home) in the same field overrides",
        "use": "D016: out-of-sample check only",
        "stats": osm_stats,
    })

    # ---------------- population ----------------
    pop_info = None
    if not args.no_population:
        print("[4/6] census population")
        pop, pop_info = build_population(g)
        write_parquet(pop, paths.silver_dir() / "ext_district_population.parquet", {
            "source_url": CENSUS_URL, "license": LICENSES["census"],
            "grain": "one row per NFHS district_key",
            "method": "Census 2011 districts matched to NFHS districts by name (exact, then fuzzy, with a short "
                      "list of renamings). An NFHS district with no census district of its own is given a parent: "
                      "the census-matched district of the same state whose post offices are nearest to its own "
                      "(majority vote over its offices). Each census district's population is then split across "
                      "itself and the districts given to it, in proportion to post-office counts. Every split value "
                      "has is_estimate = true. Population is 2011, not current.",
            "stats": pop_info,
        })

    # ---------------- validation ----------------
    print("[5/6] validation")
    q, res = validation(g, laq, fac, osm)

    # ---------------- sources + report ----------------
    notes["Overpass API attempts"] = (
        "The national query in the script (OVERPASS_QUERY: India's boundary area, amenity=hospital|clinic|doctors or "
        "healthcare=hospital|clinic|centre, `out center tags`) was sent to " + OVERPASS_URL + " four times on "
        "2026-11-21 with backoff: 21:21 UTC answered 504 ('dispatcher timeout, server probably too busy'); 21:24 "
        "answered 502 ('at capacity', with a status page asking automated users to run their own instance); 21:27 "
        "failed to resolve the host; 21:33 returned HTTP 200 but the server closed the stream after 2,555,308 bytes "
        "(about 7,100 nodes, invalid JSON), kept as `osm/overpass_india_health.truncated.json` and not used. At 21:47 "
        "the status endpoint did not answer within 20 seconds. The script also holds a tiled fallback (eight bounding "
        "boxes, each limited to India's area, 30 s pauses, every tile cached); run it with `--osm-source overpass` when "
        "the server has capacity, and later runs pick the tiles up automatically.\n\n"
        + ("This run used the Overpass tiles." if osm_src == "overpass" else
           "This run used the HOT OpenStreetMap country export from HDX instead: the same OSM data (snapshot of "
           "2026-10-11, refreshed monthly), already filtered to health facilities, with fewer tags (name, name:en, "
           "amenity, healthcare, healthcare:speciality, operator:type, source; no operator, no emergency). Its "
           "features with amenity or healthcare outside the requested values (pharmacy, dentist and so on) are dropped "
           "by the script, so the feature set matches the Overpass query."))
    write_sources(sources, notes)
    figs = [] if args.no_figures else make_figures(q, res, laq_stats, osm)
    write_report(q, res, laq_stats, osm_stats, pop_info, osm_src, figs, res["qmeta"])
    print_summary(laq_stats, osm_stats, pop_info, res, time.time() - t0)
    return 0


# ==========================================================================
# figures and report (filled in below)
# ==========================================================================

QCOLOR = {"care_desert": "#c98500", "data_desert": "#3987e5", "cost_or_quality": "#d55181", "served": None}


def make_figures(q: pd.DataFrame, res: dict, laq_stats: dict, osm: pd.DataFrame) -> list[Path]:
    import matplotlib
    matplotlib.use("Agg")
    from vf import plotstyle as ps

    ps.use()
    QCOLOR["served"] = ps.CONTEXT
    figdir = paths.code_root() / "reports" / "figures"
    out = []
    order = ["data_desert", "care_desert", "cost_or_quality", "served"]
    labels = [QLABEL[k] + f" ({res['all']['laq_any']['by_quadrant'][k]['n']})" for k in order]
    cols = [QCOLOR[k] for k in order]
    src = ("Sources: NHSRC LaQshya master list (8 Apr 2026); OpenStreetMap health facilities "
           f"({'Overpass API' if osm.osm_source.iloc[0] == 'overpass' else 'HOT export on HDX'}), "
           "(c) OpenStreetMap contributors, ODbL; quadrants from gold/district_quadrants.parquet (emoc, rule).")

    # ---- figure 1: public capacity by quadrant (three small panels) ----
    fig, gs = ps.figure(5.6, "Data deserts have more public hospitals on the map, and nearer ones, than care deserts",
                        "Emergency obstetric care, rule quadrants. Bars: median district (share of districts in the "
                        "first panel). Lines: 95% interval, bootstrap over states.",
                        note=src, ncols=3, wspace=0.55, left_in=1.75, panel_title_lines=2)
    panels = [
        ("laq_any", "Share with a LaQshya-certified facility", lambda v: f"{100 * v:.0f}%"),
        ("osm_public_hospitals_per_1k_po", "OSM public hospitals per 1,000 post offices", lambda v: f"{v:.0f}"),
        ("osm_public_hosp_dist_km_p50", "Km to the nearest OSM public hospital", lambda v: f"{v:.1f}"),
    ]
    for j, (m, title, f) in enumerate(panels):
        ax = fig.add_subplot(gs[0, j])
        bq = res["all"][m]["by_quadrant"]
        vals = [bq[k]["median"] for k in order]
        ci = [bq[k]["ci95"] for k in order]
        top = max(max(c[1] for c in ci), max(vals))
        ps.hbars(ax, labels if j == 0 else [""] * 4, vals, colors=cols, label_values=False, xmax=top * 1.32)
        for i, ((lo, hi), v) in enumerate(zip(ci, vals)):
            ax.plot([lo, hi], [i, i], color=ps.INK, lw=ps.px(1.5), zorder=3, solid_capstyle="butt")
            ax.annotate(f(v), xy=(max(hi, v), i), xytext=(ps.px(6), 0), textcoords="offset points",
                        va="center", ha="left", fontsize=11, color=ps.INK_2)
        if j:
            ax.set_yticks([])
        ps.panel_title(ax, title, fontsize=12)
    out.append(ps.save(fig, figdir / "wp2b_01_public_capacity_by_quadrant.png"))

    # ---- figure 2: augmented access ----
    aug = res["augmented"]
    layers = [("aug_dist_km_p50", "Plus every LaQshya facility located in OSM"),
              ("aug_ot_dist_km_p50", "Plus LaQshya facilities with a certified maternity OT"),
              ("aug_valid_dist_km_p50", "Plus LaQshya facilities with a certificate valid on 21 Nov 2026"),
              ("aug_osm_public_dist_km_p50", "Upper bound: plus every OSM likely-public hospital")]
    cut = res["access_cut_km"]
    fig, gs = ps.figure(5.4, "Adding certified public facilities moves most data deserts and most care deserts "
                             "out of the far third",
                        f"Share of districts whose median post office would fall below the {cut:.1f} km cut "
                        "if these facilities were added to the scraped grade 2+ EmOC facilities",
                        note=src, left_in=4.1)
    ax = fig.add_subplot(gs[0, 0])
    y = np.arange(len(layers))
    h = 0.34
    for k, (qd, dy) in enumerate([("data_desert", -h / 2 - 0.02), ("care_desert", h / 2 + 0.02)]):
        v = [aug[c][qd]["leave_far_third"] / aug[c][qd]["far_original"] for c, _ in layers]
        bars = ax.barh(y + dy, v, height=h, color=QCOLOR[qd], linewidth=0)
        ps.register_bars(ax, bars, "h")
        for i, b in enumerate(bars):
            c = layers[i][0]
            ax.annotate(f"{100 * v[i]:.0f}%  ({aug[c][qd]['leave_far_third']} of {aug[c][qd]['far_original']})",
                        xy=(v[i], b.get_y() + b.get_height() / 2), xytext=(ps.px(6), 0),
                        textcoords="offset points", va="center", ha="left", fontsize=10.5, color=ps.INK_2)
    ax.set_yticks(y, [ps.wrap(t, 11, 3.6) for _, t in layers])
    ax.set_ylim(len(layers) - 0.5, -0.6)
    ax.set_xlim(0, 1.35)
    ax.set_xticks([])
    ax.tick_params(axis="y", length=0)
    ps.clean_axes(ax, keep=("left",))
    ps.legend(ax, [("Data desert", QCOLOR["data_desert"]), ("Care desert", QCOLOR["care_desert"])])
    out.append(ps.save(fig, figdir / "wp2b_02_augmented_access.png"))

    # ---- figure 3: OSM coverage by state ----
    st = pd.DataFrame(res["by_state"])
    st = st[st.pos >= 500].sort_values("osm_public_per_1k_po", ascending=False)
    ratio = st.osm_public_per_1k_po.max() / st.osm_public_per_1k_po.min()
    fig, gs = ps.figure(9.0, f"Mapped public hospitals per post office differ {ratio:.0f}-fold between states, "
                             "so OSM is uneven evidence",
                        "OSM likely-public hospitals per 1,000 post offices, states with 500+ post offices",
                        note=src + " A low value can mean few public hospitals or few mapped ones.", left_in=2.2)
    ax = fig.add_subplot(gs[0, 0])
    ps.hbars(ax, st.state_ut.tolist(), st.osm_public_per_1k_po.tolist(), color=ps.ORANGE,
             fmt=lambda v: f"{v:.0f}", fontsize=10)
    ax.tick_params(axis="y", labelsize=10)
    out.append(ps.save(fig, figdir / "wp2b_03_osm_public_coverage_by_state.png"))
    return out


def _q_table(r: dict, f) -> list[str]:
    rows = []
    for qd in ["data_desert", "care_desert", "cost_or_quality", "served"]:
        b = r["by_quadrant"][qd]
        rows.append(f"| {QLABEL[qd]} | {b['n']} | {f(b['median'])} | {f(b['ci95'][0])} to {f(b['ci95'][1])} | {f(b['mean'])} |")
    return ["| quadrant | districts | median | 95% interval (states) | mean |", "|---|---:|---:|---:|---:|"] + rows


def _pair_row(name: str, r: dict, f) -> str:
    p = r["pair"]
    return (f"| {name} | {f(r['by_quadrant']['data_desert']['median'])} | {f(r['by_quadrant']['care_desert']['median'])} | "
            f"{f(p['median_diff'])} ({f(p['median_diff_ci95'][0])} to {f(p['median_diff_ci95'][1])}) | "
            f"{fmt_p(p['mann_whitney_p'])} | {f(p['within_state_mean_diff'])} | {fmt_p(p['within_state_perm_p'])} | "
            f"{p['within_state_states']} |")


METRIC_LABEL = {
    "laq_any": ("Has a LaQshya-certified facility (share of districts; columns are shares, not medians)", lambda v: fmt(v, 2)),
    "laq_valid_any": ("Has one with a certificate valid on 21 Nov 2026 (share of districts)", lambda v: fmt(v, 2)),
    "laq_facilities": ("LaQshya facilities per district", lambda v: fmt(v, 1)),
    "laq_per_1k_po": ("LaQshya facilities per 1,000 post offices", lambda v: fmt(v, 1)),
    "laq_valid_per_1k_po": ("Valid-certificate facilities per 1,000 post offices", lambda v: fmt(v, 1)),
    "osm_public_hospitals": ("OSM likely-public hospitals per district", lambda v: fmt(v, 1)),
    "osm_public_hospitals_per_1k_po": ("OSM likely-public hospitals per 1,000 post offices", lambda v: fmt(v, 1)),
    "osm_public_hosp_dist_km_p50": ("Median km to nearest OSM likely-public hospital", lambda v: fmt(v, 1)),
    "osm_public_fru_dist_km_p50": ("Median km to nearest OSM public hospital named at FRU level", lambda v: fmt(v, 1)),
    "emoc_g2_dist_km_p50": ("Median km to nearest scraped grade 2+ EmOC facility (original)", lambda v: fmt(v, 1)),
}


def write_report(q: pd.DataFrame, res: dict, laq_stats: dict, osm_stats: dict, pop_info: Optional[dict],
                 osm_src: str, figs: list[Path], qmeta: dict) -> Path:
    A, S = res["all"], res.get("stable")
    aug = res["augmented"]
    cut = res["access_cut_km"]
    dd, cd = "data_desert", "care_desert"
    L = []
    w = L.append
    nq = {k: A["laq_any"]["by_quadrant"][k]["n"] for k in QUADS}
    w("# WP2b: outside public-facility data as a check on the rule quadrants")
    w("")
    w(f"Generated {utcnow()} by `{SCRIPT}`. Quadrants: `gold/district_quadrants.parquet` created "
      f"{qmeta.get('created_at_utc')} (stage `{qmeta.get('stage')}`, grades `{qmeta.get('grades_method')}`), "
      f"service line emoc, {sum(nq.values())} districts: {nq[dd]} data deserts, {nq[cd]} care deserts, "
      f"{nq['cost_or_quality']} cost or quality, {nq['served']} served. Far third: median post office "
      f"{cut:.1f} km or more from a grade 2+ facility. OpenStreetMap source: "
      f"{'national Overpass query' if osm_src == 'overpass' else 'HOT country export on HDX (Overpass was at capacity)'}. "
      "Per decision D016 none of this feeds the quadrants; it only checks them and adds a side-by-side access layer.")
    w("")
    # ---------------- findings ----------------
    o = A["osm_public_hosp_dist_km_p50"]
    oc = A["osm_public_hospitals_per_1k_po"]
    la = A["laq_any"]
    lp = A["laq_per_1k_po"]
    fr = A["osm_public_fru_dist_km_p50"]
    sh = res["share_with_laq"]
    shv = res["share_with_laq_valid"]
    w("## Findings")
    w("")
    w(f"1. **OpenStreetMap: data deserts have nearer public hospitals than care deserts, and the gap holds inside states.** "
      f"The median data-desert district has its typical post office {fmt(o['by_quadrant'][dd]['median'])} km from the "
      f"nearest likely-public hospital on OSM, against {fmt(o['by_quadrant'][cd]['median'])} km in care deserts: "
      f"{fmt(o['pair']['median_diff'])} km (95% interval {fmt(o['pair']['median_diff_ci95'][0])} to "
      f"{fmt(o['pair']['median_diff_ci95'][1])}, bootstrap over states; Mann-Whitney p = {fmt_p(o['pair']['mann_whitney_p'])}). "
      f"Comparing districts of the same state the gap is {fmt(o['pair']['within_state_mean_diff'])} km "
      f"(permutation p = {fmt_p(o['pair']['within_state_perm_p'])}, {o['pair']['within_state_states']} states with both). "
      f"Data deserts also have more of them: median {fmt(oc['by_quadrant'][dd]['median'], 0)} against "
      f"{fmt(oc['by_quadrant'][cd]['median'], 0)} per 1,000 post offices, but within states that count gap is "
      f"{fmt(oc['pair']['within_state_mean_diff'])} (p = {fmt_p(oc['pair']['within_state_perm_p'])}), so the count "
      "difference is mostly between states. The scraped table puts both groups equally far from grade 2+ care "
      f"(medians {fmt(A['emoc_g2_dist_km_p50']['by_quadrant'][dd]['median'], 0)} and "
      f"{fmt(A['emoc_g2_dist_km_p50']['by_quadrant'][cd]['median'], 0)} km), so this difference is information "
      "the quadrant rule did not have.")
    w(f"2. **LaQshya: data deserts more often have a certified public labour room or maternity OT, but only between states.** "
      f"{100 * sh[dd]:.0f}% of data deserts have at least one LaQshya-certified facility against {100 * sh[cd]:.0f}% of "
      f"care deserts (Mann-Whitney p = {fmt_p(la['pair']['mann_whitney_p'])}); per 1,000 post offices the medians are "
      f"{fmt(lp['by_quadrant'][dd]['median'])} and {fmt(lp['by_quadrant'][cd]['median'])} "
      f"(difference {fmt(lp['pair']['median_diff'])}, interval {fmt(lp['pair']['median_diff_ci95'][0])} to "
      f"{fmt(lp['pair']['median_diff_ci95'][1])}). Within states the share gap is "
      f"{fmt(la['pair']['within_state_mean_diff'], 2)} (p = {fmt_p(la['pair']['within_state_perm_p'])}): "
      f"it does not hold. LaQshya certification follows state programmes ({', '.join(laq_stats['top3_states'])} "
      f"alone hold {laq_stats['top3_state_share']:.0%} of the certified facilities), and the quadrants are also "
      "state-clustered. "
      f"Counting only certificates still valid on 21 Nov 2026: {100 * shv[dd]:.0f}% against {100 * shv[cd]:.0f}%.")
    if S:
        so, sl = S["osm_public_hosp_dist_km_p50"], S["laq_any"]
        sc = res["stable_counts"]
        w(f"3. **The stable cores agree, more strongly on OSM distance.** For the {sc.get(dd, 0)} data deserts and "
          f"{sc.get(cd, 0)} care deserts that keep their label under all 16 threshold pairs: OSM public-hospital distance "
          f"{fmt(so['by_quadrant'][dd]['median'])} against {fmt(so['by_quadrant'][cd]['median'])} km "
          f"(within states {fmt(so['pair']['within_state_mean_diff'])} km, p = {fmt_p(so['pair']['within_state_perm_p'])}, "
          f"{so['pair']['within_state_states']} states); share with a LaQshya facility "
          f"{100 * sl['by_quadrant'][dd]['mean']:.0f}% against {100 * sl['by_quadrant'][cd]['mean']:.0f}% "
          f"(interval for the difference {100 * sl['pair']['median_diff_ci95'][0]:.0f} to "
          f"{100 * sl['pair']['median_diff_ci95'][1]:.0f} points; within states {fmt(sl['pair']['within_state_mean_diff'], 2)}, "
          f"p = {fmt_p(sl['pair']['within_state_perm_p'])}). "
          "The brief's 69 and 46 came from an earlier quadrant run; the current file gives the counts above.")
    else:
        w("3. **Stable subset: skipped.** The quadrants file has no stability flag and it could not be reproduced from its meta.")
    a0 = aug["aug_dist_km_p50"]
    av = aug["aug_valid_dist_km_p50"]
    ao = aug["aug_ot_dist_km_p50"]
    ap = aug["aug_osm_public_dist_km_p50"]
    w(f"4. **Augmented access: the far third is mostly a blind spot for both groups, slightly more for data deserts.** "
      f"Adding the {laq_stats['facilities_located_osm']} LaQshya facilities that could be placed on an OSM point to the "
      f"scraped grade 2+ facilities, {a0[dd]['leave_far_third']} of {a0[dd]['far_original']} data deserts "
      f"({100 * a0[dd]['leave_far_third'] / a0[dd]['far_original']:.0f}%) and {a0[cd]['leave_far_third']} of "
      f"{a0[cd]['far_original']} care deserts ({100 * a0[cd]['leave_far_third'] / a0[cd]['far_original']:.0f}%) fall below "
      f"the {cut:.1f} km cut. Median distance drops from {fmt(a0[dd]['median_original'], 0)} to "
      f"{fmt(a0[dd]['median_augmented'], 0)} km in data deserts and from {fmt(a0[cd]['median_original'], 0)} to "
      f"{fmt(a0[cd]['median_augmented'], 0)} km in care deserts. With maternity-OT-certified facilities only: "
      f"{ao[dd]['leave_far_third']} and {ao[cd]['leave_far_third']}. With certificates valid on 21 Nov 2026 only: "
      f"{av[dd]['leave_far_third']} and {av[cd]['leave_far_third']}. The difference between the two groups in who leaves "
      f"is not significant within states (p = {fmt_p(res['left_far_compare']['pair']['within_state_perm_p'])}). "
      f"If every OSM likely-public hospital counted (an upper bound: many cannot do a C-section), "
      f"{ap[dd]['leave_far_third']} and {ap[cd]['leave_far_third']} would leave.")
    w(f"5. **What this does and does not support.** It supports the data-desert reading in one specific way: where the "
      "rule says \"far from care, good outcomes\", public hospitals are in fact nearer than where it says \"far from care, "
      "poor outcomes\", within the same state. It does not show that care deserts are far from public facilities: once "
      f"certified public facilities are added, most care deserts leave the far third too (median {fmt(a0[cd]['median_augmented'], 0)} km "
      f"against {fmt(a0[dd]['median_augmented'], 0)} km for data deserts). On this evidence the care-desert label rests partly on "
      "distance the scraped table cannot see, and poor outcomes there point at use, cost or quality as much as at "
      "distance. LaQshya presence does not separate the two groups within states.")
    w("")
    # ---------------- tables ----------------
    w("## The test in numbers")
    w("")
    w("Data desert against care desert, all districts. Median columns are medians over districts; the difference "
      "interval is a 95% bootstrap over states (2,000 draws, whole states resampled); within-state is the "
      "weighted mean over states of (data-desert mean minus care-desert mean), weights n_a n_b / (n_a + n_b), with a "
      "permutation p (10,000 shuffles of labels inside each state).")
    w("")
    hdr = ["| measure | data desert | care desert | difference (95% interval) | Mann-Whitney p | within-state diff | p | states |",
           "|---|---:|---:|---:|---:|---:|---:|---:|"]
    L.extend(hdr)
    for m, (lab, f) in METRIC_LABEL.items():
        if m in A:
            w(_pair_row(lab, A[m], f))
    w("")
    if S:
        w(f"Stable cores only ({res['stable_counts'].get(dd, 0)} data deserts, {res['stable_counts'].get(cd, 0)} care deserts):")
        w("")
        L.extend(hdr)
        for m, (lab, f) in METRIC_LABEL.items():
            if m in S:
                w(_pair_row(lab, S[m], f))
        w("")
    for m in ["osm_public_hosp_dist_km_p50", "osm_public_hospitals_per_1k_po", "laq_per_1k_po"]:
        w(f"All four quadrants: {METRIC_LABEL[m][0]}.")
        w("")
        L.extend(_q_table(A[m], METRIC_LABEL[m][1]))
        w("")
    w("Share of districts with at least one LaQshya-certified facility: " +
      ", ".join(f"{QLABEL[k]} {100 * res['share_with_laq'][k]:.0f}%" for k in QUADS) +
      ". With a certificate valid on 21 Nov 2026: " +
      ", ".join(f"{QLABEL[k]} {100 * res['share_with_laq_valid'][k]:.0f}%" for k in QUADS) + ".")
    w("")
    w(f"Augmented access (districts in the far third, {cut:.1f} km, that would leave it):")
    w("")
    w("| layer added to scraped grade 2+ EmOC | data deserts leaving | care deserts leaving | median km, data deserts (before, after) | median km, care deserts (before, after) |" +
      (" stable data deserts | stable care deserts |" if S else ""))
    w("|---|---:|---:|---:|---:|" + ("---:|---:|" if S else ""))
    names = {"aug_dist_km_p50": "all LaQshya facilities located in OSM",
             "aug_ot_dist_km_p50": "LaQshya facilities with a maternity OT certificate",
             "aug_valid_dist_km_p50": "LaQshya facilities with a certificate valid on 21 Nov 2026",
             "aug_osm_public_dist_km_p50": "every OSM likely-public hospital (upper bound)"}
    for c, nm in names.items():
        r = aug[c]
        line = (f"| {nm} | {r[dd]['leave_far_third']} of {r[dd]['far_original']} | {r[cd]['leave_far_third']} of "
                f"{r[cd]['far_original']} | {fmt(r[dd]['median_original'], 0)}, {fmt(r[dd]['median_augmented'], 0)} | "
                f"{fmt(r[cd]['median_original'], 0)}, {fmt(r[cd]['median_augmented'], 0)} |")
        if S:
            line += f" {r[dd]['stable_leave_far_third']} of {r[dd]['stable_n']} | {r[cd]['stable_leave_far_third']} of {r[cd]['stable_n']} |"
        w(line)
    w("")
    w(f"Districts in each quadrant with at least one LaQshya facility mapped to them: " +
      ", ".join(f"{QLABEL[k]} {v}" for k, v in res["laq_located_in_far"].items()) +
      f". Spearman correlation across all districts between the original grade 2+ distance and the OSM public-hospital "
      f"distance: rho = {res['spearman_emoc_vs_osm_public']['rho']:.2f} (p = {fmt_p(res['spearman_emoc_vs_osm_public']['p'])}).")
    w("")
    if figs:
        w("Figures:")
        w("")
        for fpath in figs:
            w(f"![{fpath.stem}](figures/{fpath.name})")
        w("")
    # ---------------- sources and match rates ----------------
    ls = laq_stats
    w("## Sources and match rates")
    w("")
    w(f"**LaQshya master list** (NHSRC, dated 8 Apr 2026, 70 pages). Parsed {ls['pdf_rows']:,} rows (serial numbers 1 to "
      f"{ls['pdf_rows']:,}, none missing) into {ls['facilities']:,} facilities. Rows are facility by department: "
      f"{ls['department'].get('Labour Room', 0):,} labour room, {ls['department'].get('Maternity OT', 0):,} maternity OT, "
      f"{ls['department'].get('not stated', 0)} with no department (a block of 2026 results at the end of the file). "
      f"Status: {ls['certification_status'].get('certified', 0):,} certified, "
      f"{ls['certification_status'].get('conditionally_certified', 0):,} conditionally certified. "
      f"Facility types: " + ", ".join(f"{k} {v}" for k, v in ls["facility_type"].items()) + ". "
      f"Text overflowing a cell was repaired in {sum(ls['parse_repairs'].values())} rows ("
      + ", ".join(f"{k} {v}" for k, v in ls["parse_repairs"].items()) + ").")
    w("")
    w(f"Expired certificates are kept and flagged, not dropped: {ls['expired_rows_as_of_retrieval']:,} of "
      f"{ls['pdf_rows']:,} rows had a validity date before 21 Nov 2026 (retrieval), {ls['expired_rows_on_list_date']:,} "
      f"before 8 Apr 2026 (the list date). {ls['facilities_with_valid_certificate_as_of_retrieval']} facilities hold at "
      f"least one certificate valid on 21 Nov 2026 ({ls['facilities_with_valid_certificate_on_list_date']} on the list date).")
    w("")
    rm = ls["row_match_counts"]
    tot = sum(rm.values())
    w("District matching (rows): " + ", ".join(f"{k} {v:,} ({100 * v / tot:.1f}%)" for k, v in rm.items()) +
      ". Exact means equal, after normalising, to an NFHS district name or to a PIN-directory district name that the "
      "WP2 crosswalk maps to an NFHS district (this resolves post-2019 districts such as Tirupati or Eluru to their "
      "NFHS parent). Fuzzy means a name score of 85+ within the state, 5 points clear of any other district, same first "
      "letter unless the match is by word order or containment. Place means the district text, or a town in the facility "
      "name, equals the name of head or sub post offices that sit 70%+ in one district. Manual is a short reviewed list "
      "in the script (towns named as districts, misspellings).")
    w("")
    w("Unmatched rows:")
    w("")
    w("| S.No | state | district text | facility | reason |")
    w("|---:|---|---|---|---|")
    for r in ls["unmatched_rows"]:
        w(f"| {r['sno']} | {r['state_raw']} | {r['district_raw'] or '(blank)'} | {r['facility_name']} | {r.get('reason', '')} |")
    w("")
    w(f"Located on the map: {ls['facilities_located_osm']} of {ls['facilities']} facilities "
      f"({100 * ls['facilities_located_osm'] / ls['facilities']:.0f}%) matched an OSM feature in the same district by "
      f"place-name tokens (score 90+, OSM feature likely public or named with the facility's type; AYUSH, dispensary and "
      f"similar names excluded). {ls['facilities_district_only']} are located only to their district and are left out of "
      f"every distance; {ls['facilities_no_district']} have no district.")
    w("")
    os_ = osm_stats
    w(f"**OpenStreetMap** ({'Overpass API' if osm_src == 'overpass' else 'HOT export'}). {os_['kept']:,} features tagged "
      f"amenity=hospital, clinic or doctors, or healthcare=hospital, clinic or centre, inside India "
      f"({os_['outside_india_bbox_or_no_coords']} dropped outside the India box or without coordinates). "
      f"Likely public: {os_['likely_public']:,}, of which {os_['likely_public_hospitals']:,} are hospitals "
      f"(amenity or healthcare = hospital) and {os_['public_fru_level_hospitals']:,} of those are named at first-referral "
      f"level (district, sub-district, sub-divisional, civil, taluk, area, sadar, rural or referral hospital, CHC or "
      f"medical college, and not a PHC). {os_['with_operator_type']:,} features carry operator:type. "
      "Rule counts (likely public):")
    w("")
    w("| rule | features |")
    w("|---|---:|")
    for k, v in list(os_["public_rule_counts"].items())[:20]:
        w(f"| {k} | {v:,} |")
    w("")
    qn = os_["nearest_po_km_quantiles"]
    w(f"District: nearest geocoded, non-suspect post office. Distance to it: median {qn.get(0.5, qn.get('0.5'))} km, "
      f"90th percentile {qn.get(0.9, qn.get('0.9'))} km, maximum {qn.get(1.0, qn.get('1.0'))} km.")
    w("")
    if pop_info:
        mc = pop_info["method_counts"]
        w(f"**Census 2011 population** (Hugging Face indiaset/census-2011, CC-BY-4.0). {pop_info['census_districts']} census "
          f"districts: " + ", ".join(f"{k} {v}" for k, v in pop_info["census_match_counts"].items()) +
          f" by name. Unmatched: " + "; ".join(f"{r['census_district']} ({r['census_state']}, {r['pop_total']:,})"
                                              for r in pop_info["census_unmatched"]) +
          f". NFHS districts: " + ", ".join(f"{k} {v}" for k, v in mc.items()) +
          f"; {pop_info['estimates']} values are estimates (is_estimate = true). The parent of each of the "
          f"{pop_info['nfhs_districts_new_since_2011_with_parent']} NFHS districts created after 2011 was chosen by a "
          f"vote of nearest post offices; {len(pop_info['parents_low_vote_share_lt_0_6'])} votes were under 60%, and "
          "spot checks found wrong parents (Hojai was given Udalguri instead of Nagaon; West Karbi Anglong was given "
          "Cachar instead of Karbi Anglong; Suryapet was given Khammam instead of Nalgonda; Siddipet was given Karimnagar, "
          "though most of it came from Medak). The vote fails where the PIN directory files offices under the wrong "
          "district, and post-office counts are a weak proxy for people. Treat every split value as rough until a reviewed "
          f"parent list replaces the vote; the {len(pop_info['census_name_unsplit'])} unsplit values are Census 2011 "
          "totals (or sums of them). The table was not finished "
          "to that standard within the time box and is not used in the validation, which normalises by post offices.")
        w("")
    # ---------------- method ----------------
    w("## Method")
    w("")
    w("- LaQshya PDF: pdfplumber finds the table grid; each row's characters are read in content-stream order and cut "
      "into text runs, and a whole run goes to the cell where it starts. Long names overflow into the next column, so "
      "position alone interleaves two cells (\"Vishakhapatna\" + \"mCHC\"). Remaining overflows (state into district, "
      "district into type, department into name) are undone with the closed lists of states, types and departments.")
    w("- A facility is one id per district and core name (the name without generic words such as hospital, district, "
      "CHC, govt), so the labour-room and OT rows of one hospital count once; type must agree (DH and SDH may mix).")
    w("- OSM likely-public flag, in order: operator:type government, public, state, military, municipal or national is "
      "public; private, NGO, religious and similar is not; otherwise the first name marker found in name, name:en or "
      "operator (Government, Govt, District Hospital, Sub District Hospital, Sub Divisional Hospital, Community Health "
      "Centre, CHC, Primary Health Centre, PHC, Civil Hospital, Medical College, and the additions Taluk, Area, Sadar, "
      "Rural and Referral Hospital), unless the same field says Pvt, Private, Ltd or Nursing Home. `public_rule` records "
      "which rule fired for every row.")
    w("- Distances: straight line, BallTree haversine, from every geocoded, non-suspect post office with a district "
      "(WP5's rollup: offices stacked on one placeholder point count once per district), to the nearest target "
      "anywhere in India. District value = median over its offices. Augmented distance per office = min(original grade 2+ "
      "distance from `post_office_access`, distance to the nearest located LaQshya facility), which is the nearest of "
      "the union.")
    w(f"- Check: the original grade 2+ medians recomputed this way match `district_access` (emoc, min_grade 2) to within "
      f"{res['emoc_g2_recompute_max_abs_diff_km']:.3f} km in every district.")
    w("- Stability: same label under all 16 threshold pairs, recomputed from the cut values in the quadrants meta; "
      "used only because it reproduces the meta's published counts exactly.")
    w("")
    w("## Caveats")
    w("")
    w("- OSM completeness varies by state (figure 3). A district with few mapped public hospitals may have many unmapped. "
      "The within-state comparisons exist to blunt this, not remove it.")
    w("- The public-name rule is crude. \"Medical College\" also matches private colleges; a government hospital named "
      "after a person without a marker is missed; PHCs tagged as hospitals count as hospitals. The first-referral subset "
      f"(no PHC names) does not separate the groups overall (medians {fmt(fr['by_quadrant'][dd]['median'])} and "
      f"{fmt(fr['by_quadrant'][cd]['median'])} km) but does within states ({fmt(fr['pair']['within_state_mean_diff'])} km, "
      f"p = {fmt_p(fr['pair']['within_state_perm_p'])}), so part of the overall OSM gap comes from PHCs.")
    w("- LaQshya lists certified facilities only. Absence means not certified, not no care. Certification follows state "
      "programme intensity, which is why the between-state gap disappears within states.")
    w(f"- About {100 - round(100 * ls['facilities_located_osm'] / ls['facilities'])}% of LaQshya facilities could not be "
      "placed on an OSM point and are excluded from augmented distances, so the augmented layer understates access; a "
      "few name matches may land on a same-town facility rather than the certified one, an error of a few km.")
    w(f"- Upstream gold tables are being regenerated by other work packages. These numbers use the quadrants file "
      f"created {qmeta.get('created_at_utc')}; rerun the script after any upstream change.")
    w("- What the OSM test can and cannot tell apart: nearer public hospitals in data deserts is what a blind facility "
      "table predicts, and it is also what one expects if nearby public hospitals raise institutional births. Both "
      "readings say the scraped distance understates access in data deserts; neither is ruled out here.")
    w("- Straight-line distance, not travel time.")
    w("")
    w("## Files")
    w("")
    w("- `data/silver/ext_laqshya.parquet`, `data/silver/ext_osm_health.parquet`, "
      "`data/silver/ext_district_population.parquet` (each with `.meta.json`)")
    w("- `data/external/SOURCES.md` and the raw downloads beside it (with `.source.json` sidecars)")
    w("- `reports/figures/wp2b_*.png`")
    body = "\n".join(L) + "\n"
    path = paths.code_root() / "reports" / "wp2b_external_validation.md"
    write_atomic(path, lambda p: p.write_text(body))
    return path


def print_summary(laq_stats, osm_stats, pop_info, res, elapsed):
    A = res["all"]
    print(f"LaQshya: {laq_stats['pdf_rows']} rows, {laq_stats['facilities']} facilities, row matches "
          f"{laq_stats['row_match_counts']}, located in OSM {laq_stats['facilities_located_osm']}")
    print(f"OSM: {osm_stats['kept']} features, {osm_stats['likely_public_hospitals']} likely-public hospitals "
          f"({osm_stats['osm_source']})")
    if pop_info:
        print(f"Population: {pop_info['method_counts']}")
    for m in ["osm_public_hosp_dist_km_p50", "laq_any", "laq_per_1k_po"]:
        p = A[m]["pair"]
        print(f"  {m}: DD {A[m]['by_quadrant']['data_desert']['median']:.2f} vs CD "
              f"{A[m]['by_quadrant']['care_desert']['median']:.2f}; within-state {p['within_state_mean_diff']:.3f} "
              f"(p {p['within_state_perm_p']:.3g})")
    a = res["augmented"]["aug_dist_km_p50"]
    print(f"  augmented: DD {a['data_desert']['leave_far_third']}/{a['data_desert']['far_original']} leave far third, "
          f"CD {a['care_desert']['leave_far_third']}/{a['care_desert']['far_original']}")
    print(f"done in {elapsed:.0f}s")


if __name__ == "__main__":
    sys.exit(main())
