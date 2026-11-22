"""WP8: mock app data for the demo map.

Writes every file in the "App data" section of docs/07_data_contracts.md into
vf.paths.app_data_dir(), in the contract's shapes, so the app can be built
before WP5, WP6 and WP7 land.

What is real and what is fake:

* REAL: post office coordinates (silver/post_offices.parquet if it exists,
  else the raw PIN directory through vf.io, inside the India bounding box),
  and district and state names.
* FAKE: every facility (placed near real post offices, named "Mock facility
  N"), every grade, tag and evidence string, every NFHS value, quadrant,
  residual, model metric and scenario. Distances are computed from the fake
  facilities, so they are internally consistent but say nothing about India.

meta.json carries "mock": true and the app shows a MOCK badge. WP7's export
replaces these files and the badge disappears on its own.

The greedy scenarios use exactly the browser's coverage rule (haversine,
earth radius 6371.0088 km, distances rounded to float32, covered when
dist <= R), so the in-browser numbers can be checked against scenarios.json.

Run:  .venv/bin/python scripts/wp8_mock_app_data.py
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import zlib
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import sparse
from scipy.spatial import cKDTree

from vf import io, paths

SEED = 20261121
VERSION = "wp8-mock-v1"
EARTH_R_KM = 6371.0088
DEG = np.pi / 180.0
LINES = ["emoc", "dialysis", "icu"]
GRADES = [1, 2, 3]
GRADE_LABELS = {0: "none", 1: "claimed", 2: "supported", 3: "corroborated"}
RADII_KM = [30, 60]
GREEDY_STEPS = 20
SIM_MIN_GRADE = 2
UPGRADE_TO_GRADE = 3
NO_INDEX = 65535  # u16 sentinel: no facility / no district

# Target counts for the mock grade mix (facilities at grade >= k).
MOCK_GRADE_COUNTS = {
    "emoc": {1: 4600, 2: 450, 3: 160},
    "dialysis": {1: 2150, 2: 1050, 3: 420},
    "icu": {1: 2600, 2: 2050, 3: 380},
}
N_FACILITIES = 9960

QUADRANTS = ["care_desert", "data_desert", "cost_barrier", "served"]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=paths.code_root(),
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except Exception:
        return "unknown"


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def haversine_km(lat1, lon1, lat2, lon2):
    """Same formula, same operation order as app/src/geo.ts."""
    p1 = np.asarray(lat1, dtype=np.float64) * DEG
    p2 = np.asarray(lat2, dtype=np.float64) * DEG
    dlat = p2 - p1
    dlon = (np.asarray(lon2, dtype=np.float64) - np.asarray(lon1, dtype=np.float64)) * DEG
    s1 = np.sin(dlat / 2.0)
    s2 = np.sin(dlon / 2.0)
    a = s1 * s1 + np.cos(p1) * np.cos(p2) * s2 * s2
    return 2.0 * EARTH_R_KM * np.arcsin(np.sqrt(np.minimum(1.0, a)))


def unit_xyz(lat, lon):
    la = np.asarray(lat, dtype=np.float64) * DEG
    lo = np.asarray(lon, dtype=np.float64) * DEG
    return np.column_stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)])


def chord_for_km(km: float) -> float:
    return 2.0 * np.sin(km / (2.0 * EARTH_R_KM))


def stable_hash(s: str) -> int:
    return zlib.crc32(s.encode("utf-8"))


class Writer:
    """Atomic writes plus a .meta.json sidecar per file."""

    def __init__(self, out_dir: Path, commit: str):
        self.out_dir = paths.ensure(out_dir)
        self.commit = commit
        self.written: list[tuple[str, int]] = []

    def _atomic(self, name: str, data: bytes) -> None:
        final = self.out_dir / name
        tmp = self.out_dir / f".{name}.tmp-{os.getpid()}"
        tmp.write_bytes(data)
        os.replace(tmp, final)
        self.written.append((name, len(data)))

    def _sidecar(self, name: str, rows: int, extra: dict | None = None) -> None:
        meta = {
            "file": name,
            "rows": int(rows),
            "created": now_iso(),
            "git_commit": self.commit,
            "recipe_version": VERSION,
            "script": "scripts/wp8_mock_app_data.py",
            "mock": True,
        }
        if extra:
            meta.update(extra)
        body = json.dumps(meta, indent=2).encode("utf-8")
        tmp = self.out_dir / f".{name}.meta.json.tmp-{os.getpid()}"
        tmp.write_bytes(body)
        os.replace(tmp, self.out_dir / f"{name}.meta.json")

    def binary(self, name: str, arr: np.ndarray) -> None:
        self._atomic(name, np.ascontiguousarray(arr).tobytes())
        self._sidecar(name, len(arr), {"dtype": str(arr.dtype)})

    def json(self, name: str, obj, rows: int) -> None:
        body = json.dumps(obj, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        self._atomic(name, body.encode("utf-8"))
        self._sidecar(name, rows)


# ---------------------------------------------------------------------------
# 1. post offices (real coordinates)
# ---------------------------------------------------------------------------

def load_post_offices() -> tuple[pd.DataFrame, str, int]:
    silver = paths.silver_dir() / "post_offices.parquet"
    if silver.exists():
        df = pd.read_parquet(silver)
        n_total = len(df)
        df = df[df["coord_valid"].fillna(False).astype(bool)].copy()
        df = df.sort_values("po_id").reset_index(drop=True)
        out = pd.DataFrame({
            "po_id": df["po_id"].astype(int),
            "lat": df["lat"].astype(float),
            "lon": df["lon"].astype(float),
            "office_type": df["office_type"].astype(str),
            "state": df["state_pin"].astype(str),
            "district": df["district_pin"].astype(str),
            "district_key": df["district_key"],
        })
        return out, "silver/post_offices.parquet", n_total

    raw = io.read_pincodes_raw()
    lat = raw["latitude"].map(io.to_float)
    lon = raw["longitude"].map(io.to_float)
    ok = np.array([io.in_india(a, b) for a, b in zip(lat, lon)])
    df = pd.DataFrame({
        "po_id": np.arange(len(raw))[ok],
        "lat": lat[ok].astype(float).values,
        "lon": lon[ok].astype(float).values,
        "office_type": raw["officetype"][ok].values,
        "state": raw["statename"][ok].values,
        "district": raw["district"][ok].values,
    })
    # Mock district key from the PIN directory's own names (no NFHS crosswalk yet).
    df["district_key"] = df["state"] + "|" + df["district"]
    return df.reset_index(drop=True), "raw india_post_pincode_directory.csv (India bbox only, as WP2's coord_valid)", len(raw)


def title(s: str) -> str:
    return " ".join(w.capitalize() if w.isupper() or w.islower() else w for w in str(s).split())


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    rng = np.random.default_rng(SEED)
    commit = git_commit()
    out_dir = paths.app_data_dir()
    w = Writer(out_dir, commit)

    po, po_source, n_po_total = load_post_offices()
    n_nfhs = len(io.read_nfhs_raw())
    n_po = len(po)
    # The app stores coordinates as float32. Use exactly those values everywhere.
    po_lon32 = po["lon"].to_numpy(np.float32)
    po_lat32 = po["lat"].to_numpy(np.float32)
    po_lon = po_lon32.astype(np.float64)
    po_lat = po_lat32.astype(np.float64)

    # ---- districts ------------------------------------------------------
    keys = po["district_key"].dropna().astype(str)
    dkeys = sorted(keys.unique())
    if len(dkeys) >= NO_INDEX:
        raise SystemExit("too many districts for u16")
    dindex = {k: i for i, k in enumerate(dkeys)}
    po_district = po["district_key"].map(lambda k: dindex.get(k, NO_INDEX) if isinstance(k, str) else NO_INDEX)
    po_district = po_district.to_numpy(np.uint16)
    n_d = len(dkeys)
    d_state = {}
    d_name = {}
    for k in dkeys:
        s, _, n = k.partition("|")
        d_state[k] = title(s)
        d_name[k] = title(n)

    # ---- mock facilities ------------------------------------------------
    otype = po["office_type"].astype(str).str.upper().to_numpy()
    base_w = np.where(otype == "HO", 60.0, np.where(otype == "PO", 5.0, np.where(otype == "SO", 5.0, 0.25)))
    state_names = po["state"].astype(str).to_numpy()
    state_f = {s: float(np.exp(np.random.default_rng(stable_hash(s) ^ SEED).normal(0, 0.8)))
               for s in np.unique(state_names)}
    dist_f = {k: float(np.exp(np.random.default_rng(stable_hash(k) ^ (SEED + 1)).normal(0, 1.0)))
              for k in dkeys}
    wts = base_w * np.array([state_f[s] for s in state_names])
    wts *= np.array([dist_f.get(k, 1.0) if isinstance(k, str) else 1.0 for k in po["district_key"]])
    wts /= wts.sum()
    host = rng.choice(n_po, size=N_FACILITIES, replace=True, p=wts)
    # jitter about 2 km
    f_lat = po_lat[host] + rng.normal(0, 0.018, N_FACILITIES)
    f_lon = po_lon[host] + rng.normal(0, 0.018, N_FACILITIES)
    f_lat = np.round(f_lat, 5)
    f_lon = np.round(f_lon, 5)
    size = np.where(otype[host] == "HO", 1.0, np.where(otype[host] == "BO", -0.8, 0.0)) + rng.normal(0, 0.7, N_FACILITIES)

    grades = {}
    for line in LINES:
        g = np.zeros(N_FACILITIES, dtype=np.int8)
        score = size + rng.gumbel(0, 0.9, N_FACILITIES)
        order = np.argsort(-score, kind="stable")
        for k in GRADES:
            g[order[: MOCK_GRADE_COUNTS[line][k]]] = k
            # re-shuffle within the next tier so higher grades are not a pure function of size
            score2 = size[order[: MOCK_GRADE_COUNTS[line][k]]] + rng.gumbel(0, 1.2, MOCK_GRADE_COUNTS[line][k])
            order = order[: MOCK_GRADE_COUNTS[line][k]][np.argsort(-score2, kind="stable")]
        grades[line] = g

    mock_facts = {
        "emoc": {
            1: (["ob_delivery"], ["Mock fact: lists obstetrics and gynaecology"]),
            2: (["csection"], ["Mock fact: caesarean section mentioned"]),
            3: (["blood", "ot"], ["Mock fact: blood storage unit named", "Mock fact: operating theatre named"]),
        },
        "dialysis": {
            1: ([], ["Mock fact: lists nephrology"]),
            2: (["dialysis"], ["Mock fact: dialysis mentioned"]),
            3: (["dialysis_unit"], ["Mock fact: dialysis unit with machines named"]),
        },
        "icu": {
            1: ([], ["Mock fact: lists critical care medicine"]),
            2: (["icu"], ["Mock fact: intensive care unit mentioned"]),
            3: (["ventilator"], ["Mock fact: ventilators named"]),
        },
    }
    op_draw = rng.random(N_FACILITIES)
    type_draw = rng.random(N_FACILITIES)
    n_domains = np.minimum(6, 1 + rng.poisson(0.8, N_FACILITIES))
    facilities = []
    for i in range(N_FACILITIES):
        h = int(host[i])
        gi = {line: int(grades[line][i]) for line in LINES}
        ev, tg = {}, {}
        for line in LINES:
            tags, facts = [], []
            for k in range(1, gi[line] + 1):
                t, f = mock_facts[line][k]
                tags += t
                facts += f
            if facts:
                ev[line] = facts[:5]
            if tags:
                tg[line] = tags
        facilities.append({
            "id": f"mock-{i:05d}",
            "name": f"Mock facility {i + 1:05d}",
            "lat": float(f_lat[i]),
            "lon": float(f_lon[i]),
            "type": "hospital" if type_draw[i] < 0.45 else ("clinic" if type_draw[i] < 0.9 else None),
            "operator": "private" if op_draw[i] < 0.88 else ("public" if op_draw[i] < 0.93 else None),
            "city": title(po["district"].iloc[h]),
            "state": title(po["state"].iloc[h]),
            "grades": gi,
            "evidence": ev,
            "tags": tg,
            "n_domains": int(n_domains[i]),
        })

    # ---- distances and nearest facility per line and grade -------------
    po_xyz = unit_xyz(po_lat, po_lon)
    f_xyz = unit_xyz(f_lat, f_lon)
    dist = {}
    near = {}
    for line in LINES:
        for k in GRADES:
            idx = np.flatnonzero(grades[line] >= k)
            tree = cKDTree(f_xyz[idx])
            _, j = tree.query(po_xyz, k=1)
            fi = idx[j]
            d = haversine_km(po_lat, po_lon, f_lat[fi], f_lon[fi])
            dist[(line, k)] = d.astype(np.float32)
            near[(line, k)] = fi.astype(np.uint16)

    # ---- districts.json (fake NFHS, access, quadrants) -----------------
    d_rng = np.random.default_rng(SEED + 7)
    dev = {k: float(np.log(state_f.get(k.partition("|")[0], 1.0)) * 0.6 + d_rng.normal(0, 0.8)) for k in dkeys}
    df_po = pd.DataFrame({"d": po_district})
    access = {}
    for line in LINES:
        for k in GRADES:
            df_po[f"{line}_g{k}"] = dist[(line, k)]
    grp = df_po[df_po["d"] != NO_INDEX].groupby("d")
    med = {line: grp[f"{line}_g2"].median() for line in LINES}
    within30 = {line: grp[f"{line}_g2"].apply(lambda s: float((s <= 30).mean())) for line in LINES}
    within60 = {line: grp[f"{line}_g2"].apply(lambda s: float((s <= 60).mean())) for line in LINES}
    n_po_d = grp.size()

    methods = d_rng.choice(["exact", "fuzzy", "manual", "parent_district", "unmatched"], size=n_d,
                           p=[0.75, 0.15, 0.03, 0.02, 0.05])
    districts = []
    quad_counts = {line: {q: 0 for q in QUADRANTS + ["unscored"]} for line in LINES}
    # thresholds from the fake distributions
    acc_thr = {line: float(np.nanpercentile(med[line].values, 67)) for line in LINES}
    inst = {}
    for i, key in enumerate(dkeys):
        a = float(med["emoc"].get(i, np.nan))
        inst[key] = float(np.clip(86 + 7 * dev[key] - 0.05 * np.nan_to_num(a, nan=60) + d_rng.normal(0, 5), 35, 99.5))
    out_thr = float(np.percentile(list(inst.values()), 33))
    for i, key in enumerate(dkeys):
        method = str(methods[i])
        nf = {
            "institutional_birth_5y_pct": round(inst[key], 1),
            "institutional_birth_in_public_facility_5y_pct": round(float(np.clip(55 - 6 * dev[key] + d_rng.normal(0, 10), 5, 95)), 1),
            "births_delivered_by_csection_5y_pct": round(float(np.clip(18 + 6 * dev[key] + d_rng.normal(0, 6), 1, 60)), 1),
            "hh_member_covered_health_insurance_pct": round(float(np.clip(35 + d_rng.normal(0, 15), 2, 95)), 1),
            "women_age_15_49_who_are_literate_pct": round(float(np.clip(70 + 10 * dev[key] + d_rng.normal(0, 6), 30, 99)), 1),
        }
        flags = {c: ("low_sample" if d_rng.random() < 0.05 else "ok") for c in nf}
        acc = {}
        quad = {}
        residual_emoc = None
        for line in LINES:
            if i not in med[line].index:
                acc[line] = None
                quad[line] = None
                quad_counts[line]["unscored"] += 1
                continue
            acc[line] = {
                "min_grade": SIM_MIN_GRADE,
                "dist_km_p50": round(float(med[line][i]), 1),
                "share_within_30km": round(within30[line][i], 4),
                "share_within_60km": round(within60[line][i], 4),
                "n_post_offices": int(n_po_d[i]),
            }
            if line == "icu" or method == "unmatched":
                quad[line] = None  # ICU: access map only; unmatched: cannot be scored
                quad_counts[line]["unscored"] += 1
                continue
            poor_access = float(med[line][i]) >= acc_thr[line]
            outcome = inst[key] if line == "emoc" else float(np.clip(60 + 10 * dev[key] + d_rng.normal(0, 8), 10, 99))
            poor_outcome = outcome <= (out_thr if line == "emoc" else 55)
            q_rule = ("care_desert" if poor_outcome else "data_desert") if poor_access else (
                "cost_barrier" if poor_outcome else "served")
            predicted = outcome - d_rng.normal(0, 4) - (4 if (poor_access and not poor_outcome) else 0)
            residual = outcome - predicted
            if poor_access:
                q_model = "data_desert" if residual > 2.0 else "care_desert"
            else:
                q_model = "cost_barrier" if residual < -3.0 or poor_outcome and residual < 0 else "served"
            p_gap = float(1.0 / (1.0 + np.exp(residual / 3.0)))
            quad[line] = {
                "quadrant_rule": q_rule,
                "quadrant_model": q_model,
                "p_real_gap": round(p_gap, 4),
                "access_value": round(float(med[line][i]), 1),
                "outcome_value": round(outcome, 1),
                "outcome_flag": flags["institutional_birth_5y_pct"] if line == "emoc" else "ok",
                "predicted_outcome": round(float(predicted), 1),
                "residual": round(float(residual), 2),
            }
            quad_counts[line][q_model] += 1
            if line == "emoc":
                residual_emoc = round(float(residual), 2)
        districts.append({
            "key": key,
            "state": d_state[key],
            "name": d_name[key],
            "quadrant": quad,
            "nfhs": nf,
            "nfhs_flags": flags,
            "access": acc,
            "residual": residual_emoc,
            "crosswalk_method": method,
        })

    # ---- scenarios.json (greedy, same rule as the browser) -------------
    po_tree = cKDTree(po_xyz)
    p_gap_po = {}
    for line in LINES:
        pg = np.full(n_d + 1, 0.5)
        for i, dct in enumerate(districts):
            q = dct["quadrant"].get(line)
            if q is not None:
                pg[i] = q["p_real_gap"]
        di = po_district.astype(np.int64)
        di[di == NO_INDEX] = n_d
        p_gap_po[line] = pg[di]
    # district of each facility = district of its nearest post office
    _, f_near_po = po_tree.query(f_xyz, k=1)
    f_district = po_district[f_near_po]

    settings = []
    for line in LINES:
        cand = np.flatnonzero(grades[line] == 1)
        base32 = dist[(line, SIM_MIN_GRADE)]
        for R in RADII_KM:
            covered0 = base32 <= np.float32(R)
            rows, cols = [], []
            balls = po_tree.query_ball_point(f_xyz[cand], r=chord_for_km(R) * (1 + 1e-9) + 1e-12)
            for ci, (fi, ball) in enumerate(zip(cand, balls)):
                if not ball:
                    continue
                b = np.asarray(ball, dtype=np.int64)
                d = haversine_km(po_lat[b], po_lon[b], f_lat[fi], f_lon[fi]).astype(np.float32)
                b = b[d <= np.float32(R)]
                rows.append(np.full(len(b), ci, dtype=np.int64))
                cols.append(b)
            rows = np.concatenate(rows) if rows else np.zeros(0, np.int64)
            cols = np.concatenate(cols) if cols else np.zeros(0, np.int64)
            A = sparse.csr_matrix((np.ones(len(rows), dtype=np.float64), (rows, cols)), shape=(len(cand), n_po))
            plans = {}
            for variant in ["naive", "desert_aware"]:
                weight = np.ones(n_po) if variant == "naive" else p_gap_po[line]
                cov = covered0.copy()
                chosen = np.zeros(len(cand), dtype=bool)
                steps = []
                for s in range(GREEDY_STEPS):
                    gains = A @ (weight * (~cov))
                    gains[chosen] = -1.0
                    j = int(np.argmax(gains))
                    if gains[j] <= 0:
                        break
                    chosen[j] = True
                    newly = A.indices[A.indptr[j]:A.indptr[j + 1]]
                    newly = newly[~cov[newly]]
                    cov[newly] = True
                    fi = int(cand[j])
                    dd = int(f_district[fi])
                    dkey = dkeys[dd] if dd != NO_INDEX else None
                    q = districts[dd]["quadrant"].get(line) if dd != NO_INDEX else None
                    steps.append({
                        "step": s + 1,
                        "kind": "upgrade",
                        "facility_id": facilities[fi]["id"],
                        "lat": facilities[fi]["lat"],
                        "lon": facilities[fi]["lon"],
                        "marginal_gain": round(float(gains[j]), 4),
                        "newly_covered": int(len(newly)),
                        "covered": int(cov.sum()),
                        "cumulative_coverage": round(float(cov.sum()) / n_po, 6),
                        "district_key": dkey,
                        "quadrant_model": q["quadrant_model"] if q else None,
                    })
                plans[variant] = steps
            settings.append({
                "service_line": line,
                "radius_km": R,
                "min_grade": SIM_MIN_GRADE,
                "n_post_offices": int(n_po),
                "baseline_covered": int(covered0.sum()),
                "baseline_coverage": round(float(covered0.sum()) / n_po, 6),
                "naive": plans["naive"],
                "desert_aware": plans["desert_aware"],
            })
    scenarios = {
        "mock": True,
        "generated_at": now_iso(),
        "coverage_rule": (
            "A post office is covered for a service line when a facility at grade >= min_grade "
            "is within radius_km, straight-line (haversine)."
        ),
        "earth_radius_km": EARTH_R_KM,
        "distance_precision": "float32",
        "upgrade_to_grade": UPGRADE_TO_GRADE,
        "candidates": "existing facilities at grade 1 (claimed) for the service line",
        "weights": {"naive": "1 per uncovered post office",
                    "desert_aware": "p_real_gap of the post office's district (0.5 when unscored)"},
        "settings": settings,
    }

    # ---- story focus: a post office in a care desert with a near claim and a far supported site
    story_focus = None
    emoc_g1 = dist[("emoc", 1)]
    emoc_g2 = dist[("emoc", 2)]
    care = np.array([(dct["quadrant"].get("emoc") or {}).get("quadrant_model") == "care_desert" for dct in districts] + [False])
    di = po_district.astype(np.int64)
    di[di == NO_INDEX] = n_d
    mask = care[di] & (emoc_g1 < 8) & (emoc_g2 > 70) & (emoc_g2 < 140)
    cand_po = np.flatnonzero(mask)
    if len(cand_po):
        pick = int(cand_po[np.argmin(np.abs(emoc_g2[cand_po] - 95))])
        story_focus = {"lon": round(float(po_lon[pick]), 5), "lat": round(float(po_lat[pick]), 5)}
    else:
        story_focus = {"lon": 82.0, "lat": 22.5}
    data_desert_example = None
    dd_mask = np.array([(dct["quadrant"].get("emoc") or {}).get("quadrant_model") == "data_desert" for dct in districts])
    if dd_mask.any():
        # the data-desert district with the most post offices (a mainland, dense example)
        counts = np.bincount(po_district[po_district != NO_INDEX].astype(np.int64), minlength=n_d)
        j = int(np.flatnonzero(dd_mask)[np.argmax(counts[np.flatnonzero(dd_mask)])])
        sel = po_district == j
        data_desert_example = {"lon": round(float(po_lon[sel].mean()), 4), "lat": round(float(po_lat[sel].mean()), 4)}

    story = build_story(story_focus, data_desert_example)

    # ---- meta.json ---------------------------------------------------
    binaries = [("po_lon.f32", po_lon32), ("po_lat.f32", po_lat32), ("po_district.u16", po_district)]
    for line in LINES:
        for k in GRADES:
            binaries.append((f"po_dist_{line}_g{k}.f32", dist[(line, k)]))
    for line in LINES:
        for k in GRADES:
            binaries.append((f"po_near_{line}_g{k}.u16", near[(line, k)]))

    xw_counts = pd.Series([d["crosswalk_method"] for d in districts]).value_counts().to_dict()
    n_private = sum(1 for f in facilities if f["operator"] == "private")
    meta = {
        "mock": True,
        "mock_note": "MOCK DATA: real post office coordinates; every facility, grade, survey value, model metric and scenario is fake.",
        "generated_at": now_iso(),
        "git_commit": commit,
        "producer": {"script": "scripts/wp8_mock_app_data.py", "version": VERSION},
        "versions": {"recipe": "mock", "fact_tagger": "mock", "outcome_model": "mock", "simulator": VERSION},
        "post_office_source": po_source,
        "service_lines": [
            {"key": "emoc", "label": "Obstetrics", "long": "Emergency obstetric care"},
            {"key": "dialysis", "label": "Dialysis", "long": "Dialysis"},
            {"key": "icu", "label": "ICU", "long": "Intensive care with ventilation"},
        ],
        "grades": [{"k": k, "label": GRADE_LABELS[k]} for k in [1, 2, 3]],
        "radii_km": RADII_KM,
        "simulator": {"min_grade": SIM_MIN_GRADE, "upgrade_to_grade": UPGRADE_TO_GRADE,
                      "earth_radius_km": EARTH_R_KM, "steps": GREEDY_STEPS},
        "no_index": NO_INDEX,
        "binary": [{"file": n, "dtype": str(a.dtype), "length": int(len(a))} for n, a in binaries],
        "counts": {
            "facilities": N_FACILITIES,
            "facilities_with_coords": N_FACILITIES,
            "facilities_private_share": round(n_private / N_FACILITIES, 4),
            "post_offices_total": int(n_po_total),
            "post_offices_geocoded": int(n_po),
            "districts": int(n_d),
            "nfhs_districts": int(n_nfhs),
            "districts_matched": {k: int(v) for k, v in xw_counts.items()},
            "facilities_by_grade": {line: {str(k): int((grades[line] >= k).sum()) for k in [1, 2, 3]} for line in LINES},
            "quadrants_model": quad_counts,
        },
        "models": {
            "fact_tagger": {
                "mock": True,
                "gold_set": {"n_facts": 400, "labeled_by": "mock"},
                "systems": ["regex", "teacher", "student"],
                "per_tag": [
                    {"tag": t, "regex": {"p": p1, "r": r1, "f1": f1}, "teacher": {"p": p2, "r": r2, "f1": f2},
                     "student": {"p": p3, "r": r3, "f1": f3}}
                    for t, (p1, r1, f1, p2, r2, f2, p3, r3, f3) in {
                        "csection": (0.81, 0.92, 0.86, 0.95, 0.93, 0.94, 0.92, 0.9, 0.91),
                        "blood": (0.74, 0.88, 0.8, 0.93, 0.9, 0.91, 0.9, 0.87, 0.88),
                        "icu": (0.7, 0.95, 0.81, 0.94, 0.92, 0.93, 0.91, 0.9, 0.9),
                        "dialysis": (0.88, 0.97, 0.92, 0.97, 0.95, 0.96, 0.95, 0.94, 0.94),
                    }.items()
                ],
                "cost_note": "mock",
            },
            "outcome_model": {
                "mock": True,
                "target": "institutional_birth_5y_pct",
                "cv_scheme": "GroupKFold by state, 5 folds",
                "metric": "MAE, percentage points",
                "without_access": {"mean": 6.1, "sd": 0.9},
                "with_access": {"mean": 5.4, "sd": 0.8},
                "n_districts": int(n_d),
            },
        },
        "caveats": {
            "distance": "Straight-line km, not travel time.",
            "sample": "The facility table is a web-scraped sample: the true nearest facility may be closer.",
            "post_offices": "Each dot is one post office area, not a person. Counts are post offices, not people.",
            "evidence": "No evidence is not evidence of absence. Grades describe what web pages say.",
            "data_desert": "Care likely exists, not in this data (est.): district-level estimate from the outcome model.",
            "greedy": "Greedy plan, not an optimal plan. Upgrades are candidate sites, a modeling assumption.",
        },
        "limits": [
            "Straight-line distance is not travel time. Roads and terrain make real trips longer.",
            "The facilities table is a sample, so the true nearest facility may be closer than the one in the data.",
            "Post offices stand in for settlements. They are not population-weighted.",
            "No evidence is not evidence of absence. Grades describe what the web pages say.",
            "NFHS-5 is from 2019 to 2021; facility pages are newer.",
            "District matching between the PIN directory and NFHS is imperfect.",
        ],
    }

    # ---- write ------------------------------------------------------
    for name, arr in binaries:
        w.binary(name, arr)
    w.json("facilities.json", facilities, len(facilities))
    w.json("districts.json", districts, len(districts))
    w.json("scenarios.json", scenarios, len(settings))
    story_path = out_dir / "story.json"
    w.json("story.json", story, len(story["beats"]))
    # meta.json last, so a reader never sees a meta that points at missing files
    w.json("meta.json", meta, 1)
    # story.json is meant to be edited by hand: rewrite it pretty-printed.
    tmp = out_dir / f".story.json.tmp-{os.getpid()}"
    tmp.write_text(json.dumps(story, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, story_path)

    total = sum((out_dir / n).stat().st_size for n, _ in w.written)
    # ---- summary ----------------------------------------------------
    print(f"wrote {len(w.written)} files to {out_dir} (MOCK), total {total / 1e6:.2f} MB")
    print(f"post offices: {n_po:,} from {po_source}; districts: {n_d}")
    print(f"mock facilities: {N_FACILITIES:,}; emoc grade>=1/2/3: "
          + "/".join(str(int((grades['emoc'] >= k).sum())) for k in GRADES))
    for s in settings:
        if s["service_line"] != "emoc":
            continue
        naive_dd = sum(1 for e in s["naive"][:10] if e["quadrant_model"] == "data_desert")
        aware_dd = sum(1 for e in s["desert_aware"][:10] if e["quadrant_model"] == "data_desert")
        print(f"emoc R={s['radius_km']} km: baseline coverage {s['baseline_coverage']:.4f}, "
              f"naive top-10 -> {s['naive'][min(9, len(s['naive']) - 1)]['cumulative_coverage']:.4f} "
              f"({naive_dd} picks in data deserts), desert-aware top-10 -> "
              f"{s['desert_aware'][min(9, len(s['desert_aware']) - 1)]['cumulative_coverage']:.4f} ({aware_dd})")
    print(f"emoc quadrants (model, mock): {quad_counts['emoc']}")
    if total > 15e6:
        print("WARNING: app data is over 15 MB", file=sys.stderr)
    return 0


def build_story(focus: dict, data_desert_example: dict | None) -> dict:
    """Beats from docs/09_presentation.md. Bracketed values stay as brackets."""
    india = {"longitude": 80.5, "latitude": 22.6, "zoom": 4.1}
    near = {"longitude": focus["lon"], "latitude": focus["lat"], "zoom": 7.6}
    dd = data_desert_example or {"lon": 80.5, "lat": 22.6}
    return {
        "mock": True,
        "note": "Draft beats from docs/09_presentation.md. Every [bracketed] value must be replaced with a real one before recording.",
        "defaults": {"line": "emoc", "radius_km": 30, "transition_s": 2.5},
        "beats": [
            {"id": 1, "time": "0:00", "duration_s": 15, "camera": near, "view": "distance", "grade": 1,
             "pulse": {"lon": focus["lon"], "lat": focus["lat"]},
             "caption": "A woman in [district] goes into labor and starts bleeding. Where does she go?"},
            {"id": 2, "time": "0:15", "duration_s": 20, "camera": near, "view": "distance", "grade": 1,
             "patient": {"lon": focus["lon"], "lat": focus["lat"]},
             "caption": "[N] maternity facilities listed within [X] km. The nearest with evidence of a C-section is [Y] km away."},
            {"id": 3, "time": "0:35", "duration_s": 20, "camera": india, "view": "distance", "grade": 0,
             "sweep": [0, 1, 2, 3],
             "caption": "Across India: [4,585] claim obstetrics, [443] show evidence of a C-section."},
            {"id": 4, "time": "0:55", "duration_s": 15, "camera": india, "view": "distance", "grade": 2,
             "show_counter_change": {"from_grade": 1, "to_grade": 2},
             "caption": "Post offices within [R] km of care: [A]% on claims, [B]% on evidence."},
            {"id": 5, "time": "1:10", "duration_s": 15, "camera": india, "view": "quadrant", "grade": 2,
             "caption": "Care gap or data gap? This dataset is [88]% private clinics with websites."},
            {"id": 6, "time": "1:25", "duration_s": 15,
             "camera": {"longitude": dd["lon"], "latitude": dd["lat"], "zoom": 5.4}, "view": "quadrant", "grade": 2,
             "highlight": "data_desert",
             "caption": "Data deserts: [P]% of births still happen in institutions. Care exists; the data cannot see it."},
            {"id": 7, "time": "1:40", "duration_s": 15, "camera": india, "view": "quadrant", "grade": 2,
             "highlight": "care_desert",
             "caption": "Care deserts: poor access and poor outcomes. These are the real gaps."},
            {"id": 8, "time": "1:55", "duration_s": 20, "camera": india, "view": "quadrant", "grade": 2,
             "panel": "drawer",
             "caption": "How we know: fact tagger F1 [F1] against hand labels; outcome model improves by [delta] with access."},
            {"id": 9, "time": "2:15", "duration_s": 25, "camera": india, "view": "swarm", "grade": 2, "radius_km": 60,
             "scenario": {"plans": ["naive", "desert_aware"], "steps": 10},
             "caption": "The next ten upgrades: the naive plan wastes [k] on data deserts. The desert-aware plan does not."},
            {"id": 10, "time": "2:40", "duration_s": 15, "camera": india, "view": "quadrant", "grade": 2,
             "panel": "lists",
             "caption": "Three lists to act on: send care, send data collectors, fix cost."},
            {"id": 11, "time": "2:55", "duration_s": 10, "camera": india, "view": "distance", "grade": 2,
             "panel": "limits",
             "caption": "What this cannot tell you, and where to try it: [repo link] [map link]"},
        ],
    }


if __name__ == "__main__":
    raise SystemExit(main())
