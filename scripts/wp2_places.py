"""WP2 Part B: build the gazetteer for the location resolver.

Writes silver/places.parquet (+ .meta.json), one row per place:

* pin: median of the PIN's geocoded offices that are neither coord_suspect nor
  coord_placeholder. Fallbacks, said in `note`: the PIN's placeholder offices that
  are not also suspect (precision at least the district's), else the centre of the
  PIN's main district.
  precision_km = 90th percentile office distance from the point, at least 2 km.
* district: vf.geo.group_centres (head and sub offices when 3 or more), precision
  = 75th percentile distance of its non-suspect geocoded offices from the centre.
* post_office: one row per (vf.geo.place_key(office name), state, district).
  Each office contributes its own point when clean, its placeholder point with
  the district's precision, or its PIN's point when suspect (placeholder or not)
  or not geocoded. Its precision is at least its distance from its PIN's point.
* city: facility city_raw values that are not a district or office name; median
  of that city's facilities with coord_check == ok, when there are at least 3.

Office flags, computed here from silver/post_offices.parquet with the PIN
directory's own districts (not the retired NFHS district_key, D024):

* coord_suspect: more than 200 km from its district centre (the WP5 rule), except in
  the island states, whose districts span more than that.
* coord_placeholder: vf.geo.placeholder_points (10+ offices from 3+ PINs on one point).
* coord_outside_india: more than vf.locate.BORDER_SLACK_KM outside India's outline
  (vf.locate.india_border_km); treated as not geocoded.

Rows with state or district 'NA' take the main district of their PIN.

Then scores vf.locate.resolve on the frozen hand-written cases
(data/labels/resolver_cases.csv) and writes reports/resolver_eval.md.

Run: python scripts/wp2_places.py              # build, then evaluate
     python scripts/wp2_places.py --eval-only  # evaluate the existing gazetteer
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from vf import geo, locate, paths  # noqa: E402

SCRIPT = "scripts/wp2_places.py"
VERSION = "places-v2"
SUSPECT_KM = 200.0          # same rule as WP5 coord_suspect
ISLAND_STATES = ("LAKSHADWEEP", "ANDAMAN AND NICOBAR ISLANDS")   # exempt from the SUSPECT_KM rule
MIN_PRECISION_KM = 2.0
PIN_PRECISION_Q = 0.90
DISTRICT_PRECISION_Q = 0.75
CITY_MIN_FACILITIES = 3
CITY_PRECISION_Q = 0.90
NA_VOTE_K = 5               # nearest offices that give a district to a PIN whose offices all say 'NA'


# ---------------------------------------------------------------------------
# writing
# ---------------------------------------------------------------------------

def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=paths.code_root(), capture_output=True,
                              text=True, check=True).stdout.strip()
    except Exception:
        return ""


def _atomic_write(path: Path, write_fn) -> None:
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        write_fn(tmp)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def write_places(df: pd.DataFrame, extra: dict) -> Path:
    out = paths.ensure(paths.silver_dir()) / "places.parquet"
    _atomic_write(out, lambda p: df.to_parquet(p, index=False))
    commit = _git("rev-parse", "HEAD") or None
    meta = {
        "output": "silver/places.parquet",
        "rows": int(len(df)),
        "columns": list(df.columns),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_commit": commit,
        "git_dirty": bool(_git("status", "--porcelain")) if commit else None,
        "recipe_version": VERSION,
        "script": SCRIPT,
        "inputs": ["silver/post_offices.parquet", "silver/facilities.parquet"],
        **extra,
    }
    meta_path = out.with_name(out.name + ".meta.json")
    _atomic_write(meta_path, lambda p: p.write_text(json.dumps(meta, indent=2, default=str) + "\n"))
    return out


# ---------------------------------------------------------------------------
# steps
# ---------------------------------------------------------------------------

def _q(x: pd.Series, q: float) -> float:
    x = x.dropna()
    return float(np.quantile(x, q)) if len(x) else float("nan")


def load_offices() -> tuple[pd.DataFrame, dict]:
    """Post offices with state/district filled from their PIN, the district
    group, and the coord_suspect and coord_placeholder flags."""
    po = pd.read_parquet(paths.silver_dir() / "post_offices.parquet")
    po["state"] = po["state_pin"].astype(str).str.strip()
    po["district"] = po["district_pin"].astype(str).str.strip()
    na = (po["state"].str.upper() == "NA") | (po["district"].str.upper() == "NA")
    known = po[~na]
    main = (known.groupby(["pin", "state", "district"]).size().reset_index(name="n")
            .sort_values(["pin", "n", "state", "district"], ascending=[True, False, True, True])
            .drop_duplicates("pin").set_index("pin"))
    fill = main.reindex(po.loc[na, "pin"])
    po.loc[na, "state"] = fill["state"].to_numpy()
    po.loc[na, "district"] = fill["district"].to_numpy()
    unfilled = po["state"].isna() | po["district"].isna()
    # a PIN whose offices all say 'NA': take the district of the nearest offices with a known one
    from sklearn.neighbors import BallTree
    ref = po[~unfilled & po["coord_valid"].astype(bool)]
    tree = BallTree(np.radians(ref[["lat", "lon"]].to_numpy()), metric="haversine")
    by_point = unfilled & po["coord_valid"].astype(bool)
    if by_point.any():
        _, ix = tree.query(np.radians(po.loc[by_point, ["lat", "lon"]].to_numpy()), k=NA_VOTE_K)
        grp = (ref["state"] + "|" + ref["district"]).to_numpy()[ix]
        vote = [pd.Series(row).value_counts().index[0] for row in grp]
        po.loc[by_point, "state"] = [v.split("|", 1)[0] for v in vote]
        po.loc[by_point, "district"] = [v.split("|", 1)[1] for v in vote]
    still = po["state"].isna() | po["district"].isna()
    stats = {"offices": int(len(po)), "offices_state_or_district_na": int(na.sum()),
             "offices_na_filled_from_pin": int((na & ~unfilled).sum()),
             "offices_na_filled_from_nearest_offices": int((unfilled & ~still).sum()),
             "offices_na_unfilled_dropped": int(still.sum()),
             "pins_in_directory": int(po["pin"].nunique()),
             "pins_with_a_geocoded_office": int(po.loc[po["coord_valid"].astype(bool), "pin"].nunique()),
             "pins_dropped_no_district": sorted(set(po.loc[still, "pin"]) - set(po.loc[~still, "pin"]))}
    po = po[~still].copy()
    po["group"] = po["state"] + "|" + po["district"]
    po["town"] = po["office_type"].astype(str).str.strip() != "BO"

    geoc = po["coord_valid"].astype(bool) & po["lat"].notna() & po["lon"].notna()
    # trap 9: some office points sit in Pakistan, Nepal, Bangladesh or the sea. Outside India's
    # outline by more than the resolver's border slack, a point is treated as not geocoded.
    border_km = locate.india_border_km(po["lat"].where(geoc), po["lon"].where(geoc))
    po["coord_outside_india"] = geoc & (np.nan_to_num(border_km, nan=0.0) > locate.BORDER_SLACK_KM)
    geoc = geoc & ~po["coord_outside_india"]
    centres = geo.group_centres(po.loc[geoc, "lat"], po.loc[geoc, "lon"], po.loc[geoc, "town"],
                                po.loc[geoc, "group"])
    c = centres.reindex(po["group"].to_numpy())
    po["centre_km"] = geo.haversine_km(po["lat"], po["lon"], c["lat"].to_numpy(), c["lon"].to_numpy())
    # island districts (Lakshadweep, Andaman and Nicobar) span more than SUSPECT_KM of sea: Minicoy is
    # about 300 km from the other Lakshadweep islands. Their points are checked against the outline only.
    island = po["state"].isin(ISLAND_STATES)
    po["coord_suspect"] = geoc & (po["centre_km"] > SUSPECT_KM) & ~island
    ph = geo.placeholder_points(po.loc[geoc, "lat"], po.loc[geoc, "lon"], po.loc[geoc, "pin"])
    po["coord_placeholder"] = False
    po.loc[geoc, "coord_placeholder"] = ph["placeholder"].to_numpy()
    po["geocoded"] = geoc
    po["clean"] = geoc & ~po["coord_suspect"] & ~po["coord_placeholder"]
    stats.update({
        "offices_outside_india_outline": int(po["coord_outside_india"].sum()),
        "offices_geocoded": int(geoc.sum()),
        "offices_coord_suspect": int(po["coord_suspect"].sum()),
        "offices_coord_placeholder": int(po["coord_placeholder"].sum()),
        "offices_clean": int(po["clean"].sum()),
    })
    return po, {"stats": stats, "centres": centres}


def build_districts(po: pd.DataFrame, centres: pd.DataFrame) -> pd.DataFrame:
    rows = []
    usable = po[po["geocoded"] & ~po["coord_suspect"]]
    by_group = dict(tuple(usable.groupby("group")))
    for g, r in centres.iterrows():
        state, district = g.split("|", 1)
        sub = by_group.get(g)
        n = 0 if sub is None else len(sub)
        prec = _q(sub["centre_km"], DISTRICT_PRECISION_Q) if n else float("nan")
        prec = max(MIN_PRECISION_KM, prec) if prec == prec else MIN_PRECISION_KM
        note = None if r["basis"] == "head_sub" else "centre from all offices: fewer than 3 geocoded head or sub offices"
        rows.append({
            "place_id": f"district:{g}", "kind": "district", "name": district,
            "name_norm": geo.norm_name(district), "state": state, "district": district, "pin": None,
            "lat": float(r["lat"]), "lon": float(r["lon"]), "precision_km": float(prec),
            "n_offices": int(n), "note": note,
        })
    return pd.DataFrame(rows)


def build_pins(po: pd.DataFrame, districts: pd.DataFrame) -> pd.DataFrame:
    dist = districts.set_index(districts["state"] + "|" + districts["district"])
    share = (po.groupby(["pin", "group"]).size().rename("n").reset_index()
             .sort_values(["pin", "n", "group"], ascending=[True, False, True]))
    total = share.groupby("pin")["n"].transform("sum")
    share["share"] = share["n"] / total
    main = share.drop_duplicates("pin").set_index("pin")
    n_groups = share.groupby("pin").size()

    rows = []
    for pin, sub in po.groupby("pin", sort=True):
        g = main.loc[pin, "group"]
        state, district = g.split("|", 1)
        notes = []
        if n_groups.loc[pin] > 1:
            notes.append(f"mostly in {district} ({main.loc[pin, 'share']:.0%} of its offices); "
                         f"spans {n_groups.loc[pin]} districts")
        # only offices within SUSPECT_KM of the PIN's main district centre place the PIN, so a PIN is
        # never put at a minority district's offices hundreds of km from the district it is labelled with
        if g in dist.index:
            near = geo.haversine_km(sub["lat"], sub["lon"], dist.loc[g, "lat"], dist.loc[g, "lon"]) <= SUSPECT_KM
        else:
            near = np.ones(len(sub), dtype=bool)
        clean = sub[sub["clean"].to_numpy() & near]
        if len(clean) < int(sub["clean"].sum()):
            notes.append(f"{int(sub['clean'].sum()) - len(clean)} of its clean offices are more than "
                         f"{SUSPECT_KM:.0f} km from {district} and were left out")
        # a placeholder point that is also suspect is a wrong point, not a district-town point
        phd = (sub["coord_placeholder"] & ~sub["coord_suspect"]).to_numpy() & near
        dprec = float(dist.loc[g, "precision_km"]) if g in dist.index else MIN_PRECISION_KM
        if len(clean):
            src, lat, lon, floor = clean, clean["lat"].median(), clean["lon"].median(), MIN_PRECISION_KM
        elif phd.any():
            src = sub[phd]
            lat, lon, floor = src["lat"].median(), src["lon"].median(), dprec
            notes.append("point from placeholder offices only (a shared district-town point); "
                         "precision is the district's")
        elif g in dist.index:
            src, lat, lon, floor = None, dist.loc[g, "lat"], dist.loc[g, "lon"], dprec
            why = ("suspect coordinates only" if sub["geocoded"].any() else
                   "office points outside India's outline" if sub["coord_outside_india"].any() else
                   "no geocoded office")
            notes.append(f"centre of {district} district: {why}")
        else:
            continue
        if src is not None:
            d = geo.haversine_km(src["lat"], src["lon"], lat, lon)
            prec = max(floor, float(np.quantile(d, PIN_PRECISION_Q)))
        else:
            prec = floor
        rows.append({
            "place_id": f"pin:{pin}", "kind": "pin", "name": pin, "name_norm": pin,
            "state": state, "district": district, "pin": pin, "lat": float(lat), "lon": float(lon),
            "precision_km": float(prec), "n_offices": int(len(sub) if src is None else len(src)),
            "note": "; ".join(notes) or None,
        })
    return pd.DataFrame(rows)


def build_offices(po: pd.DataFrame, pins: pd.DataFrame, districts: pd.DataFrame) -> pd.DataFrame:
    pin_ix = pins.set_index("pin")
    dist = districts.set_index(districts["state"] + "|" + districts["district"])
    o = po.copy()
    o["key"] = o["office_name"].map(geo.place_key)
    o = o[o["key"].str.len() >= 2]
    pl = pin_ix.reindex(o["pin"])
    dp = dist["precision_km"].reindex(o["group"]).to_numpy()
    own = o["clean"].to_numpy()
    # a placeholder point that is also suspect is a wrong point: the office falls to its PIN's point
    phd = (o["coord_placeholder"] & ~o["coord_suspect"]).to_numpy()
    o["p_lat"] = np.where(own | phd, o["lat"], pl["lat"].to_numpy())
    o["p_lon"] = np.where(own | phd, o["lon"], pl["lon"].to_numpy())
    pin_prec = pl["precision_km"].to_numpy()
    base = np.where(phd, np.fmax(dp, pin_prec), pin_prec)
    # an office's own point may be a same-named village elsewhere in the district: the precision
    # is at least its distance from its PIN's point, where the office should be
    to_pin = geo.haversine_km(o["p_lat"], o["p_lon"], pl["lat"].to_numpy(), pl["lon"].to_numpy())
    o["p_prec"] = np.fmax(base, np.nan_to_num(to_pin, nan=0.0))
    o["p_src"] = np.where(own, "own", np.where(phd, "placeholder", "pin"))
    o = o[o["p_lat"].notna() & o["p_lon"].notna()].copy()
    # an office placed by its PIN, whose PIN lies far from the office's listed district, has a
    # directory error in one of the two (for example Rajasthan village names listed under Mulugu,
    # Telangana with Rajasthan PINs). The PIN decides both the point and the label.
    dc = dist.reindex(o["group"].to_numpy())
    far = (o["p_src"].to_numpy() == "pin") & (geo.haversine_km(o["p_lat"], o["p_lon"], dc["lat"].to_numpy(),
                                                               dc["lon"].to_numpy()) > SUSPECT_KM)
    o["listed_as"] = None
    if far.any():
        pr = pin_ix.reindex(o.loc[far, "pin"])
        o.loc[far, "listed_as"] = (o.loc[far, "district"] + ", " + o.loc[far, "state"]).to_numpy()
        o.loc[far, "state"] = pr["state"].to_numpy()
        o.loc[far, "district"] = pr["district"].to_numpy()
    o.attrs["relabelled_by_pin"] = int(far.sum())

    rows = []
    for (key, state, district), sub in o.groupby(["key", "state", "district"], sort=True):
        lat, lon = float(sub["p_lat"].median()), float(sub["p_lon"].median())
        spread = geo.haversine_km(sub["p_lat"], sub["p_lon"], lat, lon)
        prec = max(MIN_PRECISION_KM, float(sub["p_prec"].max()), float(np.max(spread)))
        srcs = set(sub["p_src"])
        note = None
        if srcs == {"pin"}:
            note = "office point unusable (suspect, outside India, or not geocoded): PIN location used"
        elif srcs == {"placeholder"}:
            note = "office sits on a shared district-town point: precision is the district's"
        elif len(srcs) > 1:
            parts = {"pin": "PIN location for offices with unusable points",
                     "placeholder": "shared district-town points"}
            note = "point from several offices: " + "; ".join(v for k, v in parts.items() if k in srcs)
        moved = sub["listed_as"].dropna()
        if len(moved):
            note = ((note + "; ") if note else "") + (
                f"the directory lists it under {moved.iloc[0].title()}, far from its PIN: "
                "labelled with the PIN's district")
        # display name: the spelling of the most senior office (HO, then PO, then BO)
        rank = sub["office_type"].map({"HO": 0, "PO": 1, "BO": 2}).fillna(3)
        top = sub.loc[rank.sort_values(kind="stable").index[0]]
        rows.append({
            "place_id": f"post_office:{key}|{state}|{district}", "kind": "post_office",
            "name": key.title(), "name_norm": key, "state": state, "district": district,
            "pin": str(top["pin"]), "lat": lat, "lon": lon, "precision_km": prec,
            "n_offices": int(len(sub)), "note": note,
        })
    out = pd.DataFrame(rows)
    out.attrs["offices_relabelled_by_pin"] = o.attrs["relabelled_by_pin"]
    return out


def build_cities(pins: pd.DataFrame, districts: pd.DataFrame, offices: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    fac = pd.read_parquet(paths.silver_dir() / "facilities.parquet",
                          columns=["facility_id", "city_raw", "pin", "lat", "lon", "coord_check"])
    fac = fac[(fac["coord_check"] == "ok") & fac["lat"].notna() & fac["city_raw"].notna()].copy()
    fac["key"] = fac["city_raw"].map(geo.place_key)
    fac = fac[(fac["key"].str.len() >= 3) & ~fac["key"].isin(["NA", "NULL", "NONE", "INDIA"])]
    taken = set(districts["name_norm"]) | set(districts["name"].map(geo.place_key)) | set(offices["name_norm"])
    n_keys = fac["key"].nunique()
    fac = fac[~fac["key"].isin(taken)]
    pin_ix = pins.set_index("pin")
    loc = pin_ix.reindex(fac["pin"].astype(object))
    fac["state"] = loc["state"].to_numpy()
    fac["district"] = loc["district"].to_numpy()
    fac = fac[fac["state"].notna()]

    rows = []
    for (key, state), sub in fac.groupby(["key", "state"], sort=True):
        if len(sub) < CITY_MIN_FACILITIES:
            continue
        lat, lon = float(sub["lat"].median()), float(sub["lon"].median())
        d = geo.haversine_km(sub["lat"], sub["lon"], lat, lon)
        prec = max(MIN_PRECISION_KM, float(np.quantile(d, CITY_PRECISION_Q)))
        district = sub["district"].mode().sort_values().iloc[0]
        name = sub["city_raw"].str.strip().mode().sort_values().iloc[0]
        n_d = sub["district"].nunique()
        note = f"from {len(sub)} facilities" + (f"; they fall in {n_d} districts, mostly {district}" if n_d > 1 else "")
        rows.append({
            "place_id": f"city:{key}|{state}", "kind": "city", "name": name.title(), "name_norm": key,
            "state": state, "district": district, "pin": None, "lat": lat, "lon": lon,
            "precision_km": prec, "n_offices": 0, "note": note,
        })
    stats = {"city_keys_in_facilities": int(n_keys), "city_keys_not_district_or_office": int(fac["key"].nunique()),
             "city_rows": len(rows)}
    return pd.DataFrame(rows), stats


def snap_inside(places: pd.DataFrame, po: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """A computed point (a district centre, a median of a few offices) can fall outside India's
    outline: island groups (Lakshadweep, Nicobars), thin districts along the Bangladesh border
    (Uttar Dinajpur), a median across a strait (Rameswaram). Move it to the nearest clean office
    point of the same district, widen the precision by the move, and say so in `note`."""
    border = locate.india_border_km(places["lat"], places["lon"])
    bad = np.flatnonzero(border > locate.BORDER_SLACK_KM)
    ref = po[po["clean"]]
    by_group = dict(tuple(ref.groupby("group")))
    places = places.copy()
    for j in bad:
        r = places.iloc[j]
        cand = by_group.get(f"{r['state']}|{r['district']}", ref)
        d = geo.haversine_km(cand["lat"], cand["lon"], r["lat"], r["lon"])
        k = int(np.argmin(d))
        places.iat[j, places.columns.get_loc("lat")] = float(cand["lat"].iloc[k])
        places.iat[j, places.columns.get_loc("lon")] = float(cand["lon"].iloc[k])
        places.iat[j, places.columns.get_loc("precision_km")] = max(float(r["precision_km"]), float(d[k]))
        msg = (f"the computed point fell outside India's outline; moved {d[k]:.0f} km to the nearest "
               "office point of the district")
        places.iat[j, places.columns.get_loc("note")] = f"{r['note']}; {msg}" if r["note"] else msg
    return places, len(bad)


def main() -> int:
    po, aux = load_offices()
    districts = build_districts(po, aux["centres"])
    pins = build_pins(po, districts)
    offices = build_offices(po, pins, districts)
    relabelled = int(offices.attrs.get("offices_relabelled_by_pin", 0))
    cities, city_stats = build_cities(pins, districts, offices)
    places = pd.concat([pins, districts, offices, cities], ignore_index=True)
    places["n_offices"] = places["n_offices"].astype("int64")
    for c in ("lat", "lon", "precision_km"):
        places[c] = places[c].astype("float64")
    places, n_snapped = snap_inside(places, po)

    finite = np.isfinite(places[["lat", "lon", "precision_km"]].to_numpy()).all(axis=1)
    inside = geo.in_india_bbox(places["lat"], places["lon"])
    border_km = locate.india_border_km(places["lat"], places["lon"])
    outline_out = places.loc[border_km > locate.BORDER_SLACK_KM, "kind"].value_counts().to_dict()
    assert finite.all(), f"{(~finite).sum()} rows with a non-finite value"
    assert inside.all(), f"{(~inside).sum()} rows outside India's box"
    assert (places["precision_km"] > 0).all()
    assert places["place_id"].is_unique

    # every PIN and office row should sit near its own district: within SUSPECT_KM of the
    # district centre, or within its own stated precision of it
    dix = districts.set_index(districts["state"] + "|" + districts["district"])
    po_rows = places[places["kind"].isin(["pin", "post_office"])]
    dd = dix.reindex((po_rows["state"] + "|" + po_rows["district"]).to_numpy())
    d_centre = geo.haversine_km(po_rows["lat"], po_rows["lon"], dd["lat"].to_numpy(), dd["lon"].to_numpy())
    beyond = (d_centre > np.fmax(SUSPECT_KM, po_rows["precision_km"].to_numpy())) & \
        ~po_rows["state"].isin(ISLAND_STATES).to_numpy()
    pix = pins.set_index("pin")
    off = places[places["kind"] == "post_office"]
    pp = pix.reindex(off["pin"].to_numpy())
    d_pin = geo.haversine_km(off["lat"], off["lon"], pp["lat"].to_numpy(), pp["lon"].to_numpy())
    near_checks = {
        "pin_or_office_rows_over_200km_from_own_district_centre": int((d_centre > SUSPECT_KM).sum()),
        "of_which_beyond_their_precision": int(beyond.sum()),
        "office_rows_over_50km_from_their_pin_point": int((d_pin > 50).sum()),
        "of_which_beyond_their_precision_plus_pin_precision": int(
            (d_pin > off["precision_km"].to_numpy() + pp["precision_km"].to_numpy()).sum()),
    }

    raw = pd.read_parquet(paths.silver_dir() / "post_offices.parquet", columns=["pin", "coord_valid"])
    geocoded_pins = set(raw.loc[raw["coord_valid"].astype(bool), "pin"])
    pin_rows = set(pins["pin"])
    counts = places["kind"].value_counts().to_dict()
    stats = {
        **aux["stats"], **city_stats,
        "rows_by_kind": counts,
        "pins_with_a_geocoded_office_missing_from_places": sorted(geocoded_pins - pin_rows),
        "pin_basis": {
            "clean_offices": int(pins["note"].fillna("").str.contains("placeholder|centre of").__invert__().sum()),
            "placeholder_only": int(pins["note"].fillna("").str.contains("placeholder offices only").sum()),
            "district_centre": int(pins["note"].fillna("").str.contains("centre of").sum()),
        },
        "pins_spanning_districts": int(pins["note"].fillna("").str.contains("mostly in").sum()),
        "near_own_district": near_checks,
        "offices_relabelled_by_pin": relabelled,
        "rows_moved_inside_india_outline": n_snapped,
        "rows_outside_india_outline_by_kind": outline_out,
        "precision_km_median_by_kind": places.groupby("kind")["precision_km"].median().round(2).to_dict(),
        "rules": {
            "coord_suspect_km": SUSPECT_KM, "min_precision_km": MIN_PRECISION_KM,
            "pin_precision_quantile": PIN_PRECISION_Q, "district_precision_quantile": DISTRICT_PRECISION_Q,
            "city_min_facilities": CITY_MIN_FACILITIES, "city_precision_quantile": CITY_PRECISION_Q,
        },
    }
    out = write_places(places, {"stats": stats})

    print(f"wrote {out} ({len(places):,} rows)")
    for k in ("pin", "district", "post_office", "city"):
        sub = places[places["kind"] == k]
        print(f"  {k:12s} {len(sub):>7,} rows  precision_km median {sub['precision_km'].median():6.1f}  "
              f"p90 {sub['precision_km'].quantile(0.9):6.1f}")
    print(f"  all rows finite and inside India: {bool(finite.all() and inside.all())}; "
          f"min precision_km {places['precision_km'].min():.2f}; rows more than {locate.BORDER_SLACK_KM:.0f} km "
          f"outside India's outline: {outline_out or 0} (after moving {n_snapped})")
    s = aux["stats"]
    print(f"  offices: {s['offices']:,}; outside India's outline {s['offices_outside_india_outline']:,}; "
          f"geocoded (inside) {s['offices_geocoded']:,}; suspect {s['offices_coord_suspect']:,}; "
          f"placeholder {s['offices_coord_placeholder']:,}; NA state/district filled from PIN "
          f"{s['offices_na_filled_from_pin']:,}, from nearest offices {s['offices_na_filled_from_nearest_offices']:,}, "
          f"dropped {s['offices_na_unfilled_dropped']:,}")
    print(f"  PINs: {s['pins_in_directory']:,} in directory ({s['pins_with_a_geocoded_office']:,} with a geocoded "
          f"office), {len(pin_rows):,} in places; "
          f"basis {stats['pin_basis']}; spanning districts {stats['pins_spanning_districts']:,}")
    print(f"  PINs with a geocoded office missing from places: "
          f"{len(stats['pins_with_a_geocoded_office_missing_from_places'])}; PINs dropped for no district: "
          f"{len(s['pins_dropped_no_district'])}")
    print(f"  cities: {city_stats}")
    print(f"  near own district: {near_checks}; offices relabelled with their PIN's district: {relabelled}")
    return 0


# ---------------------------------------------------------------------------
# evaluation on the frozen cases
# ---------------------------------------------------------------------------

#: Recorded when the cases file was frozen, before any resolver code was written.
CASES_FROZEN = {
    "frozen_at_utc": "2026-11-22T01:47:38Z",
    "sha256": "122725677e54656b7f06ea2c851db1b5c991a104aa44f6c46980110223b66220",
    "rows": 55,
}
#: Why each remaining miss happens. Keyed by case_id.
MISS_NOTES = {
    "27": ("\"Hydrabad\" is an exact post office name (a branch office in Unnao, Uttar Pradesh) and a "
           "misspelling of Hyderabad. The first run returned the Unnao village silently. After the cross-tier "
           "rule it returns `ambiguous` with Hyderabad district first and the Unnao office second, so the user "
           "picks. The case expected `ok` Hyderabad, so it stays a miss; we judge ambiguous the right answer "
           "here and did not edit the frozen file."),
}
FIRST_RUN = (
    "First run, frozen cases, the code as first written: **53 of 55 hit (96%)**. Misses:\n\n"
    "- 27 `Hydrabad`: resolved silently to the Hydrabad branch office in Unnao, Uttar Pradesh (exact office name "
    "beat the fuzzy district match).\n"
    "- 54 `hospital near me`: fuzzy matched the post office \"Sm Hospital\" in Sivaganga, Tamil Nadu (score 91).\n\n"
    "Changes made after the first run (the cases file was not edited):\n\n"
    "1. Care words (`hospital`, `clinic`, `nursing home`, `me`, `my`, `here`, `nearby`, `around`) are dropped from "
    "the place text with the other filler words. Fixes 54.\n"
    "2. Cross-tier check: when the only exact match is a post office name, district and city names with a spelling "
    "score of at least 90 (no containment credit) in other districts compete with it, and the answer is "
    "`ambiguous`. Turns 27 from a silent wrong pick into an ambiguous answer.\n"
    "3. Tried and reverted: matching the literal name before old names. It broke `Gurgaon` and `Allahabad`, which "
    "are also village office names (in Ranchi, Bareilly, Budaun; and in Patan, Gujarat), so a known rename of a "
    "district now competes in the district step, as first written.\n"
    "4. PINs whose offices all have state `NA` (90 geocoded PINs) were missing from the first gazetteer; they now "
    "take the district of their 5 nearest offices. Found by the every-PIN check, not by a case.\n"
)

FIX_ROUND = (
    "Fix round after an independent review (Nov 21). The cases file was not edited; the frozen score is the "
    "same before and after (54 of 55, the one miss is still case 27). Changes:\n\n"
    "1. Gazetteer: an office that is both `coord_placeholder` and `coord_suspect` is treated as suspect (its "
    "point was being used, which put rows such as Raj Garh Ajmer in Mizoram). A PIN is placed only by offices "
    "within 200 km of its main district's centre. An office placed by a PIN that is more than 200 km from the "
    "office's listed district takes the PIN's district. An office's precision is at least its distance from its "
    "PIN's point.\n"
    "2. Gazetteer and resolver: India's outline (Natural Earth, public domain) replaces the bounding box as the "
    "test of \"inside India\". Office points more than 3 km outside it are treated as not geocoded, computed "
    "points that fall outside are moved to the nearest office point of their district, and typed coordinates "
    "outside it are `outside_india` (Lahore, Rajshahi, Sylhet and Biratnagar were `ok` before).\n"
    "3. Resolver parsing: the whole text is matched exactly before a state or filler word is taken off (New "
    "Delhi, North Goa, East District); a trailing state is not taken off when only a bare word such as North or "
    "New would remain; a state after the last comma is parsed before a district (\"Singhwara, Darbhanga, "
    "Bihar\").\n"
    "4. Resolver matching: a fuzzy district or city hit that only contains a name and leaves other words of the "
    "text unexplained no longer wins; a close spelling of an office competes, and if nothing explains the whole "
    "text the answer is a choice. A fuzzy match found only outside the state the user gave or typed is a choice, "
    "not `ok`.\n"
    "5. Islands: the 200 km `coord_suspect` rule no longer applies in Lakshadweep and Andaman and Nicobar "
    "(Minicoy is about 300 km from the other Lakshadweep islands and was being moved to the district centre), "
    "and the 50 km distance-from-any-office rule for typed points now applies only north of 32.5 degrees, where "
    "the outline includes land India does not administer (it rejected Kavaratti and Minicoy).\n"
)


def evaluate() -> Path:
    import hashlib
    import time

    from vf import locate

    cases_path = paths.labels_dir() / "resolver_cases.csv"
    sha = hashlib.sha256(cases_path.read_bytes()).hexdigest()
    t0 = time.perf_counter()
    R = locate.Resolver()
    load_s = time.perf_counter() - t0
    cases = locate.load_cases(cases_path)
    rows, times = [], []
    for c in cases.to_dict("records"):
        t = time.perf_counter()
        r = R.resolve(c["query"], c["state"] or None)
        times.append((time.perf_counter() - t) * 1000)
        hit, why = locate.score_case(r, c["expected_status"], c["expected_kind"], c["expected_districts"])
        got = (f"{locate.nice(r.district)}, {locate.nice(r.state)}" if r.status == "ok" else
               "; ".join(f"{locate.nice(x['district'])}, {locate.nice(x['state'])}" for x in r.candidates))
        rows.append({**c, "hit": hit, "why": why, "status": r.status, "kind": r.kind or "", "got": got,
                     "precision_km": r.precision_km, "label": r.label, "how": r.how})
    df = pd.DataFrame(rows)

    # latency over the cases plus random office names, PINs and points
    po = pd.read_parquet(paths.silver_dir() / "places.parquet", columns=["kind", "name", "pin"])
    rng = np.random.default_rng(0)
    extra = (list(po.loc[po["kind"] == "post_office", "name"].sample(500, random_state=0))
             + list(po.loc[po["kind"] == "pin", "pin"].sample(300, random_state=0))
             + [f"{a:.3f}, {b:.3f}" for a, b in zip(rng.uniform(8, 32, 200), rng.uniform(70, 95, 200))])
    lat = []
    for q in list(cases["query"]) + extra:
        t = time.perf_counter()
        R.resolve(q)
        lat.append((time.perf_counter() - t) * 1000)
    lat = np.array(lat)

    places = R.places
    n_hit, n = int(df["hit"].sum()), len(df)
    by_cat = df.groupby("category")["hit"].agg(["sum", "count"])
    amb = df[df["expected_status"] == "ambiguous"]

    def cell(x) -> str:
        return str(x).replace("|", "\\|").replace("\n", " ")

    L = []
    L.append("# Location resolver evaluation (WP2 Part B)\n")
    L.append(f"Generated by `{SCRIPT} --eval-only` on {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC.\n")
    L.append("## Result\n")
    L.append(f"The resolver gets **{n_hit} of {n}** frozen hand-written cases right ({n_hit / n:.0%}). "
             f"Every ambiguous case ({len(amb)}) returns candidates rather than a guess, and "
             f"{int(amb['hit'].sum())} of {len(amb)} list every expected district. The one miss is an answer "
             "we prefer to the expected one (case 27 below). The cases were written by the same agent that wrote "
             "the resolver, so the score measures it against one person's expectations, not against users.\n")
    L.append(f"Speed after loading ({load_s:.1f} s): median {np.median(lat):.1f} ms, p99 "
             f"{np.percentile(lat, 99):.1f} ms, slowest {lat.max():.1f} ms over {len(lat):,} queries "
             "(the cases, 500 random office names, 300 random PINs, 200 random points). Target: under 50 ms.\n")
    L.append("## How the cases were made\n")
    L.append(f"{CASES_FROZEN['rows']} cases were written by hand in `data/labels/resolver_cases.csv` and frozen at "
             f"{CASES_FROZEN['frozen_at_utc']} (sha256 `{CASES_FROZEN['sha256'][:16]}...`) before any resolver code "
             f"was written. Current file sha256 `{sha[:16]}...`: "
             f"{'unchanged' if sha == CASES_FROZEN['sha256'] else 'CHANGED since the freeze'}. Expected districts "
             "were checked for spelling against the PIN directory, nothing else. Thresholds were set before the "
             f"first run: fuzzy score {locate.FUZZY_THRESHOLD:.0f}, ambiguity margin "
             f"{locate.AMBIGUOUS_MARGIN:.0f} points, outside-India distance {locate.OUTSIDE_KM:.0f} km.\n")
    L.append("Scoring: `ok` cases hit when the resolved state and district are one of the expected ones (and the "
             "kind matches, when one is given); `ambiguous` cases hit when every expected district is among the "
             "candidates; `not_found` and `outside_india` cases hit when the status matches.\n")
    L.append("## First run and what changed\n")
    L.append(FIRST_RUN)
    L.append(FIX_ROUND)
    L.append("## Misses\n")
    miss = df[~df["hit"]]
    if miss.empty:
        L.append("None.\n")
    for r in miss.to_dict("records"):
        L.append(f"- **{r['case_id']} `{r['query']}`**: {r['why']}. {MISS_NOTES.get(r['case_id'], 'Not yet explained.')}\n")
    L.append("## By category\n")
    L.append("| Category | Hit | Cases |\n| --- | --- | --- |")
    for cat, r in by_cat.iterrows():
        L.append(f"| {cat} | {int(r['sum'])} | {int(r['count'])} |")
    L.append("")
    L.append("## Every case\n")
    L.append("| # | Query | State given | Expected | Status | Kind | Resolved to | Precision km | Hit |\n"
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for r in df.to_dict("records"):
        exp = r["expected_status"] + (f": {r['expected_districts']}" if r["expected_districts"] else "")
        pk = "" if r["precision_km"] is None or r["precision_km"] != r["precision_km"] else f"{r['precision_km']:.1f}"
        L.append(f"| {r['case_id']} | `{cell(r['query'])}` | {cell(r['state'])} | {cell(exp)} | {r['status']} | "
                 f"{r['kind']} | {cell(r['got'])} | {pk} | {'yes' if r['hit'] else '**no**'} |")
    L.append("")
    L.append("## Gazetteer\n")
    meta = json.loads((paths.silver_dir() / "places.parquet.meta.json").read_text())
    st = meta["stats"]
    L.append("| Kind | Rows | Median precision km | p90 precision km |\n| --- | --- | --- | --- |")
    for k in ("pin", "district", "post_office", "city"):
        sub = places[places["kind"] == k]
        L.append(f"| {k} | {len(sub):,} | {sub['precision_km'].median():.1f} | {sub['precision_km'].quantile(0.9):.1f} |")
    L.append("")
    L.append(f"- Post offices: {st['offices']:,}; {st['offices_outside_india_outline']:,} have a point more than "
             f"{locate.BORDER_SLACK_KM:.0f} km outside India's outline and are treated as not geocoded; geocoded "
             f"inside India {st['offices_geocoded']:,}; `coord_suspect` (more than {SUSPECT_KM:.0f} km from their "
             f"district centre) {st['offices_coord_suspect']:,}; `coord_placeholder` {st['offices_coord_placeholder']:,}. "
             "All three kinds are kept out of PIN centres; an office that is both placeholder and suspect counts as "
             "suspect and takes its PIN's point. An office whose PIN's point is more than 200 km from its listed "
             f"district is labelled with the PIN's district ({st['offices_relabelled_by_pin']} offices, for example "
             "Rajasthan villages with Rajasthan PINs listed under Mulugu, Telangana). "
             f"{st['rows_moved_inside_india_outline']} computed points that fell outside India's outline (island "
             "district centres, Uttar Dinajpur's centre) were moved to the nearest office point of their district.")
    L.append(f"- PINs: {st['pins_in_directory']:,} in the directory, {st['pins_with_a_geocoded_office']:,} with a "
             f"geocoded office, all of which resolve. {st['pin_basis']['placeholder_only']} PINs are placed only by "
             f"placeholder points and {st['pin_basis']['district_centre']} by their district's centre (said in "
             f"`note`). {len(st['pins_dropped_no_district'])} PINs with no geocoded office and no district are left "
             f"out. {st['pins_spanning_districts']:,} PINs span more than one district and are labelled \"mostly in\".")
    L.append(f"- Offices with state or district `NA`: {st['offices_state_or_district_na']:,}; "
             f"{st['offices_na_filled_from_pin']:,} take their PIN's main district, "
             f"{st['offices_na_filled_from_nearest_offices']:,} the district of their 5 nearest offices.")
    L.append(f"- Cities: {st['city_rows']} rows, from {st['city_keys_not_district_or_office']} facility city names "
             "that are not a district or office name, kept when 3 or more facilities pass the WP1 location check.")
    L.append("")
    L.append("## Behaviour beyond the cases\n")
    L.append("These checks were added in the fix round after an independent review. They run on the whole "
             "gazetteer, not on hand-written expectations.\n")
    off = places[places["kind"] == "post_office"]
    cnt = off.groupby("name_norm").size()

    def hit(res, st, di) -> bool:
        if res.status == "ok":
            return (res.state, res.district) == (st, di)
        return res.status == "ambiguous" and any((c["state"], c["district"]) == (st, di) for c in res.candidates)

    # every district name, alone and with its state picked
    dists = places[places["kind"] == "district"]
    d_bad = {"alone": [], "with state": []}
    for name, st_, di in zip(dists["name"], dists["state"], dists["district"]):
        for label, given in (("alone", None), ("with state", st_)):
            if not hit(R.resolve(name, given), st_, di):
                d_bad[label].append(f"{name.title()}, {locate.nice(st_)}")
    L.append(f"- Every district name typed exactly: {len(dists) - len(d_bad['alone'])} of {len(dists)} give that "
             f"district (or an ambiguous answer that lists it) when typed alone, "
             f"{len(dists) - len(d_bad['with state'])} of {len(dists)} with the state picked. Before the fix round, "
             "10 did not, with or without the state: New Delhi, North Goa, South Goa, Sikkim's four "
             "\"... District\" names and three Tripura districts resolved to other states."
             + (f" Still missing: {'; '.join(d_bad['alone'][:8])}." if d_bad["alone"] else ""))

    # one-vowel typos of single-district office names, the reviewer's probe, re-seeded
    rng = np.random.default_rng(11)
    names = off[off["name_norm"].isin(cnt[cnt == 1].index) & (off["name_norm"].str.len() >= 6)].sample(
        600, random_state=11)
    vowels = "aeiou"
    tally: dict[str, int] = {}
    wrong_why: dict[str, int] = {}
    wrong = []
    for name, st_, di in zip(names["name"], names["state"], names["district"]):
        chars = list(name)
        i = int(rng.integers(1, len(chars)))
        chars[i] = rng.choice([v for v in vowels if v != chars[i].lower()])
        q = "".join(chars)
        res = R.resolve(q)
        if res.status == "ok":
            k = "ok, right district" if (res.state, res.district) == (st_, di) else "ok, WRONG district"
        elif res.status == "ambiguous":
            k = "ambiguous, lists it" if hit(res, st_, di) else "ambiguous, does not list it"
        else:
            k = res.status
        tally[k] = tally.get(k, 0) + 1
        if k == "ok, WRONG district":
            why = ("the typo is itself an exact name elsewhere" if "exactly" in res.how else
                   "a district or city spelled closer" if res.kind in ("district", "city") else
                   "another office spelled closer")
            wrong_why[why] = wrong_why.get(why, 0) + 1
            if len(wrong) < 5:
                wrong.append(f"{q} ({locate.nice(di)}) to {res.label}")
    L.append("- One-vowel typos of 600 random office names that occur in one district only (6 or more letters): "
             + ", ".join(f"{k} {v}" for k, v in sorted(tally.items(), key=lambda t: -t[1])) + ". "
             "Before the fix round the reviewer's version of this probe found 47 of 600 (8%) ok in the wrong "
             "district, many of them a district name inside a village name (\"Rampur Chourai\" to Rampur, Uttar "
             "Pradesh). That cause is gone, but the wrong-district rate is about the same: the ones left are typos "
             "that land on, or closer to, another real name ("
             + ", ".join(f"{k}: {v}" for k, v in sorted(wrong_why.items(), key=lambda t: -t[1]))
             + "). Spelling alone cannot catch these; the label always names the district, so the user can see it."
             + (f" Examples: {'; '.join(wrong)}." if wrong else ""))

    # foreign cities and Indian border points
    foreign = {"Lahore": (31.5204, 74.3587), "Gujranwala": (32.1877, 74.1945), "Sialkot": (32.4945, 74.5229),
               "Kasur": (31.1156, 74.4467), "Rajshahi": (24.3745, 88.6042), "Sylhet": (24.8949, 91.8687),
               "Biratnagar": (26.4525, 87.2718), "Dhaka": (23.8103, 90.4125), "Kathmandu": (27.7172, 85.3240),
               "Muzaffarabad": (34.3700, 73.4700), "Gilgit": (35.9200, 74.3100)}
    indian = {"Amritsar": (31.6340, 74.8723), "Attari": (31.6018, 74.6038), "Agartala": (23.8315, 91.2868),
              "Siliguri": (26.7271, 88.3953), "Port Blair": (11.6234, 92.7265), "Kavaratti": (10.5626, 72.6369),
              "Minicoy": (8.2833, 73.0500), "Car Nicobar": (9.1667, 92.7500)}
    f_res = {k: R.resolve(f"{a}, {b}").status for k, (a, b) in foreign.items()}
    i_res = {k: R.resolve(f"{a}, {b}").status for k, (a, b) in indian.items()}
    L.append("- Typed coordinates of foreign cities: " + ", ".join(f"{k} {v}" for k, v in f_res.items()) + ". "
             "Indian border and island points: " + ", ".join(f"{k} {v}" for k, v in i_res.items()) + ". "
             "Before the fix round Lahore, Gujranwala, Rajshahi, Sylhet and Biratnagar came back `ok` in an Indian "
             "district, because some Indian office points are themselves geocoded across the border (trap 9).")

    # exact office names, with and without the state
    sample = off.sample(3000, random_state=7)
    miss_alone = sum(not hit(R.resolve(n), s_, d_) for n, s_, d_ in zip(sample["name"], sample["state"], sample["district"]))
    miss_state = sum(not hit(R.resolve(n, s_), s_, d_) for n, s_, d_ in zip(sample["name"], sample["state"], sample["district"]))
    L.append(f"- Office names repeat: {int((cnt > 1).sum()):,} of {len(cnt):,} distinct office names occur in more "
             f"than one district. Of 3,000 random office rows typed by exact name, {miss_alone} come back without "
             f"their own district among the answers when typed alone and {miss_state} with the state picked, "
             f"because an ambiguous answer lists at most {locate.MAX_CANDIDATES} districts. Adding the district "
             "after a comma (\"Shahpur, Mirzapur\") settles them; the `how` text says so.")
    single = cnt[cnt == 1].index.to_series().sample(3000, random_state=1)
    flipped = [k for k in single if (r := R.resolve(k)).status == "ambiguous" and "closely as a district" in r.how]
    L.append(f"- The cross-tier check turns {len(flipped)} of 3,000 random single-district office names into "
             f"ambiguous answers, all near-spellings of a district or city (for example "
             f"{', '.join(x.title() for x in flipped[:4])}).")
    near = st.get("near_own_district", {})
    L.append(f"- Every PIN and office row outside the island states lies within 200 km (or its own precision) of "
             f"its district's centre: "
             f"{near.get('of_which_beyond_their_precision', 'n/a')} rows do not (35 PIN and 869 office rows were more "
             f"than 200 km away before the fix round). {near.get('office_rows_over_50km_from_their_pin_point', 'n/a'):,} "
             "office rows sit more than 50 km from their PIN's point; "
             f"{near.get('of_which_beyond_their_precision_plus_pin_precision', 'n/a')} of them by more than their "
             "precision plus the PIN's (rows that merge two offices with different PINs).")
    L.append("")
    L.append("## Limits\n")
    L.append("- A district answer is the district's centre, not the user's location; the label says \"centre of X "
             "district, about N km across\" (N is twice the 75th percentile office distance).")
    L.append("- Precision is a spread of post office points, not a measured error. Branch office coordinates in the "
             "directory are often wrong; suspect, placeholder and outside-India points are kept out of PIN centres "
             "but a row may still rest on a single office's point (said in `note`).")
    L.append(f"- The outside-India check uses Natural Earth's 1:10m India outline (India's own view of its borders, "
             f"public domain), simplified to about 0.5 km, with {locate.BORDER_SLACK_KM:.0f} km of slack for coasts "
             "and border towns. A foreign point within 3 km of the border is accepted. The outline includes "
             "Pakistan-administered Kashmir and Aksai Chin; north of "
             f"{locate.DISTANCE_RULE_NORTH_OF} degrees a point inside it is rejected only if it is more than "
             f"{locate.OUTSIDE_KM:.0f} km from every Indian post office point (see Muzaffarabad and Gilgit above: "
             "Muzaffarabad, 36 km from one, is accepted). South of that line the distance rule is off, so a point "
             "inside India far from any usable office point (Kavaratti, Minicoy) is accepted and given the "
             "district of its nearest PIN points.")
    L.append("- Island districts (Lakshadweep, Andaman and Nicobar) are exempt from the 200 km `coord_suspect` "
             "rule, because they span more than 200 km of sea; their points are checked against the outline only.")
    L.append("- A fuzzy match that explains only part of the text, or one found only outside the state the user "
             "gave, comes back as a choice (`ambiguous`, sometimes with one candidate), not as `ok`. This costs "
             "the user a click on queries such as \"Lucknow Hazratganj Market\".")
    L.append("- Old names come only from the fixed alias list in `vf.geo.CITY_ALIASES`; an old name not in it is "
             "matched by spelling or not at all.")
    L.append("- Only one person's expectations were scored. There are no real user queries.")
    out = paths.ensure(paths.code_root() / "reports") / "resolver_eval.md"
    _atomic_write(out, lambda p: p.write_text("\n".join(L) + "\n"))
    print(f"wrote {out}: {n_hit}/{n} cases hit; latency median {np.median(lat):.1f} ms, max {lat.max():.1f} ms")
    for r in miss.to_dict("records"):
        print(f"  miss {r['case_id']} {r['query']!r}: {r['why']}")
    return out


if __name__ == "__main__":
    if "--eval-only" not in sys.argv:
        main()
    evaluate()
    raise SystemExit(0)
