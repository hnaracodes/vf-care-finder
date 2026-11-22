"""WP6 part 1 (threshold quadrants) and part 4 (lists), rule-based only.

Obstetrics (emoc) is the only service line with a utilization outcome in NFHS,
so it is the only line that gets quadrants. Dialysis gets a need-versus-access
readout instead (step 11), which is a different kind of comparison. ICU is
skipped. Model B (residuals, quadrant_model, p_real_gap) runs later and fills
the null columns of gold/district_quadrants.parquet.

Inputs (vf.paths folders):
  silver/nfhs.parquet, silver/nfhs_flags.parquet
  silver/district_crosswalk.parquet          (match method per NFHS district)
  gold/district_access.parquet               (WP5; emoc and dialysis, min_grade 2)

Outputs:
  gold/district_quadrants.parquet (+ .meta.json, stage "rule_only")
  reports/send_care.csv, reports/send_data_collectors.csv, reports/cost_or_quality.csv
  reports/wp6_dialysis_need_vs_access.csv
  reports/wp6_rule_quadrants.md
  reports/figures/wp6_0*_*.png (skip with --no-figures)
  every CSV also gets a .meta.json sidecar

Method:
  * Access reading: district median (over its post offices) of the straight-line
    distance to the nearest emoc facility of grade 2 (supported) or better.
  * Outcome reading: institutional_birth_5y_pct.
  * poor access  = access >= the 2/3 quantile of the 705 districts (top third)
    poor outcome = outcome < the 1/3 quantile (bottom third)
    care_desert = poor access and poor outcome; data_desert = poor access, good
    outcome; cost_or_quality = good access, poor outcome; served = neither.
  * Fragility: 16 threshold pairs (each axis cut at the median, the tertile, the
    quartile, or a fixed value: 60 km, 80%). label_stability is the share of the
    16 pairs that give a district its primary label.
  * Step 4: public share of births by quadrant. Medians with 95% intervals from
    a bootstrap over states (districts in a state are not independent),
    Mann-Whitney U, and a within-state comparison with a permutation test that
    shuffles labels only inside each state.

Run:
  .venv/bin/python scripts/wp6_rule_quadrants.py
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from scipy import stats

from vf import geo, paths

SCRIPT = "scripts/wp6_rule_quadrants.py"
VERSION = "wp6-rule-v2"
STAGE = "rule_only"

LINE = "emoc"
MIN_GRADE = 2
OUTCOME = "institutional_birth_5y_pct"
PUBLIC = "institutional_birth_in_public_facility_5y_pct"
CSEC = "births_delivered_by_csection_5y_pct"
CSEC_PUBLIC = "births_in_a_public_fac_that_were_delivered_by_csection_5y_pct"
INSURANCE = "hh_member_covered_health_insurance_pct"
OOP = "average_out_of_pocket_expenditure_per_delivery_in_a_public_fac"
CSEC_LOW = 5.0

ACCESS_Q = 2 / 3      # poor access: at or above this quantile of district medians
OUTCOME_Q = 1 / 3     # poor outcome: below this quantile of institutional births

#: sensitivity grid: each axis cut four ways
ACCESS_VARIANTS = {"median": ("q", 0.5), "tertile": ("q", ACCESS_Q),
                   "quartile": ("q", 0.75), "fixed_60km": ("fixed", 60.0)}
OUTCOME_VARIANTS = {"median": ("q", 0.5), "tertile": ("q", OUTCOME_Q),
                    "quartile": ("q", 0.25), "fixed_80pct": ("fixed", 80.0)}
PRIMARY = ("tertile", "tertile")

QUADRANTS = ("care_desert", "data_desert", "cost_or_quality", "served")
QUADRANT_NAMES = {"care_desert": "Care desert", "data_desert": "Data desert",
                  "cost_or_quality": "Cost or quality barrier", "served": "Served"}

METHOD_ORDER = ("exact", "fuzzy", "manual", "parent_district")

# dialysis need (step 11): diabetes and hypertension burden, women and men 15+
DIALYSIS_GRADE = 2
NEED_COLS = {
    "w15_plus_with_high_or_very_high_gt_140_mg_dl_blood_sugar_or_pct": "high_blood_sugar_women_pct",
    "m15_plus_with_high_or_very_high_gt_140_mg_dl_blood_sugar_or_pct": "high_blood_sugar_men_pct",
    "w15_plus_with_high_bp_sys_gte_140_mmhg_and_or_dia_gte_90_mm_pct": "high_bp_women_pct",
    "m15_plus_with_high_bp_sys_gte_140_mmhg_and_or_dia_gte_90_mm_pct": "high_bp_men_pct",
}
NEED_Q = 2 / 3        # high need: at or above this quantile

# reliability notes in the lists
FEW_POST_OFFICES = 20
HIGH_EXCLUDED_SHARE = 0.25
#: more than this share of a district's used post offices on placeholder points
#: (WP5 coord_placeholder: one point shared with other PINs' offices)
HIGH_PLACEHOLDER_SHARE = 0.5
SMALL_SURVEY_HH = 600     # NFHS-5 sampled about 900 households per district
LOW_STABILITY = 0.5
#: island territories: the nearest listed facility can be across the sea
ISLAND_STATES = ("Andaman & Nicobar Islands", "Lakshadweep")

N_BOOT = 2000
N_PERM = 10000
SEED = 20261121
SWEEP = np.round(np.linspace(0.15, 0.60, 46), 4)   # share of districts called "poor"

QUADRANT_SCHEMA = pa.schema([
    ("district_key", pa.string()),
    ("service_line", pa.string()),
    ("access_value", pa.float64()),
    ("outcome_value", pa.float64()),
    ("outcome_flag", pa.string()),
    ("predicted_outcome", pa.float64()),
    ("residual", pa.float64()),
    ("quadrant_rule", pa.string()),
    ("quadrant_model", pa.string()),
    ("p_real_gap", pa.float64()),
    ("crosswalk_method", pa.string()),
])
MODEL_B_COLUMNS = ("predicted_outcome", "residual", "quadrant_model", "p_real_gap")


# --------------------------------------------------------------------------
# small pure helpers (unit tested)
# --------------------------------------------------------------------------

def assign_quadrant(access, outcome, access_cut: float, outcome_cut: float) -> np.ndarray:
    """poor access: access >= access_cut; poor outcome: outcome < outcome_cut."""
    a = np.asarray(access, dtype=float)
    o = np.asarray(outcome, dtype=float)
    poor_a = a >= access_cut
    poor_o = o < outcome_cut
    out = np.select([poor_a & poor_o, poor_a & ~poor_o, ~poor_a & poor_o],
                    ["care_desert", "data_desert", "cost_or_quality"], "served").astype(object)
    out[np.isnan(a) | np.isnan(o)] = None
    return out


def resolve_cut(spec: tuple, values: np.ndarray) -> float:
    kind, v = spec
    return float(np.quantile(values, v)) if kind == "q" else float(v)


def crosswalk_method_by_district(cw: pd.DataFrame) -> pd.Series:
    """One string per NFHS district: the distinct match methods of the PIN
    districts that feed it, in METHOD_ORDER, joined by '+'
    (for example 'exact+parent_district')."""
    rank = {m: i for i, m in enumerate(METHOD_ORDER)}
    m = cw.dropna(subset=["district_key"])
    m = m[m["match_method"] != "unmatched"]
    return m.groupby("district_key")["match_method"].agg(
        lambda s: "+".join(sorted(set(s), key=lambda x: rank.get(x, 99))))


def within_state_diff(x, is_a, is_b, state) -> tuple[float, int]:
    """Weighted mean over states of (mean of a minus mean of b), weights
    n_a * n_b / (n_a + n_b). Only states that have both groups count."""
    x = np.asarray(x, dtype=float)
    is_a = np.asarray(is_a, dtype=bool)
    is_b = np.asarray(is_b, dtype=bool)
    state = np.asarray(state)
    num = den = 0.0
    used = 0
    for s in pd.unique(state):
        m = state == s
        a, b = x[m & is_a], x[m & is_b]
        if len(a) and len(b):
            w = len(a) * len(b) / (len(a) + len(b))
            num += w * (a.mean() - b.mean())
            den += w
            used += 1
    return (num / den if den else float("nan")), used


def within_state_perm_p(x, is_a, is_b, state, n_perm: int = N_PERM, seed: int = SEED) -> tuple[float, float, int]:
    """Two-sided permutation p for within_state_diff, shuffling the a/b labels
    only inside each state. Returns (observed, p, states used)."""
    x = np.asarray(x, dtype=float)
    is_a = np.asarray(is_a, dtype=bool)
    is_b = np.asarray(is_b, dtype=bool)
    state = np.asarray(state)
    obs, used = within_state_diff(x, is_a, is_b, state)
    if not used:
        return obs, float("nan"), 0
    rng = np.random.default_rng(seed)
    num = np.zeros(n_perm)
    den = 0.0
    for s in pd.unique(state):
        m = state == s
        na, nb = int((m & is_a).sum()), int((m & is_b).sum())
        if not (na and nb):
            continue
        v = x[m & (is_a | is_b)]
        w = na * nb / (na + nb)
        order = rng.random((n_perm, len(v))).argsort(axis=1)
        pv = v[order]
        diff = pv[:, :na].mean(axis=1) - pv[:, na:].mean(axis=1)
        num += w * diff
        den += w
    perm = num / den
    p = (1 + np.sum(np.abs(perm) >= abs(obs) - 1e-12)) / (1 + n_perm)
    return obs, float(p), used


# --------------------------------------------------------------------------
# inputs
# --------------------------------------------------------------------------

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_inputs() -> dict:
    files = {
        "silver/nfhs.parquet": paths.silver_dir() / "nfhs.parquet",
        "silver/nfhs_flags.parquet": paths.silver_dir() / "nfhs_flags.parquet",
        "silver/district_crosswalk.parquet": paths.silver_dir() / "district_crosswalk.parquet",
        "gold/district_access.parquet": paths.gold_dir() / "district_access.parquet",
    }
    missing = [k for k, p in files.items() if not p.exists()]
    if missing:
        raise SystemExit(f"blocked: missing input(s): {', '.join(missing)}")
    da_meta_path = files["gold/district_access.parquet"].with_name("district_access.parquet.meta.json")
    da_meta = json.loads(da_meta_path.read_text()) if da_meta_path.exists() else {}
    nfhs_meta_path = files["silver/nfhs.parquet"].with_name("nfhs.parquet.meta.json")
    nfhs_meta = json.loads(nfhs_meta_path.read_text()) if nfhs_meta_path.exists() else {}
    return {
        "nfhs": pd.read_parquet(files["silver/nfhs.parquet"]),
        "flags": pd.read_parquet(files["silver/nfhs_flags.parquet"]),
        "cw": pd.read_parquet(files["silver/district_crosswalk.parquet"]),
        "da": pd.read_parquet(files["gold/district_access.parquet"]),
        "da_meta": da_meta,
        "nfhs_meta": nfhs_meta,
        "inputs": {k: {"sha256": sha256(p)} for k, p in files.items()},
    }


def access_slice(da: pd.DataFrame, line: str, grade: int, prefix: str) -> pd.DataFrame:
    a = da[(da["service_line"] == line) & (da["min_grade"] == grade)].set_index("district_key")
    cols = ["dist_km_p50", "share_within_30km", "share_within_60km", "n_facilities_in_district",
            "n_post_offices", "n_post_offices_excluded", "n_post_offices_placeholder", "n_placeholder_points"]
    return a[cols].add_prefix(prefix)


def district_table(inp: dict) -> pd.DataFrame:
    nfhs, flags, cw, da = inp["nfhs"], inp["flags"], inp["cw"], inp["da"]
    n = nfhs.set_index("district_key")
    f = flags.set_index("district_key")
    keep = list(n.index)  # WP2 already dropped the duplicate row Mizoram|Chandel
    cols = ["state_ut", "district_name", OUTCOME, PUBLIC, CSEC, CSEC_PUBLIC, INSURANCE, OOP,
            "households_surveyed", *NEED_COLS]
    d = n.loc[keep, cols].copy()
    d["outcome_flag"] = f.loc[keep, OUTCOME]
    d["oop_flag"] = f.loc[keep, OOP]
    d["csection_public_flag"] = f.loc[keep, CSEC_PUBLIC]
    d = d.join(access_slice(da, LINE, MIN_GRADE, "acc_"))
    d = d.join(access_slice(da, "dialysis", DIALYSIS_GRADE, "dia_"))
    d["crosswalk_method"] = crosswalk_method_by_district(cw).reindex(d.index)
    d["public_share_of_institutional_pct"] = 100 * d[PUBLIC] / d[OUTCOME]
    d.index.name = "district_key"
    return d.reset_index()


# --------------------------------------------------------------------------
# analysis
# --------------------------------------------------------------------------

def thresholds(d: pd.DataFrame) -> dict:
    a = d["acc_dist_km_p50"].to_numpy(float)
    o = d[OUTCOME].to_numpy(float)
    return {
        "access": {k: resolve_cut(v, a) for k, v in ACCESS_VARIANTS.items()},
        "outcome": {k: resolve_cut(v, o) for k, v in OUTCOME_VARIANTS.items()},
    }


def sensitivity(d: pd.DataFrame, cuts: dict) -> dict:
    a = d["acc_dist_km_p50"].to_numpy(float)
    o = d[OUTCOME].to_numpy(float)
    primary = d["quadrant_rule"].to_numpy(object)
    grid, labels = [], []
    for ak, av in cuts["access"].items():
        for ok, ov in cuts["outcome"].items():
            q = assign_quadrant(a, o, av, ov)
            labels.append(q)
            counts = {k: int((q == k).sum()) for k in QUADRANTS}
            grid.append({"access_cut": ak, "access_km": round(av, 2), "outcome_cut": ok,
                         "outcome_pct": round(ov, 2), **counts,
                         "changed_vs_primary": int((q != primary).sum()),
                         "primary": (ak, ok) == PRIMARY})
    L = np.vstack(labels)
    stability = (L == primary[None, :]).mean(axis=0)
    core = {k: int(((primary == k) & (stability == 1.0)).sum()) for k in QUADRANTS}
    # one threshold at a time, the other at the primary cut
    one_at_a_time = [g for g in grid if g["outcome_cut"] == PRIMARY[1] or g["access_cut"] == PRIMARY[0]]
    sweep_a, sweep_o = [], []
    oq = float(np.quantile(o, OUTCOME_Q))
    aq = float(np.quantile(a, ACCESS_Q))
    for s in SWEEP:
        q = assign_quadrant(a, o, float(np.quantile(a, 1 - s)), oq)
        sweep_a.append({"share": float(s), **{k: int((q == k).sum()) for k in QUADRANTS},
                        "changed": int((q != primary).sum())})
        q = assign_quadrant(a, o, aq, float(np.quantile(o, s)))
        sweep_o.append({"share": float(s), **{k: int((q == k).sum()) for k in QUADRANTS},
                        "changed": int((q != primary).sum())})
    return {"grid": grid, "one_at_a_time": one_at_a_time, "stability": stability, "core": core,
            "sweep_access": sweep_a, "sweep_outcome": sweep_o}


def cluster_bootstrap_medians(d: pd.DataFrame, col: str, n_boot: int = N_BOOT, seed: int = SEED) -> np.ndarray:
    """(n_boot, 4) medians per quadrant, resampling whole states."""
    rng = np.random.default_rng(seed)
    sub = d[d[col].notna()]
    states = sub["state_ut"].unique()
    idx = [np.flatnonzero(sub["state_ut"].to_numpy() == s) for s in states]
    v = sub[col].to_numpy(float)
    q = sub["quadrant_rule"].to_numpy(object)
    out = np.full((n_boot, len(QUADRANTS)), np.nan)
    for b in range(n_boot):
        ii = np.concatenate([idx[k] for k in rng.integers(0, len(states), len(states))])
        vv, qq = v[ii], q[ii]
        for j, g in enumerate(QUADRANTS):
            m = vv[qq == g]
            if len(m):
                out[b, j] = np.median(m)
    return out


def public_share_check(d: pd.DataFrame) -> dict:
    metrics = {
        PUBLIC: "births in a public facility, % of all births",
        "public_share_of_institutional_pct": "public facility births, % of institutional births",
        CSEC_PUBLIC: "C-section rate in public facilities, % of public facility births",
    }
    pairs = [("data_desert", "served"), ("data_desert", "care_desert"), ("data_desert", "cost_or_quality"),
             ("care_desert", "served")]
    res = {}
    q = d["quadrant_rule"].to_numpy(object)
    for col, label in metrics.items():
        boot = cluster_bootstrap_medians(d, col)
        per_q = {}
        for j, g in enumerate(QUADRANTS):
            v = d.loc[(q == g) & d[col].notna().to_numpy(), col]
            lo, hi = np.nanpercentile(boot[:, j], [2.5, 97.5])
            per_q[g] = {"n": int(len(v)), "median": float(v.median()), "mean": float(v.mean()),
                        "ci95_state_bootstrap": [float(lo), float(hi)]}
        per_pair = {}
        for a, b in pairs:
            ja, jb = QUADRANTS.index(a), QUADRANTS.index(b)
            ok = d[col].notna().to_numpy()
            va = d.loc[(q == a) & ok, col].to_numpy(float)
            vb = d.loc[(q == b) & ok, col].to_numpy(float)
            mw = stats.mannwhitneyu(va, vb, alternative="two-sided")
            bd = boot[:, ja] - boot[:, jb]
            lo, hi = np.nanpercentile(bd, [2.5, 97.5])
            ws, p_perm, used = within_state_perm_p(d.loc[ok, col].to_numpy(float), (q == a)[ok], (q == b)[ok],
                                                   d.loc[ok, "state_ut"].to_numpy())
            per_pair[f"{a}_vs_{b}"] = {
                "median_diff": float(np.median(va) - np.median(vb)),
                "mean_diff": float(va.mean() - vb.mean()),
                "median_diff_ci95_state_bootstrap": [float(lo), float(hi)],
                "mann_whitney_u": float(mw.statistic), "mann_whitney_p": float(mw.pvalue),
                "within_state_mean_diff": float(ws), "within_state_perm_p": p_perm,
                "within_state_states_used": int(used),
            }
        res[col] = {"label": label, "by_quadrant": per_q, "pairs": per_pair}
    rho = stats.spearmanr(d["acc_dist_km_p50"], d["public_share_of_institutional_pct"])
    res["spearman_access_vs_public_share_of_institutional"] = {"rho": float(rho.statistic), "p": float(rho.pvalue)}
    return res


def csection_check(d: pd.DataFrame) -> dict:
    low = d[CSEC] < CSEC_LOW
    by_q = {g: {"n": int((d["quadrant_rule"] == g).sum()),
                "csection_below_5": int((low & (d["quadrant_rule"] == g)).sum())} for g in QUADRANTS}
    for g in QUADRANTS:
        by_q[g]["share"] = by_q[g]["csection_below_5"] / by_q[g]["n"] if by_q[g]["n"] else float("nan")
    ex = d[low].sort_values(CSEC)
    return {"threshold_pct": CSEC_LOW, "n_districts": int(low.sum()), "by_quadrant": by_q,
            "median_csection_by_quadrant": {g: float(d.loc[d["quadrant_rule"] == g, CSEC].median()) for g in QUADRANTS},
            "districts": ex[["district_key", "quadrant_rule", CSEC, OUTCOME]].to_dict(orient="records")}


def dialysis_readout(d: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    t = d[["district_key", "state_ut", "district_name", *NEED_COLS, "dia_dist_km_p50", "dia_share_within_60km",
           "dia_n_facilities_in_district", "dia_n_post_offices", "crosswalk_method"]].copy()
    t = t.rename(columns=NEED_COLS)
    t["need_index_pct"] = t[list(NEED_COLS.values())].mean(axis=1)
    need_cut = float(np.quantile(t["need_index_pct"], NEED_Q))
    acc_cut = float(np.quantile(t["dia_dist_km_p50"], ACCESS_Q))
    hi_need = t["need_index_pct"] >= need_cut
    far = t["dia_dist_km_p50"] >= acc_cut
    t["need_high"] = hi_need
    t["access_poor"] = far
    t["cell"] = np.select([hi_need & far, hi_need & ~far, ~hi_need & far],
                          ["high_need_poor_access", "high_need_good_access", "lower_need_poor_access"],
                          "lower_need_good_access")
    order = {"high_need_poor_access": 0, "high_need_good_access": 1, "lower_need_poor_access": 2,
             "lower_need_good_access": 3}
    t["_o"] = t["cell"].map(order)
    t = t.sort_values(["_o", "need_index_pct"], ascending=[True, False]).drop(columns="_o")
    rho = stats.spearmanr(t["need_index_pct"], t["dia_dist_km_p50"])
    cells = t["cell"].value_counts().to_dict()
    top_states = (t[t["cell"] == "high_need_poor_access"]["state_ut"].value_counts().head(6).to_dict())
    info = {"need_cut_pct": need_cut, "access_cut_km": acc_cut, "min_grade": DIALYSIS_GRADE,
            "need_definition": "mean of four NFHS percentages: women and men 15+ with high or very high blood "
                               "sugar or on medicine, women and men 15+ with elevated blood pressure or on medicine",
            "cells": {k: int(v) for k, v in cells.items()},
            "spearman_need_vs_distance": {"rho": float(rho.statistic), "p": float(rho.pvalue)},
            "high_need_poor_access_top_states": {k: int(v) for k, v in top_states.items()},
            "median_need_pct": float(t["need_index_pct"].median()),
            "median_distance_km": float(t["dia_dist_km_p50"].median())}
    return t, info


def coverage(inp: dict, d: pd.DataFrame) -> dict:
    cw, nfhs = inp["cw"], inp["nfhs"]
    um = cw[cw["match_method"] == "unmatched"]
    meta_nokey = inp["da_meta"].get("geocoded_without_district_key")
    pd_feed = cw[cw["match_method"] == "parent_district"]
    nm = inp.get("nfhs_meta", {})
    dropped = nm.get("dropped_rows", {})
    lost = inp["da_meta"].get("facilities_lost_no_coords", {}).get(LINE, {}).get(str(MIN_GRADE), {})
    fcc = inp["da_meta"].get("facility_coordinate_check", {}).get("coord_check_counts", {})
    return {
        "nfhs_districts": int(len(nfhs)),
        "nfhs_raw_rows": nm.get("raw_rows"),
        "nfhs_dropped_rows": {k: v.get("copy_of") for k, v in dropped.items()},
        "facility_coord_check_counts": fcc,
        "emoc_g2_facilities": lost.get("graded"),
        "emoc_g2_moved_to_pin": lost.get("moved_to_pin"),
        "emoc_g2_dropped_by_pin_check": lost.get("lost_excluded_by_pin_check"),
        "emoc_g2_missing_coords": (lost.get("lost_no_coords", 0) - lost.get("lost_excluded_by_pin_check", 0)
                                   if lost else None),
        "scored": int(d["quadrant_rule"].notna().sum()),
        "scored_missing_access": int(d["acc_dist_km_p50"].isna().sum()),
        "scored_missing_outcome": int(d[OUTCOME].isna().sum()),
        "pin_districts": int(len(cw)),
        "pin_districts_unmatched": int(len(um)),
        "pin_districts_unmatched_list": [f"{r.state_pin}|{r.district_pin}" for r in um.itertuples()],
        "geocoded_post_offices_without_district_key": meta_nokey,
        "pin_districts_parent_district": int(len(pd_feed)),
        "nfhs_districts_with_parent_district_feed": int(d["crosswalk_method"].fillna("").str.contains("parent").sum()),
        "crosswalk_method_counts": {k: int(v) for k, v in d["crosswalk_method"].value_counts().items()},
        "outcome_flag_counts": {k: int(v) for k, v in d["outcome_flag"].value_counts().items()},
        "outcome_low_sample_or_suppressed": int(d["outcome_flag"].isin(["low_sample", "suppressed"]).sum()),
        "few_post_offices_lt20": int((access_values(d) < FEW_POST_OFFICES).sum()),
        "excluded_share_gt25pct": int((excluded_share(d) > HIGH_EXCLUDED_SHARE).sum()),
        "placeholder_share_gt50pct": int((placeholder_share(d) > HIGH_PLACEHOLDER_SHARE).sum()),
        "placeholder_share_gt50pct_list": d.loc[placeholder_share(d) > HIGH_PLACEHOLDER_SHARE, "district_key"]
                                            .tolist(),
        "facility_pin_check_km": geo.FACILITY_PIN_CHECK_KM,
        "facility_pin_far_km": geo.FACILITY_PIN_KM,
        "small_survey_lt600_households": int((d["households_surveyed"] < SMALL_SURVEY_HH).sum()),
    }


def excluded_share(d: pd.DataFrame) -> pd.Series:
    tot = d["acc_n_post_offices"] + d["acc_n_post_offices_excluded"]
    return d["acc_n_post_offices_excluded"] / tot.where(tot > 0)


def placeholder_share(d: pd.DataFrame) -> pd.Series:
    """Share of a district's used post offices that sit on a placeholder point."""
    return d["acc_n_post_offices_placeholder"] / d["acc_n_post_offices"].where(d["acc_n_post_offices"] > 0)


