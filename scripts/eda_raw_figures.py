#!/usr/bin/env python
"""Exploratory figures from the raw data (EDA).

Reads the three raw tables through vf.io (the cleaned silver tables may not
exist yet) and handles the data traps from CLAUDE.md inline. Writes:

  reports/figures/eda_<NN>_<slug>.png   one figure per question, 1600 px wide
  reports/figures/eda_stats.json        every number the figures and notes use
  reports/eda_notes.md                  one section per figure, numbers filled
                                        in from the stats by this script

Run from the repo root:

  .venv/bin/python scripts/eda_raw_figures.py              # everything, ~1 min
  .venv/bin/python scripts/eda_raw_figures.py --only 2 4   # some figures only;
                                                           # stats and notes are
                                                           # not rewritten

Every number in the figures and in the notes is computed here. The notes'
prose was written against the raw files of Nov 21, 2026; `guard()` calls stop
the run if a direction the prose relies on ever flips.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from types import SimpleNamespace
from urllib.parse import urlparse

import matplotlib

matplotlib.use("Agg")
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from vf import io, paths  # noqa: E402
from vf import plotstyle as ps  # noqa: E402

FIG_DIR = paths.code_root() / "reports" / "figures"
NOTES_PATH = paths.code_root() / "reports" / "eda_notes.md"
STATS_PATH = FIG_DIR / "eda_stats.json"

EARTH_KM = 6371.0088

# Evidence patterns, copied from docs/06_capability_recipes.md (recipe v0).
TAG_PATTERNS = {
    "ob_delivery": r"maternity|labou?r (?:room|ward)|normal delivery|childbirth|obstetric",
    "csection": r"c[- ]?section|caesarean|cesarean|\blscs\b",
    "blood": r"blood bank|blood storage|blood transfusion",
    "anesthesia": r"ana?esthe",
    "ot": r"operation theat|operating theat|operating room",
    "dialysis": r"dialysis",
    "dialysis_unit": r"dialysis (?:unit|machine|centre|center)|h(?:a)?emodialysis|nephrologist",
    "icu": r"\bicu\b|intensive care|critical care unit",
    "ventilator": r"ventilator",
}
OB_SPECIALTIES = {"gynecologyAndObstetrics", "obstetricsAndMaternityCare",
                  "maternalFetalMedicineOrPerinatology"}

OPS = ["private", "public", "unknown"]
OP_LABEL = {"private": "Private", "public": "Public", "unknown": "Operator unknown"}
OP_COLOR = {"private": ps.PRIVATE, "public": ps.PUBLIC, "unknown": ps.UNKNOWN}
TYPES = ["hospital", "clinic", "dentist", "other", "unknown"]
TYPE_LABEL = {"hospital": "Hospital", "clinic": "Clinic", "dentist": "Dentist",
              "other": "Other types", "unknown": "Type unknown"}

SRC_FAC = "Source: facilities.csv (Virtue Foundation, web-scraped), read with vf.io."
SRC_PIN = "India Post PIN directory (data.gov.in)."
SRC_NFHS = "Source: NFHS-5 district fact sheets, 2019-21 (data.gov.in)."


def guard(cond: bool, msg: str) -> None:
    """Stop if a statement the notes rely on is no longer true."""
    if not cond:
        raise AssertionError(f"notes prose assumption failed: {msg}. "
                             "Re-read the figure and update NOTES_TEMPLATE.")


def null_to_na(s: pd.Series) -> pd.Series:
    """The raw files spell missing as the string 'null' (trap 2)."""
    return s.where(~s.map(io.is_null))


def git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=paths.code_root(),
                              capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        return "unknown"


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (pd.Timestamp, dt.date)):
        return str(o)
    raise TypeError(type(o))


def write_json_atomic(path, obj) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(obj, indent=2, default=_json_default) + "\n")
    os.replace(tmp, path)


def write_text_atomic(path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text)
    os.replace(tmp, path)




def haversine_nearest_km(points_latlon: np.ndarray, targets_latlon: np.ndarray) -> np.ndarray:
    """Straight-line km from each point to its nearest target (BallTree)."""
    from sklearn.neighbors import BallTree
    tree = BallTree(np.radians(targets_latlon), metric="haversine")
    d, _ = tree.query(np.radians(points_latlon), k=1)
    return d[:, 0] * EARTH_KM


# ==========================================================================
# load and prepare
# ==========================================================================

def load() -> SimpleNamespace:
    t0 = time.time()
    D = SimpleNamespace()

    # --- facilities -------------------------------------------------------
    raw = io.read_facilities_raw()
    D.n_raw_rows = len(raw)
    D.n_junk = int((io.facility_row_status(raw) == "junk").sum())
    fac_real = io.read_facilities()                 # junk rows dropped (trap 3)
    D.n_real_rows = len(fac_real)
    D.n_ob_claim_before_dedupe = int(fac_real.specialties.map(
        lambda v: bool(set(io.parse_list(v)) & OB_SPECIALTIES)).sum())
    fac = fac_real.drop_duplicates("unique_id").reset_index(drop=True)
    D.n_dup_rows = D.n_real_rows - len(fac)         # exact duplicate rows
    D.n_truncated = int((fac.row_status == "truncated").sum())

    op = null_to_na(fac.operatorTypeId).replace({"government": "public"})
    fac["op"] = op.fillna("unknown")
    ft = null_to_na(fac.facilityTypeId)
    fac["ftype"] = np.where(ft.isin(["hospital", "clinic", "dentist"]), ft,
                            np.where(ft.isna(), "unknown", "other"))

    fac["lat"] = fac.latitude.map(io.to_float)
    fac["lon"] = fac.longitude.map(io.to_float)
    fac["coord_ok"] = [io.in_india(a, b) for a, b in zip(fac.lat, fac.lon)]

    # PIN: strip whitespace inside the code ("411 001"), keep 6 digits only.
    z = null_to_na(fac.address_zipOrPostcode).str.replace(r"\s", "", regex=True)
    fac["pin"] = z.where(z.str.fullmatch(r"\d{6}", na=False))

    # Specialties: dedupe inside each row (trap 5).
    fac["spec"] = fac.specialties.map(lambda v: io.parse_list(v, dedupe=True))

    # Facts: long format, one row per fact string (empty strings dropped).
    parts = []
    for field in ["capability", "procedure", "equipment"]:
        lists = fac[field].map(io.parse_list)
        fac[f"n_{field}"] = lists.map(len)
        ex = lists.explode().dropna()
        parts.append(pd.DataFrame({"fac_idx": ex.index.values, "field": field,
                                   "text": ex.values}))
    facts = pd.concat(parts, ignore_index=True)
    fac["n_facts"] = fac[["n_capability", "n_procedure", "n_equipment"]].sum(axis=1)

    # Evidence tags at facility level: regex over all of a facility's facts.
    joined = (facts.assign(t=facts.text.str.lower())
              .groupby("fac_idx").t.agg(" \n ".join))
    text = joined.reindex(fac.index).fillna("")
    for tag, pat in TAG_PATTERNS.items():
        fac[f"tag_{tag}"] = text.str.contains(pat, regex=True)

    D.fac = fac
    D.facts = facts
    D.n_farmacy = int((fac.facilityTypeId == "farmacy").sum())
    # The scrape date is not documented; the latest social post date bounds it.
    posts = pd.to_datetime(null_to_na(fac.post_metrics_most_recent_social_media_post_date),
                           errors="coerce", format="%Y-%m-%d")
    D.scrape_cutoff = posts.max()

    # --- post offices -----------------------------------------------------
    po = io.read_pincodes_raw()
    po["lat"] = po.latitude.map(io.to_float)
    po["lon"] = po.longitude.map(io.to_float)
    po["valid"] = [io.in_india(a, b) for a, b in zip(po.lat, po.lon)]
    D.po = po
    # Each PIN gets its most common (state, district); ties broken by name.
    # This keeps the facility join one-to-one (trap 8).
    c = (po.groupby(["pincode", "statename", "district"]).size().rename("n")
         .reset_index()
         .sort_values(["pincode", "n", "statename", "district"],
                      ascending=[True, False, True, True]))
    D.n_pins = c.pincode.nunique()
    D.n_pins_ambiguous = int((c.groupby("pincode").size() > 1).sum())
    D.pin_modal = c.drop_duplicates("pincode").set_index("pincode")[["statename", "district"]]
    D.districts = po.groupby(["statename", "district"]).size().index  # 755 pairs

    # --- NFHS ---------------------------------------------------------------
    nf = io.read_nfhs_raw()
    D.nf_cols = list(nf.columns[2:])
    parsed = {c: nf[c].map(io.parse_nfhs_value) for c in D.nf_cols}
    D.nf_val = pd.DataFrame({c: parsed[c].map(lambda t: t[0]) for c in D.nf_cols}).astype(float)
    D.nf_flag = pd.DataFrame({c: parsed[c].map(lambda t: t[1]) for c in D.nf_cols})
    D.nf_state = nf.state_ut.str.strip()            # one value has a leading space
    D.nf_district = nf.district_name.str.strip()
    D.n_state_leading_space = int((nf.state_ut != nf.state_ut.str.strip()).sum())

    D.load_seconds = round(time.time() - t0, 1)
    return D


# ==========================================================================
# figures
# ==========================================================================

def fig01_types(D, S):
    """What is in the facilities table: type by operator."""
    fac = D.fac
    n = len(fac)
    opc = fac.op.value_counts().reindex(OPS, fill_value=0)
    ct = pd.crosstab(fac.ftype, fac.op).reindex(index=TYPES, columns=OPS, fill_value=0)
    st = {
        "n_raw_rows": D.n_raw_rows, "n_junk": D.n_junk, "n_farmacy": D.n_farmacy,
        "n_real_rows": D.n_real_rows, "n_dup_rows": D.n_dup_rows, "n": n,
        "n_truncated": D.n_truncated,
        "ops": {k: int(v) for k, v in opc.items()},
        "ops_pct": {k: round(100 * v / n, 1) for k, v in opc.items()},
        "types": {t: int(ct.loc[t].sum()) for t in TYPES},
        "public_by_type": {t: int(ct.loc[t, "public"]) for t in TYPES},
        "public_hospital_share_of_public": round(100 * ct.loc["hospital", "public"] / opc["public"], 1),
        "public_share_of_hospitals": round(100 * ct.loc["hospital", "public"] / ct.loc["hospital"].sum(), 1),
        "public_share_of_clinics": round(100 * ct.loc["clinic", "public"] / ct.loc["clinic"].sum(), 1),
        "n_government_raw": int((fac.operatorTypeId == "government").sum()),
    }
    S["f01"] = st
    guard(st["ops_pct"]["private"] > 85, "about 88% private")
    guard(st["public_hospital_share_of_public"] > 85, "public facilities are almost all hospitals")

    fig, gs = ps.figure(
        5.4,
        f"{st['ops_pct']['private']:.0f}% of facilities are private; public ones are mostly hospitals",
        f"{n:,} facilities in the raw table, after dropping {D.n_junk} junk rows and "
        f"{D.n_dup_rows} exact duplicate rows. Public includes the "
        f"{st['n_government_raw']} rows tagged 'government'.",
        note=SRC_FAC + " Operator and type are as scraped; 'unknown' means the "
             f"field holds the string 'null'. Other types: doctor, pharmacy "
             f"(spelled 'farmacy' in {D.n_farmacy} rows), nursing home.",
        nrows=2, height_ratios=[0.2, 1], left_in=1.75, right_in=0.5, hspace=0.8,
        legend_rows=1)
    ax0 = fig.add_subplot(gs[0])
    parts = [(OP_LABEL[o], int(opc[o]), OP_COLOR[o]) for o in OPS]
    unlabeled = ps.segmented_bar(ax0, parts)
    # a block too narrow for its share prints none; the legend carries it
    ps.panel_title(ax0, "Operator, all facilities", [
        (f"{OP_LABEL[o]}  {int(opc[o]):,}" + (f" ({st['ops_pct'][o]:.1f}%)"
                                              if OP_LABEL[o] in unlabeled else ""),
         OP_COLOR[o]) for o in OPS])

    ax = fig.add_subplot(gs[1])
    ps.panel_title(ax, "Facility type, split by operator (number of facilities)")
    y = np.arange(len(TYPES))
    left = np.zeros(len(TYPES))
    for o in OPS:
        vals = ct[o].values
        bars = ax.barh(y, vals, left=left, height=0.66, color=OP_COLOR[o], **ps.gap_edge())
        # round only the outermost segment of each bar
        last = [ct.loc[t, OPS[OPS.index(o) + 1:]].sum() == 0 for t in TYPES]
        ps.register_bars(ax, [b for b, l in zip(bars, last) if l], "h")
        ps.register_bars(ax, [b for b, l in zip(bars, last) if not l], "h", round_end=False)
        left += vals
    ax.set_yticks(y, [TYPE_LABEL[t] for t in TYPES])
    ax.set_ylim(len(TYPES) - 0.5, -0.5)
    ax.tick_params(axis="y", length=0)
    ax.set_xlim(0, left.max() * 1.32)
    for i, t in enumerate(TYPES):
        tot, pub = int(left[i]), int(ct.loc[t, "public"])
        ax.annotate(f"{tot:,}  ({pub:,} public)", xy=(tot, i), xytext=(ps.px(6), 0),
                    textcoords="offset points", va="center", fontsize=11, color=ps.INK_2)
    ps.clean_axes(ax, keep=("left",))
    ax.set_xticks([])
    ps.save(fig, FIG_DIR / "eda_01_facility_types.png")


def fig02_map(D, S):
    """Where the facilities are versus where people are."""
    po = D.po[D.po.valid]
    f = D.fac[D.fac.coord_ok]
    pts = po[["lat", "lon"]].to_numpy()
    sets = {
        "any": ("Any facility", f, ps.BLUE),
        "public": ("Public facility", f[f.op == "public"], ps.ORANGE),
        "csection": ("Mentions a C-section", f[f.tag_csection], ps.AQUA),
    }
    dist = {k: haversine_nearest_km(pts, v[1][["lat", "lon"]].to_numpy()) for k, v in sets.items()}
    st = {
        "n_po_total": len(D.po), "n_po_valid": len(po),
        "n_po_na": int((D.po.latitude.str.strip() == "NA").sum()),
        "n_po_numeric": int(D.po.lat.notna().sum()),
        "n_fac_coord_ok": len(f), "n_fac": len(D.fac),
        "n_public_coord_ok": len(sets["public"][1]), "n_csection_coord_ok": len(sets["csection"][1]),
    }
    for k, d in dist.items():
        st[k] = {"median_km": round(float(np.median(d)), 1),
                 "p90_km": round(float(np.percentile(d, 90)), 1),
                 "pct_over_30km": round(100 * float((d > 30).mean()), 1),
                 "pct_over_60km": round(100 * float((d > 60).mean()), 1)}
    # The India bounding box lets through misplaced points: count post offices
    # far from their own state's median position, and stacked duplicates.
    med = po.groupby("statename")[["lat", "lon"]].transform("median")
    far_deg = np.hypot(po.lat - med.lat, (po.lon - med.lon) * np.cos(np.radians(22)))
    st["n_po_far_from_state_8deg"] = int((far_deg > 8).sum())
    st["max_po_same_coord"] = int(po.groupby(["latitude", "longitude"]).size().max())
    # A placeholder latitude shared across states (found while drawing the map).
    top_lat = po.latitude.str.strip().value_counts()
    st["top_lat_value"] = top_lat.index[0]
    st["top_lat_n"] = int(top_lat.iloc[0])
    same = po[po.latitude.str.strip() == top_lat.index[0]]
    st["top_lat_n_states"] = int(same.statename.nunique())
    st["top_lat_n_districts"] = int(same.groupby(["statename", "district"]).ngroups)
    S["f02"] = st
    guard(st["csection"]["median_km"] > 2 * st["any"]["median_km"],
          "C-section evidence is much farther than any facility")

    fig, gs = ps.figure(
        7.9,
        "Facilities cluster in cities, and facilities that mention a C-section "
        "are far from most post offices",
        f"Left: {len(po):,} post offices with usable coordinates and "
        f"{len(f):,} facilities. Right: straight-line distance from each post "
        f"office to the nearest facility of each kind; dots mark the median.",
        note=SRC_FAC + " " + SRC_PIN + " Post offices stand in for settlements and "
             "are not weighted by population. A facility with no C-section mention "
             "is no evidence of C-section care, not proof there is none.",
        ncols=2, width_ratios=[1.12, 1.0], left_in=0.4, right_in=0.45, wspace=0.16)
    ax = fig.add_subplot(gs[0])
    ax.scatter(po.lon, po.lat, s=0.35, c=ps.CONTEXT, lw=0, rasterized=True)
    ax.scatter(f.lon, f.lat, s=3.5, c=ps.BLUE, lw=0, alpha=0.75, rasterized=True)
    ax.set_aspect(1 / np.cos(np.radians(22)))
    ax.set_anchor("N")
    ax.set_xlim(67.8, 97.6)
    ax.set_ylim(6.4, 37.4)
    ax.set_axis_off()
    ax.annotate(f"Placeholder latitude {float(st['top_lat_value']):.4f},\n"
                f"shared by {st['top_lat_n']:,} post offices",
                xy=(80.3, float(st["top_lat_value"])), xytext=(97.6, 12.6),
                ha="right", va="top", fontsize=9.5, color=ps.INK_2,
                arrowprops=dict(arrowstyle="-", color=ps.MUTED, lw=ps.px(1)))
    ps.legend(ax, [("Post office", ps.CONTEXT, "dot"), ("Facility", ps.BLUE, "dot")],
              loc="lower left", anchor=(0.0, 0.0), ncol=1, fontsize=11)
    ps.panel_title(ax, "Post offices and facilities")

    ax = fig.add_subplot(gs[1])
    xs = np.linspace(0, 150, 601)
    for k, (label, _, color) in sets.items():
        d = np.sort(dist[k])
        ys = 100 * np.searchsorted(d, xs, side="right") / len(d)
        ax.plot(xs, ys, color=color, zorder=3)
        # the median: where each line crosses 50%
        ax.scatter([st[k]["median_km"]], [50], s=ps.px(9) ** 2, color=color,
                   edgecolors=ps.SURFACE, linewidths=ps.px(2), zorder=4)
    ax.axhline(50, color=ps.AXIS, lw=ps.px(1), zorder=1)
    ax.set_xlim(0, 150)
    ax.set_ylim(0, 101)
    ax.set_xticks([0, 30, 60, 90, 120, 150])
    ax.set_xlabel("Distance to the nearest facility (km, straight line)")
    ps.pct_axis(ax, "y")
    ps.clean_axes(ax, grid="both")
    ax.tick_params(length=0)
    ps.legend(ax, [(f"{sets[k][0]}, median {st[k]['median_km']:.0f} km", sets[k][2], "line")
                   for k in sets], loc="lower right", anchor=(1.0, 0.02), ncol=1, fontsize=11)
    ps.panel_title(ax, "Post offices within a distance")
    ps.save(fig, FIG_DIR / "eda_02_facilities_vs_people.png")


def district_counts(D):
    """Facilities per PIN-directory district via each PIN's modal district."""
    fac = D.fac
    m = fac[["pin"]].join(D.pin_modal, on="pin")
    matched = m.dropna(subset=["district"])
    per = matched.groupby(["statename", "district"]).size().reindex(D.districts, fill_value=0)
    return m, matched, per


