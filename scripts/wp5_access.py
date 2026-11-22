"""WP5: access scores.

For every geocoded post office, the straight-line distance to the nearest
facility at each minimum evidence grade (1 claimed, 2 supported,
3 corroborated) for each service line, then rolled up to NFHS districts.

Inputs (vf.paths folders):
  silver/facilities.parquet        facility coordinates (coord_valid; WP1 has already
                                   checked them against each facility's PIN, moved
                                   or nulled the misplaced ones, see coord_check)
  silver/post_offices.parquet      post office coordinates, district_key
  gold/capability_grades.parquet   grades (regex baseline from WP3, or WP4's);
                                   any grades file can be passed with --grades

Outputs:
  gold/post_office_access.parquet  one row per geocoded post office
  gold/district_access.parquet     one row per district x service line x min grade
  gold/access_summary_<method>.json  national headline numbers for this grades
                                   method, kept so a later rerun can print how
                                   they moved (step 6)
  each with a <output>.meta.json sidecar
  reports/figures/wp5_*_<method>.png  (skip with --no-figures)

Method:
  * Facilities: coord_valid only. A facility qualifies for (line, k) when its
    grade on that line is >= k. Graded facilities without coordinates are
    counted and reported, not silently dropped, split into coordinates missing
    in the scraped row and coordinates set to null by WP1's PIN check.
  * Nearest facility: scikit-learn BallTree, haversine metric, inputs in
    radians, distance times 6371 km. Distances are straight lines.
  * Coordinate check: an office is coord_suspect when it lies more than 200 km
    from its district's centre. The centre is the median point of the
    district's head and sub offices (office_type HO or PO), which are placed
    reliably; the median of all its offices is used when a district has fewer
    than 3 of them. Branch offices are often geocoded to a same-named village
    in another state, and in some districts (Vadodara, Bihar's Aurangabad)
    more than half are, so a median over all offices lands in the wrong place.
    Offices without a district_key are grouped by their PIN-directory state and
    district instead; the ones with state 'NA' are not assessed.
    Suspect offices stay in post_office_access (flagged, column coord_suspect),
    and are left out of district_access and the national summary. Consumers of
    post_office_access must filter on coord_suspect the same way (contract).
  * Placeholder points: an office is coord_placeholder when its exact point is
    shared by 10 or more geocoded offices from 3 or more PINs
    (vf.geo.placeholder_points). Different PINs are different delivery areas,
    so such a point (usually the district town) is not where those offices are:
    in Bihar's Samastipur 358 of 376 offices, from 35 PINs, sit on one point.
    Every aggregate (district_access, the national summary, the figures) counts
    each placeholder point once per district instead of once per office; the
    offices' own rows keep the point's distances. n_post_offices_placeholder
    and n_placeholder_points in district_access say how much a district rests
    on such points.
  * Facility district (for n_facilities_in_district): majority district_key of
    the 5 nearest non-suspect post offices, so it follows the coordinates the
    distances use.

Run:
  .venv/bin/python scripts/wp5_access.py
  .venv/bin/python scripts/wp5_access.py --grades path/to/capability_grades.parquet
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from sklearn.neighbors import BallTree

from vf import geo, paths

SCRIPT = "scripts/wp5_access.py"
VERSION = "wp5-v2"

EARTH_RADIUS_KM = 6371.0
SERVICE_LINES = ("emoc", "dialysis", "icu")
MIN_GRADES = (1, 2, 3)
GRADE_LABELS = {0: "none", 1: "claimed", 2: "supported", 3: "corroborated"}
RADII_KM = (30, 60)

SUSPECT_KM = 200.0           # office further than this from its district centre
CENTER_MIN_TOWN_OFFICES = 3  # head/sub offices needed to use them as the centre
FAC_DISTRICT_K = 5           # nearest post offices voting on a facility's district

SPOT_CHECK_SEED = 20261121
SPOT_CHECK_TOL_KM = 1e-6


def dist_col(line: str, k: int) -> str:
    return f"dist_km_{line}_g{k}"


def near_col(line: str, k: int) -> str:
    return f"nearest_facility_{line}_g{k}"


PO_ACCESS_SCHEMA = pa.schema(
    [
        ("po_id", pa.int64()),
        ("district_key", pa.string()),
        ("lat", pa.float64()),
        ("lon", pa.float64()),
        *[
            f
            for line in SERVICE_LINES
            for k in MIN_GRADES
            for f in ((dist_col(line, k), pa.float64()), (near_col(line, k), pa.string()))
        ],
        ("coord_suspect", pa.bool_()),
        ("coord_placeholder", pa.bool_()),
    ]
)

DISTRICT_ACCESS_SCHEMA = pa.schema(
    [
        ("district_key", pa.string()),
        ("service_line", pa.string()),
        ("min_grade", pa.int64()),
        ("n_post_offices", pa.int64()),
        ("dist_km_p50", pa.float64()),
        ("dist_km_p75", pa.float64()),
        ("dist_km_p90", pa.float64()),
        ("share_within_30km", pa.float64()),
        ("share_within_60km", pa.float64()),
        ("n_facilities_in_district", pa.int64()),
        ("n_post_offices_excluded", pa.int64()),
        ("n_post_offices_placeholder", pa.int64()),
        ("n_placeholder_points", pa.int64()),
    ]
)


# --------------------------------------------------------------------------
# inputs
# --------------------------------------------------------------------------

def load_inputs(grades_path: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    silver = paths.silver_dir()
    need = [silver / "facilities.parquet", silver / "post_offices.parquet", grades_path]
    missing = [str(p) for p in need if not p.exists()]
    if missing:
        raise FileNotFoundError("missing input(s): " + ", ".join(missing))
    fac = pd.read_parquet(
        need[0], columns=["facility_id", "name", "row_status", "lat", "lon", "coord_valid", "coord_check"]
    )
    po = pd.read_parquet(
        need[1],
        columns=["po_id", "office_name", "office_type", "pin", "state_pin", "district_pin",
                 "lat", "lon", "coord_valid", "district_key"],
    )
    grades = pd.read_parquet(
        grades_path, columns=["facility_id", "service_line", "grade", "method", "recipe_version"]
    )
    return fac, po, grades


def validate_grades(grades: pd.DataFrame, fac: pd.DataFrame) -> dict:
    """Fail loudly on a grades file this script cannot interpret."""
    lines = set(grades["service_line"].unique())
    if lines != set(SERVICE_LINES):
        raise ValueError(f"grades service lines {sorted(lines)} != {list(SERVICE_LINES)}")
    if grades.duplicated(["facility_id", "service_line"]).any():
        raise ValueError("grades has duplicate (facility_id, service_line) rows")
    if not grades["grade"].between(0, 3).all():
        raise ValueError("grades outside 0..3")
    unknown = ~grades["facility_id"].isin(fac["facility_id"])
    if unknown.any():
        raise ValueError(f"{int(unknown.sum())} graded facility ids are not in silver/facilities")
    methods = grades["method"].dropna().unique().tolist()
    versions = grades["recipe_version"].dropna().unique().tolist()
    if len(methods) != 1:
        raise ValueError(f"expected one grades method, got {methods}")
    return {"method": str(methods[0]), "recipe_version": ",".join(map(str, sorted(versions)))}


# --------------------------------------------------------------------------
# geometry
# --------------------------------------------------------------------------

def haversine_km(lat1, lon1, lat2, lon2):
    """Vectorized great-circle distance in km (numpy), degrees in."""
    lat1, lon1, lat2, lon2 = (np.radians(np.asarray(x, dtype="float64")) for x in (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def manual_haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Independent scalar haversine (math module only), for the spot check."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(min(1.0, h)))


def nearest(fac_lat, fac_lon, fac_ids, q_lat, q_lon) -> tuple[np.ndarray, np.ndarray]:
    """Nearest facility to each query point: (distance km, facility id).

    BallTree with the haversine metric takes [lat, lon] in radians and returns
    central angles; times the earth radius gives km. An empty facility set
    gives NaN distances and None ids.
    """
    n = len(q_lat)
    if len(fac_ids) == 0:
        return np.full(n, np.nan), np.full(n, None, dtype=object)
    tree = BallTree(np.radians(np.column_stack([fac_lat, fac_lon])), metric="haversine")
    d, i = tree.query(np.radians(np.column_stack([q_lat, q_lon])), k=1)
    return d[:, 0] * EARTH_RADIUS_KM, np.asarray(fac_ids, dtype=object)[i[:, 0]]


def coord_check(po: pd.DataFrame) -> pd.DataFrame:
    """Distance from each geocoded office to its district centre, and the flag.

    Returns a frame indexed like `po` with columns check_group, center_km,
    coord_suspect. See the module docstring for the rule.
    """
    key = po["district_key"].astype(object)
    state = po["state_pin"].astype(object).fillna("NA").astype(str)
    alt = "PIN:" + state + "|" + po["district_pin"].astype(object).fillna("NA").astype(str)
    use_alt = key.isna() & (state.str.strip().str.upper() != "NA")
    group = key.where(key.notna(), alt.where(use_alt))

    has = group.notna()
    g = group[has]
    town = (po["office_type"] != "BO") & has
    all_med = po.loc[has, ["lat", "lon"]].groupby(g).median()
    town_med = po.loc[town, ["lat", "lon"]].groupby(group[town]).median()
    town_n = group[town].value_counts().reindex(all_med.index, fill_value=0)
    center = all_med.copy()
    use_town = town_n[town_n >= CENTER_MIN_TOWN_OFFICES].index
    center.loc[use_town] = town_med.loc[use_town]

    c = center.reindex(group.to_numpy())
    d = haversine_km(po["lat"].to_numpy(), po["lon"].to_numpy(), c["lat"].to_numpy(), c["lon"].to_numpy())
    d = pd.Series(d, index=po.index)
    out = pd.DataFrame({"check_group": group, "center_km": d}, index=po.index)
    out["coord_suspect"] = (d > SUSPECT_KM).fillna(False).astype(bool)
    out.attrs["center_source"] = {
        "groups": int(len(center)),
        "groups_centred_on_head_and_sub_offices": int(len(use_town)),
        "groups_centred_on_all_offices": int(len(center) - len(use_town)),
    }
    return out


def assign_facility_districts(fac_geo: pd.DataFrame, ref: pd.DataFrame) -> pd.DataFrame:
    """Majority district_key of the k nearest reference post offices.

    Ties go to the district of the nearer office (Counter keeps first-seen
    order among equal counts, and neighbours arrive nearest first).
    """
    tree = BallTree(np.radians(ref[["lat", "lon"]].to_numpy()), metric="haversine")
    d, i = tree.query(np.radians(fac_geo[["lat", "lon"]].to_numpy()), k=FAC_DISTRICT_K)
    keys = ref["district_key"].to_numpy(dtype=object)[i]
    votes = [Counter(row).most_common(1)[0] for row in keys]
    return pd.DataFrame(
        {
            "facility_id": fac_geo["facility_id"].to_numpy(),
            "district_key": [v[0] for v in votes],
            "district_votes": [int(v[1]) for v in votes],
            "nearest_po_km": d[:, 0] * EARTH_RADIUS_KM,
        }
    )


# --------------------------------------------------------------------------
# build
# --------------------------------------------------------------------------

def qualifying_sets(grades: pd.DataFrame, fac: pd.DataFrame) -> dict:
    """{(line, k): DataFrame of qualifying facilities with coordinates}, sorted
    by facility_id so the trees (and tie-breaks) do not depend on file order."""
    g = grades.merge(fac[["facility_id", "lat", "lon", "coord_valid"]], on="facility_id", how="left")
    out = {}
    for line in SERVICE_LINES:
        gl = g[g["service_line"] == line]
        for k in MIN_GRADES:
            q = gl[(gl["grade"] >= k) & gl["coord_valid"]]
            out[(line, k)] = q.sort_values("facility_id").reset_index(drop=True)[
                ["facility_id", "lat", "lon", "grade"]
            ]
    return out


def facilities_lost(grades: pd.DataFrame, fac: pd.DataFrame) -> dict:
    """Graded facilities that drop out for lack of coordinates, and those WP1 moved."""
    g = grades.merge(fac[["facility_id", "row_status", "coord_valid", "coord_check"]], on="facility_id",
                     how="left")
    out = {}
    for line in SERVICE_LINES:
        gl = g[g["service_line"] == line]
        out[line] = {
            str(k): {
                "graded": int((gl["grade"] >= k).sum()),
                "with_coords": int(((gl["grade"] >= k) & gl["coord_valid"]).sum()),
                "lost_no_coords": int(((gl["grade"] >= k) & ~gl["coord_valid"]).sum()),
                "lost_truncated_rows": int(
                    ((gl["grade"] >= k) & ~gl["coord_valid"] & (gl["row_status"] == "truncated")).sum()
                ),
                "lost_excluded_by_pin_check": int(
                    ((gl["grade"] >= k) & (gl["coord_check"] == "excluded_unresolved")).sum()
                ),
                "moved_to_pin": int(((gl["grade"] >= k) & (gl["coord_check"] == "moved_to_pin")).sum()),
            }
            for k in MIN_GRADES
        }
    return out


def build_po_access(po_geo: pd.DataFrame, qsets: dict) -> pd.DataFrame:
    out = po_geo[["po_id", "district_key", "lat", "lon"]].copy()
    lat, lon = po_geo["lat"].to_numpy(), po_geo["lon"].to_numpy()
    for line in SERVICE_LINES:
        for k in MIN_GRADES:
            q = qsets[(line, k)]
            d, ids = nearest(q["lat"].to_numpy(), q["lon"].to_numpy(), q["facility_id"].to_numpy(), lat, lon)
            out[dist_col(line, k)] = d
            out[near_col(line, k)] = ids
    out["coord_suspect"] = po_geo["coord_suspect"].to_numpy()
    out["coord_placeholder"] = po_geo["coord_placeholder"].to_numpy()
    return out[PO_ACCESS_SCHEMA.names]


def rollup_offices(po_access: pd.DataFrame) -> pd.DataFrame:
    """The offices behind every aggregate: not coord_suspect, and offices on a
    placeholder point (coord_placeholder) kept once per district and point.

    All offices on one point have the same distances, so keeping one of them is
    the same as giving each a weight of 1 / (the district's offices on that
    point). Offices without a district_key are grouped together.
    """
    used = po_access[~po_access["coord_suspect"]]
    key = pd.DataFrame({"k": used["district_key"].fillna(""), "lat": used["lat"], "lon": used["lon"]})
    repeat = used["coord_placeholder"] & key.duplicated()
    return used[~repeat]


def build_district_access(po_access: pd.DataFrame, fac_district: pd.DataFrame, qsets: dict) -> pd.DataFrame:
    keyed = po_access[po_access["district_key"].notna()]
    used = keyed[~keyed["coord_suspect"]]
    roll = rollup_offices(keyed)
    districts = sorted(set(keyed["district_key"]))
    excluded = keyed[keyed["coord_suspect"]].groupby("district_key").size()
    n_used = used.groupby("district_key").size().reindex(districts).fillna(0).astype("int64")
    ph = used[used["coord_placeholder"]]
    n_ph = ph.groupby("district_key").size().reindex(districts).fillna(0).astype("int64")
    n_ph_points = (ph.drop_duplicates(["district_key", "lat", "lon"]).groupby("district_key").size()
                   .reindex(districts).fillna(0).astype("int64"))
    fd = fac_district.set_index("facility_id")["district_key"]
    rows = []
    for line in SERVICE_LINES:
        for k in MIN_GRADES:
            col = dist_col(line, k)
            grp = roll.groupby("district_key")[col]
            q = grp.quantile([0.5, 0.75, 0.9]).unstack()
            n_fac = fd.reindex(qsets[(line, k)]["facility_id"]).value_counts()
            t = pd.DataFrame(index=pd.Index(districts, name="district_key"))
            t["service_line"] = line
            t["min_grade"] = k
            t["n_post_offices"] = n_used
            t["dist_km_p50"] = q[0.5].reindex(districts)
            t["dist_km_p75"] = q[0.75].reindex(districts)
            t["dist_km_p90"] = q[0.9].reindex(districts)
            for r in RADII_KM:
                t[f"share_within_{r}km"] = (roll[col] <= r).groupby(roll["district_key"]).mean().reindex(districts)
            t["n_facilities_in_district"] = n_fac.reindex(districts).fillna(0).astype("int64")
            t["n_post_offices_excluded"] = excluded.reindex(districts).fillna(0).astype("int64")
            t["n_post_offices_placeholder"] = n_ph
            t["n_placeholder_points"] = n_ph_points
            rows.append(t.reset_index())
    out = pd.concat(rows, ignore_index=True)
    order = {l: i for i, l in enumerate(SERVICE_LINES)}
    out = out.sort_values(
        ["district_key", "service_line", "min_grade"], key=lambda s: s.map(order) if s.name == "service_line" else s
    ).reset_index(drop=True)
    return out[DISTRICT_ACCESS_SCHEMA.names]


def national_summary(po_access: pd.DataFrame, lost: dict) -> list[dict]:
    """Headline numbers over rollup_offices (placeholder points once per district),
    plus the same with every used office counted and with suspect offices too."""
    used = po_access[~po_access["coord_suspect"]]
    roll = rollup_offices(po_access)
    recs = []
    for line in SERVICE_LINES:
        for k in MIN_GRADES:
            col = dist_col(line, k)
            d, d_used, d_all = roll[col], used[col], po_access[col]
            rec = {
                "service_line": line,
                "min_grade": k,
                "min_grade_label": GRADE_LABELS[k],
                "n_facilities_graded": lost[line][str(k)]["graded"],
                "n_facilities_with_coords": lost[line][str(k)]["with_coords"],
                "n_facilities_lost_no_coords": lost[line][str(k)]["lost_no_coords"],
                "n_post_offices": int(d_used.notna().sum()),
                "n_post_offices_placeholder": int(used["coord_placeholder"].sum()),
                "n_values": int(d.notna().sum()),
                "median_km": float(d.median()),
                "p75_km": float(d.quantile(0.75)),
                "p90_km": float(d.quantile(0.90)),
            }
            for r in RADII_KM:
                rec[f"share_within_{r}km"] = float((d <= r).mean())
            rec["placeholders_counted_per_office"] = {
                "n_post_offices": int(d_used.notna().sum()),
                "median_km": float(d_used.median()),
                **{f"share_within_{r}km": float((d_used <= r).mean()) for r in RADII_KM},
            }
            rec["all_geocoded"] = {
                "n_post_offices": int(d_all.notna().sum()),
                "median_km": float(d_all.median()),
                **{f"share_within_{r}km": float((d_all <= r).mean()) for r in RADII_KM},
            }
            recs.append(rec)
    return recs


# --------------------------------------------------------------------------
# checks
# --------------------------------------------------------------------------

def spot_check(po_access: pd.DataFrame, po_geo: pd.DataFrame, qsets: dict, fac: pd.DataFrame) -> dict:
    """Three post offices recomputed by hand: an independent scalar haversine to
    the reported facility, and a brute-force nearest over the whole set."""
    pool = po_access[~po_access["coord_suspect"] & po_access["district_key"].notna()]
    pick = [int(pool.loc[pool[dist_col("emoc", 3)].idxmax(), "po_id"])]
    rng = np.random.default_rng(SPOT_CHECK_SEED)
    rest = pool.loc[~pool["po_id"].isin(pick), "po_id"].to_numpy()
    pick += [int(x) for x in rng.choice(rest, size=2, replace=False)]

    names = fac.set_index("facility_id")["name"]
    meta = po_geo.set_index("po_id")
    rows = []
    for pid in pick:
        r = po_access.loc[po_access["po_id"] == pid].iloc[0]
        for line in SERVICE_LINES:
            for k in MIN_GRADES:
                q = qsets[(line, k)]
                fid = r[near_col(line, k)]
                f = q.loc[q["facility_id"] == fid].iloc[0]
                manual = manual_haversine_km(r["lat"], r["lon"], f["lat"], f["lon"])
                brute = min(
                    manual_haversine_km(r["lat"], r["lon"], a, b)
                    for a, b in zip(q["lat"].to_numpy(), q["lon"].to_numpy())
                )
                rows.append(
                    {
                        "po_id": pid,
                        "office_name": str(meta.at[pid, "office_name"]),
                        "district_key": r["district_key"],
                        "po_lat": float(r["lat"]),
                        "po_lon": float(r["lon"]),
                        "service_line": line,
                        "min_grade": k,
                        "nearest_facility_id": fid,
                        "nearest_facility_name": str(names.get(fid, "")),
                        "fac_lat": float(f["lat"]),
                        "fac_lon": float(f["lon"]),
                        "balltree_km": float(r[dist_col(line, k)]),
                        "manual_km": manual,
                        "brute_force_min_km": brute,
                        "abs_diff_km": abs(float(r[dist_col(line, k)]) - manual),
                    }
                )
    t = pd.DataFrame(rows)
    ok = bool(
        (t["abs_diff_km"] <= SPOT_CHECK_TOL_KM).all()
        and ((t["manual_km"] - t["brute_force_min_km"]).abs() <= SPOT_CHECK_TOL_KM).all()
    )
    return {"po_ids": pick, "passed": ok, "max_abs_diff_km": float(t["abs_diff_km"].max()), "rows": t}


def run_checks(po_access, district_access, po_geo, qsets, spot, n_geocoded) -> list[dict]:
    res = []

    def check(name, passed, value):
        res.append({"check": name, "passed": bool(passed), "value": value})

    check("one row per geocoded post office", len(po_access) == n_geocoded and po_access["po_id"].is_unique,
          f"{len(po_access):,} rows, {n_geocoded:,} geocoded, po_id unique={po_access['po_id'].is_unique}")
    viol = {}
    for line in SERVICE_LINES:
        d1, d2, d3 = (po_access[dist_col(line, k)] for k in MIN_GRADES)
        viol[line] = int(((d1 > d2) | (d2 > d3)).sum())
    check("dist g1 <= g2 <= g3 for every post office", sum(viol.values()) == 0, f"violations {viol}")
    nan = {c: int(po_access[c].isna().sum()) for c in po_access.columns if c.startswith("dist_km_")}
    empty = {f"{l}_g{k}": len(qsets[(l, k)]) == 0 for l in SERVICE_LINES for k in MIN_GRADES}
    check("no missing distances (no empty facility set)", sum(nan.values()) == 0 and not any(empty.values()),
          f"missing {sum(nan.values())}")
    inset = all(
        po_access[near_col(l, k)].isin(qsets[(l, k)]["facility_id"]).all() for l in SERVICE_LINES for k in MIN_GRADES
    )
    check("every nearest id is in its qualifying set", inset, inset)
    check("spot check: 3 post offices vs manual haversine and brute force", spot["passed"],
          f"po_ids {spot['po_ids']}, max |balltree - manual| = {spot['max_abs_diff_km']:.2e} km")
    nokey = int(po_access["district_key"].isna().sum())
    check("post offices without district_key kept and counted", nokey == int(po_geo["district_key"].isna().sum()),
          f"{nokey} kept in post_office_access (excluded from district_access only)")
    da = district_access
    uniq = not da.duplicated(["district_key", "service_line", "min_grade"]).any()
    shares_ok = da[["share_within_30km", "share_within_60km"]].stack().between(0, 1).all()
    pct_ok = ((da["dist_km_p50"] <= da["dist_km_p75"] + 1e-9) & (da["dist_km_p75"] <= da["dist_km_p90"] + 1e-9)
              | da["dist_km_p50"].isna()).all()
    check("district_access unique keys, shares in [0,1], p50 <= p75 <= p90", uniq and shares_ok and pct_ok,
          f"rows {len(da):,}, districts {da['district_key'].nunique()}")
    keyed = po_access[po_access["district_key"].notna()]
    one = da[(da["service_line"] == SERVICE_LINES[0]) & (da["min_grade"] == 1)]
    total = int(one["n_post_offices"].sum() + one["n_post_offices_excluded"].sum())
    check("district counts reconcile with keyed post offices", total == len(keyed),
          f"{total:,} used + excluded in districts vs {len(keyed):,} keyed offices")
    roll = rollup_offices(keyed)
    behind = roll.groupby("district_key").size().reindex(one["district_key"]).fillna(0).astype("int64").to_numpy()
    expect = (one["n_post_offices"] - one["n_post_offices_placeholder"] + one["n_placeholder_points"]).to_numpy()
    check("district values count each placeholder point once (values = n_post_offices - n_post_offices_placeholder "
          "+ n_placeholder_points)", bool((behind == expect).all()),
          f"{int(one['n_post_offices_placeholder'].sum()):,} used placeholder offices on "
          f"{int(one['n_placeholder_points'].sum()):,} district points; {int(behind.sum()):,} values behind the "
          f"district percentiles from {int(one['n_post_offices'].sum()):,} used offices")
    return res


# --------------------------------------------------------------------------
# output
# --------------------------------------------------------------------------

def git_info() -> dict:
    root = paths.code_root()
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
                                text=True, check=True).stdout.strip()
    except Exception:
        commit = None
    try:
        dirty = bool(subprocess.run(["git", "--no-optional-locks", "status", "--porcelain", "--", SCRIPT],
                                    cwd=root, capture_output=True, text=True, check=True).stdout.strip())
    except Exception:
        dirty = None
    return {"git_commit": commit, "code_uncommitted": dirty}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_atomic(path: Path, write) -> None:
    """Call write(tmp_path), then rename onto path, so readers never see a partial file."""
    paths.ensure(path.parent)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        write(tmp)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def write_json(path: Path, obj) -> None:
    body = json.dumps(obj, indent=2, default=str) + "\n"
    write_atomic(path, lambda p: p.write_text(body))


def write_table(df: pd.DataFrame, schema: pa.Schema, path: Path, meta: dict) -> None:
    table = pa.Table.from_pandas(df, schema=schema, preserve_index=False)
    write_atomic(path, lambda p: pq.write_table(table, p))
    write_json(path.with_name(path.name + ".meta.json"), dict(meta, rows=table.num_rows, columns=schema.names))


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(paths.data_dir().resolve()))
    except ValueError:
        return str(path)


def method_slug(method: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", method).strip("-") or "unknown"


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------

def print_summary(recs: list[dict], header: str) -> None:
    print(header)
    print(f"  {'line':9s} {'grade':>14s} {'facilities':>10s} {'lost':>5s} {'median km':>10s} "
          f"{'p90 km':>8s} {'<=30 km':>8s} {'<=60 km':>8s}")
    for r in recs:
        print(f"  {r['service_line']:9s} {('>=' + str(r['min_grade']) + ' ' + r['min_grade_label']):>14s} "
              f"{r['n_facilities_with_coords']:>10,d} {r['n_facilities_lost_no_coords']:>5d} "
              f"{r['median_km']:>10.1f} {r['p90_km']:>8.1f} "
              f"{100 * r['share_within_30km']:>7.1f}% {100 * r['share_within_60km']:>7.1f}%")


def print_movement(old: dict, new_recs: list[dict]) -> None:
    """Step 6: how the headline numbers moved against an earlier summary."""
    prev = {(r["service_line"], r["min_grade"]): r for r in old.get("national", [])}
    og = old.get("grades", {})
    print(f"\nMovement vs {og.get('method', '?')} grades (recipe {og.get('recipe_version', '?')}, "
          f"created {old.get('created_at_utc', '?')}):")
    print(f"  {'line':9s} {'g':>2s} {'facilities':>16s} {'median km':>18s} {'<=30 km (pp)':>20s} {'<=60 km (pp)':>20s}")
    for r in new_recs:
        o = prev.get((r["service_line"], r["min_grade"]))
        if o is None:
            print(f"  {r['service_line']:9s} {r['min_grade']:>2d}  (not in the earlier summary)")
            continue
        def pp(key):
            a, b = 100 * o[key], 100 * r[key]
            return f"{a:5.1f} -> {b:5.1f} ({b - a:+5.1f})"
        print(f"  {r['service_line']:9s} {r['min_grade']:>2d} "
              f"{o['n_facilities_with_coords']:>6,d} -> {r['n_facilities_with_coords']:>6,d} "
              f"{o['median_km']:>7.1f} -> {r['median_km']:>7.1f} "
              f"{pp('share_within_30km'):>20s} {pp('share_within_60km'):>20s}")


# --------------------------------------------------------------------------
# figures
# --------------------------------------------------------------------------

LINE_TITLES = {"emoc": "Emergency obstetric care", "dialysis": "Dialysis", "icu": "ICU"}


def make_figures(po_access: pd.DataFrame, recs: list[dict], grades_info: dict, fig_dir: Path,
                 n_suspect: int) -> list[Path]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt  # noqa: F401  (backend set before plotstyle imports pyplot)
    from vf import plotstyle as ps

    ps.use()
    slug = method_slug(grades_info["method"])
    used = rollup_offices(po_access)  # placeholder points once per district
    by = {(r["service_line"], r["min_grade"]): r for r in recs}
    grade_names = {1: "Claimed or better", 2: "Supported or better", 3: "Corroborated"}
    colors = dict(zip(MIN_GRADES, ps.ORDINAL_3))
    method_note = (f"Grades: {grades_info['method']} method, recipe {grades_info['recipe_version']}. ")
    common_note = (
        "Straight-line distance from each post office to the nearest facility in the scraped table, "
        "which is a sample, so real distances can be shorter. Post offices are not people: branch offices "
        f"dominate rural areas. {n_suspect:,} offices placed more than 200 km from their district are left out, and "
        "offices stacked on one placeholder point with other PINs' offices count once per district. "
        "No evidence of a service is not evidence that it is absent."
    )
    out = []

    # 1. distance curves, one panel per service line
    e3, e1 = by[("emoc", 3)], by[("emoc", 1)]
    title = (f"The median post office is {e1['median_km']:.0f} km from a facility that claims emergency "
             f"obstetric care, and {e3['median_km']:.0f} km from one with corroborated evidence")
    fig, gs = ps.figure(
        6.6, title,
        f"Share of {len(used):,} post office locations within a straight-line distance of the nearest facility, "
        "by the minimum evidence grade required. Dots mark the median.",
        note="Source: WP5 access scores (scripts/wp5_access.py). " + method_note + common_note,
        ncols=3, legend_rows=1, wspace=0.18, left_in=0.75,
    )
    xs = np.linspace(0, 300, 601)
    for j, line in enumerate(SERVICE_LINES):
        ax = fig.add_subplot(gs[0, j])
        for k in MIN_GRADES:
            d = np.sort(used[dist_col(line, k)].to_numpy())
            ys = 100 * np.searchsorted(d, xs, side="right") / len(d)
            ax.plot(xs, ys, color=colors[k], lw=ps.px(2), zorder=3)
            m = by[(line, k)]["median_km"]
            if m <= xs[-1]:
                ax.scatter([m], [50], s=ps.px(9) ** 2, color=colors[k], edgecolors=ps.SURFACE,
                           linewidths=ps.px(1.5), zorder=4)
        for r in RADII_KM:
            ax.axvline(r, color=ps.AXIS, lw=ps.px(1), zorder=1)
        ax.set_xlim(0, 300)
        ax.set_ylim(0, 100)
        ax.set_xticks([0, 30, 60, 100, 200, 300])
        ax.set_yticks([0, 25, 50, 75, 100])
        ps.pct_axis(ax, "y")
        if j:
            ax.set_yticklabels([])
        ps.clean_axes(ax, grid="y")
        meds = " / ".join(f"{by[(line, k)]['median_km']:.0f}" for k in MIN_GRADES)
        ax.text(0.98, 0.04, f"Medians {meds} km", transform=ax.transAxes, ha="right", va="bottom",
                fontsize=10.5, color=ps.INK_2,
                bbox=dict(facecolor=ps.SURFACE, edgecolor="none", pad=2))
        leg = [(grade_names[k], colors[k], "line") for k in MIN_GRADES] if j == 0 else None
        ps.panel_title(ax, LINE_TITLES[line], leg, reserve_rows=1, ncol=3)
        if j == 1:
            ax.set_xlabel("Distance to the nearest facility (km, straight line)")
    out.append(ps.save(fig, fig_dir / f"wp5_01_access_curves_{slug}.png"))

    # 2. maps at grade 2 (supported or better), one per service line.
    # Offices within 60 km are context gray; colour marks only the far ones.
    bins = [60, 120]
    bin_labels = ["Within 60 km", "60 to 120 km", "Over 120 km"]
    bin_colors = [ps.CONTEXT, ps.BLUE_RAMP[350], ps.BLUE_RAMP[700]]
    s2 = {line: by[(line, 2)] for line in SERVICE_LINES}
    worst = max(SERVICE_LINES, key=lambda l: s2[l]["median_km"])
    title = (f"Evidence-backed {LINE_TITLES[worst].lower()} is the furthest away: "
             f"{100 * (1 - s2[worst]['share_within_60km']):.0f}% of post offices are more than 60 km "
             f"from the nearest facility with evidence of it")
    fig, gs = ps.figure(
        7.0, title,
        "Each dot is a post office, coloured by the straight-line distance to the nearest facility at grade 2 "
        "(supported) or better.",
        note="Source: WP5 access scores (scripts/wp5_access.py). " + method_note + common_note,
        ncols=3, legend_rows=1, wspace=0.04, left_in=0.4, x_axis_band_in=0.05,
    )
    for j, line in enumerate(SERVICE_LINES):
        ax = fig.add_subplot(gs[0, j])
        d = used[dist_col(line, 2)].to_numpy()
        b = np.digitize(d, bins, right=True)  # 0: <=60, 1: (60,120], 2: >120
        idx = np.argsort(b, kind="stable")  # far offices drawn last, on top
        cols = np.asarray(bin_colors, dtype=object)[b[idx]]
        ax.scatter(used["lon"].to_numpy()[idx], used["lat"].to_numpy()[idx], s=0.5, c=list(cols), lw=0,
                   rasterized=True)
        ax.set_aspect(1 / np.cos(np.radians(22)))
        ax.set_xlim(67.5, 98.0)
        ax.set_ylim(6.0, 37.5)
        ax.set_axis_off()
        # top-right corner of the frame is empty (north of Nepal and Sikkim)
        ax.text(0.99, 0.99,
                f"Median {s2[line]['median_km']:.0f} km\n"
                f"{100 * (1 - s2[line]['share_within_60km']):.0f}% beyond 60 km",
                transform=ax.transAxes, ha="right", va="top", fontsize=11, color=ps.INK_2, linespacing=1.4)
        leg = [(lab, c, "dot") for lab, c in zip(bin_labels, bin_colors)] if j == 0 else None
        ps.panel_title(ax, LINE_TITLES[line], leg, reserve_rows=1, ncol=3)
    out.append(ps.save(fig, fig_dir / f"wp5_02_access_maps_g2_{slug}.png"))
    return out


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--grades", type=Path, default=paths.gold_dir() / "capability_grades.parquet",
                    help="capability grades parquet (default: gold/capability_grades.parquet)")
    ap.add_argument("--out-dir", type=Path, default=paths.gold_dir(), help="where to write the outputs")
    ap.add_argument("--compare-to", type=Path, default=None,
                    help="an earlier access_summary_<method>.json to print movement against "
                         "(default: the regex baseline when the grades are not regex, else the "
                         "previous summary for the same method)")
    ap.add_argument("--fig-dir", type=Path, default=paths.code_root() / "reports" / "figures")
    ap.add_argument("--no-figures", action="store_true")
    args = ap.parse_args(argv)
    t0 = time.time()

    grades_path = args.grades.resolve()
    fac, po, grades = load_inputs(grades_path)
    ginfo = validate_grades(grades, fac)
    slug = method_slug(ginfo["method"])
    out_dir = args.out_dir
    summary_path = out_dir / f"access_summary_{slug}.json"

    # earlier summary for step 6, read before anything is overwritten
    compare_path = args.compare_to
    if compare_path is None:
        base = out_dir / "access_summary_regex.json"
        compare_path = base if (slug != "regex" and base.exists()) else (summary_path if summary_path.exists() else None)
    old_summary = json.loads(compare_path.read_text()) if compare_path and compare_path.exists() else None

    # post offices: geocoded only, with the coordinate check
    po_geo = po[po["coord_valid"]].sort_values("po_id").reset_index(drop=True)
    chk = coord_check(po_geo)
    po_geo["coord_suspect"] = chk["coord_suspect"]
    ph = geo.placeholder_points(po_geo["lat"], po_geo["lon"], po_geo["pin"])
    po_geo["coord_placeholder"] = ph["placeholder"].to_numpy()
    n_geo = len(po_geo)

    # facilities
    fac_geo = fac[fac["coord_valid"]].sort_values("facility_id").reset_index(drop=True)
    qsets = qualifying_sets(grades, fac)
    lost = facilities_lost(grades, fac)

    # nearest-facility search
    po_access = build_po_access(po_geo, qsets)

    # facility districts, then district roll-up
    ref = po_geo[po_geo["district_key"].notna() & ~po_geo["coord_suspect"]]
    fac_district = assign_facility_districts(fac_geo, ref)
    district_access = build_district_access(po_access, fac_district, qsets)

    recs = national_summary(po_access, lost)
    spot = spot_check(po_access, po_geo, qsets, fac)
    checks = run_checks(po_access, district_access, po_geo, qsets, spot, n_geo)

    # ---- provenance and stats
    nokey = po_geo[po_geo["district_key"].isna()]
    nokey_by = (nokey.groupby(["state_pin", "district_pin"]).size().sort_values(ascending=False))
    suspect = po_geo[po_geo["coord_suspect"]]
    susp_by_district = suspect.groupby(suspect["district_key"].fillna("(no district_key)")).size().sort_values(
        ascending=False)
    shared = po_geo.groupby(["lat", "lon"])["po_id"].transform("size")
    used_geo = po_geo[~po_geo["coord_suspect"]]
    one = district_access[(district_access["service_line"] == SERVICE_LINES[0])
                          & (district_access["min_grade"] == 1)].set_index("district_key")
    ph_share = (one["n_post_offices_placeholder"] / one["n_post_offices"].where(one["n_post_offices"] > 0)).dropna()
    ph_top = ph_share.sort_values(ascending=False).head(15)
    inputs = {
        "silver/facilities.parquet": {"sha256": sha256(paths.silver_dir() / "facilities.parquet")},
        "silver/post_offices.parquet": {"sha256": sha256(paths.silver_dir() / "post_offices.parquet")},
        rel(grades_path): {"sha256": sha256(grades_path), **ginfo},
    }
    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    common = {
        "created_at_utc": created,
        **git_info(),
        "script": SCRIPT,
        "version": VERSION,
        "grades_method": ginfo["method"],
        "recipe_version": ginfo["recipe_version"],
        "inputs": inputs,
        "distance": "straight-line great-circle km (haversine, R = 6371 km) via sklearn BallTree",
    }
    cc = fac["coord_check"].value_counts()
    fac_coord = {
        "source": "silver/facilities.parquet coord_check (WP1): coordinates checked against each facility's PIN",
        "coord_check_counts": {str(k): int(v) for k, v in cc.items()},
        "facilities_with_coords": int(fac["coord_valid"].sum()),
    }
    po_stats = {
        "post_offices_total": int(len(po)),
        "geocoded": n_geo,
        "geocoded_without_district_key": int(len(nokey)),
        "geocoded_without_district_key_by_pin_district": {f"{s}|{d}": int(n) for (s, d), n in nokey_by.items()},
        "coord_suspect": int(po_geo["coord_suspect"].sum()),
        "coord_suspect_with_district_key": int(suspect["district_key"].notna().sum()),
        "coord_suspect_by_office_type": {str(k): int(v) for k, v in suspect["office_type"].value_counts().items()},
        "coord_suspect_top_districts": {str(k): int(v) for k, v in susp_by_district.head(15).items()},
        "coord_not_assessed": int(chk["check_group"].isna().sum()),
        "coord_check_centres": chk.attrs.get("center_source"),
        "offices_on_a_point_shared_by_10_or_more": int((shared >= 10).sum()),
        "coord_placeholder": int(po_geo["coord_placeholder"].sum()),
        "coord_placeholder_points": int(len(po_geo.loc[po_geo["coord_placeholder"], ["lat", "lon"]]
                                            .drop_duplicates())),
        "coord_placeholder_used": int(used_geo["coord_placeholder"].sum()),
        "coord_placeholder_used_with_district_key": int((used_geo["coord_placeholder"]
                                                         & used_geo["district_key"].notna()).sum()),
        "districts_placeholder_share_over_half": int((ph_share > 0.5).sum()),
        "districts_placeholder_share_over_80pct": int((ph_share > 0.8).sum()),
        "placeholder_top_districts": {
            str(k): {"share": round(float(v), 3), "offices": int(one.at[k, "n_post_offices"]),
                     "placeholder_offices": int(one.at[k, "n_post_offices_placeholder"]),
                     "placeholder_points": int(one.at[k, "n_placeholder_points"])}
            for k, v in ph_top.items()
        },
        "used_in_aggregates": int((~po_geo["coord_suspect"]).sum()),
        "values_in_aggregates": int(len(rollup_offices(po_access))),
    }
    suspect_rule = (
        f"coord_suspect: more than {SUSPECT_KM:.0f} km from the district centre, the median point of the "
        f"district's HO and PO (head and sub) offices when it has at least {CENTER_MIN_TOWN_OFFICES}, else of all "
        "its geocoded offices. Offices without district_key are grouped by PIN-directory state and district; "
        "state 'NA' ones are not assessed (flag false)."
    )
    placeholder_rule = (
        f"coord_placeholder: the office's exact point is shared by {geo.PLACEHOLDER_MIN_OFFICES} or more geocoded "
        f"offices from {geo.PLACEHOLDER_MIN_PINS} or more PINs (vf.geo.placeholder_points), a placeholder such as the "
        "district town rather than the office's location. Aggregates (district_access, national summary, figures) "
        "count each placeholder point once per district; the office rows keep the point's distances."
    )

    po_meta = {
        "output": rel(out_dir / "post_office_access.parquet"),
        **common,
        "grain": "one row per geocoded post office (silver/post_offices.parquet coord_valid)",
        "facility_filter": "coord_valid; qualifies for (line, k) when grade >= k",
        "coord_suspect_rule": suspect_rule,
        "coord_placeholder_rule": placeholder_rule,
        "contract_note": "coord_suspect and coord_placeholder are in docs/07_data_contracts.md: filter out "
                         "coord_suspect and count each coord_placeholder point once per district for any statistic",
        "post_offices": po_stats,
        "facilities_lost_no_coords": lost,
        "facility_coordinate_check": fac_coord,
        "spot_check": {k: v for k, v in spot.items() if k != "rows"}
        | {"rows": spot["rows"].to_dict(orient="records")},
        "checks": checks,
    }
    da_meta = {
        "output": rel(out_dir / "district_access.parquet"),
        **common,
        "grain": "one row per district_key x service_line x min_grade",
        "districts": int(district_access["district_key"].nunique()),
        "post_offices_used": "geocoded, district_key present, coord_suspect false",
        "coord_suspect_rule": suspect_rule,
        "coord_placeholder_rule": placeholder_rule,
        "percentiles": "pandas quantile, linear interpolation, over the used offices with each placeholder point "
                       "kept once (n_post_offices - n_post_offices_placeholder + n_placeholder_points values)",
        "share_within": "share of those values with distance <= radius (inclusive), as a fraction 0..1",
        "placeholder_districts": {
            "share_over_half": int((ph_share > 0.5).sum()),
            "share_over_80pct": int((ph_share > 0.8).sum()),
            "top": {str(k): round(float(v), 3) for k, v in ph_top.items()},
        },
        "n_facilities_in_district": (
            f"qualifying facilities whose district is the majority district_key of their {FAC_DISTRICT_K} "
            "nearest used post offices (ties to the nearer office)"
        ),
        "facility_district_assignment": {
            "facilities": int(len(fac_district)),
            "unanimous_votes": int((fac_district["district_votes"] == FAC_DISTRICT_K).sum()),
            "nearest_po_km_median": float(fac_district["nearest_po_km"].median()),
            "nearest_po_km_max": float(fac_district["nearest_po_km"].max()),
        },
        "contract_note": "n_post_offices_excluded (coord_suspect offices of the district, not in the "
                         "percentiles), n_post_offices_placeholder and n_placeholder_points are in "
                         "docs/07_data_contracts.md",
        "geocoded_without_district_key": int(len(nokey)),
        "facility_coordinate_check": fac_coord,
        "facilities_lost_no_coords": lost,
        "national": recs,
        "checks": [c for c in checks if c["check"].startswith("district")],
    }
    summary = {
        "output": rel(summary_path),
        **common,
        "grades": {"path": rel(grades_path), "sha256": inputs[rel(grades_path)]["sha256"], **ginfo},
        "post_offices": po_stats,
        "national": recs,
        "note": "post offices used: geocoded and not coord_suspect, each placeholder point once per district "
                "(n_values); 'placeholders_counted_per_office' counts every used office, 'all_geocoded' keeps the "
                "suspect ones too",
    }

    write_table(po_access, PO_ACCESS_SCHEMA, out_dir / "post_office_access.parquet", po_meta)
    write_table(district_access, DISTRICT_ACCESS_SCHEMA, out_dir / "district_access.parquet", da_meta)
    write_json(summary_path, summary)
    write_json(summary_path.with_name(summary_path.name + ".meta.json"),
               {k: v for k, v in summary.items() if k not in ("national", "post_offices")}
               | {"rows": len(recs), "columns": list(recs[0].keys())})

    figs = []
    if not args.no_figures:
        figs = make_figures(po_access, recs, ginfo, args.fig_dir, po_stats["coord_suspect"])

    # ---- print
    print(f"WP5 access scores, grades {ginfo['method']} (recipe {ginfo['recipe_version']}) from {rel(grades_path)}")
    print(f"Post offices: {len(po):,} total, {n_geo:,} geocoded, {po_stats['used_in_aggregates']:,} used "
          f"({po_stats['coord_suspect']:,} coord_suspect, more than {SUSPECT_KM:.0f} km from their district centre; "
          f"{po_stats['coord_not_assessed']} not assessed)")
    print(f"Placeholder points ({geo.PLACEHOLDER_MIN_OFFICES}+ offices from {geo.PLACEHOLDER_MIN_PINS}+ PINs on one "
          f"point): {po_stats['coord_placeholder']:,} offices on {po_stats['coord_placeholder_points']} points "
          f"({po_stats['coord_placeholder_used']:,} used), counted once per district and point: "
          f"{po_stats['values_in_aggregates']:,} values behind the aggregates. Districts with more than half their "
          f"used offices on such points: {po_stats['districts_placeholder_share_over_half']} (over 80%: "
          f"{po_stats['districts_placeholder_share_over_80pct']}); top: " + ", ".join(
              f"{k} {100 * v['share']:.0f}%" for k, v in list(po_stats["placeholder_top_districts"].items())[:5]))
    print(f"Geocoded offices without district_key: {len(nokey):,} "
          + ", ".join(f"{s}|{d} {n}" for (s, d), n in nokey_by.items())
          + ". Kept in post_office_access and the national summary, not in district_access.")
    print(f"Facility coordinates (WP1 check against each facility's PIN): {fac_coord['coord_check_counts']}")
    print("Graded facilities lost for lack of coordinates (grade >= k, not coord_valid; of which set to null by "
          "the PIN check), and moved to their PIN:")
    for line in SERVICE_LINES:
        print("  " + line + ": " + ", ".join(
            f"g>={k} {lost[line][str(k)]['lost_no_coords']} ({lost[line][str(k)]['lost_excluded_by_pin_check']}) "
            f"of {lost[line][str(k)]['graded']:,}, moved {lost[line][str(k)]['moved_to_pin']}" for k in MIN_GRADES))
    print_summary(recs, "\nNational summary (straight-line km; post offices, not people):")
    print("  Placeholder offices counted one by one (old rule): " + "; ".join(
        f"{r['service_line']} g{r['min_grade']} {r['placeholders_counted_per_office']['median_km']:.1f} km "
        f"{100 * r['placeholders_counted_per_office']['share_within_60km']:.1f}%<=60" for r in recs))
    print("  All geocoded offices, suspect ones included: " + "; ".join(
        f"{r['service_line']} g{r['min_grade']} {r['all_geocoded']['median_km']:.1f} km "
        f"{100 * r['all_geocoded']['share_within_60km']:.1f}%<=60" for r in recs))

    print(f"\nSpot check (independent math.haversine and brute force over the qualifying set), "
          f"post offices {spot['po_ids']}:")
    st = spot["rows"]
    for pid, g in st.groupby("po_id", sort=False):
        r0 = g.iloc[0]
        print(f"  po_id {pid} {r0['office_name']} ({r0['district_key']}) at {r0['po_lat']:.5f}, {r0['po_lon']:.5f}")
        for _, r in g.iterrows():
            print(f"    {r['service_line']:8s} g>={r['min_grade']} {r['nearest_facility_name'][:38]:38s} "
                  f"balltree {r['balltree_km']:9.4f}  manual {r['manual_km']:9.4f}  "
                  f"brute-force min {r['brute_force_min_km']:9.4f}  diff {r['abs_diff_km']:.1e}")

    print("\nChecks:")
    for c in checks:
        print(f"  [{'PASS' if c['passed'] else 'FAIL'}] {c['check']}: {c['value']}")

    if old_summary is not None:
        print_movement(old_summary, recs)
    for p in figs:
        print(f"figure: {p}")
    print(f"\nWrote {rel(out_dir / 'post_office_access.parquet')} ({len(po_access):,} rows), "
          f"{rel(out_dir / 'district_access.parquet')} ({len(district_access):,} rows), {rel(summary_path)}")
    print(f"Runtime {time.time() - t0:.1f} s")
    return 0 if all(c["passed"] for c in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