def access_values(d: pd.DataFrame) -> pd.Series:
    """Values behind the district's access median: used offices, each placeholder point once (WP5)."""
    return d["acc_n_post_offices"] - d["acc_n_post_offices_placeholder"] + d["acc_n_placeholder_points"]


def reliability_notes(d: pd.DataFrame) -> pd.Series:
    ex = excluded_share(d)
    ph = placeholder_share(d)
    nv = access_values(d)
    notes = []
    for i, r in d.iterrows():
        n = []
        if r["state_ut"] in ISLAND_STATES:
            n.append("island district: distance runs across the sea to the mainland or another island")
        if r["outcome_flag"] != "ok":
            n.append(f"outcome is {r['outcome_flag']}")
        m = r["crosswalk_method"] or ""
        if "parent_district" in m:
            n.append("pools post offices of district(s) split off after the survey")
        if "fuzzy" in m or "manual" in m:
            n.append(f"name matched by {'fuzzy match' if 'fuzzy' in m else 'hand'}")
        if nv.loc[i] < FEW_POST_OFFICES:
            if nv.loc[i] < r["acc_n_post_offices"]:
                n.append(f"access from only {int(nv.loc[i])} post office locations (a shared placeholder point "
                         "counts once)")
            else:
                n.append(f"access from only {int(r['acc_n_post_offices'])} post offices")
        if ex.loc[i] > HIGH_EXCLUDED_SHARE:
            n.append(f"coordinate check dropped {100 * ex.loc[i]:.0f}% of post offices")
        if ph.loc[i] > HIGH_PLACEHOLDER_SHARE:
            pts = int(r["acc_n_placeholder_points"])
            n.append(f"{100 * ph.loc[i]:.0f}% of post offices sit on {pts} placeholder point"
                     f"{'s' if pts != 1 else ''} shared with other PINs (usually the district town), counted once "
                     "each")
        if r["households_surveyed"] < SMALL_SURVEY_HH:
            n.append(f"small survey ({int(r['households_surveyed'])} households)")
        if r["label_stability"] < LOW_STABILITY:
            n.append("label changes under most threshold choices")
        notes.append("; ".join(n))
    return pd.Series(notes, index=d.index)