def fig03_districts(D, S):
    """Facilities per district: zero-heavy, and concentrated."""
    m, matched, per = district_counts(D)
    n_matched = len(matched)
    ranked = per.sort_values(ascending=False)
    cum = ranked.cumsum() / n_matched
    bins = [(0, 0, "0"), (1, 1, "1"), (2, 2, "2"), (3, 5, "3-5"), (6, 10, "6-10"),
            (11, 20, "11-20"), (21, 50, "21-50"), (51, 10**9, "51+")]
    counts = [int(((per >= lo) & (per <= hi)).sum()) for lo, hi, _ in bins]
    top = ranked.head(20)
    covered = per[per > 0]
    # the same join without stripping spaces inside PIN codes, for comparison
    z0 = null_to_na(D.fac.address_zipOrPostcode)
    pin0 = z0.where(z0.str.fullmatch(r"\d{6}", na=False))
    m0 = pd.DataFrame({"pin": pin0}).join(D.pin_modal, on="pin").dropna(subset=["district"])
    per0 = m0.groupby(["statename", "district"]).size().reindex(D.districts, fill_value=0)
    st = {
        "n_pin6_nostrip": int(pin0.notna().sum()), "n_matched_nostrip": len(m0),
        "n_zero_nostrip": int((per0 == 0).sum()),
        "n_fac": len(D.fac), "n_pin6": int(D.fac.pin.notna().sum()),
        "n_distinct_pin": int(D.fac.pin.nunique()),
        "n_pins_dir": D.n_pins, "n_pins_ambiguous": D.n_pins_ambiguous,
        "n_matched": n_matched, "n_pin_not_in_dir": int(D.fac.pin.notna().sum() - n_matched),
        "n_districts": len(per), "n_zero": int((per == 0).sum()),
        "n_covered": int(len(covered)), "median_covered": float(covered.median()),
        "n_le2_covered": int(((per > 0) & (per <= 2)).sum()), "n_ge50": int((per >= 50).sum()),
        "top20_sum": int(top.sum()), "top20_pct": round(100 * top.sum() / n_matched, 1),
        "n_districts_half": int((cum < 0.5).sum() + 1),
        "bins": {b[2]: c for b, c in zip(bins, counts)},
        "top20": [{"state": s, "district": d, "n": int(v)} for (s, d), v in top.items()],
        "zero_by_state_top": {s: int(v) for s, v in per[per == 0].reset_index()
                              .statename.value_counts().head(6).items()},
    }
    S["f03"] = st
    guard(st["n_zero"] > 150, "about 200 districts at zero")

    fig, gs = ps.figure(
        7.2,
        f"{st['n_zero']} of {st['n_districts']} districts have no facility in the data; "
        f"20 districts hold {st['top20_pct']:.0f}%",
        f"{n_matched:,} facilities placed in a district through their PIN code. "
        f"Each PIN is assigned its most common district so the join cannot fan out.",
        note=SRC_FAC + " " + SRC_PIN + " A count per district measures web "
             "presence, not care: use distance instead (CLAUDE.md trap 4).",
        ncols=2, width_ratios=[1.2, 1], left_in=0.75, right_in=0.45, wspace=0.75)
    ax = fig.add_subplot(gs[0])
    x = np.arange(len(bins))
    colors = [ps.BLUE] + [ps.DEEMPH] * (len(bins) - 1)
    ps.vbars(ax, x, counts, colors=colors)
    for xi, c in zip(x, counts):
        ax.annotate(f"{c:,}", xy=(xi, c), xytext=(0, ps.px(4)), textcoords="offset points",
                    ha="center", va="bottom", fontsize=11, color=ps.INK_2)
    ax.set_xticks(x, [b[2] for b in bins], fontsize=10.5)
    ax.set_xlabel("Facilities in the district")
    ax.set_ylabel("Districts")
    ax.set_ylim(0, max(counts) * 1.12)
    ps.clean_axes(ax, grid="y", keep=("bottom",))
    ax.tick_params(length=0)
    ps.panel_title(ax, f"All {st['n_districts']} districts, by number of facilities")

    ax = fig.add_subplot(gs[1])
    labels = [f"{d.title()}, {s.title()}" for (s, d), _ in top.items()]
    labels = [re.sub(r"\bAnd\b", "and", lab) for lab in labels]
    ps.hbars(ax, labels, top.values.tolist(), fontsize=10.5)
    ax.tick_params(axis="y", labelsize=10.5)
    ps.panel_title(ax, f"Top 20 districts: {st['top20_sum']:,} facilities")
    ps.save(fig, FIG_DIR / "eda_03_facilities_per_district.png")


def fig04_claims(D, S):
    """Claims (specialty tags) versus evidence (procedures named in facts)."""
    fac = D.fac
    ok = fac.row_status == "ok"
    has = lambda names: fac.spec.map(lambda s: bool(set(s) & names))  # noqa: E731
    lines = {
        "emoc": {
            "title": "Emergency obstetric care",
            "claim": has(OB_SPECIALTIES), "claim_lab": "Lists an obstetrics specialty",
            "ev": fac.tag_csection, "ev_lab": "Facts mention a C-section",
            "corr": fac.tag_csection & fac.tag_blood & (fac.tag_anesthesia | fac.tag_ot),
            "corr_lab": "C-section + blood bank + anaesthesia or OT",
        },
        "dialysis": {
            "title": "Dialysis",
            "claim": has({"nephrology"}), "claim_lab": "Lists a nephrology specialty",
            "ev": fac.tag_dialysis, "ev_lab": "Facts mention dialysis",
            "corr": fac.tag_dialysis & fac.tag_dialysis_unit,
            "corr_lab": "Dialysis + a unit, machine or nephrologist",
        },
        "icu": {
            "title": "Intensive care",
            "claim": has({"criticalCareMedicine"}), "claim_lab": "Lists a critical care specialty",
            "ev": fac.tag_icu, "ev_lab": "Facts mention an ICU",
            "corr": fac.tag_icu & fac.tag_ventilator, "corr_lab": "ICU + ventilator",
        },
    }
    st = {"n_ok": int(ok.sum()), "emoc_claim_before_dedupe": D.n_ob_claim_before_dedupe}
    for k, L in lines.items():
        c, e, r = L["claim"], L["ev"], L["corr"]
        st[k] = {"claim": int(c.sum()), "evidence": int(e.sum()), "corroborated": int(r.sum()),
                 "claim_and_evidence": int((c & e).sum()),
                 "evidence_without_claim": int((e & ~c).sum()),
                 "pct_claimers_with_evidence": round(100 * (c & e).sum() / c.sum(), 1)}
    spec_n = fac.loc[ok, "spec"].map(len)
    st["spec_per_fac_median"] = float(spec_n.median())
    st["spec_per_fac_hospital_median"] = float(spec_n[fac.loc[ok, "ftype"] == "hospital"].median())
    st["ob_claim_by_type"] = {t: int((lines["emoc"]["claim"] & (fac.ftype == t)).sum()) for t in TYPES}
    S["f04"] = st
    guard(st["emoc"]["claim"] > 8 * st["emoc"]["evidence"], "OB claims outnumber C-section mentions ~10x")
    guard(st["icu"]["evidence"] > st["icu"]["claim"], "ICU mentions exceed critical-care claims")

    fig, gs = ps.figure(
        7.6,
        f"For obstetrics, claims outnumber evidence about "
        f"{round(st['emoc']['claim'] / st['emoc']['evidence'])} to one: "
        f"{st['emoc']['claim']:,} facilities claim the specialty, "
        f"{st['emoc']['evidence']:,} mention a C-section",
        "Facilities at three levels of evidence per service line (recipe v0): "
        "claimed (a specialty tag), supported (the defining procedure is named in "
        "the facts), corroborated (the procedure plus the pieces it needs).",
        note=SRC_FAC + " Obstetrics specialty: gynecologyAndObstetrics, "
             "obstetricsAndMaternityCare or maternalFetalMedicineOrPerinatology. "
             "Keyword patterns from docs/06_capability_recipes.md over capability, "
             "procedure and equipment facts. A missing keyword is no evidence of "
             "the service, not evidence of its absence.",
        nrows=3, left_in=3.35, right_in=0.6, hspace=0.62, legend_rows=1)
    xmax = max(max(st[k]["claim"], st[k]["evidence"]) for k in lines) * 1.16
    leg = [("Claimed", ps.ORDINAL_3[0]), ("Supported", ps.ORDINAL_3[1]),
           ("Corroborated", ps.ORDINAL_3[2])]
    for i, (k, L) in enumerate(lines.items()):
        ax = fig.add_subplot(gs[i])
        vals = [st[k]["claim"], st[k]["evidence"], st[k]["corroborated"]]
        ps.hbars(ax, [L["claim_lab"], L["ev_lab"], L["corr_lab"]], vals,
                 colors=list(ps.ORDINAL_3), xmax=xmax, fontsize=11)
        ax.tick_params(axis="y", labelsize=10.5)
        ps.panel_title(ax, f"{L['title']}: {st[k]['pct_claimers_with_evidence']:.0f}% of "
                           f"claimants name the procedure",
                       leg if i == 0 else None, fontsize=12.5)
    ps.save(fig, FIG_DIR / "eda_04_claims_vs_evidence.png")


# Rough noise categories for fact strings, checked in precedence order; a fact
# goes to the first category whose keywords it contains. Anything left over
# has no noise keyword and is mostly clinical. Hand-tuned on random samples:
# rough by design, not a classifier.
NOISE_CATS = [
    ("reputation", "Reputation and listings",
     r"\brated\b|\bratings?\b|\breviews?\b|\bstars?\b|\btop[- ]rated\b"
     r"|\btop \d*\s*\w*\s*(?:hospitals?|clinics?|doctors?)\b|\blisted (?:among|as|in|on|under)\b"
     r"|\blisting\b|\bawards?\b|\bawarded\b|recogni[sz]ed|years of (?:clinical |medical )?experience"
     r"|experience of \d|\btrusted\b|\brenowned\b|\breputed\b|well[- ]known|\bpopular\b"
     r"|success rate|accredit|\bnabh\b|\bnabl\b|\biso\b|\d[\d,]*\+? (?:happy )?(?:patients|visits|surgeries|deliveries)"),
    ("photo", "Photo captions and amenities",
     r"visible in|shown in|\bimages?\b|\bphotos?\b|\bpictures?\b|wheel ?chair|parking|accessib"
     r"|washroom|restroom|toilet|waiting (?:area|room|hall)|reception|air[- ]condition|elevator"
     r"|\bramp\b|canteen|cafeteria|seating|lobby|signage|\bbuilding\b|\bcampus\b|\bacres?\b"
     r"|spacious|ambience"),
    ("staff", "Staff and people",
     r"\bdr\.? ?[a-z]|\bled by\b|\bfounded by\b|\bheaded by\b|\bstaff\b|\bteam of\b|\bmbbs\b"
     r"|\bbds\b|\bmds\b|\bemployees\b|\bdoctors? on\b|\bconsultants? include"),
    ("web", "Web and contact",
     r"facebook|instagram|twitter|linkedin|youtube|website|social media|whatsapp|e-?mail"
     r"|phone number|contact (?:number|details)|\bonline\b|justdial|practo|google|\bweb ?page\b"
     r"|\bpage\b|\.com\b|\.org\b|\.in\b"),
    ("location", "Location",
     r"\blocated\b|\bsituated\b|\baddress\b|\blandmark\b|\bopposite\b|\bnear\b|\bcity of\b"
     r"|\bdistrict\b|\bserves (?:the )?(?:local|people|residents|community|patients from)"),
]
CASE_LOCATION = r"\bin [A-Z][A-Za-z .]+, [A-Z]"   # "in Agra, Uttar Pradesh"
CLINICAL_LABEL = "No noise keyword (mostly clinical)"


def classify_facts(facts: pd.DataFrame) -> pd.Series:
    low = facts.text.str.lower()
    cat = pd.Series("clinical", index=facts.index)
    done = pd.Series(False, index=facts.index)
    for key, _, pat in NOISE_CATS:
        hit = low.str.contains(pat, regex=True)
        if key == "location":
            hit |= facts.text.str.contains(CASE_LOCATION, regex=True)
        hit &= ~done
        cat[hit] = key
        done |= hit
    return cat


def fig05_facts(D, S):
    """How noisy the facts are."""
    fac, facts = D.fac, D.facts.copy()
    ok = fac[fac.row_status == "ok"]
    facts["cat"] = classify_facts(facts)
    keys = ["clinical"] + [k for k, _, _ in NOISE_CATS]
    names = {"clinical": CLINICAL_LABEL, **{k: n for k, n, _ in NOISE_CATS}}
    short = {"clinical": "Mostly clinical", "reputation": "Reputation, listings",
             "photo": "Photo captions, amenities", "staff": "Staff, people",
             "web": "Web, contact", "location": "Location"}
    fields = ["capability", "procedure", "equipment"]
    share = (pd.crosstab(facts.field, facts.cat, normalize="index") * 100).reindex(
        index=fields, columns=keys, fill_value=0)
    overall = facts.cat.value_counts(normalize=True).reindex(keys, fill_value=0) * 100
    nf = ok.n_facts
    st = {
        "n_facts": len(facts), "n_ok": len(ok),
        "by_field": {f: int((facts.field == f).sum()) for f in fields},
        "n_distinct_text": int(facts.text.str.lower().str.strip().nunique()),
        "facts_median": float(nf.median()), "facts_p90": float(nf.quantile(0.9)),
        "facts_max": int(nf.max()), "n_ok_zero_facts": int((nf == 0).sum()),
        "n_fac_any_field_at_50": int((ok[[f"n_{f}" for f in fields]] == 50).any(axis=1).sum()),
        "max_per_field": int(ok[[f"n_{f}" for f in fields]].max().max()),
        "pct_overall": {k: round(float(overall[k]), 1) for k in keys},
        "pct_by_field": {f: {k: round(float(share.loc[f, k]), 1) for k in keys} for f in fields},
    }
    st["pct_noise_overall"] = round(100 - st["pct_overall"]["clinical"], 1)
    st["pct_noise_capability"] = round(100 - st["pct_by_field"]["capability"]["clinical"], 1)
    S["f05"] = st
    guard(st["pct_noise_capability"] > st["pct_by_field"]["procedure"]["photo"],
          "capability is the noisiest field")
    guard(st["max_per_field"] == 50, "fact arrays capped at 50 per field")

    fig, gs = ps.figure(
        6.8,
        f"A median facility has {st['facts_median']:.0f} facts, and "
        f"{st['pct_noise_capability']:.0f}% of capability facts carry a noise keyword "
        f"(reputation, place, people, photos, web)",
        f"Left: facts per facility ({len(ok):,} complete rows). Right: a rough keyword "
        f"split of all {len(facts):,} fact strings (capability, procedure and equipment "
        f"facts), by the field they sit in.",
        note=SRC_FAC + " Rough split: keyword rules checked in the legend's order; a fact "
             "with no noise keyword is counted as mostly clinical, which also catches "
             "marketing such as 'state-of-the-art equipment'. Arrays are capped at 50 "
             "facts per field, hence the spike at 150.",
        ncols=2, width_ratios=[0.85, 1.25], left_in=0.85, right_in=0.4, wspace=0.42,
        legend_rows=3)
    ax = fig.add_subplot(gs[0])
    edges = np.arange(0, 155, 5)
    counts, _ = np.histogram(nf.clip(upper=150), bins=edges)
    ps.vbars(ax, edges[:-1] + 2.5, counts, width=5, gap=True, cap=False, round_end=False)
    med = st["facts_median"]
    ax.axvline(med, color=ps.INK_2, lw=ps.px(1.5), zorder=4)
    ax.annotate(f"median {med:.0f}", xy=(med, counts.max() * 0.98), xytext=(ps.px(6), 0),
                textcoords="offset points", ha="left", va="top", fontsize=11, color=ps.INK_2)
    ax.set_xlim(0, 152)
    ax.set_xticks([0, 25, 50, 75, 100, 125, 150])
    ax.set_xlabel("Facts per facility")
    ax.set_ylabel("Facilities")
    ps.int_axis(ax, "y")
    ps.clean_axes(ax, grid="y", keep=("bottom",))
    ax.tick_params(length=0)
    ps.panel_title(ax, "Facts per facility", reserve_rows=3)

    ax = fig.add_subplot(gs[1])
    rows = ["All facts"] + [f.capitalize() for f in fields]
    M = np.vstack([overall.values] + [share.loc[f].values for f in fields])
    colors = list(ps.SERIES[:len(keys)])
    rows = [f"{r}\n{n:,} facts" for r, n in zip(rows, [len(facts)] + [st["by_field"][f] for f in fields])]
    ps.stacked_hbars(ax, rows, M, colors, xmax=100, fontsize=10.5,
                     label_fmt=lambda v, t: f"{v:.0f}%")
    ax.set_xticks([0, 25, 50, 75, 100])
    ps.pct_axis(ax, "x")
    ps.clean_axes(ax, keep=())
    ax.tick_params(length=0)
    ps.panel_title(ax, "Share of fact strings by rough category",
                   [(short[k], c) for k, c in zip(keys, colors)], ncol=2, legend_rows=3,
                   legend_fontsize=10.5)
    ps.save(fig, FIG_DIR / "eda_05_fact_noise.png")


def fig06_completeness(D, S):
    """Column completeness for the fields an analyst would want."""
    fac = D.fac
    n = len(fac)
    nn = lambda c: int((~fac[c].map(io.is_null)).sum())  # noqa: E731
    analyst = {
        "Coordinates inside India": int(fac.coord_ok.sum()),
        "Facility type": nn("facilityTypeId"),
        "Specialties (at least one)": int((fac.spec.map(len) > 0).sum()),
        "6-digit PIN code": int(fac.pin.notna().sum()),
        "Operator (private or public)": nn("operatorTypeId"),
        "Year established": nn("yearEstablished"),
        "Doctor count": nn("numberDoctors"),
        "Bed count": nn("capacity"),
        "Affiliation (e.g. government)": nn("affiliationTypeIds"),
        "Floor area": nn("area"),
        "Accepts volunteers": nn("acceptsVolunteers"),
    }
    web = {
        "Social platform count": nn("distinct_social_media_presence_count"),
        "Staff named on page (flag)": nn("affiliated_staff_presence"),
        "Facebook link": nn("facebookLink"),
        "Custom logo (flag)": nn("custom_logo_presence"),
        "Phone number": nn("officialPhone"),
        "Follower count": nn("engagement_metrics_n_followers"),
        "Email": nn("email"),
        "Official website": nn("officialWebsite"),
        "Last social post date": nn("post_metrics_most_recent_social_media_post_date"),
        "Page update date": nn("recency_of_page_update"),
    }
    beds = fac.capacity.map(io.to_float)
    docs = fac.numberDoctors.map(io.to_float)
    year = fac.yearEstablished.map(io.to_float)
    st = {
        "n": n,
        "analyst": {k: {"n": v, "pct": round(100 * v / n, 1)} for k, v in analyst.items()},
        "web": {k: {"n": v, "pct": round(100 * v / n, 1)} for k, v in web.items()},
        "beds_median": float(beds.median()), "beds_max": float(beds.max()),
        "beds_implausible": int(((beds == 0) | (beds > 5000)).sum()),
        "doctors_median": float(docs.median()), "doctors_max": float(docs.max()),
        "doctors_implausible": int(((docs == 0) | (docs > 5000)).sum()),
        "year_min": float(year.min()), "year_implausible": int((year < 1800).sum()),
    }
    S["f06"] = st
    guard(st["analyst"]["Bed count"]["pct"] < 40, "bed count is sparse")

    items = [(k, v, "analyst") for k, v in analyst.items()] + [(k, v, "web") for k, v in web.items()]
    items.sort(key=lambda t: -t[1])
    fig, gs = ps.figure(
        8.0,
        f"Location, type and contact fields are nearly complete; beds "
        f"({st['analyst']['Bed count']['pct']:.0f}%) and doctors "
        f"({st['analyst']['Doctor count']['pct']:.0f}%) are mostly missing",
        f"Share of the {n:,} facilities where each field holds a value (anything but the "
        f"string 'null').",
        note=SRC_FAC + f" Present is not the same as plausible: bed counts reach "
             f"{st['beds_max']:,.0f}, doctor counts {st['doctors_max']:,.0f}, and "
             f"{st['year_implausible']} founding years are below 1800 (some are 0).",
        left_in=3.0, right_in=0.6, legend_rows=1)
    ax = fig.add_subplot(gs[0])
    colors = [ps.BLUE if g == "analyst" else ps.DEEMPH for _, _, g in items]
    ps.hbars(ax, [k for k, _, _ in items], [100 * v / n for _, v, _ in items],
             colors=colors, fmt=lambda v: f"{v:.1f}%", xmax=112, fontsize=10.5)
    ax.tick_params(axis="y", labelsize=10.5)
    ps.panel_title(ax, "Share of facilities with a value", [
        ("Fields an analyst needs", ps.BLUE), ("Web and contact fields", ps.DEEMPH)])
    ps.save(fig, FIG_DIR / "eda_06_column_completeness.png")


def url_domain(u: str):
    u = u.strip()
    if not re.match(r"^[a-z][a-z0-9+.-]*://", u, re.I):
        u = "http://" + u
    try:
        h = urlparse(u).hostname
    except ValueError:
        return None
    if not h or "." not in h:
        return None
    h = h.lower()
    return h[4:] if h.startswith("www.") else h


SOCIAL = {"facebook.com", "m.facebook.com", "in.linkedin.com", "linkedin.com",
          "instagram.com", "twitter.com", "x.com", "youtube.com"}
DIRECTORY = {"justdial.com", "practo.com", "medindia.net", "onefivenine.com", "lybrate.com",
             "docindia.org", "hexahealth.com", "medicineindia.org", "bajajfinservhealth.in",
             "healthfrog.in", "chotu.com", "drlogy.com", "sulekha.com", "clinicspots.com",
             "cardiologistindia.com", "grotal.com", "bdir.in"}
OTHER_SITES = {"mappls.com": "maps", "latlong.net": "coordinates", "proptiger.com": "real estate",
               "policyx.com": "insurance", "policybazaar.com": "insurance",
               "healthinsuranceindia.org": "insurance", "indiamart.com": "B2B",
               "scribd.com": "documents", "zoominfo.com": "company data",
               "en.wikipedia.org": "encyclopedia"}