# --------------------------------------------------------------------------
# lists
# --------------------------------------------------------------------------

LIST_COLUMNS = {
    "district_key": "district_key",
    "state_ut": "state",
    "district_name": "district",
    "quadrant_rule": "quadrant_rule",
    "label_stability": "label_stability",
    "acc_dist_km_p50": "emoc_g2_dist_km_p50",
    "acc_share_within_60km": "emoc_g2_share_within_60km",
    "acc_n_facilities_in_district": "emoc_g2_facilities_in_district",
    "acc_n_post_offices": "n_post_offices",
    "acc_n_post_offices_excluded": "n_post_offices_excluded",
    "acc_n_post_offices_placeholder": "n_post_offices_placeholder",
    OUTCOME: "institutional_birth_pct",
    "outcome_flag": "outcome_flag",
    PUBLIC: "public_facility_birth_pct",
    "public_share_of_institutional_pct": "public_share_of_institutional_pct",
    CSEC: "csection_pct",
    "csection_below_5pct": "csection_below_5pct",
    CSEC_PUBLIC: "public_facility_csection_pct",
    "residual": "residual",
    "crosswalk_method": "crosswalk_method",
    "reliability_notes": "reliability_notes",
}
LIST_SPECS = {
    "send_care": {"quadrant": "care_desert", "sort": [(OUTCOME, True), ("acc_dist_km_p50", False)],
                  "rank_rule": "institutional births, lowest first (ties: farther first)", "extra": []},
    "send_data_collectors": {"quadrant": "data_desert", "sort": [("acc_dist_km_p50", False), (OUTCOME, False)],
                             "rank_rule": "distance to the nearest grade 2+ EmOC facility, farthest first",
                             "extra": []},
    "cost_or_quality": {"quadrant": "cost_or_quality", "sort": [(OUTCOME, True), ("acc_dist_km_p50", True)],
                        "rank_rule": "institutional births, lowest first (ties: nearer first)",
                        "extra": [(INSURANCE, "health_insurance_pct"), (OOP, "oop_per_public_delivery_inr"),
                                  ("oop_flag", "oop_flag")]},
}


def build_lists(d: pd.DataFrame) -> dict[str, pd.DataFrame]:
    out = {}
    for name, spec in LIST_SPECS.items():
        sub = d[d["quadrant_rule"] == spec["quadrant"]].copy()
        by = [c for c, _ in spec["sort"]] + ["district_key"]
        asc = [a for _, a in spec["sort"]] + [True]
        sub = sub.sort_values(by, ascending=asc)
        cols = dict(LIST_COLUMNS)
        for src, dst in spec["extra"]:
            cols[src] = dst
        t = sub[list(cols)].rename(columns=cols)
        t.insert(0, "rank", np.arange(1, len(t) + 1))
        for c in ("emoc_g2_dist_km_p50", "public_share_of_institutional_pct"):
            t[c] = t[c].round(1)
        t["emoc_g2_share_within_60km"] = t["emoc_g2_share_within_60km"].round(3)
        t["label_stability"] = t["label_stability"].round(3)
        out[name] = t.reset_index(drop=True)
    return out


# --------------------------------------------------------------------------
# output helpers
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
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return o


def write_json(path: Path, obj) -> None:
    body = json.dumps(_clean(obj), indent=2, default=str) + "\n"
    write_atomic(path, lambda p: p.write_text(body))


def write_csv(df: pd.DataFrame, path: Path, meta: dict) -> None:
    write_atomic(path, lambda p: df.to_csv(p, index=False))
    write_json(path.with_name(path.name + ".meta.json"),
               dict(meta, output=rel(path), rows=int(len(df)), columns=list(df.columns)))


def rel(path: Path) -> str:
    for root in (paths.data_dir(), paths.code_root()):
        try:
            return str(path.resolve().relative_to(root.resolve()))
        except ValueError:
            continue
    return str(path)


# --------------------------------------------------------------------------
# figures
# --------------------------------------------------------------------------

def make_figures(d: pd.DataFrame, cuts: dict, sens: dict, pub: dict, csec: dict, dia: pd.DataFrame,
                 dia_info: dict, grades_method: str, fig_dir: Path) -> list[Path]:
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib.lines import Line2D
    from matplotlib.ticker import FixedLocator, FuncFormatter, NullLocator

    from vf import plotstyle as ps

    ps.use()
    qcol = {"care_desert": "#c98500", "data_desert": "#3987e5", "cost_or_quality": "#d55181",
            "served": ps.CONTEXT}
    qtext = {"care_desert": "Care desert", "data_desert": "Data desert",
             "cost_or_quality": "Cost or quality", "served": "Served"}
    a_cut, o_cut = cuts["access"][PRIMARY[0]], cuts["outcome"][PRIMARY[1]]
    counts = d["quadrant_rule"].value_counts()
    src = (f"Source: NFHS-5 (2019-21) district fact sheets; distances from WP5 (straight-line km from each "
           f"post office to the nearest facility graded from its web pages, {grades_method} grades). "
           "A district's access value is the median over its post offices. Post offices are not people.")
    out = []
    km_ticks = [2, 5, 10, 20, 50, 100, 200, 500, 1000]

    def km_axis(ax, lo, hi):
        ax.set_xscale("log")
        ax.set_xlim(lo, hi)
        ax.xaxis.set_major_locator(FixedLocator([t for t in km_ticks if lo <= t <= hi]))
        ax.xaxis.set_minor_locator(NullLocator())
        ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}"))

    # ---- 1. quadrant scatter
    n_data = int(counts.get("data_desert", 0))
    n_care = int(counts.get("care_desert", 0))
    fig, gs = ps.figure(
        7.6,
        f"{n_data} districts look far from obstetric care in the data but still deliver most births in "
        f"facilities; {n_care} are far and deliver fewer",
        "Each dot is a district. Across: median distance from its post offices to the nearest facility with "
        "supported evidence of emergency obstetric care (grade 2 or better). Up: births in a health facility "
        "in the 5 years before the survey.",
        note=src + f" Lines: top third of distance ({a_cut:.1f} km or more) and bottom third of institutional "
                   f"births (below {o_cut:.1f}%).",
        legend_rows=1, x_axis_band_in=0.6)
    ax = fig.add_subplot(gs[0, 0])
    x = d["acc_dist_km_p50"].clip(lower=1.0)
    y = d[OUTCOME]
    for g in ("served", "cost_or_quality", "care_desert", "data_desert"):
        m = d["quadrant_rule"] == g
        ax.scatter(x[m], y[m], s=ps.px(8) ** 2, c=qcol[g], edgecolors=ps.SURFACE, linewidths=ps.px(1),
                   zorder=3 if g != "served" else 2)
    low = d[CSEC] < CSEC_LOW
    ax.scatter(x[low], y[low], s=ps.px(15) ** 2, facecolors="none", edgecolors=ps.INK, linewidths=ps.px(1.2),
               zorder=4)
    lo_x = max(1.0, float(x.min()) * 0.8)
    hi_x = float(x.max()) * 1.25
    km_axis(ax, lo_x, hi_x)
    ax.set_ylim(max(0, float(y.min()) - 4), 101)
    ax.axvline(a_cut, color=ps.INK_2, lw=ps.px(1.5), zorder=1)
    ax.axhline(o_cut, color=ps.INK_2, lw=ps.px(1.5), zorder=1)
    ps.clean_axes(ax, grid="y")
    ps.pct_axis(ax, "y")
    ax.set_xlabel("Median distance to grade 2+ emergency obstetric care, km (log scale)")
    ax.set_ylabel("Institutional births")
    lab = {"served": (0.02, 0.97, "left", "top"), "data_desert": (0.98, 0.97, "right", "top"),
           "cost_or_quality": (0.02, 0.03, "left", "bottom"), "care_desert": (0.98, 0.03, "right", "bottom")}
    for g, (tx, ty, ha, va) in lab.items():
        ax.text(tx, ty, f"{qtext[g]}: {int(counts.get(g, 0))}", transform=ax.transAxes, ha=ha, va=va,
                fontsize=12.5, weight="bold", color=ps.INK,
                bbox=dict(boxstyle="round,pad=0.25", facecolor=ps.SURFACE, edgecolor="none", alpha=0.85))
    handles = [Line2D([0], [0], color=qcol[g], lw=0, marker="o", markersize=ps.px(9),
                      markeredgecolor=ps.SURFACE, label=qtext[g]) for g in QUADRANTS]
    handles.append(Line2D([0], [0], color=ps.INK, lw=0, marker="o", markersize=ps.px(13), markerfacecolor="none",
                          markeredgewidth=ps.px(1.2), label=f"C-section rate under 5% ({int(low.sum())})"))
    ax.legend(handles=handles, loc="lower left", bbox_to_anchor=(0.0, 1.0), ncol=5, frameon=False,
              borderaxespad=0.3, fontsize=11)
    p = fig_dir / "wp6_01_quadrants_emoc.png"
    out.append(ps.save(fig, p))

    # ---- 2. threshold sensitivity sweeps
    core = sens["core"]
    fig, gs = ps.figure(
        6.3,
        f"Quadrant counts depend on where the cuts sit; {core['data_desert']} of {n_data} data deserts and "
        f"{core['care_desert']} of {n_care} care deserts keep their label under all 16 cut pairs",
        "Districts in each quadrant as one cut moves and the other stays at a third. Left: share of districts "
        "called poor access. Right: share called poor outcome. The black line counts districts whose label "
        "differs from the chosen split.",
        note="Chosen split: a third on each axis (vertical line). The 16 cut pairs: each axis at the median, the "
             "tertile, the quartile, or a fixed value (60 km; 80% institutional births).",
        ncols=2, wspace=0.12, legend_rows=1)
    keys = list(QUADRANTS) + ["changed"]
    colors = dict(qcol, changed=ps.INK)
    names = dict(qtext, changed="Label differs from chosen split")
    ymax = max(max(r[k] for r in sens["sweep_access"] + sens["sweep_outcome"] for k in keys), 1) * 1.08
    for j, (key, title) in enumerate([("sweep_access", "Moving the access cut"),
                                      ("sweep_outcome", "Moving the outcome cut")]):
        ax = fig.add_subplot(gs[0, j])
        sw = sens[key]
        xs = [100 * r["share"] for r in sw]
        for k in keys:
            ys = [r[k] for r in sw]
            ax.plot(xs, ys, color=colors[k], lw=ps.px(2) if k != "changed" else ps.px(1.5),
                    zorder=3 if k != "changed" else 4)
        ax.axvline(100 / 3, color=ps.AXIS, lw=ps.px(1.5), zorder=1)
        ax.set_ylim(0, ymax)
        ax.set_xlim(xs[0], xs[-1])
        ps.clean_axes(ax, grid="y")
        ps.pct_axis(ax, "x")
        ax.set_xlabel("Share of districts called poor " + ("access" if j == 0 else "outcome"))
        if j == 0:
            ax.set_ylabel("Districts")
            ps.panel_title(ax, title, [(names[k], colors[k], "line") for k in keys], ncol=5,
                           legend_fontsize=11)
        else:
            ax.tick_params(axis="y", labelleft=False)
            ps.panel_title(ax, title, reserve_rows=1)
    p = fig_dir / "wp6_02_threshold_sensitivity.png"
    out.append(ps.save(fig, p))

    # ---- 3. public share by quadrant (step 4)
    pp = pub[PUBLIC]["pairs"]["data_desert_vs_served"]
    bq = pub[PUBLIC]["by_quadrant"]
    fig, gs = ps.figure(
        6.6,
        f"Data-desert districts deliver more births in public facilities: median "
        f"{bq['data_desert']['median']:.0f}% of all births, against {bq['served']['median']:.0f}% in served "
        "districts",
        "Each dot is a district; the bar marks the quadrant median with its 95% interval (bootstrap over "
        "states). Left: public facility births as a share of all births. Right: as a share of births in any "
        "facility.",
        note=f"Within the same state the data-desert minus served gap is "
             f"{pp['within_state_mean_diff']:+.1f} points (permutation p = {pp['within_state_perm_p']:.3g}). "
             "The public share also rises with rurality, so this is consistent with, not proof of, public care "
             "the facility data cannot see. Source: NFHS-5 (2019-21).",
        ncols=2, wspace=0.18, legend_rows=0)
    rng = np.random.default_rng(SEED)
    for j, (col, title) in enumerate([(PUBLIC, "Public facility births, % of all births"),
                                      ("public_share_of_institutional_pct",
                                       "Public facility births, % of institutional births")]):
        ax = fig.add_subplot(gs[0, j])
        for i, g in enumerate(QUADRANTS):
            v = d.loc[d["quadrant_rule"] == g, col].dropna().to_numpy()
            jit = rng.uniform(-0.22, 0.22, len(v))
            ax.scatter(i + jit, v, s=ps.px(6) ** 2, c=qcol[g] if g != "served" else ps.CONTEXT, alpha=0.45,
                       edgecolors="none", zorder=2)
            st = pub[col]["by_quadrant"][g]
            lo, hi = st["ci95_state_bootstrap"]
            ax.plot([i, i], [lo, hi], color=ps.INK, lw=ps.px(2.5), zorder=4, solid_capstyle="butt")
            ax.plot([i - 0.3, i + 0.3], [st["median"], st["median"]], color=ps.INK, lw=ps.px(2.5), zorder=4)
            ax.annotate(f"{st['median']:.0f}%", xy=(i + 0.3, st["median"]), xytext=(ps.px(4), 0),
                        textcoords="offset points", va="center", ha="left", fontsize=11, weight="bold",
                        color=ps.INK)
        ax.set_xticks(range(len(QUADRANTS)), [f"{qtext[g]}\n(n = {int(counts.get(g, 0))})" for g in QUADRANTS])
        ax.tick_params(axis="x", length=0)
        ax.set_xlim(-0.55, len(QUADRANTS) - 0.35)
        ax.set_ylim(0, 102)
        ps.clean_axes(ax, grid="y")
        ps.pct_axis(ax, "y")
        ps.panel_title(ax, title)
    p = fig_dir / "wp6_03_public_share_by_quadrant.png"
    out.append(ps.save(fig, p))

    # ---- 4. quadrants by state
    ct = pd.crosstab(d["state_ut"], d["quadrant_rule"]).reindex(columns=list(QUADRANTS), fill_value=0)
    ct = ct[ct.sum(axis=1) >= 5]
    ct = ct.sort_values(["data_desert", "care_desert"], ascending=False)
    top = ct.head(3).index.tolist()
    fig, gs = ps.figure(
        0.32 * len(ct) + 2.6,
        f"Deserts cluster by state: {', '.join(top[:-1])} and {top[-1]} hold the most data deserts",
        "Districts per state by rule quadrant, states with 5 or more districts, sorted by data-desert count. "
        "Segment labels are district counts.",
        note="Thresholds are national, so a state with few web-listed facilities can be far from evidenced care "
             "in every district. Source: NFHS-5; WP5 distances.",
        legend_rows=1, left_in=2.2, panel_titles=False, x_axis_band_in=0.15)
    ax = fig.add_subplot(gs[0, 0])
    ps.stacked_hbars(ax, list(ct.index), ct.to_numpy(), [qcol[g] for g in QUADRANTS],
                     label_fmt=lambda v, tot: f"{int(v)}", fontsize=9.5,
                     label_parts=[True, True, True, False])
    ax.tick_params(axis="y", labelsize=10.5)
    ps.clean_axes(ax, keep=("left",))
    ax.set_xticks([])
    ps.legend(ax, [(qtext[g], qcol[g]) for g in QUADRANTS])
    p = fig_dir / "wp6_04_quadrants_by_state.png"
    out.append(ps.save(fig, p))

    # ---- 5. dialysis need versus access
    n_hf = dia_info["cells"].get("high_need_poor_access", 0)
    fig, gs = ps.figure(
        7.0,
        f"Dialysis, need versus access: {n_hf} districts are in the top third for diabetes and blood pressure "
        "burden and the top third for distance",
        "A different comparison from obstetrics: need is not an outcome, so this cannot separate a care gap "
        "from a data gap. Across: median distance to the nearest grade 2+ dialysis facility. Up: mean of the "
        "four NFHS diabetes and blood pressure percentages (women and men 15+).",
        note=src, legend_rows=1, x_axis_band_in=0.6)
    ax = fig.add_subplot(gs[0, 0])
    xx = dia["dia_dist_km_p50"].clip(lower=1.0)
    yy = dia["need_index_pct"]
    hf = dia["cell"] == "high_need_poor_access"
    ax.scatter(xx[~hf], yy[~hf], s=ps.px(8) ** 2, c=ps.CONTEXT, edgecolors=ps.SURFACE, linewidths=ps.px(1),
               zorder=2)
    ax.scatter(xx[hf], yy[hf], s=ps.px(8) ** 2, c=ps.VIOLET, edgecolors=ps.SURFACE, linewidths=ps.px(1),
               zorder=3)
    km_axis(ax, max(1.0, float(xx.min()) * 0.8), float(xx.max()) * 1.25)
    ax.axvline(dia_info["access_cut_km"], color=ps.INK_2, lw=ps.px(1.5), zorder=1)
    ax.axhline(dia_info["need_cut_pct"], color=ps.INK_2, lw=ps.px(1.5), zorder=1)
    ps.clean_axes(ax, grid="y")
    ps.pct_axis(ax, "y")
    ax.set_xlabel("Median distance to grade 2+ dialysis, km (log scale)")
    ax.set_ylabel("Need index (mean of 4 percentages)")
    # label the four highest-need districts in the cell, stacked in the empty upper right with leader lines,
    # and the farthest one beside its dot
    ax.set_ylim(float(yy.min()) - 1, float(yy.max()) + 2)
    top4 = dia[hf].head(4)
    y_top = float(yy.max()) + 0.5
    for i, r in enumerate(top4.itertuples()):
        ax.annotate(f"{r.district_name} ({r.state_ut}), {r.need_index_pct:.1f}%, {r.dia_dist_km_p50:.0f} km",
                    xy=(max(r.dia_dist_km_p50, 1.0), r.need_index_pct), xytext=(95, y_top - 1.7 * i),
                    textcoords="data", ha="left", va="center", fontsize=9.5, color=ps.INK_2,
                    arrowprops=dict(arrowstyle="-", color=ps.MUTED, lw=ps.px(1), shrinkA=2, shrinkB=4))
    far = dia[hf].sort_values("dia_dist_km_p50").iloc[-1]
    if far["district_key"] not in set(top4["district_key"]):
        ax.annotate(f"{far['district_name']}, {far['dia_dist_km_p50']:.0f} km",
                    xy=(far["dia_dist_km_p50"], far["need_index_pct"]), xytext=(0, ps.px(12)),
                    textcoords="offset points", ha="right", va="bottom", fontsize=9.5, color=ps.INK_2)
    ps.legend(ax, [(f"High need, poor access ({n_hf})", ps.VIOLET, "dot"), ("Other districts", ps.CONTEXT, "dot")])
    p = fig_dir / "wp6_05_dialysis_need_vs_access.png"
    out.append(ps.save(fig, p))
    return out


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------