def fig07_sources(D, S):
    """Where the facts come from."""
    fac = D.fac
    ok = fac[fac.row_status == "ok"]
    urls = ok.source_urls.map(io.parse_list)
    doms = urls.map(lambda l: sorted({d for d in (url_domain(u) for u in l) if d}))
    nd = doms.map(len)
    n_unparsed = int(sum(1 for l in urls for u in l if url_domain(u) is None))
    cnt = Counter(d for l in doms for d in l)
    top = cnt.most_common(20)
    kind = {}
    for d, _ in top:
        kind[d] = ("social" if d in SOCIAL else "directory" if d in DIRECTORY
                   else "other" if d in OTHER_SITES else None)
        if kind[d] is None:
            raise AssertionError(f"classify top domain {d} in SOCIAL/DIRECTORY/OTHER_SITES")
    own = null_to_na(ok.officialWebsite).map(lambda w: url_domain(w) if isinstance(w, str) else None)
    own_in = [o is not None and o in set(d) for o, d in zip(own, doms)]
    n_own = int(own.notna().sum())
    top_other = {d for d, _ in top if kind[d] == "other"}
    other_any = doms.map(lambda l: any(d in top_other for d in l))
    dist = nd.clip(upper=21).value_counts().sort_index()
    st = {
        "n_ok": len(ok), "urls_median": float(urls.map(len).median()),
        "urls_max": int(urls.map(len).max()), "n_urls": int(urls.map(len).sum()),
        "n_url_unparsed": n_unparsed,
        "domains_median": float(nd.median()),
        "domains_dist": {("21+" if k == 21 else str(k)): int(v) for k, v in dist.items()},
        "n_one_domain": int((nd == 1).sum()), "n_ge6": int((nd >= 6).sum()),
        "top20": [{"domain": d, "n": int(c), "kind": kind[d]} for d, c in top],
        "n_with_official_site": n_own, "n_official_in_sources": int(sum(own_in)),
        "pct_official_in_sources": round(100 * sum(own_in) / n_own, 1),
        "n_other_top20": len(top_other), "n_any_other_site": int(other_any.sum()),
        "pct_any_other_site": round(100 * other_any.mean(), 1),
        "n_distinct_domains": len(cnt),
    }
    S["f07"] = st
    guard(top[0][0] == "facebook.com", "facebook is the top source")
    guard(st["n_official_in_sources"] < top[1][1], "own sites cited less than the top listing site")

    fig, gs = ps.figure(
        7.4,
        f"Facts come from Facebook and listing sites more than from facilities' own pages",
        f"Left: the 20 web domains cited by the most facilities ({len(ok):,} complete rows). "
        f"Right: how many distinct domains each facility's facts draw on.",
        note=SRC_FAC + f" Domains parsed from source_urls with urllib ('www.' removed). "
             f"Only {st['pct_official_in_sources']:.0f}% of facilities with an official website "
             f"cite that site among their sources. Some sources are unrelated pages, so a "
             f"fact may describe a different organization.",
        ncols=2, width_ratios=[1.25, 1], left_in=2.25, right_in=0.4, wspace=0.35,
        legend_rows=1)
    ax = fig.add_subplot(gs[0])
    kc = {"social": ps.BLUE, "directory": ps.ORANGE, "other": ps.AQUA}
    labels = [d + (f" ({OTHER_SITES[d]})" if d in OTHER_SITES else "") for d, _ in top]
    ps.hbars(ax, labels, [c for _, c in top], colors=[kc[kind[d]] for d, _ in top],
             fontsize=10.5)
    ax.tick_params(axis="y", labelsize=10.5)
    ps.panel_title(ax, "Facilities citing each domain", [
        ("Social", kc["social"]), ("Listing site", kc["directory"]),
        ("Maps, insurance, other", kc["other"])], legend_fontsize=10.5)

    ax = fig.add_subplot(gs[1])
    xs = np.arange(1, 22)
    vals = [int(dist.get(x, 0)) for x in xs]
    ps.vbars(ax, xs, vals, width=0.8, cap=False)
    ax.set_xticks([1, 5, 10, 15, 21], ["1", "5", "10", "15", "21+"])
    ax.set_xlabel("Distinct domains per facility")
    ax.set_ylabel("Facilities")
    ps.int_axis(ax, "y")
    ps.clean_axes(ax, grid="y", keep=("bottom",))
    ax.tick_params(length=0)
    ps.panel_title(ax, f"Domains per facility (median {st['domains_median']:.0f})",
                   reserve_rows=1)
    ps.save(fig, FIG_DIR / "eda_07_fact_sources.png")


STATE_DISPLAY = {"Maharastra": "Maharashtra",
                 "Dadra and Nagar Haveli & Daman and Diu": "Dadra NH & Daman Diu",
                 "Andaman & Nicobar Islands": "Andaman & Nicobar"}


def nfhs_frame(D) -> pd.DataFrame:
    df = D.nf_val.copy()
    df.insert(0, "state", D.nf_state.values)
    df.insert(1, "district", D.nf_district.values)
    return df


def fig08_nfhs_births(D, S):
    """NFHS: institutional births across districts; within vs between states."""
    df = nfhs_frame(D)
    y = df.institutional_birth_5y_pct
    g = y.groupby(df.state)
    between = float(((g.transform("mean") - y.mean()) ** 2).sum() / ((y - y.mean()) ** 2).sum())
    by_state = g.agg(["count", "median", "min", "max"]).sort_values("median")
    st = {
        "n_districts": int(y.notna().sum()), "n_states": int(df.state.nunique()),
        "median": float(y.median()), "mean": round(float(y.mean()), 1),
        "min": float(y.min()), "max": float(y.max()),
        "n_below_80": int((y < 80).sum()), "n_ge_95": int((y >= 95).sum()),
        "min_district": f"{df.district[y.idxmin()]}, {df.state[y.idxmin()]}",
        "pct_between_states": round(100 * between, 1),
        "pct_within_states": round(100 * (1 - between), 1),
        "lowest_states": {s: float(r["median"]) for s, r in by_state.head(5).iterrows()},
        "highest_states": {s: float(r["median"]) for s, r in by_state.tail(5).iterrows()},
        "widest_states": {s: round(float(r["max"] - r["min"]), 1) for s, r in
                          (by_state.assign(rng=by_state["max"] - by_state["min"])
                           .sort_values("rng", ascending=False).head(3).iterrows())},
        "flags": D.nf_flag.institutional_birth_5y_pct.value_counts().to_dict(),
    }
    S["f08"] = st
    guard(st["pct_between_states"] > 50, "most variation is between states")

    fig, gs = ps.figure(
        9.6,
        f"Half of districts have {st['median']:.0f}% or more institutional births, and "
        f"{st['pct_between_states']:.0f}% of the variation is between states",
        f"Share of births in the five years before the survey that took place in a health "
        f"facility, for {st['n_districts']} NFHS-5 districts in {st['n_states']} states and "
        f"union territories.",
        note=SRC_NFHS + " Between-state share: the variance of state means (each district "
             "weighted equally) over the total variance across districts. Numbers in brackets: "
             "districts per state. State names as in NFHS, except the spelling 'Maharastra'.",
        nrows=2, ncols=2, width_ratios=[1, 1.15], height_ratios=[1, 0.32],
        left_in=0.85, right_in=0.4, wspace=0.75, hspace=0.6)
    ax = fig.add_subplot(gs[0, 0])
    edges = np.arange(20, 102.5, 2.5)
    counts, _ = np.histogram(y, bins=edges)
    ps.vbars(ax, edges[:-1] + 1.25, counts, width=2.5, gap=True, cap=False, round_end=False)
    ax.axvline(st["median"], color=ps.INK_2, lw=ps.px(1.5), zorder=4)
    ax.annotate(f"median\n{st['median']:.1f}%", xy=(st["median"], counts.max() * 0.97),
                xytext=(-ps.px(6), 0), textcoords="offset points", ha="right", va="top",
                fontsize=11, color=ps.INK_2)
    ax.set_xlim(20, 101)
    ps.pct_axis(ax, "x")
    ax.set_xlabel("Institutional births")
    ax.set_ylabel("Districts")
    ps.clean_axes(ax, grid="y", keep=("bottom",))
    ax.tick_params(length=0)
    ps.panel_title(ax, "Districts by institutional births")

    ax = fig.add_subplot(gs[1, 0])
    ps.segmented_bar(ax, [("Between states", st["pct_between_states"], ps.BLUE),
                          ("Within states", st["pct_within_states"], ps.DEEMPH)],
                     label_fmt=lambda v, sh: f"{sh:.0f}%")
    ps.panel_title(ax, "Where the variation sits", [("Between states", ps.BLUE),
                                                    ("Within states", ps.DEEMPH)])

    ax = fig.add_subplot(gs[:, 1])
    order = by_state.index.tolist()
    pos = {s: i for i, s in enumerate(order)}
    yy = df.state.map(pos)
    for s_, r in by_state.iterrows():
        ax.plot([r["min"], r["max"]], [pos[s_]] * 2, color=ps.AXIS, lw=ps.px(1.5), zorder=1,
                solid_capstyle="butt")
    ax.scatter(y, yy, s=14, color=ps.BLUE, alpha=0.55, lw=0, zorder=2)
    ax.scatter(by_state["median"], range(len(order)), marker="|", s=110, color=ps.INK,
               lw=ps.px(2), zorder=3)
    ax.set_yticks(range(len(order)), [f"{STATE_DISPLAY.get(s_, s_)} ({int(by_state.loc[s_, 'count'])})"
                                      for s_ in order], fontsize=9.5)
    ax.set_ylim(len(order) - 0.4, -0.6)
    ax.set_xlim(20, 101)
    ps.pct_axis(ax, "x")
    ax.set_xlabel("Institutional births")
    ps.clean_axes(ax, grid="x", keep=())
    ax.tick_params(length=0)
    ps.panel_title(ax, "Districts (dots) by state; tick = median")
    ps.save(fig, FIG_DIR / "eda_08_nfhs_institutional_births.png")


PROXIES = [  # (NFHS column, label, kind): kind "dev" = development proxy, "care" = care use
    ("women_age_15_49_with_10_or_more_years_of_schooling_pct", "Women with 10+ years of school", "dev"),
    ("hh_electricity_pct", "Households with electricity", "dev"),
    ("hh_member_covered_health_insurance_pct", "Households with an insured member", "dev"),
]
PROXY_SHORT = {"women_age_15_49_with_10_or_more_years_of_schooling_pct": "Female schooling",
               "hh_electricity_pct": "Electricity",
               "hh_member_covered_health_insurance_pct": "Health insurance"}
CANDIDATES = PROXIES + [
    ("women_age_15_49_who_are_literate_pct", "Women who are literate", "dev"),
    ("female_population_age_6_years_and_above_ever_schooled_pct", "Females 6+ ever schooled", "dev"),
    ("households_using_clean_fuel_for_cooking_pct", "Clean cooking fuel", "dev"),
    ("hh_use_improved_sanitation_pct", "Improved sanitation", "dev"),
    ("hh_improved_water_pct", "Improved drinking water", "dev"),
    ("w20_24_married_before_age_18_years_pct", "Women 20-24 married before 18", "dev"),
    ("population_below_age_15_years_pct", "Population under 15", "dev"),
    ("mothers_who_had_at_least_4_anc_visits_lb5y_pct", "Mothers with 4+ antenatal visits", "care"),
    ("births_attended_by_skilled_hp_5y_10_pct", "Births with a skilled attendant", "care"),
]


def fig09_nfhs_proxies(D, S):
    """NFHS: institutional births against development proxies."""
    df = nfhs_frame(D)
    y = df.institutional_birth_5y_pct
    corr = {}
    for col, label, kind in CANDIDATES:
        x = df[col]
        m = x.notna() & y.notna()
        corr[col] = {"label": label, "kind": kind, "n": int(m.sum()),
                     "pearson": round(float(x[m].corr(y[m])), 2),
                     "spearman": round(float(x[m].corr(y[m], method="spearman")), 2)}
    st = {"corr": corr, "n": int(y.notna().sum()),
          "electricity_median": float(df.hh_electricity_pct.median()),
          "n_electricity_ge90": int((df.hh_electricity_pct >= 90).sum())}
    dev = {c: v for c, v in corr.items() if v["kind"] == "dev"}
    best_dev = max(dev, key=lambda c: abs(dev[c]["spearman"]))
    st["best_dev"] = best_dev
    S["f09"] = st
    guard(corr["births_attended_by_skilled_hp_5y_10_pct"]["spearman"] > 0.8,
          "skilled attendance nearly duplicates the target")
    guard(corr["hh_member_covered_health_insurance_pct"]["spearman"]
          < corr["hh_electricity_pct"]["spearman"], "insurance is weaker than electricity")

    fig, gs = ps.figure(
        10.2,
        "Institutional births rise with schooling and electricity; insurance tells "
        "much less, and care-use columns would leak the answer",
        f"Top: each dot is one of {st['n']} NFHS-5 districts; the dark line joins the "
        f"median within each tenth of the x values. Bottom: rank correlation with "
        f"institutional births for candidate model features.",
        note=SRC_NFHS + " Correlation across districts, not causation, and each district "
             "counts once whatever its population. Spearman's rank correlation is used "
             "because the relationships are curved and the target is bunched near 100%.",
        nrows=2, ncols=3, height_ratios=[1, 1.05], left_in=0.85, right_in=0.4,
        wspace=0.32, hspace=0.62)
    for i, (col, label, _) in enumerate(PROXIES):
        ax = fig.add_subplot(gs[0, i])
        x = df[col]
        ax.scatter(x, y, s=12, color=ps.BLUE, alpha=0.45, lw=0, zorder=2)
        m = x.notna() & y.notna()
        bins = pd.qcut(x[m], 10, duplicates="drop")
        med = pd.DataFrame({"x": x[m], "y": y[m], "b": bins}).groupby("b", observed=True).median()
        ax.plot(med.x, med.y, color=ps.INK, lw=ps.px(2), zorder=3)
        ax.scatter(med.x, med.y, s=ps.px(8) ** 2, color=ps.INK, edgecolors=ps.SURFACE,
                   linewidths=ps.px(2), zorder=4)
        c = corr[col]
        ax.text(0.97, 0.04, f"Pearson {c['pearson']:.2f}\nSpearman {c['spearman']:.2f}",
                transform=ax.transAxes, ha="right", va="bottom", fontsize=10.5, color=ps.INK_2)
        ax.set_ylim(15, 102)
        ps.pct_axis(ax, "x")
        ps.pct_axis(ax, "y")
        if i == 0:
            ax.set_ylabel("Institutional births")
        ax.set_xlabel(label, fontsize=11)
        ps.clean_axes(ax, grid="both", keep=())
        ax.tick_params(length=0)
        ps.panel_title(ax, PROXY_SHORT[col], fontsize=12.5)

    # the bar panel spans the row but needs a wider left margin for its labels
    bb = gs[1, :].get_position(fig)
    shift = 2.4 / ps.FIG_W_IN
    ax = fig.add_axes([bb.x0 + shift, bb.y0, bb.width - shift, bb.height])
    items = sorted(corr.items(), key=lambda kv: -kv[1]["spearman"])
    yy = np.arange(len(items))
    vals = [v["spearman"] for _, v in items]
    colors = [ps.BLUE if v["kind"] == "dev" else ps.DEEMPH for _, v in items]
    bars = ax.barh(yy, vals, height=0.62, color=colors, lw=0)
    ps.register_bars(ax, bars, "h")
    for b, v in zip(bars, vals):
        ax.annotate(f"{v:+.2f}", xy=(v, b.get_y() + b.get_height() / 2),
                    xytext=(ps.px(6) if v >= 0 else -ps.px(6), 0), textcoords="offset points",
                    ha="left" if v >= 0 else "right", va="center", fontsize=10.5, color=ps.INK_2)
    ax.set_yticks(yy, [v["label"] for _, v in items], fontsize=10.5)
    ax.set_ylim(len(items) - 0.5, -0.5)
    ax.set_xlim(-1.0, 1.05)
    ax.axvline(0, color=ps.AXIS, lw=ps.px(1))
    ax.set_xticks([-1, -0.5, 0, 0.5, 1])
    ax.set_xlabel("Spearman rank correlation with institutional births")
    ps.clean_axes(ax, grid="x", keep=())
    ax.tick_params(length=0)
    ps.panel_title(ax, "Candidate features for the outcome model (Model B)", [
        ("Development proxy", ps.BLUE), ("Care use: measures the outcome itself", ps.DEEMPH)])
    # this panel's legend needs room under the scatter x labels; hspace covers it
    ps.save(fig, FIG_DIR / "eda_09_nfhs_development_proxies.png")


def norm_state(s: str) -> str:
    s = s.upper().replace("&", " AND ")
    s = re.sub(r"[^A-Z ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return re.sub(r"^THE ", "", s)


NFHS_STATE_FIX = {"MAHARASTRA": "MAHARASHTRA", "NCT OF DELHI": "DELHI"}


def fig10_public(D, S):
    """The public-care blind spot."""
    fac = D.fac
    df = nfhs_frame(D)
    w = df.households_surveyed
    inst, pub = df.institutional_birth_5y_pct, df.institutional_birth_in_public_facility_5y_pct
    guard(bool((pub <= inst + 0.05).all()), "public-facility births are a share of all births")
    nat_inst = float(np.average(inst, weights=w))
    nat_pub_all = float(np.average(pub, weights=w))
    nat_pub_of_inst = 100 * nat_pub_all / nat_inst
    n = len(fac)
    n_pub = int((fac.op == "public").sum())
    hosp = fac.ftype == "hospital"
    n_known = int((fac.op != "unknown").sum())

    # state-level comparison: facility state from the PIN directory
    m = fac[["pin", "op"]].join(D.pin_modal, on="pin").dropna(subset=["statename"])
    m["skey"] = m.statename.map(norm_state)
    fs = m.groupby("skey").agg(n=("op", "size"), n_pub=("op", lambda o: int((o == "public").sum())))
    df["skey"] = df.state.map(norm_state).replace(NFHS_STATE_FIX)
    grp = df.groupby("skey")
    ns = pd.DataFrame({
        "state": grp.state.first(),
        "pub_of_inst": grp.apply(lambda g: 100 * np.average(
            g.institutional_birth_in_public_facility_5y_pct, weights=g.households_surveyed)
            / np.average(g.institutional_birth_5y_pct, weights=g.households_surveyed),
            include_groups=False)})
    pin_states = set(D.po.statename.map(norm_state))
    name_mismatch = sorted(set(ns.index) - pin_states)
    no_facilities = sorted(set(ns.index) - set(fs.index))
    j = ns.join(fs, how="inner")
    j["fac_pub_pct"] = 100 * j.n_pub / j.n
    MIN_N = 30
    jj = j[j.n >= MIN_N].copy()
    st = {
        "n": n, "n_public": n_pub, "pct_public": round(100 * n_pub / n, 1),
        "pct_public_of_known": round(100 * n_pub / n_known, 1),
        "pct_public_hospitals": round(100 * (hosp & (fac.op == "public")).sum() / hosp.sum(), 1),
        "nfhs_inst_weighted": round(nat_inst, 1), "nfhs_pub_all_weighted": round(nat_pub_all, 1),
        "nfhs_pub_of_inst": round(nat_pub_of_inst, 1),
        "nfhs_pub_of_inst_unweighted": round(100 * pub.mean() / inst.mean(), 1),
        "hh_surveyed_median": float(w.median()), "hh_surveyed_min": float(w.min()),
        "hh_surveyed_max": float(w.max()),
        "nfhs_states_not_in_pin_dir": name_mismatch, "nfhs_states_without_facilities": no_facilities,
        "min_n_state": MIN_N, "n_states_plotted": len(jj),
        "states_dropped_small": int((j.n < MIN_N).sum()),
        "state_rows": {r.state: {"n": int(r.n), "fac_pub_pct": round(float(r.fac_pub_pct), 1),
                                 "nfhs_pub_of_inst": round(float(r.pub_of_inst), 1)}
                       for _, r in jj.iterrows()},
        "n_states_nfhs_above_fac": int((jj.pub_of_inst > jj.fac_pub_pct).sum()),
        "max_fac_pub_state": jj.fac_pub_pct.idxmax(),
    }
    st["max_fac_pub_state"] = jj.loc[st["max_fac_pub_state"], "state"]
    gap = jj.pub_of_inst - jj.fac_pub_pct
    st["min_gap_points"] = round(float(gap.min()), 1)
    st["min_gap_state"] = jj.loc[gap.idxmin(), "state"]
    st["min_nfhs_state"] = jj.loc[jj.pub_of_inst.idxmin(), "state"]
    st["max_fac_pub_pct"] = round(float(jj.fac_pub_pct.max()), 1)
    S["f10"] = st
    guard(st["nfhs_pub_of_inst"] > 10 * st["pct_public"], "survey public share dwarfs the data's")
    guard(not name_mismatch, f"every NFHS state matches a PIN-directory state: {name_mismatch}")

    fig, gs = ps.figure(
        7.6,
        f"Public facilities are {st['pct_public']:.1f}% of this dataset but deliver about "
        f"{st['nfhs_pub_of_inst']:.0f}% of institutional births",
        "Left: public share in the facilities table next to the public share of institutional "
        "births in NFHS-5. Right: the same comparison by state.",
        note=SRC_FAC + " " + SRC_NFHS.replace("Source: ", "") + " Survey shares are district "
             f"values averaged with households surveyed as weights (median "
             f"{st['hh_surveyed_median']:.0f} per district), so close to a plain district "
             f"average and not population-weighted. NFHS is 2019-21; the latest web date in "
             f"the facilities table is {D.scrape_cutoff.date()}.",
        ncols=2, width_ratios=[1, 1.1], left_in=0.5, right_in=0.5, wspace=0.28)
    ax = fig.add_subplot(gs[0])
    rows = [
        ("Facilities in this dataset that are public", st["pct_public"],
         f"{n_pub:,} of {n:,}"),
        ("Hospitals in this dataset that are public", st["pct_public_hospitals"],
         f"{int((hosp & (fac.op == 'public')).sum()):,} of {int(hosp.sum()):,}"),
        ("Institutional births in a public facility (NFHS-5)", st["nfhs_pub_of_inst"],
         f"{st['nfhs_pub_all_weighted']:.1f}% of all births, out of "
         f"{st['nfhs_inst_weighted']:.1f}% in any facility"),
    ]
    # meter list: name, bar against 100% (track in a lighter step), figure at right
    ax.set_xlim(0, 128)
    ax.set_ylim(len(rows) * 2.4, 0)
    ax.set_axis_off()
    for i, (name, val, sub) in enumerate(rows):
        y0 = i * 2.4 + 0.5
        ax.text(0, y0, name, fontsize=11.5, color=ps.INK, va="bottom", ha="left")
        track = ax.barh([y0 + 0.55], [100], height=0.5, color=ps.BLUE_RAMP[100], lw=0)
        fill = ax.barh([y0 + 0.55], [val], height=0.5, color=ps.BLUE, lw=0)
        ps.register_bars(ax, list(track) + list(fill), "h", cap=False)
        ax.text(104, y0 + 0.55, f"{val:.1f}%", fontsize=15, weight="bold", color=ps.INK,
                va="center", ha="left")
        ax.text(0, y0 + 1.05, sub, fontsize=10, color=ps.MUTED, va="top", ha="left")
    ps.panel_title(ax, "Public share, nationally")

    ax = fig.add_subplot(gs[1])
    ax.plot([0, 100], [0, 100], color=ps.AXIS, lw=ps.px(1), zorder=1)
    ax.set_xlim(0, 30)
    ax.set_ylim(0, 100)
    ax.text(29, 22, "equal shares", fontsize=10, color=ps.MUTED, ha="right", va="top")
    ax.scatter(jj.fac_pub_pct, jj.pub_of_inst, s=ps.px(9) ** 2, color=ps.BLUE,
               edgecolors=ps.SURFACE, linewidths=ps.px(2), zorder=3)
    lab = jj.sort_values("n", ascending=False).head(3).index.tolist()
    lab += [jj.fac_pub_pct.idxmax(), jj.pub_of_inst.idxmin(), jj.pub_of_inst.idxmax(),
            jj.fac_pub_pct.idxmin()]
    lab = list(dict.fromkeys(lab))
    st["labeled_states"] = [jj.loc[k, "state"] for k in lab]
    ps.pct_axis(ax, "x")
    ps.pct_axis(ax, "y")
    ax.set_xlabel("Public share of facilities in this dataset")
    ax.set_ylabel("Public share of institutional births")
    ps.clean_axes(ax, grid="both", keep=())
    ax.tick_params(length=0)
    ps.label_points(ax, jj.loc[lab, "fac_pub_pct"].tolist(), jj.loc[lab, "pub_of_inst"].tolist(),
                    [STATE_DISPLAY.get(jj.loc[k, "state"], jj.loc[k, "state"]) for k in lab])
    ps.panel_title(ax, f"By state ({len(jj)} states with {MIN_N}+ facilities)")
    ps.save(fig, FIG_DIR / "eda_10_public_blind_spot.png")


NFHS_GROUPS = [  # (label, first column, last column), contiguous in the file
    ("Population and household", "female_population_age_6_years_and_above_ever_schooled_pct",
     "hh_member_covered_health_insurance_pct"),
    ("Schooling, marriage, fertility", "child_5y_who_attended_pre_primary_school_during_the_school_pct",
     "w15_24_who_use_menstrual_hygiene_pct"),
    ("Family planning", "fp_cm_w15_49_any_method_pct",
     "current_users_ever_told_about_side_effects_of_current_metho_pct"),
    ("Maternal care", "mothers_who_had_an_anc_visit_in_the_first_trimester_lb5y_pct",
     "children_who_received_pnc_from_a_doctor_nurse_lhv_anm_midwi_pct"),
    ("Delivery care", "institutional_birth_5y_pct",
     "births_in_a_public_fac_that_were_delivered_by_csection_5y_pct"),
    ("Child vaccination", "child_12_23m_fully_vaccinated_based_on_information_from_eit_pct",
     "child_12_23m_who_received_most_of_their_vaccinations_in_a_2_pct"),
    ("Child illness and treatment", "prev_diarrhoea_2wk_child_u5_pct",
     "children_with_fever_or_symptoms_of_ari_2wk_taken_to_a_healt_pct"),
    ("Child feeding", "children_under_age_3_years_breastfed_within_one_hour_of_bir_pct",
     "total_child_6_23m_receiving_an_adequate_diet16_17_pct"),
    ("Nutrition status", "child_u5_who_are_stunted_height_for_age_18_pct",
     "women_age_15_49_years_who_have_high_risk_whr_gte_0_85_pct"),
    ("Anaemia", "child_6_59m_who_are_anaemic_lt_11_0_g_dl_22_pct", "all_w15_19_who_are_anaemic_pct"),
    ("Blood sugar", "women_age_15_years_and_above_with_high_141_160_mg_dl_blood_pct",
     "m15_plus_with_high_or_very_high_gt_140_mg_dl_blood_sugar_or_pct"),
    ("Blood pressure", "w15_plus_with_mildly_high_bp_sys_140_159_mmhg_and_or_dia_90_pct",
     "m15_plus_with_high_bp_sys_gte_140_mmhg_and_or_dia_gte_90_mm_pct"),
    ("Cancer screening", "women_age_30_49_years_ever_undergone_a_cervical_screen_pct",
     "women_age_30_49_years_ever_undergone_an_oral_cancer_exam_pct"),
    ("Tobacco and alcohol", "w15_plus_who_use_any_kind_of_tobacco_pct", "m15_plus_who_consume_alcohol_pct"),
]
PLAN_COLUMNS = [
    ("institutional_birth_5y_pct", "Institutional births"),
    ("institutional_birth_in_public_facility_5y_pct", "Births in a public facility"),
    ("births_delivered_by_csection_5y_pct", "C-section births"),
    ("births_in_a_private_fac_that_were_delivered_by_csection_5y_pct", "C-section rate, private facilities"),
    ("births_in_a_public_fac_that_were_delivered_by_csection_5y_pct", "C-section rate, public facilities"),
    ("births_attended_by_skilled_hp_5y_10_pct", "Skilled birth attendance"),
    ("mothers_who_had_at_least_4_anc_visits_lb5y_pct", "4+ antenatal visits"),
    ("average_out_of_pocket_expenditure_per_delivery_in_a_public_fac", "Cost per delivery, public"),
    ("hh_member_covered_health_insurance_pct", "Health insurance"),
    ("women_age_15_49_with_10_or_more_years_of_schooling_pct", "Women with 10+ years of school"),
    ("w15_plus_with_high_or_very_high_gt_140_mg_dl_blood_sugar_or_pct", "High blood sugar, women"),
    ("w15_plus_with_high_bp_sys_gte_140_mmhg_and_or_dia_gte_90_mm_pct", "High blood pressure, women"),
]
FLAG_KEYS = ["ok", "low_sample", "suppressed"]
FLAG_LABEL = {"ok": "Plain number", "low_sample": "Low sample (in brackets)",
              "suppressed": "Suppressed (*)"}
FLAG_COLOR = {"ok": ps.CONTEXT, "low_sample": ps.BLUE_RAMP[350], "suppressed": ps.BLUE_RAMP[650]}


def fig11_nfhs_flags(D, S):
    """NFHS reliability: suppressed and low-sample cells per indicator group."""
    cols = D.nf_cols
    fl = D.nf_flag
    groups, covered = [], []
    for label, a, b in NFHS_GROUPS:
        ia, ib = cols.index(a), cols.index(b)
        gcols = cols[ia:ib + 1]
        covered += gcols
        vc = fl[gcols].stack().value_counts()
        tot = int(vc.sum())
        groups.append((label, len(gcols), {k: int(vc.get(k, 0)) for k in FLAG_KEYS}, tot))
    size_cols = ["households_surveyed", "women_15_49_interviewed", "men_15_54_interviewed"]
    assert sorted(covered + size_cols) == sorted(cols), "NFHS groups must cover every indicator"
    allvc = fl[[c for c in cols if c not in size_cols]].stack().value_counts()
    st = {
        "n_indicators": len(covered), "n_cells": int(allvc.sum()),
        "cells": {k: int(allvc.get(k, 0)) for k in allvc.index},
        "groups": {g[0]: {"n_cols": g[1], **g[2], "pct_flagged": round(100 * (g[2]["low_sample"] + g[2]["suppressed"]) / g[3], 1)}
                   for g in groups},
        "plan": {lab: {k: int((fl[c] == k).sum()) for k in FLAG_KEYS} for c, lab in PLAN_COLUMNS},
        "n_cols_majority_flagged": int(((fl[covered] != "ok").mean() > 0.5).sum()),
    }
    S["f11"] = st
    guard(st["plan"]["Institutional births"]["ok"] == len(fl), "the main target is fully reported")
    guard(all(st["groups"][k]["pct_flagged"] == 0 for k in
              ["Blood sugar", "Blood pressure", "Cancer screening", "Tobacco and alcohol"]),
          "adult chronic-disease groups have no flags")

    groups.sort(key=lambda g: -(g[2]["low_sample"] + g[2]["suppressed"]) / g[3])
    fig, gs = ps.figure(
        7.4,
        "Child-health indicators are often suppressed; the delivery columns the plan "
        "needs are clean except the private C-section rate",
        f"How NFHS-5 reports {st['n_indicators']} indicators across {len(fl)} districts: a plain "
        f"number, a low-sample estimate in brackets (25 to 49 cases), or * (suppressed).",
        note=SRC_NFHS + " Read * as missing, never as zero, and carry a flag for bracketed "
             "values (CLAUDE.md trap 10). Groups follow the column order of the file; the number "
             "after a group is how many columns it holds.",
        ncols=2, width_ratios=[1, 1], left_in=2.3, right_in=0.4, wspace=1.05, legend_rows=1)
    ax = fig.add_subplot(gs[0])
    M = np.array([[100 * g[2][k] / g[3] for k in FLAG_KEYS] for g in groups])
    ps.stacked_hbars(ax, [f"{g[0]} ({g[1]})" for g in groups], M,
                     [FLAG_COLOR[k] for k in FLAG_KEYS], xmax=100, fontsize=10,
                     label_fmt=lambda v, t: f"{v:.0f}%", label_parts=[False, True, True])
    ax.tick_params(axis="y", labelsize=10.5)
    ax.set_xticks([0, 50, 100])
    ps.pct_axis(ax, "x")
    ps.clean_axes(ax, keep=())
    ax.tick_params(length=0)
    ps.panel_title(ax, "Cells by indicator group", [
        (FLAG_LABEL[k], FLAG_COLOR[k]) for k in FLAG_KEYS], legend_fontsize=10.5)

    ax = fig.add_subplot(gs[1])
    M2 = np.array([[st["plan"][lab][k] for k in FLAG_KEYS] for _, lab in PLAN_COLUMNS])
    ps.stacked_hbars(ax, [lab for _, lab in PLAN_COLUMNS], M2, [FLAG_COLOR[k] for k in FLAG_KEYS],
                     xmax=len(fl), fontsize=10, label_fmt=lambda v, t: f"{v:.0f}",
                     label_parts=[False, True, True])
    ax.tick_params(axis="y", labelsize=10.5)
    ax.set_xticks([0, 350, 706])
    ps.int_axis(ax, "x")
    ps.clean_axes(ax, keep=())
    ax.tick_params(length=0)
    ps.panel_title(ax, "Plan columns, by district", reserve_rows=1)
    ps.save(fig, FIG_DIR / "eda_11_nfhs_reliability.png")


def year_bins(dates: pd.Series, first: int = 2019, last: int = 2026):
    cats = [f"{first} or before"] + [str(y) for y in range(first + 1, last + 1)]
    out = pd.Series("No usable date", index=dates.index)
    yr = dates.dt.year
    out[yr <= first] = cats[0]
    for y in range(first + 1, last + 1):
        out[yr == y] = str(y)
    return out, ["No usable date"] + cats


def grouped_columns(ax, cats, series, *, gap_after_first: bool = True):
    """Two or three series side by side per category; the first category
    (missing values) is set a little apart."""
    k = len(series)
    w = 0.8 / k
    x0 = np.arange(len(cats)) + (np.r_[0, np.full(len(cats) - 1, 0.5)] if gap_after_first else 0)
    for j, (label, vals, color) in enumerate(series):
        ps.vbars(ax, x0 - 0.4 + w * (j + 0.5), vals, width=w * 0.92, color=color)
    ax.set_xticks(x0, cats)
    ax.set_xlim(x0[0] - 0.6, x0[-1] + 0.6)
    return x0


SHORT_BIN = {"No usable date": "None", "No count": "None", "2019 or before": "<'20",
             "2020": "'20", "2021": "'21", "2022": "'22", "2023": "'23", "2024": "'24",
             "2025": "'25", "2026": "'26", "0-9": "<10", "10-99": "<100", "100-999": "<1K",
             "1K-9.9K": "<10K", "10K+": "10K+"}


def fig12_web(D, S):
    """Web footprint: followers and recency, private vs public."""
    fac = D.fac
    sub = fac[fac.op.isin(["private", "public"])].copy()
    fol = sub.engagement_metrics_n_followers.map(io.to_float)
    fbins = ["No count", "0-9", "10-99", "100-999", "1K-9.9K", "10K+"]
    fb = pd.cut(fol, [-1, 9, 99, 999, 9999, np.inf], labels=fbins[1:]).astype(object).fillna("No count")
    post = pd.to_datetime(null_to_na(sub.post_metrics_most_recent_social_media_post_date),
                          errors="coerce", format="%Y-%m-%d")
    upd_raw = null_to_na(sub.recency_of_page_update)
    upd = pd.to_datetime(upd_raw, errors="coerce", format="%Y-%m-%d")
    future = upd > D.scrape_cutoff
    upd_ok = upd.where(~future)
    pb, pcats = year_bins(post)
    ub, ucats = year_bins(upd_ok)

    def shares(binned, cats):
        return {o: (binned[sub.op == o].value_counts(normalize=True)
                    .reindex(cats, fill_value=0) * 100).round(1) for o in ["private", "public"]}

    F, P, U = shares(fb, fbins), shares(pb, pcats), shares(ub, ucats)
    all_upd = null_to_na(fac.recency_of_page_update)
    all_upd_dt = pd.to_datetime(all_upd, errors="coerce", format="%Y-%m-%d")
    st = {
        "n_private": int((sub.op == "private").sum()), "n_public": int((sub.op == "public").sum()),
        "followers_median": {o: float(fol[sub.op == o].median()) for o in ["private", "public"]},
        "pct_no_follower_count": {o: float(F[o]["No count"]) for o in F},
        "pct_10k_plus": {o: float(F[o]["10K+"]) for o in F},
        "pct_post_2025_26": {o: round(float(P[o]["2025"] + P[o]["2026"]), 1) for o in P},
        "pct_no_post_date": {o: float(P[o]["No usable date"]) for o in P},
        "pct_no_update_date": {o: float(U[o]["No usable date"]) for o in U},
        "latest_post": str(pd.to_datetime(null_to_na(fac.post_metrics_most_recent_social_media_post_date),
                                          errors="coerce").max().date()),
        "n_update_present": int(all_upd.notna().sum()),
        "n_update_unparsed": int((all_upd.notna() & all_upd_dt.isna()).sum()),
        "update_unparsed_examples": sorted(set(all_upd[all_upd.notna() & all_upd_dt.isna()]))[:5],
        "n_update_future": int((all_upd_dt > D.scrape_cutoff).sum()),
        "scrape_cutoff": str(D.scrape_cutoff.date()),
        "max_update": str(all_upd_dt.max().date()),
        "latest_valid_update": str(all_upd_dt[all_upd_dt <= D.scrape_cutoff].max().date()),
        "pct_update_2025_of_parsed": round(100 * float((all_upd_dt.dt.year == 2025).sum())
                                           / max(int(all_upd_dt.notna().sum()), 1), 1),
        "pct_update_2025": {o: float(U[o]["2025"]) for o in U},
    }
    S["f12"] = st
    guard(st["followers_median"]["public"] < st["followers_median"]["private"] / 3,
          "public facilities have far fewer followers")
    guard(all(40 < v < 60 for v in st["pct_no_post_date"].values()), "about half have no post date")
    guard(st["pct_no_update_date"]["public"] < st["pct_no_update_date"]["private"],
          "public facilities more often have a page update date")

    fig, gs = ps.figure(
        6.6,
        f"Public facilities have far fewer social followers: median "
        f"{st['followers_median']['public']:.0f} against "
        f"{st['followers_median']['private']:.0f} for private ones",
        f"Share of facilities in each bin, within each operator ({st['n_private']:,} private, "
        f"{st['n_public']:,} public). Web presence is how a facility enters this dataset, so "
        f"these fields hint at who gets included.",
        note=SRC_FAC + f" Follower bins: <10 means 0 to 9. Page update dates include text such as "
             f"'{st['update_unparsed_examples'][0]}' ({st['n_update_unparsed']} rows) and "
             f"{st['n_update_future']} date after the scrape ({st['max_update']}); both count as "
             f"no usable date. The latest social post is {st['latest_post']}.",
        ncols=3, width_ratios=[0.95, 1, 1], left_in=0.85, right_in=0.35, wspace=0.26,
        legend_rows=1, x_axis_band_in=0.75)
    panels = [("Social media followers", fbins, F), ("Year of the latest social post", pcats, P),
              ("Year the page was last updated", ucats, U)]
    for i, (title, cats, D_) in enumerate(panels):
        ax = fig.add_subplot(gs[i])
        labels = [SHORT_BIN.get(c, c) for c in cats]
        grouped_columns(ax, labels, [("Private", D_["private"].values, ps.PRIVATE),
                                     ("Public", D_["public"].values, ps.PUBLIC)])
        ax.tick_params(axis="x", labelsize=9.5 if i == 0 else 10, rotation=0)
        ax.set_ylim(0, max(D_["private"].max(), D_["public"].max()) * 1.1)
        ps.pct_axis(ax, "y")
        if i == 0:
            ax.set_ylabel("Share of facilities")
        ps.clean_axes(ax, grid="y", keep=("bottom",))
        ax.tick_params(length=0)
        ps.panel_title(ax, title, [("Private", ps.PRIVATE), ("Public", ps.PUBLIC)] if i == 0 else None,
                       reserve_rows=1, fontsize=12.5)
    ps.save(fig, FIG_DIR / "eda_12_web_footprint.png")


def render_notes(S) -> str:
    """eda_notes.md, with every number taken from the stats dict S."""
    f1, f2, f3, f4, f5, f6 = (S[f"f0{i}"] for i in range(1, 7))
    f7, f8, f9 = S["f07"], S["f08"], S["f09"]
    f10, f11, f12 = S["f10"], S["f11"], S["f12"]
    meta = S["_meta"]

    def n(x):
        return f"{int(round(x)):,}"

    def p(x, nd=1):
        return f"{x:.{nd}f}%"

    c = f9["corr"]
    sp = lambda col: f"{c[col]['spearman']:.2f}"  # noqa: E731
    pe = lambda col: f"{c[col]['pearson']:.2f}"  # noqa: E731
    SCH = "women_age_15_49_with_10_or_more_years_of_schooling_pct"
    ELE = "hh_electricity_pct"
    INS = "hh_member_covered_health_insurance_pct"
    FUEL = "households_using_clean_fuel_for_cooking_pct"
    U15 = "population_below_age_15_years_pct"
    SBA = "births_attended_by_skilled_hp_5y_10_pct"
    ANC = "mothers_who_had_at_least_4_anc_visits_lb5y_pct"
    top = f7["top20"]
    dom = {d["domain"]: d["n"] for d in top}
    other = [d for d in top if d["kind"] == "other"]
    low_states = ", ".join(f"{k} {v:.1f}%" for k, v in list(f8["lowest_states"].items())[:4])
    wide = ", ".join(f"{k} {v:.0f} points" for k, v in f8["widest_states"].items())
    zero_states = ", ".join(f"{k.title()} {v}" for k, v in f3["zero_by_state_top"].items())
    top3 = ", ".join(f"{d['district'].title()} {d['n']}" for d in f3["top20"][:3])
    g = f11["groups"]
    pc = f11["plan"]["C-section rate, private facilities"]
    cost = f11["plan"]["Cost per delivery, public"]
    sr = f10["state_rows"]
    ratio_emoc = f4["emoc"]["claim"] / f4["emoc"]["evidence"]
    kmin = f10["min_nfhs_state"]
    no_fac = " and ".join(x.title() for x in f10["nfhs_states_without_facilities"])

    return f"""# Exploratory analysis of the raw data

Exploratory data analysis (EDA) is the step before cleaning rules and models: you learn what the data is, what one row means, and where it will mislead you. The usual order is (1) profile each table: rows, columns, how full each column is; (2) check the grain and the joins: what one row stands for, and whether a join multiplies rows; (3) look at one variable at a time: distributions, outliers, impossible values; (4) look at relationships between variables; (5) write down every trap the moment you find it, so the next step does not rediscover it. In this dataset that meant: the facilities table is one row per facility but {f1['n_dup_rows']} rows are exact duplicates; the PIN directory is one row per post office, not per PIN, so joining on PIN fans out; NFHS is one row per district with its own spellings; and the literal string `null` means missing, so `notna()` is true everywhere. Each section below answers one question with one figure, says how the numbers were computed (with the pandas operations, so you can redo them in a notebook), what the figure shows, the caveat, and what it means for the project.

How to rerun: `.venv/bin/python scripts/eda_raw_figures.py` (this run took {meta['runtime_s']:.0f} seconds). The script reads the raw files through `vf.io`, draws the twelve figures in `reports/figures/`, writes every number to `reports/figures/eda_stats.json`, and writes this file from those numbers. Figures use the shared style in `src/vf/plotstyle.py`.

Inputs: {n(f1['n_raw_rows'])} raw facility rows, of which {f1['n_junk']} are junk fragments and {f1['n_dup_rows']} are exact duplicate rows, leaving {n(f1['n'])} facilities ({f1['n_truncated']} truncated, with no facts or coordinates); {n(f2['n_po_total'])} post offices; {f8['n_districts']} NFHS-5 districts. Generated {meta['created']} at commit `{meta['git_commit'][:7]}`.

## 1. What is in the facilities table?

![Facility types by operator](figures/eda_01_facility_types.png)

**Question.** What kinds of facilities are listed, and who runs them?

**How.** `io.read_facilities()` drops the junk rows, then `drop_duplicates("unique_id")` removes the exact duplicates. Operator and type are cleaned by turning the string `null` into a missing value (`Series.where(~s.map(io.is_null))`) and mapping `government` to public. `pd.crosstab(fac.ftype, fac.op)` counts type by operator.

**What it shows.** {n(f1['ops']['private'])} facilities are private ({p(f1['ops_pct']['private'])}), {n(f1['ops']['public'])} public ({p(f1['ops_pct']['public'])}) and {n(f1['ops']['unknown'])} have no operator ({p(f1['ops_pct']['unknown'])}). There are {n(f1['types']['hospital'])} hospitals, {n(f1['types']['clinic'])} clinics and {n(f1['types']['dentist'])} dentists. {f1['public_by_type']['hospital']} of the {f1['ops']['public']} public facilities ({p(f1['public_hospital_share_of_public'])}) are hospitals; only {f1['public_by_type']['clinic']} clinics are public ({p(f1['public_share_of_clinics'])} of clinics).

**Caveat.** Operator is as scraped. The {n(f1['ops']['unknown'])} facilities with no operator could be either kind.

**For the project.** The public primary-care layer barely appears. Any access measure built on this table will understate public care, which is the data-desert half of the thesis.

## 2. Where are the facilities, compared with where people are?

![Map and distance to the nearest facility](figures/eda_02_facilities_vs_people.png)

**Question.** Do facilities sit where people live, and how far is the nearest one?

**How.** Post-office coordinates are parsed with `io.to_float` and kept only inside the India bounding box (`io.in_india`): {n(f2['n_po_valid'])} of {n(f2['n_po_total'])} ({n(f2['n_po_na'])} are the string `NA`). For each post office, `sklearn.neighbors.BallTree(metric="haversine").query(k=1)` finds the nearest facility; `np.median` and `(d > 30).mean()` summarise the distances. "Mentions a C-section" uses the recipe v0 regex over the facility's facts (section 4).

**What it shows.** The median post office is {f2['any']['median_km']:.1f} km from the nearest facility of any kind, and {p(f2['any']['pct_over_30km'])} are more than 30 km away. For a public facility the median is {f2['public']['median_km']:.1f} km ({p(f2['public']['pct_over_30km'])} beyond 30 km). For a facility whose facts mention a C-section ({f2['n_csection_coord_ok']} with coordinates) the median is {f2['csection']['median_km']:.1f} km, {p(f2['csection']['pct_over_30km'])} are beyond 30 km and {p(f2['csection']['pct_over_60km'])} beyond 60 km.

**Caveat.** Post offices stand in for settlements and are not weighted by population; distances are straight lines. A facility without a C-section mention is no evidence of C-section care, not proof there is none. The bounding box is not enough to clean the post offices: {n(f2['top_lat_n'])} share the placeholder latitude {float(f2['top_lat_value']):.4f} across {f2['top_lat_n_states']} states and {f2['top_lat_n_districts']} districts (the horizontal streak on the map), {n(f2['n_po_far_from_state_8deg'])} sit more than 8 degrees from their own state's median position, and up to {f2['max_po_same_coord']} post offices share one identical coordinate pair.

**For the project.** This is a preview of WP5. The jump from "any facility" to "evidence of C-sections" is the core of the story. WP2 should flag the placeholder latitude and the far-from-state points before WP5 computes distances.

## 3. How many facilities does each district have?

![Facilities per district](figures/eda_03_facilities_per_district.png)

**Question.** How are facilities spread across districts, and how many districts have none?

**How.** PIN codes are cleaned with `str.replace(r"\\s", "")` and kept if `str.fullmatch(r"\\d{{6}}")`. In the PIN directory, `groupby(["pincode", "statename", "district"]).size()`, sorted by count, then `drop_duplicates("pincode")` keeps each PIN's most common district. Joining facilities on that one-row-per-PIN table cannot fan out. `groupby(district).size().reindex(all_755_districts, fill_value=0)` counts per district; the `reindex` is what makes the empty districts appear as zeros instead of disappearing.

**What it shows.** {n(f3['n_matched'])} of {n(f3['n_fac'])} facilities land in a district ({n(f3['n_pin6'])} have a 6-digit PIN; {f3['n_pin_not_in_dir']} of those PINs are not in the directory). {f3['n_zero']} of {f3['n_districts']} districts have no facility, and {f3['bins']['1']} have exactly one. Among districts with at least one, the median is {f3['median_covered']:.0f}. The top 20 districts hold {n(f3['top20_sum'])} facilities ({p(f3['top20_pct'])}), led by {top3}; {f3['n_districts_half']} districts hold half of all placed facilities. Empty districts cluster in {zero_states}.

**Caveat.** {n(f3['n_pins_ambiguous'])} PINs span more than one district, so the most-common-district rule misplaces some facilities. Without stripping spaces inside PIN codes the same join places {n(f3['n_matched_nostrip'])} facilities and leaves {f3['n_zero_nostrip']} districts empty, which is the method behind the figure in `docs/03_data_profile.md`; stripping recovers PINs written like "411 001".

**For the project.** A count per district measures web presence, not care (CLAUDE.md trap 4). It is the reason the plan uses distance from post offices instead.

## 4. Do claims match evidence?

![Claims versus evidence](figures/eda_04_claims_vs_evidence.png)

**Question.** When a facility lists a specialty, do its facts name the procedure that specialty implies?

**How.** `io.parse_list(v, dedupe=True)` turns each specialties cell into a deduplicated list; a claim is a non-empty set intersection with the specialty names. Facts from capability, procedure and equipment are exploded to one row per fact (`explode`), lowercased, joined per facility (`groupby("fac_idx").agg(" \\n ".join)`), and searched with `str.contains(pattern)` for each recipe v0 regex. Every count is the `sum()` of a boolean column.

**What it shows.** Obstetrics: {n(f4['emoc']['claim'])} facilities list an obstetrics specialty, {n(f4['emoc']['evidence'])} mention a C-section ({ratio_emoc:.1f} claims per mention), and {n(f4['emoc']['claim_and_evidence'])} do both, so {p(f4['emoc']['pct_claimers_with_evidence'])} of claimants name the procedure. Only {f4['emoc']['corroborated']} facilities mention a C-section, a blood bank, and anaesthesia or an operating theatre together. Dialysis: {n(f4['dialysis']['claim'])} claim nephrology, {n(f4['dialysis']['evidence'])} mention dialysis ({p(f4['dialysis']['pct_claimers_with_evidence'])} of claimants), {n(f4['dialysis']['corroborated'])} also name a unit, machine or nephrologist. Intensive care runs the other way: {n(f4['icu']['evidence'])} mention an ICU but only {n(f4['icu']['claim'])} list critical care, and {n(f4['icu']['corroborated'])} mention a ventilator too. The median facility lists {f4['spec_per_fac_median']:.0f} distinct specialties (hospitals {f4['spec_per_fac_hospital_median']:.0f}), so specialty tags are cheap.

**Caveat.** The regexes are crude: they miss negations ("no ICU") and some patterns match unrelated text. Before removing the duplicate rows the obstetrics claim count is {n(f4['emoc_claim_before_dedupe'])}, the figure in `docs/03_data_profile.md`; that profile also searched the description, which this script leaves out because tags are defined on facts.

**For the project.** This is the "claims are not evidence" finding. Obstetrics has the biggest gap and only {f4['emoc']['corroborated']} corroborated facilities, so grading choices in WP3 and WP4 will drive the access maps. Model A has to beat these regexes at spotting C-section, blood and anaesthesia evidence.

## 5. How noisy are the facts?

![Facts per facility and a rough noise split](figures/eda_05_fact_noise.png)

**Question.** How many facts does a facility have, and how many of them describe care?

**How.** Facts per facility is `map(len)` on the parsed lists, summed over the three fields, drawn with `np.histogram`. The rough split checks keyword patterns in a fixed order (reputation and listings, photo captions and amenities, staff and people, web and contact, location) with `str.contains` on lowercased text; each fact takes the first category it matches (`hit & ~done`), and anything left is "no noise keyword". `pd.crosstab(field, category, normalize="index")` gives the shares.

**What it shows.** There are {n(f5['n_facts'])} fact strings (capability {n(f5['by_field']['capability'])}, procedure {n(f5['by_field']['procedure'])}, equipment {n(f5['by_field']['equipment'])}), of which {n(f5['n_distinct_text'])} are distinct after lowercasing. The median facility has {f5['facts_median']:.0f} facts and the 90th percentile {f5['facts_p90']:.0f}. Each field is capped at {f5['max_per_field']} facts, so no facility exceeds {f5['facts_max']}; {n(f5['n_fac_any_field_at_50'])} facilities hit the cap in at least one field. {p(f5['pct_noise_capability'])} of capability facts carry a noise keyword: reputation and listings {p(f5['pct_by_field']['capability']['reputation'])}, location {p(f5['pct_by_field']['capability']['location'])}, staff {p(f5['pct_by_field']['capability']['staff'])}. Procedure facts are {p(f5['pct_by_field']['procedure']['clinical'])} free of noise keywords. In equipment, {p(f5['pct_by_field']['equipment']['photo'])} look like photo captions or amenities ("wheelchair accessible washroom").

**Caveat.** This is rough by design. Keyword rules make false hits ("NABL-certified blood test laboratory" counts as reputation) and miss marketing such as "state-of-the-art equipment", which lands in the clinical share.

**For the project.** Model A must learn to ignore {p(f5['pct_noise_capability'], 0)} of capability facts; procedure facts are the cleanest evidence. Facilities at the {f5['max_per_field']}-fact cap may have lost facts, so "no evidence" is a little more likely for large facilities.

## 6. Which columns can an analyst rely on?

![Column completeness](figures/eda_06_column_completeness.png)

**Question.** How full are the fields an analyst would want?

**How.** For each column, `(~df[col].map(io.is_null)).sum()`, because `df[col].notna()` is true for every cell when missing is the string `null` (trap 2). Plausibility uses `io.to_float` and simple ranges.

**What it shows.** Location and type are nearly complete: coordinates inside India {p(f6['analyst']['Coordinates inside India']['pct'])}, facility type {p(f6['analyst']['Facility type']['pct'])}, 6-digit PIN {p(f6['analyst']['6-digit PIN code']['pct'])}, operator {p(f6['analyst']['Operator (private or public)']['pct'])}. Size is mostly missing: year established {p(f6['analyst']['Year established']['pct'])}, doctor count {p(f6['analyst']['Doctor count']['pct'])}, bed count {p(f6['analyst']['Bed count']['pct'])}, floor area {p(f6['analyst']['Floor area']['pct'])}. Contact fields are mostly filled: Facebook link {p(f6['web']['Facebook link']['pct'])}, phone {p(f6['web']['Phone number']['pct'])}, official website {p(f6['web']['Official website']['pct'])}.

**Caveat.** Present is not plausible. Bed counts reach {n(f6['beds_max'])} ({f6['beds_implausible']} values are 0 or above 5,000), doctor counts reach {n(f6['doctors_max'])} ({f6['doctors_implausible']} values are 0 or above 5,000), and {f6['year_implausible']} founding years are below 1800, the lowest {f6['year_min']:.0f}. The 5,000 and 1800 cut-offs are judgement calls.

**For the project.** Beds and doctors cannot weight access or feed a model for most facilities. Facility type and evidence tags are the usable descriptors.

## 7. Where do the facts come from?

![Source domains](figures/eda_07_fact_sources.png)

**Question.** Which websites do the facts come from, and how many independent sources does each facility have?

**How.** `io.parse_list` on `source_urls`, then `urllib.parse.urlparse(url).hostname` with `www.` removed, collected into a set per facility. `collections.Counter` over those per-facility sets counts each facility once per domain. The top 20 domains were classified by hand into social, listing site, and other.

**What it shows.** {n(f7['n_urls'])} URLs, all of which parse to a host. The median facility cites {f7['urls_median']:.0f} URLs from {f7['domains_median']:.0f} distinct domains; {f7['n_one_domain']} facilities rely on one domain and {n(f7['n_ge6'])} on six or more. facebook.com is cited by {n(dom['facebook.com'])} facilities, justdial.com by {n(dom['justdial.com'])} and practo.com by {n(dom['practo.com'])}. Sites that are not about care are common: {", ".join(f"{d['domain']} {n(d['n'])}" for d in other[:4])} (maps, real estate, insurance, B2B listings). {n(f7['n_any_other_site'])} facilities ({p(f7['pct_any_other_site'])}) cite at least one of the {f7['n_other_top20']} such domains in the top 20. Of the {n(f7['n_with_official_site'])} facilities with an official website, {n(f7['n_official_in_sources'])} ({p(f7['pct_official_in_sources'])}) cite that site among their sources.

**Caveat.** `docs/02_data_dictionary.md` reports a different distinct-domain distribution and many URLs with no parseable domain; here every URL parses, so the earlier parser (SQL) likely counted an empty domain. WP1 should use `urlparse`. The classification of domains is mine and covers only the top 20.

**For the project.** "Number of distinct domains" overstates corroboration, because listing sites copy each other and some sources are about other things (real estate, maps, insurance). Source confidence should reward the facility's own site and penalise unrelated domains.

## 8. How do institutional births vary across districts?

![Institutional births by district and state](figures/eda_08_nfhs_institutional_births.png)

**Question.** What does the main outcome look like, and does it vary more within states or between them?

**How.** Every NFHS cell goes through `io.parse_nfhs_value`, which returns a value and a flag. `describe()` and `np.histogram` give the distribution. The between-state share is a one-way variance split: `groupby("state").transform("mean")` gives each district its state mean, and the between share is the sum of squared deviations of those means from the overall mean divided by the total sum of squares.

**What it shows.** The median district has {p(f8['median'])} institutional births and the mean is {p(f8['mean'])}; the lowest is {p(f8['min'])} ({f8['min_district']}). {f8['n_below_80']} districts are below 80% and {f8['n_ge_95']} are at 95% or more. {p(f8['pct_between_states'])} of the variation is between states. The lowest state medians are {low_states}. The widest spreads within a state are {wide}.

**Caveat.** The outcome is bunched against a ceiling of 100%, so most districts differ by a few points. All {f8['n_districts']} values are plain numbers (no flags). In the raw file {meta['n_state_leading_space']} state value has a leading space and Maharashtra is spelled "Maharastra".

**For the project.** Model B needs cross-validation grouped by state (the plan already says so), and a "better than expected" district should be judged against its state. Modelling the shortfall from 100% may behave better than the raw percentage.

## 9. What predicts institutional births?

![Development proxies](figures/eda_09_nfhs_development_proxies.png)

**Question.** Which district characteristics move with institutional births, as a preview of Model B's features?

**How.** `Series.corr(method="pearson")` and `Series.corr(method="spearman")` across districts. The dark line in each scatter is a binned median: `pd.qcut(x, 10)` splits districts into tenths by the x value, then `groupby(bin).median()`.

**What it shows.** Women with 10+ years of school: Pearson {pe(SCH)}, Spearman {sp(SCH)}. Household electricity: Pearson {pe(ELE)}, Spearman {sp(ELE)}. Health insurance: Pearson {pe(INS)}, Spearman {sp(INS)}. The strongest positive development proxy is clean cooking fuel (Spearman {sp(FUEL)}); the strongest overall is the share of the population under 15 (Spearman {sp(U15)}), a marker of high fertility. Two care-use columns correlate more: births with a skilled attendant ({sp(SBA)}) and 4+ antenatal visits ({sp(ANC)}).

**Caveat.** These are correlations across districts, not causes, and every district counts once whatever its population. Electricity is at 90% or more in {f9['n_electricity_ge90']} of {f9['n']} districts (median {p(f9['electricity_median'])}), so it separates only the low tail.

**For the project.** Development proxies carry moderate signal, enough for a ridge baseline. Skilled attendance is nearly the target itself and antenatal visits are care use, so both would leak the answer and should stay out of Model B.

## 10. How big is the public-care blind spot?

![Public share in data versus births](figures/eda_10_public_blind_spot.png)

**Question.** How does the public share of facilities in this dataset compare with the public share of real births?

**How.** Dataset side: `(fac.op == "public").mean()`. Survey side: `np.average(col, weights=households_surveyed)` for institutional births and for births in a public facility. The NFHS public-facility column is a share of all births (it never exceeds institutional births, which the script checks), so dividing the two gives the public share of institutional births. By state: facilities get a state from their PIN's district, names are normalised (`str.upper`, `&` to AND, two spelling fixes), then `groupby` state.

**What it shows.** {p(f10['pct_public'])} of facilities in the dataset are public ({n(f10['n_public'])} of {n(f10['n'])}; {p(f10['pct_public_of_known'])} of those with a known operator), and {p(f10['pct_public_hospitals'])} of hospitals. In NFHS-5, {p(f10['nfhs_pub_all_weighted'])} of births happened in a public facility and {p(f10['nfhs_inst_weighted'])} in any facility, so public facilities delivered {p(f10['nfhs_pub_of_inst'])} of institutional births ({p(f10['nfhs_pub_of_inst_unweighted'])} unweighted). In all {f10['n_states_nfhs_above_fac']} states with at least {f10['min_n_state']} facilities, the survey's public share is above the dataset's, by at least {f10['min_gap_points']:.0f} percentage points ({STATE_DISPLAY.get(f10['min_gap_state'], f10['min_gap_state'])}). The highest dataset share is {f10['max_fac_pub_state']} at {p(f10['max_fac_pub_pct'])}. The lowest survey share is {STATE_DISPLAY.get(kmin, kmin)}, where public facilities deliver {p(sr[kmin]['nfhs_pub_of_inst'])} of institutional births, still well above the {p(sr[kmin]['fac_pub_pct'])} public share of its facilities in the data.

**Caveat.** Households surveyed range from {f10['hh_surveyed_min']:.0f} to {f10['hh_surveyed_max']:.0f} per district (median {f10['hh_surveyed_median']:.0f}), so the weighting barely differs from a plain district average and is not a population weight. Look up the official NFHS-5 national figure before quoting a number in the video. A share of facilities and a share of births are different units; the comparison shows a gap in coverage, not a ratio of capacity. NFHS is 2019-21 and the web data runs to {f12['scrape_cutoff']}. {no_fac} have no facility in the data and are left out of the state view.

**For the project.** This is the strongest single argument for the data-desert idea: the dataset is mostly private, while most institutional births happen in public facilities.

## 11. How reliable are the NFHS numbers?

![NFHS reliability](figures/eda_11_nfhs_reliability.png)

**Question.** How often are NFHS values suppressed or based on small samples, and does it affect the columns the plan uses?

**How.** The flag from `io.parse_nfhs_value` for every cell; `df[cols].stack().value_counts()` per group of columns (groups follow the column order of the file), and `(flags[col] == k).sum()` for each plan column.

**What it shows.** Of {n(f11['n_cells'])} indicator cells, {n(f11['cells']['ok'])} are plain numbers, {n(f11['cells']['low_sample'])} are low-sample estimates and {n(f11['cells']['suppressed'])} are suppressed. Child indicators are the worst: child illness and treatment {p(g['Child illness and treatment']['pct_flagged'])} flagged, child feeding {p(g['Child feeding']['pct_flagged'])}, vaccination {p(g['Child vaccination']['pct_flagged'])}. Adult blood sugar, blood pressure, screening and tobacco columns have no flags. The plan's delivery columns are clean except the private-facility C-section rate: {pc['ok']} plain, {pc['low_sample']} low-sample and {pc['suppressed']} suppressed. Cost per delivery has {cost['low_sample']} low-sample values and {cost['suppressed']} suppressed.

**Caveat.** A low-sample value rests on 25 to 49 unweighted cases (the NFHS definition), so it is noisy, not wrong. Treat `*` as missing, never as zero.

**For the project.** Institutional births and public-facility births are safe targets. Do not use the private C-section rate as a target; if used as a feature, carry its flag.

## 12. Who is visible on the web?

![Web footprint](figures/eda_12_web_footprint.png)

**Question.** Do public and private facilities leave the same web trail? Since the dataset is scraped from the web, this hints at who gets included.

**How.** `pd.cut` puts follower counts into bins; `pd.to_datetime(format="%Y-%m-%d", errors="coerce")` parses dates, turning anything else into missing; `value_counts(normalize=True)` within each operator gives shares that can be compared despite the very different group sizes.

**What it shows.** The median private facility has {f12['followers_median']['private']:.0f} followers and the median public one {f12['followers_median']['public']:.1f}. About half of both groups have no social post date ({p(f12['pct_no_post_date']['private'])} private, {p(f12['pct_no_post_date']['public'])} public), and few posted in 2025 or 2026 ({p(f12['pct_post_2025_26']['private'])} and {p(f12['pct_post_2025_26']['public'])}). Page update dates go the other way: {p(f12['pct_no_update_date']['private'])} of private facilities have none, but only {p(f12['pct_no_update_date']['public'])} of public ones, and {p(f12['pct_update_2025']['public'])} of public facilities carry a 2025 date. The latest social post is {f12['latest_post']}, which dates the scrape to about then.

**Caveat.** The inclusion process cannot be observed directly, and the page-update pattern does not fit a simple "public means less online" story; public pages may be government portals that carry a date. {f12['n_update_unparsed']} page dates are text such as "{f12['update_unparsed_examples'][0]}" or a bare year, and {f12['n_update_future']} ({f12['max_update']}) is after the scrape.

**For the project.** Followers measure popularity, not quality, and they differ by operator. Using them in source confidence would push public facilities down; keep them out or adjust for operator.

## Traps found in this pass

These are new or sharper than the list in CLAUDE.md. Each number comes from the script.

1. The {f1['n_dup_rows']} duplicate `unique_id` rows are exact copies, so `drop_duplicates("unique_id")` is safe and gives {n(f1['n'])} facilities.
2. Some PIN codes contain spaces ("411 001"). Stripping them gives {n(f3['n_pin6'])} valid PINs instead of {n(f3['n_pin6_nostrip'])}.
3. The India bounding box keeps bad post-office coordinates: a placeholder latitude {float(f2['top_lat_value']):.4f} on {n(f2['top_lat_n'])} offices, {n(f2['n_po_far_from_state_8deg'])} offices more than 8 degrees from their state's median, and up to {f2['max_po_same_coord']} offices on one identical point.
4. Fact arrays and `source_urls` are capped at {f5['max_per_field']} items per field.
5. Bed counts up to {n(f6['beds_max'])}, doctor counts up to {n(f6['doctors_max'])}, and founding years of {f6['year_min']:.0f} exist.
6. `recency_of_page_update` mixes ISO dates with text ("{f12['update_unparsed_examples'][0]}", bare years) and has a date in {f12['max_update'][:4]}.
7. NFHS `state_ut` has a leading space in {meta['n_state_leading_space']} value and spells Maharashtra "Maharastra".
8. NFHS public-facility births are a share of all births, not of institutional births. Divide by institutional births to get the public share of facility births.
9. NFHS households surveyed is about the same in every district (median {f10['hh_surveyed_median']:.0f}), so it is not a population weight.
10. The distinct-domain counts in `docs/02_data_dictionary.md` do not match a `urlparse`-based count; recheck before using domains for source confidence.
"""


FIGURES = {
    1: fig01_types,
    2: fig02_map,
    3: fig03_districts,
    4: fig04_claims,
    5: fig05_facts,
    6: fig06_completeness,
    7: fig07_sources,
    8: fig08_nfhs_births,
    9: fig09_nfhs_proxies,
    10: fig10_public,
    11: fig11_nfhs_flags,
    12: fig12_web,
}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--only", type=int, nargs="*", help="figure numbers to draw")
    args = ap.parse_args(argv)
    t_start = time.time()
    ps.use()
    D = load()
    print(f"loaded raw tables in {D.load_seconds}s: {len(D.fac):,} facilities, "
          f"{len(D.facts):,} facts, {len(D.po):,} post offices, {len(D.nf_val)} NFHS districts")
    S = {}
    todo = args.only or sorted(FIGURES)
    for k in todo:
        t = time.time()
        FIGURES[k](D, S)
        print(f"  figure {k:02d} done in {time.time() - t:.1f}s")
    if args.only:
        print("--only given: stats and notes not rewritten")
        return 0
    S["_meta"] = {
        "script": "scripts/eda_raw_figures.py",
        "created": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "git_commit": git_commit(),
        "inputs": {k: str(io.raw_path(k).relative_to(paths.data_dir())) for k in io.RAW_FILES},
        "rows": {"facilities_raw": D.n_raw_rows, "facilities": len(D.fac),
                 "facts": len(D.facts), "post_offices": len(D.po), "nfhs_districts": len(D.nf_val)},
        "recipe_version": "v0",
        "runtime_s": round(time.time() - t_start, 1),
        "n_state_leading_space": D.n_state_leading_space,
        "figures": sorted(p.name for p in FIG_DIR.glob("eda_[0-9][0-9]_*.png")),
    }
    write_json_atomic(STATS_PATH, S)
    write_text_atomic(NOTES_PATH, render_notes(S))
    print(f"wrote {len(S) - 1} figures, {STATS_PATH.relative_to(paths.code_root())}, "
          f"{NOTES_PATH.relative_to(paths.code_root())}")
    f4, f10 = S["f04"], S["f10"]
    print(f"  obstetrics: {f4['emoc']['claim']:,} claim the specialty, "
          f"{f4['emoc']['evidence']:,} mention a C-section, {f4['emoc']['corroborated']} corroborated")
    print(f"  public share: {f10['pct_public']}% of facilities vs {f10['nfhs_pub_of_inst']}% "
          f"of institutional births (NFHS-5)")
    print(f"  districts with no facility: {S['f03']['n_zero']} of {S['f03']['n_districts']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