def fmt_p(p: float) -> str:
    if p is None or not np.isfinite(p):
        return "n/a"
    return f"{p:.1e}" if p < 0.001 else f"{p:.3f}"


def md_table(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    lines = ["| " + " | ".join(str(c) for c in cols) + " |", "| " + " | ".join("---" for _ in cols) + " |"]
    for r in df.itertuples(index=False):
        lines.append("| " + " | ".join("" if (isinstance(v, float) and not np.isfinite(v)) else str(v)
                                       for v in r) + " |")
    return "\n".join(lines)


def write_report(path: Path, d: pd.DataFrame, cuts: dict, sens: dict, pub: dict, csec: dict, dia_info: dict,
                 cov: dict, lists: dict, figs: list[Path], grades_method: str, spearman_ao: dict,
                 created: str) -> None:
    counts = {g: int((d["quadrant_rule"] == g).sum()) for g in QUADRANTS}
    n = int(d["quadrant_rule"].notna().sum())
    a_cut, o_cut = cuts["access"]["tertile"], cuts["outcome"]["tertile"]
    P = pub[PUBLIC]
    S = pub["public_share_of_institutional_pct"]
    C = pub[CSEC_PUBLIC]
    pp, sp, cp = (P["pairs"]["data_desert_vs_served"], S["pairs"]["data_desert_vs_served"],
                  C["pairs"]["data_desert_vs_served"])
    bq, sq, cq = P["by_quadrant"], S["by_quadrant"], C["by_quadrant"]
    holds = pp["median_diff"] > 0 and pp["mann_whitney_p"] < 0.05
    within_holds = pp["within_state_mean_diff"] > 0 and pp["within_state_perm_p"] < 0.05
    grid = pd.DataFrame(sens["grid"])
    oat = pd.DataFrame(sens["one_at_a_time"])
    by_state = (d[d["quadrant_rule"] == "data_desert"]["state_ut"].value_counts())
    top_states = ", ".join(f"{s} {c}" for s, c in by_state.head(5).items())
    share_top5 = by_state.head(5).sum() / max(counts["data_desert"], 1)
    by_state_cost = d[d["quadrant_rule"] == "cost_or_quality"]["state_ut"].value_counts()
    top_cost = ", ".join(f"{s} {c}" for s, c in by_state_cost.head(3).items()) + (
        f" of {counts['cost_or_quality']}")
    cs = csec["by_quadrant"]
    stab = sens["stability"]

    L = []
    w = L.append
    w("# WP6 part 1: rule-based quadrants for emergency obstetric care")
    w("")
    w(f"Stage: `{STAGE}`. Model B has not run, so `predicted_outcome`, `residual`, `quadrant_model` and "
      f"`p_real_gap` are null in `gold/district_quadrants.parquet`. Grades behind the distances: `{grades_method}` "
      f"(WP3 regex baseline until WP4 replaces it). Generated {created} by `{SCRIPT}`.")
    w("")
    w("## Findings")
    w("")
    w(f"1. **The split.** Of {n} scored districts, {counts['care_desert']} are care deserts, "
      f"{counts['data_desert']} data deserts, {counts['cost_or_quality']} cost-or-quality barriers and "
      f"{counts['served']} served. Poor access means the district's median post office is {a_cut:.1f} km or more "
      f"from the nearest facility with supported evidence of emergency obstetric care (the top third). Poor "
      f"outcome means institutional births below {o_cut:.1f}% (the bottom third).")
    verb = "deliver more" if pp["median_diff"] > 0 else "do not deliver more"
    w(f"2. **Public share (step 4): data deserts {verb} of their births in public facilities.** Median "
      f"{bq['data_desert']['median']:.1f}% of all births (95% interval {bq['data_desert']['ci95_state_bootstrap'][0]:.1f} "
      f"to {bq['data_desert']['ci95_state_bootstrap'][1]:.1f}, bootstrap over states) against "
      f"{bq['served']['median']:.1f}% in served districts ({bq['served']['ci95_state_bootstrap'][0]:.1f} to "
      f"{bq['served']['ci95_state_bootstrap'][1]:.1f}): a gap of {pp['median_diff']:+.1f} points (interval "
      f"{pp['median_diff_ci95_state_bootstrap'][0]:+.1f} to {pp['median_diff_ci95_state_bootstrap'][1]:+.1f}), "
      f"Mann-Whitney p = {fmt_p(pp['mann_whitney_p'])}. As a share of institutional births the medians are "
      f"{sq['data_desert']['median']:.1f}% and {sq['served']['median']:.1f}% (p = {fmt_p(sp['mann_whitney_p'])}). "
      f"Both groups have high institutional births, so this compares like with like on the outcome.")
    w(f"3. **Within states the gap is {'smaller but still there' if within_holds else 'not clear'}.** In means, "
      f"the overall data-desert minus served gap in the public share of all births is {pp['mean_diff']:+.1f} "
      f"points; comparing districts of the same state it is {pp['within_state_mean_diff']:+.1f} points "
      f"(permutation p = {fmt_p(pp['within_state_perm_p'])}, {pp['within_state_states_used']} states with both "
      f"kinds). For the public share of institutional births: {sp['mean_diff']:+.1f} overall, "
      f"{sp['within_state_mean_diff']:+.1f} within states (p = {fmt_p(sp['within_state_perm_p'])}). So most of the "
      f"gap is between states: {top_states} hold {100 * share_top5:.0f}% of the data deserts. "
      f"Cost-or-quality districts concentrate elsewhere: {top_cost}.")
    lo_c, hi_c = sorted([cq["care_desert"]["median"], cq["served"]["median"]])
    between = lo_c <= cq["data_desert"]["median"] <= hi_c
    w(f"4. **Public facilities in data deserts do some surgical delivery, less than in served districts.** Median "
      f"public-facility C-section rate: {cq['data_desert']['median']:.1f}% in data deserts, "
      f"{cq['served']['median']:.1f}% in served districts, {cq['care_desert']['median']:.1f}% in care deserts"
      f"{' (data deserts sit between the two)' if between else ''}. Against served districts the gap is "
      f"{cp['median_diff']:+.1f} points (interval {cp['median_diff_ci95_state_bootstrap'][0]:+.1f} to "
      f"{cp['median_diff_ci95_state_bootstrap'][1]:+.1f}); within states it is {cp['within_state_mean_diff']:+.1f} "
      f"points (p = {fmt_p(cp['within_state_perm_p'])}). This supports public obstetric surgery existing where the "
      "data shows none nearby, but it is not at served-district levels.")
    w(f"5. **The split is moderately fragile.** {sens['core']['data_desert']} of {counts['data_desert']} data "
      f"deserts and {sens['core']['care_desert']} of {counts['care_desert']} care deserts keep their label under "
      f"all 16 threshold pairs. Moving only the access cut from a third to a half changes "
      f"{int(oat[(oat.access_cut == 'median') & (oat.outcome_cut == 'tertile')]['changed_vs_primary'].iloc[0])} "
      f"labels; moving only the outcome cut to a half changes "
      f"{int(oat[(oat.access_cut == 'tertile') & (oat.outcome_cut == 'median')]['changed_vs_primary'].iloc[0])}. "
      f"Median label stability is {np.median(stab):.2f}; {int((stab < LOW_STABILITY).sum())} districts keep their "
      f"label in fewer than half the pairs.")
    w(f"6. **Access and outcome are only loosely linked.** Spearman rho between distance and institutional "
      f"births is {spearman_ao['rho']:.2f} (p = {fmt_p(spearman_ao['p'])}). Distance and the public share of "
      f"institutional births correlate at rho = "
      f"{pub['spearman_access_vs_public_share_of_institutional']['rho']:.2f}: the farther a district is from "
      f"evidenced care in this private-heavy dataset, the more of its facility births tend to be in public "
      f"facilities.")
    w(f"7. **C-section under 5% (step 2): {csec['n_districts']} districts.** {cs['care_desert']['csection_below_5']} "
      f"are care deserts ({100 * cs['care_desert']['share']:.0f}% of them), {cs['cost_or_quality']['csection_below_5']} "
      f"cost-or-quality, {cs['data_desert']['csection_below_5']} data deserts and "
      f"{cs['served']['csection_below_5']} served. A rate under 5% signals unmet need for surgical obstetric care "
      f"whatever the institutional birth rate.")
    w(f"8. **Dialysis need versus access (a different kind of comparison).** "
      f"{dia_info['cells'].get('high_need_poor_access', 0)} districts are in the top third of diabetes and blood "
      f"pressure burden (need index {dia_info['need_cut_pct']:.1f}% or more) and the top third of distance to grade "
      f"2+ dialysis ({dia_info['access_cut_km']:.1f} km or more). Need and distance correlate at rho = "
      f"{dia_info['spearman_need_vs_distance']['rho']:.2f}: high-burden districts tend to be closer to listed "
      f"dialysis. There is no dialysis outcome in NFHS, so this cannot say whether the far ones lack care or lack "
      f"data.")
    dropped = "; ".join(f"{k} (a copy of {v})" for k, v in cov["nfhs_dropped_rows"].items()) or "none"
    w(f"9. **Coverage.** {cov['scored']} of {cov['nfhs_districts']} NFHS districts are scored. WP2 dropped the raw "
      f"file's duplicate row{'s' if len(cov['nfhs_dropped_rows']) > 1 else ''} {dropped}, so Mizoram's Saiha has "
      f"no survey data. {cov['pin_districts_unmatched']} of "
      f"{cov['pin_districts']} PIN-directory districts are unmatched "
      f"({', '.join(cov['pin_districts_unmatched_list'])}); their {cov['geocoded_post_offices_without_district_key']} "
      f"geocoded post offices have no NFHS outcome and should show as 'no survey data'. "
      f"{cov['outcome_low_sample_or_suppressed']} scored districts have a low-sample or suppressed institutional "
      f"birth value.")
    w("")
    w("## Thresholds and why")
    w("")
    w(f"- Poor access: `dist_km_p50 >= {a_cut:.2f}` km, the 2/3 quantile of the {n} district medians (emoc, "
      f"min_grade 2). Why: the work package's default (top third); the distribution has a long right tail "
      f"(median {cuts['access']['median']:.1f} km, 3/4 quantile {cuts['access']['quartile']:.1f} km), and the "
      "top third keeps the far tail without calling half the country poor access.")
    w(f"- Poor outcome: `institutional_birth_5y_pct < {o_cut:.2f}`, the 1/3 quantile. Why: the distribution is "
      f"skewed toward 100% (median {cuts['outcome']['median']:.1f}%), so a fixed cut such as 80% would call only "
      f"{int(grid[(grid.access_cut == 'tertile') & (grid.outcome_cut == 'fixed_80pct')][['care_desert', 'cost_or_quality']].sum(axis=1).iloc[0])} "
      f"districts poor; the bottom third mirrors the access cut and sits close to the published NFHS-5 national "
      f"figure of 88.6% (as sourced by WP2).")
    w(f"- C-section flag: `{CSEC} < {CSEC_LOW:.0f}`. Why: 5% is the lower end of the 5 to 15% range in the UN "
      "handbook for monitoring emergency obstetric care (WHO, UNFPA, UNICEF, AMDD, 2009); below it, some women "
      "who need a C-section are not getting one.")
    w(f"- Dialysis: high need is the top third of the need index ({dia_info['need_cut_pct']:.2f}%); poor access "
      f"the top third of district median distance to grade 2+ dialysis ({dia_info['access_cut_km']:.2f} km).")
    w("- Quantiles are numpy linear interpolation over the scored districts, recomputed on every run, so the km "
      "value moves if WP4's grades replace the regex baseline. The values above are this run's.")
    w("")
    w("## How fragile is the split")
    w("")
    w("Quadrant counts for each pair of cuts. `changed` counts districts whose label differs from the chosen "
      "split (tertile, tertile).")
    w("")
    g2 = grid.rename(columns={"changed_vs_primary": "changed"}).drop(columns=["primary"])
    w(md_table(g2))
    w("")
    w(f"Districts that keep their label under all 16 pairs: " +
      ", ".join(f"{QUADRANT_NAMES[g]} {sens['core'][g]} of {counts[g]}" for g in QUADRANTS) + ".")
    w("")
    w("## Step 4: public share of births by quadrant")
    w("")
    w("Medians, with 95% intervals from 2,000 bootstrap resamples of whole states.")
    w("")
    rows = []
    for g in QUADRANTS:
        rows.append({"quadrant": g, "n": bq[g]["n"],
                     "public, % of all births": f"{bq[g]['median']:.1f} ({bq[g]['ci95_state_bootstrap'][0]:.1f} to {bq[g]['ci95_state_bootstrap'][1]:.1f})",
                     "public, % of institutional": f"{sq[g]['median']:.1f} ({sq[g]['ci95_state_bootstrap'][0]:.1f} to {sq[g]['ci95_state_bootstrap'][1]:.1f})",
                     "public C-section rate %": f"{cq[g]['median']:.1f} ({cq[g]['ci95_state_bootstrap'][0]:.1f} to {cq[g]['ci95_state_bootstrap'][1]:.1f})",
                     "institutional births % (median)": f"{d.loc[d.quadrant_rule == g, OUTCOME].median():.1f}"})
    w(md_table(pd.DataFrame(rows)))
    w("")
    w("Pairwise comparisons (first group minus second). Within-state: weighted mean of per-state differences, "
      f"p from {N_PERM:,} permutations that shuffle labels inside each state.")
    w("")
    rows = []
    for col, nm in [(PUBLIC, "public % of all births"), ("public_share_of_institutional_pct",
                                                          "public % of institutional"),
                    (CSEC_PUBLIC, "public C-section rate")]:
        for pair, r in pub[col]["pairs"].items():
            rows.append({"measure": nm, "pair": pair.replace("_vs_", " vs "),
                         "median diff": f"{r['median_diff']:+.1f}",
                         "mean diff": f"{r['mean_diff']:+.1f}",
                         "95% interval": f"{r['median_diff_ci95_state_bootstrap'][0]:+.1f} to {r['median_diff_ci95_state_bootstrap'][1]:+.1f}",
                         "Mann-Whitney p": fmt_p(r["mann_whitney_p"]),
                         "within-state diff": f"{r['within_state_mean_diff']:+.1f}",
                         "within-state p": fmt_p(r["within_state_perm_p"])})
    w(md_table(pd.DataFrame(rows)))
    w("")
    w(f"Reading: {'the story holds' if holds else 'the story does not hold'} at the national level"
      f"{' and still holds within states, at a smaller size' if (holds and within_holds) else (', but not clearly within states' if holds else '')}. "
      "Care deserts also lean on public facilities (most births there that happen in a facility happen in a "
      "public one), so a high public share alone does not mark a data desert; the comparison that matters is "
      "with served districts, which have similar institutional birth rates.")
    w("")
    w("## C-section under 5%")
    w("")
    w(md_table(pd.DataFrame([{"quadrant": g, "districts": cs[g]["n"], "C-section under 5%": cs[g]["csection_below_5"],
                              "share": f"{100 * cs[g]['share']:.1f}%",
                              "median C-section %": f"{csec['median_csection_by_quadrant'][g]:.1f}"}
                             for g in QUADRANTS])))
    w("")
    dd = [r for r in csec["districts"] if r["quadrant_rule"] == "data_desert"]
    if dd:
        w("Data deserts with a C-section rate under 5% (good institutional births, little surgical delivery): " +
          ", ".join(f"{r['district_key']} ({r[CSEC]:.1f}%)" for r in dd) + ".")
        w("")
    w("## The lists")
    w("")
    w("All three come from `quadrant_rule`. Columns: rank, district, state, distance (median km to grade 2+ "
      "EmOC, share of post offices within 60 km, grade 2+ facilities located in the district), outcome and its "
      "NFHS flag, public share, C-section rates, `residual` (empty until Model B), crosswalk method, "
      "`label_stability` (share of the 16 threshold pairs that give the same label) and plain-language "
      "reliability notes.")
    w("")
    for name, spec in LIST_SPECS.items():
        t = lists[name]
        w(f"### `reports/{name}.csv` ({len(t)} districts, ranked by {spec['rank_rule']})")
        w("")
        show = t.head(10)[["rank", "district", "state", "emoc_g2_dist_km_p50", "institutional_birth_pct",
                           "public_facility_birth_pct", "csection_pct", "label_stability", "crosswalk_method"]]
        w(md_table(show))
        w("")
    isl = lists["send_data_collectors"]
    n_isl = int(isl["state"].isin(ISLAND_STATES).sum())
    if n_isl:
        ranks = ", ".join(str(int(r)) for r in isl.loc[isl["state"].isin(ISLAND_STATES), "rank"])
        w(f"{n_isl} island districts (Andaman and Nicobar, Lakshadweep) sit in `send_data_collectors` at ranks "
          f"{ranks}: the nearest listed facility is across the sea. Their distances are real in the data but say "
          "more about geography than about missing records. Their rows carry an island note.")
        w("")
    w("## Dialysis need versus access (step 11)")
    w("")
    w(f"Need index: {dia_info['need_definition']}. Access: district median distance to grade "
      f"{DIALYSIS_GRADE}+ dialysis. Cells: " + ", ".join(f"{k} {v}" for k, v in dia_info["cells"].items()) +
      f". States with the most high-need, poor-access districts: " +
      ", ".join(f"{k} {v}" for k, v in dia_info["high_need_poor_access_top_states"].items()) +
      ". Full table: `reports/wp6_dialysis_need_vs_access.csv`. This is a burden-versus-supply comparison, not "
      "a care-versus-data call: NFHS has no dialysis outcome.")
    w("")
    w("## Coverage and flags")
    w("")
    w(f"- NFHS districts: {cov['nfhs_districts']} in `silver/nfhs.parquet` ({cov['nfhs_raw_rows']} raw rows; "
      f"dropped by WP2: {dropped}); scored {cov['scored']}. Every scored district has an access value and an "
      f"outcome ({cov['scored_missing_access']} missing access, {cov['scored_missing_outcome']} missing outcome).")
    fc = cov["facility_coord_check_counts"]
    if fc:
        ck, fk = cov["facility_pin_check_km"], cov["facility_pin_far_km"]
        w(f"- Facility locations: WP1 checks each facility's coordinates against its own PIN. Of the facilities "
          f"more than {ck:.0f} km from their PIN and outside its district, {fc.get('moved_to_pin', 0)} were moved "
          f"to it because the city text named only the PIN's place, {fc.get('kept_city_matches_coords', 0)} were "
          f"kept because it named only the coordinates' place, {fc.get('kept_unresolved', 0)} within {fk:.0f} km "
          f"were kept because it named both or neither, and {fc.get('excluded_unresolved', 0)} beyond {fk:.0f} km "
          f"were left out for the same reason. Of the {cov['emoc_g2_facilities']} grade 2+ EmOC facilities, "
          f"{cov['emoc_g2_moved_to_pin']} were moved and {cov['emoc_g2_dropped_by_pin_check']} left out "
          f"({cov['emoc_g2_missing_coords']} more {'has' if cov['emoc_g2_missing_coords'] == 1 else 'have'} "
          "no coordinates at all).")
    w(f"- PIN-directory districts: {cov['pin_districts']}; unmatched {cov['pin_districts_unmatched']} "
      f"({', '.join(cov['pin_districts_unmatched_list'])}). Their geocoded post offices "
      f"({cov['geocoded_post_offices_without_district_key']}) cannot be scored. Purba Bardhaman is a large "
      "district with no NFHS row: 'no survey data', not 'no care'.")
    w(f"- Crosswalk method of the scored districts: " +
      ", ".join(f"{k} {v}" for k, v in cov["crosswalk_method_counts"].items()) +
      f". {cov['nfhs_districts_with_parent_district_feed']} districts pool post offices from "
      f"{cov['pin_districts_parent_district']} PIN districts created after the survey; their geography is "
      "inferred, not exact.")
    w(f"- Outcome flags for `{OUTCOME}`: " + ", ".join(f"{k} {v}" for k, v in cov["outcome_flag_counts"].items()) +
      ". No district needed a low-sample or suppressed flag on this indicator; the code carries the flag through "
      "anyway, and the lists show it.")
    w(f"- Other reliability flags in the lists: {cov['few_post_offices_lt20']} districts have fewer than "
      f"{FEW_POST_OFFICES} post office locations behind their access value; {cov['excluded_share_gt25pct']} lost "
      f"more than 25% of their offices to WP5's coordinate check; {cov['placeholder_share_gt50pct']} have more than "
      f"half their offices on placeholder points (one point shared by 10 or more offices from 3 or more PINs, "
      f"usually the district town; WP5 counts each such point once, so their access value rests on the offices "
      f"placed elsewhere); {cov['small_survey_lt600_households']} were surveyed with fewer than {SMALL_SURVEY_HH} "
      f"households (NFHS-5 sampled about 900 per district).")
    w("")
    w("## Caveats")
    w("")
    w("- Rule quadrants only. A data desert here means poor visible access plus good outcomes; Model B will "
      "adjust the outcome reading for development, which can move districts between quadrants.")
    w("- The access reading uses grades from web pages (currently the regex baseline). No evidence of a service "
      "is not evidence that it is absent, and the facilities table is a sample that is 88% private, so true "
      "distances are often shorter.")
    w("- Distances are straight lines from post offices, not travel times from where people live.")
    w("- NFHS-5 is from 2019 to 2021; the facility pages are newer.")
    w("- The public share is consistent with hidden public care in data deserts, but it also rises with "
      "rurality. It supports the reading; it does not prove that specific public facilities are missing from the "
      "data.")
    w("- Districts are not independent: state explains most of the variation in institutional births. The "
      "intervals resample whole states, and the within-state comparison is the stricter test. Mann-Whitney p "
      "values treat districts as independent and are optimistic.")
    w("- Thresholds are relative (thirds of this distribution). Counts depend on them; see the fragility table "
      "and `label_stability`.")
    w(f"- District boundaries changed after the survey; {cov['nfhs_districts_with_parent_district_feed']} districts "
      "pool post offices of later splits, and name matches outside 'exact' are inferred.")
    w("")
    w("## Files")
    w("")
    w("- `gold/district_quadrants.parquet` (+ `.meta.json`, stage `rule_only`)")
    w("- `reports/send_care.csv`, `reports/send_data_collectors.csv`, `reports/cost_or_quality.csv`, "
      "`reports/wp6_dialysis_need_vs_access.csv` (+ `.meta.json` each)")
    for f in figs:
        w(f"- `{rel(f)}`")
    w("")
    body = "\n".join(L)
    write_atomic(path, lambda p: p.write_text(body))


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def print_summary(d, cuts, sens, pub, csec, dia_info, cov, spearman_ao, elapsed):
    counts = d["quadrant_rule"].value_counts()
    print(f"WP6 rule quadrants ({LINE}, min_grade {MIN_GRADE}): {int(d['quadrant_rule'].notna().sum())} districts")
    print(f"  thresholds: poor access >= {cuts['access']['tertile']:.2f} km (top third); "
          f"poor outcome < {cuts['outcome']['tertile']:.2f}% institutional births (bottom third)")
    for g in QUADRANTS:
        print(f"  {g:16s} {int(counts.get(g, 0)):4d}   stable under all 16 cut pairs: {sens['core'][g]}")
    print(f"  spearman(access, outcome) = {spearman_ao['rho']:.3f}")
    print("  sensitivity (care / data / cost / served, changed):")
    for r in sens["grid"]:
        print(f"    access {r['access_cut']:10s} {r['access_km']:7.1f} km | outcome {r['outcome_cut']:11s} "
              f"{r['outcome_pct']:5.1f}% -> {r['care_desert']:3d} {r['data_desert']:3d} {r['cost_or_quality']:3d} "
              f"{r['served']:3d}  changed {r['changed_vs_primary']:3d}")
    for col in (PUBLIC, "public_share_of_institutional_pct", CSEC_PUBLIC):
        b = pub[col]["by_quadrant"]
        pr = pub[col]["pairs"]["data_desert_vs_served"]
        print(f"  {pub[col]['label']}: medians " +
              ", ".join(f"{g} {b[g]['median']:.1f}" for g in QUADRANTS) +
              f" | data-served {pr['median_diff']:+.1f} [{pr['median_diff_ci95_state_bootstrap'][0]:+.1f}, "
              f"{pr['median_diff_ci95_state_bootstrap'][1]:+.1f}] MW p={fmt_p(pr['mann_whitney_p'])} "
              f"within-state {pr['within_state_mean_diff']:+.1f} p={fmt_p(pr['within_state_perm_p'])}")
    print(f"  C-section < 5%: {csec['n_districts']} districts; by quadrant " +
          ", ".join(f"{g} {v['csection_below_5']}" for g, v in csec["by_quadrant"].items()))
    print(f"  dialysis need vs access: {dia_info['cells']}; rho {dia_info['spearman_need_vs_distance']['rho']:.3f}")
    print(f"  coverage: scored {cov['scored']} of {cov['nfhs_districts']} NFHS rows; PIN districts unmatched "
          f"{cov['pin_districts_unmatched']}; outcome low_sample/suppressed {cov['outcome_low_sample_or_suppressed']}; "
          f"over half the offices on placeholder points {cov['placeholder_share_gt50pct']}; fewer than "
          f"{FEW_POST_OFFICES} post office locations {cov['few_post_offices_lt20']}")
    print(f"  runtime {elapsed:.1f} s")


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--no-figures", action="store_true")
    ap.add_argument("--fig-dir", type=Path, default=paths.code_root() / "reports" / "figures")
    ap.add_argument("--reports-dir", type=Path, default=paths.code_root() / "reports")
    args = ap.parse_args(argv)
    t0 = time.time()

    inp = load_inputs()
    grades_method = inp["da_meta"].get("grades_method", "unknown")
    d = district_table(inp)
    cuts = thresholds(d)
    d["quadrant_rule"] = assign_quadrant(d["acc_dist_km_p50"], d[OUTCOME], cuts["access"]["tertile"],
                                         cuts["outcome"]["tertile"])
    sens = sensitivity(d, cuts)
    d["label_stability"] = sens["stability"]
    d["csection_below_5pct"] = d[CSEC] < CSEC_LOW
    d["residual"] = np.nan
    d["reliability_notes"] = reliability_notes(d)
    ao = stats.spearmanr(d["acc_dist_km_p50"], d[OUTCOME])
    spearman_ao = {"rho": float(ao.statistic), "p": float(ao.pvalue)}
    pub = public_share_check(d)
    csec = csection_check(d)
    dia, dia_info = dialysis_readout(d)
    cov = coverage(inp, d)
    lists = build_lists(d)

    # ---- gold table
    q = pd.DataFrame({
        "district_key": d["district_key"],
        "service_line": LINE,
        "access_value": d["acc_dist_km_p50"].astype(float),
        "outcome_value": d[OUTCOME].astype(float),
        "outcome_flag": d["outcome_flag"],
        "predicted_outcome": np.nan,
        "residual": np.nan,
        "quadrant_rule": d["quadrant_rule"],
        "quadrant_model": None,
        "p_real_gap": np.nan,
        "crosswalk_method": d["crosswalk_method"],
    }).sort_values("district_key").reset_index(drop=True)
    q["quadrant_model"] = q["quadrant_model"].astype(object)

    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    common = {
        "created_at_utc": created,
        **git_info(),
        "script": SCRIPT,
        "version": VERSION,
        "stage": STAGE,
        "grades_method": grades_method,
        "recipe_version": inp["da_meta"].get("recipe_version"),
        "inputs": inp["inputs"],
    }
    thresholds_meta = {
        "access": {"measure": f"district_access.dist_km_p50, service_line {LINE}, min_grade {MIN_GRADE}",
                   "rule": "poor access when access_value >= cut", "primary": "tertile (2/3 quantile)",
                   "cut_km": cuts["access"]["tertile"], "variants_km": cuts["access"]},
        "outcome": {"measure": OUTCOME, "rule": "poor outcome when outcome_value < cut",
                    "primary": "tertile (1/3 quantile)", "cut_pct": cuts["outcome"]["tertile"],
                    "variants_pct": cuts["outcome"]},
        "csection_flag": {"measure": CSEC, "rule": f"< {CSEC_LOW}"},
        "quantile_method": "numpy.quantile, linear interpolation, over the scored districts",
    }
    gold_path = paths.gold_dir() / "district_quadrants.parquet"
    table = pa.Table.from_pandas(q, schema=QUADRANT_SCHEMA, preserve_index=False)
    write_atomic(gold_path, lambda p: pq.write_table(table, p))
    gold_meta = {
        "output": rel(gold_path),
        **common,
        "grain": "one row per district_key x service_line; this stage writes service_line emoc only",
        "service_lines": [LINE],
        "service_lines_note": "dialysis has no NFHS outcome, so it gets a need-versus-access readout "
                              "(reports/wp6_dialysis_need_vs_access.csv), not quadrants; ICU skipped",
        "model_b_columns_null": list(MODEL_B_COLUMNS),
        "quadrant_labels": list(QUADRANTS),
        "thresholds": thresholds_meta,
        "quadrant_counts": {g: int((q["quadrant_rule"] == g).sum()) for g in QUADRANTS},
        "crosswalk_method_rule": "distinct match_method values of the PIN districts feeding the NFHS district, "
                                 "ordered exact, fuzzy, manual, parent_district, joined by '+'",
        "sensitivity_grid": sens["grid"],
        "stable_under_all_16_pairs": sens["core"],
        "spearman_access_vs_outcome": spearman_ao,
        "public_share_check": pub,
        "csection_check": {k: v for k, v in csec.items() if k != "districts"},
        "dialysis_readout": dia_info,
        "coverage": cov,
    }
    write_json(gold_path.with_name(gold_path.name + ".meta.json"),
               dict(gold_meta, rows=table.num_rows, columns=QUADRANT_SCHEMA.names))

    # ---- lists
    list_meta = {**common, "source": "gold/district_quadrants.parquet quadrant_rule",
                 "thresholds": {"access_km": cuts["access"]["tertile"], "outcome_pct": cuts["outcome"]["tertile"]},
                 "residual_note": "empty until Model B runs"}
    for name, t in lists.items():
        write_csv(t, args.reports_dir / f"{name}.csv", dict(list_meta, rank_rule=LIST_SPECS[name]["rank_rule"],
                                                            quadrant=LIST_SPECS[name]["quadrant"]))
    dia_out = dia.copy()
    for c in ("need_index_pct", "dia_dist_km_p50"):
        dia_out[c] = dia_out[c].round(2)
    dia_out = dia_out.rename(columns={"dia_dist_km_p50": "dialysis_g2_dist_km_p50",
                                      "dia_share_within_60km": "dialysis_g2_share_within_60km",
                                      "dia_n_facilities_in_district": "dialysis_g2_facilities_in_district",
                                      "dia_n_post_offices": "n_post_offices", "state_ut": "state",
                                      "district_name": "district"})
    write_csv(dia_out, args.reports_dir / "wp6_dialysis_need_vs_access.csv",
              dict(common, comparison="need versus access, not an outcome", **{k: v for k, v in dia_info.items()}))

    figs = []
    if not args.no_figures:
        figs = make_figures(d, cuts, sens, pub, csec, dia, dia_info, grades_method, args.fig_dir)
    write_report(args.reports_dir / "wp6_rule_quadrants.md", d, cuts, sens, pub, csec, dia_info, cov, lists,
                 figs, grades_method, spearman_ao, created)
    print_summary(d, cuts, sens, pub, csec, dia_info, cov, spearman_ao, time.time() - t0)
    print(f"  wrote {rel(gold_path)} ({table.num_rows} rows), {len(lists)} lists, dialysis readout, report, "
          f"{len(figs)} figures")
    return 0


if __name__ == "__main__":
    sys.exit(main())
