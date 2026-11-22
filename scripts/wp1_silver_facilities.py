#!/usr/bin/env python
"""WP1: silver facilities and facts.

Reads data/raw/facilities.csv through vf.io and writes, under vf.paths.silver_dir():

  facilities.parquet  one typed row per real facility (duplicate ids resolved)
  facts.parquet       one row per capability, procedure, or equipment fact
  <file>.meta.json    row count, created time, git commit, recipe version, stats

Coordinates are checked against the facility's own PIN, using the raw India Post
PIN directory (vf.geo.check_facility_locations). A facility more than 40 km
from its PIN's location and outside the PIN's district is moved to the PIN when
the city text names the PIN's place and not the scraped point's, and kept when
the city text names the point's place and not the PIN's. When the city text
names both or neither, the point is kept up to 100 km and set to null beyond.
lat_raw and lon_raw keep the scraped point; coord_check says what happened.

Both tables follow docs/07_data_contracts.md exactly. Rerun end to end with

  .venv/bin/python scripts/wp1_silver_facilities.py

It prints a summary and the WP1 acceptance checks, and exits 1 if any check fails.
"""
from __future__ import annotations

import collections
import hashlib
import json
import os
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from vf import clean, geo, io, paths

RECIPE_VERSION = "wp1-silver-v3"
SCRIPT = "scripts/wp1_silver_facilities.py"

#: Date of the raw export (data/raw/MANIFEST.json). Page dates after it are errors.
SNAPSHOT_DATE = date(2026, 11, 21)
#: Plausible founding years. The raw column has 0, 7, and 15; the oldest real
#: values are 1723 and 1756.
YEAR_MIN, YEAR_MAX = 1600, SNAPSHOT_DATE.year

FACT_FIELDS = ("capability", "procedure", "equipment")
FACILITY_TYPE_FIXES = {"farmacy": "pharmacy"}
OPERATOR_FIXES = {"government": "public"}

# Reference numbers from docs/03_data_profile.md and docs/02_data_dictionary.md.
PROFILE_COORD_VALID = 9964
PROFILE_SPECIALTIES = {
    "gynecologyAndObstetrics": 4497,
    "nephrology": 2156,
    "criticalCareMedicine": 1677,
}

_LIST = pa.list_(pa.string())
FACILITIES_SCHEMA = pa.schema([
    pa.field("facility_id", pa.string(), nullable=False),
    pa.field("cluster_id", pa.string()),
    pa.field("row_status", pa.string(), nullable=False),
    pa.field("name", pa.string(), nullable=False),
    pa.field("facility_type", pa.string()),
    pa.field("operator_type", pa.string()),
    pa.field("lat", pa.float64()),
    pa.field("lon", pa.float64()),
    pa.field("coord_valid", pa.bool_(), nullable=False),
    pa.field("coord_check", pa.string(), nullable=False),
    pa.field("coord_pin_km", pa.float64()),
    pa.field("lat_raw", pa.float64()),
    pa.field("lon_raw", pa.float64()),
    pa.field("pin", pa.string()),
    pa.field("city_raw", pa.string()),
    pa.field("state_raw", pa.string()),
    pa.field("specialties", _LIST, nullable=False),
    pa.field("capability", _LIST, nullable=False),
    pa.field("procedure", _LIST, nullable=False),
    pa.field("equipment", _LIST, nullable=False),
    pa.field("description", pa.string()),
    pa.field("n_doctors", pa.int64()),
    pa.field("n_beds", pa.int64()),
    pa.field("year_established", pa.int64()),
    pa.field("official_website", pa.string()),
    pa.field("source_urls", _LIST, nullable=False),
    pa.field("source_domains", _LIST, nullable=False),
    pa.field("n_source_domains", pa.int64(), nullable=False),
    pa.field("page_updated", pa.date32()),
    pa.field("staff_named", pa.bool_()),
])

FACTS_SCHEMA = pa.schema([
    pa.field("fact_id", pa.string(), nullable=False),
    pa.field("facility_id", pa.string(), nullable=False),
    pa.field("field", pa.string(), nullable=False),
    pa.field("text", pa.string(), nullable=False),
    pa.field("text_norm", pa.string(), nullable=False),
])


# --------------------------------------------------------------------------
# steps
# --------------------------------------------------------------------------

def count_facts(row) -> int:
    return sum(len(clean.parse_items(row[f])) for f in FACT_FIELDS)


def resolve_duplicates(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, list[dict]]:
    """One row per unique_id: the row with the most facts, first in file order on a tie.

    Returns (kept rows, removed rows, one summary dict per duplicated id).
    """
    df = df.copy()
    df["_n_facts"] = [count_facts(r) for r in df.to_dict("records")]
    pairs = []
    for uid, g in df[df["unique_id"].duplicated(keep=False)].groupby("unique_id", sort=False):
        body = g.drop(columns="_n_facts")
        identical = bool((body.nunique(dropna=False) <= 1).all())
        pairs.append({
            "unique_id": uid,
            "name": g["name"].iloc[0],
            "rows": len(g),
            "facts_per_row": g["_n_facts"].tolist(),
            "rows_identical": identical,
        })
    keep = sorted(df.groupby("unique_id", sort=False)["_n_facts"].idxmax())  # first max wins
    df = df.drop(columns="_n_facts")
    kept = df.loc[keep].reset_index(drop=True)
    removed = df.loc[df.index.difference(keep)]
    return kept, removed, pairs


def build_facilities(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Typed facility table in contract column order, plus cleaning counters."""
    stats = collections.Counter()
    recs = []
    for r in df.to_dict("records"):
        lat, lon = io.to_float(r["latitude"]), io.to_float(r["longitude"])
        valid = io.in_india(lat, lon)
        if lat is not None and lon is not None and not valid:
            stats["coords_outside_india_nulled"] += 1
        if not valid:
            lat = lon = None

        raw_year = clean.parse_int(r["yearEstablished"])
        year = clean.parse_int(r["yearEstablished"], lo=YEAR_MIN, hi=YEAR_MAX)
        if raw_year is not None and year is None:
            stats["year_out_of_range_nulled"] += 1

        page = clean.parse_iso_date(r["recency_of_page_update"], max_date=SNAPSHOT_DATE)
        if page is None and not io.is_null(r["recency_of_page_update"]):
            stats["page_date_unparsed_or_future_nulled"] += 1

        raw_pin = clean.clean_str(r["address_zipOrPostcode"])
        pin = clean.parse_pin(raw_pin)
        if pin is not None and pin != raw_pin:
            stats["pin_repaired_spacing"] += 1
        if raw_pin is not None and pin is None:
            stats["pin_invalid_nulled"] += 1

        ftype = clean.clean_str(r["facilityTypeId"])
        op = clean.clean_str(r["operatorTypeId"])
        if op in OPERATOR_FIXES:
            stats["operator_government_to_public"] += 1
        if ftype in FACILITY_TYPE_FIXES:
            stats["facility_type_farmacy_to_pharmacy"] += 1

        # List fields. io.parse_list is the reference parser; clean.parse_items
        # adds raw positions (for fact_id) and drops placeholders like "N/A".
        specialties = clean.dedupe(
            s for s in io.parse_list(r["specialties"], dedupe=True) if not clean.is_null_item(s)
        )
        facts = {}
        for f in FACT_FIELDS:
            facts[f] = [t for _, t in clean.parse_items(r[f])]
            ref = [t for t in io.parse_list(r[f]) if not clean.is_null_item(t)]
            assert facts[f] == ref, (r["unique_id"], f)
        urls = io.parse_list(r["source_urls"])
        domains = clean.distinct_domains(urls)

        recs.append({
            "facility_id": clean.clean_str(r["unique_id"]),
            "cluster_id": clean.clean_str(r["cluster_id"]),
            "row_status": r["row_status"],
            "name": clean.clean_str(r["name"]),
            "facility_type": FACILITY_TYPE_FIXES.get(ftype, ftype),
            "operator_type": OPERATOR_FIXES.get(op, op),
            "lat": lat,
            "lon": lon,
            "coord_valid": valid,
            "coord_check": None,
            "coord_pin_km": None,
            "lat_raw": lat,
            "lon_raw": lon,
            "pin": pin,
            "city_raw": clean.clean_str(r["address_city"]),
            "state_raw": clean.clean_str(r["address_stateOrRegion"]),
            "specialties": specialties,
            "capability": facts["capability"],
            "procedure": facts["procedure"],
            "equipment": facts["equipment"],
            "description": clean.clean_str(r["description"]),
            "n_doctors": clean.parse_int(r["numberDoctors"]),
            "n_beds": clean.parse_int(r["capacity"]),
            "year_established": year,
            "official_website": clean.clean_str(r["officialWebsite"]),
            "source_urls": urls,
            "source_domains": domains,
            "n_source_domains": len(domains),
            "page_updated": page,
            "staff_named": clean.parse_bool(r["affiliated_staff_presence"]),
        })

    fac = pd.DataFrame.from_records(recs, columns=FACILITIES_SCHEMA.names)
    for c in ("lat", "lon", "lat_raw", "lon_raw", "coord_pin_km"):
        fac[c] = fac[c].astype("float64")
    for c in ("n_doctors", "n_beds", "year_established"):
        fac[c] = pd.array(fac[c].tolist(), dtype="Int64")
    fac["n_source_domains"] = fac["n_source_domains"].astype("int64")
    fac["coord_valid"] = fac["coord_valid"].astype(bool)
    fac["staff_named"] = pd.array(fac["staff_named"].tolist(), dtype="boolean")
    return fac, dict(stats)


def check_coordinates(fac: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Check scraped coordinates against each facility's own PIN (vf.geo).

    Sets lat, lon (the location to use), coord_valid (lat/lon present),
    coord_check and coord_pin_km. lat_raw and lon_raw keep the scraped point.
    Returns the updated frame and stats for the sidecar.
    """
    raw_pins = io.read_pincodes_raw()
    po_coords, po_stats = geo.post_office_coords(raw_pins)
    ref = geo.PinReference(raw_pins, po_coords)
    chk = geo.check_facility_locations(fac["lat_raw"], fac["lon_raw"], fac["pin"], fac["city_raw"], ref)
    chk.index = fac.index
    fac = fac.copy()
    fac["lat"], fac["lon"] = chk["lat"].astype("float64"), chk["lon"].astype("float64")
    fac["coord_valid"] = (fac["lat"].notna() & fac["lon"].notna()).astype(bool)
    fac["coord_check"] = chk["coord_check"].astype(str)
    fac["coord_pin_km"] = chk["coord_pin_km"].astype("float64")

    counts = fac["coord_check"].value_counts()
    has = fac["coord_pin_km"].notna()
    far = fac["coord_pin_km"] > geo.FACILITY_PIN_KM
    band = (fac["coord_pin_km"] > geo.FACILITY_PIN_CHECK_KM) & ~far
    stats = {
        "rule": (
            f"coord_pin_km = km from the scraped point to the facility's PIN location. Over "
            f"{geo.FACILITY_PIN_CHECK_KM:.0f} km: kept when the point is in the PIN's main district (majority of "
            f"the {geo.DISTRICT_VOTE_K} nearest placed head and sub offices); otherwise the city text decides, "
            f"compared with the PIN's own office, district and division names plus the names of placed head and "
            f"sub offices within {geo.PLACE_NAMES_KM:.0f} km of each point: moved to the PIN when it names only "
            "the PIN's place, kept when it names only the point's place. When it names both or neither the point "
            f"is kept up to {geo.FACILITY_PIN_KM:.0f} km (kept_unresolved) and set to null beyond "
            "(excluded_unresolved)"
        ),
        "check_km": geo.FACILITY_PIN_CHECK_KM,
        "check_km_why": (
            f"{100 * coord_band_share(ref, geo.FACILITY_PIN_CHECK_KM):.1f}% of placed post offices lie more than "
            f"{geo.FACILITY_PIN_CHECK_KM:.0f} km from their own PIN's location, so distance alone does not move a "
            "facility; the city text has to name the PIN's place and not the point's"
        ),
        "band_check_km_to_far_km": {k: int(v) for k, v in fac.loc[band, "coord_check"].value_counts().items()},
        "pin_location": (
            "median of the PIN's placed head and sub offices, else of its placed offices of any type, else its "
            f"main district's centre. Placed: within {geo.PLACED_OFFICE_KM:.0f} km of the district centre "
            f"(median of the district's head and sub offices when it has {geo.CENTRE_MIN_TOWN_OFFICES}+, else of "
            "all its offices). Post office coordinates as WP2 cleans them (vf.geo.post_office_coords, swapped "
            "rows repaired)"
        ),
        "far_km": geo.FACILITY_PIN_KM,
        "coord_check_values": geo.COORD_CHECKS,
        "coord_check_counts": {k: int(counts.get(k, 0)) for k in geo.COORD_CHECKS},
        "scraped_in_india": int(fac["lat_raw"].notna().sum()),
        "with_pin_location": int(has.sum()),
        "coord_pin_km_quantiles": {str(q): round(float(fac.loc[has, "coord_pin_km"].quantile(q)), 2)
                                   for q in (0.5, 0.9, 0.95, 0.99)},
        "over_check_km": int((fac["coord_pin_km"] > geo.FACILITY_PIN_CHECK_KM).sum()),
        "over_far_km": int(far.sum()),
        "over_200km": int((fac["coord_pin_km"] > 200).sum()),
        "over_500km": int((fac["coord_pin_km"] > 500).sum()),
        "pin_location_basis": {str(k): int(v) for k, v in ref.pins["basis"].value_counts(dropna=False).items()},
        "post_office_swap_repair": {k: v for k, v in po_stats.items() if not isinstance(v, dict)},
    }
    return fac, stats


def coord_band_share(ref: geo.PinReference, km: float) -> float:
    """Share of placed post offices farther than km from their own PIN's location."""
    po = ref.offices
    loc = ref.location(po["pin"].tolist())
    d = geo.haversine_km(po["lat"], po["lon"], loc["lat"].to_numpy(), loc["lon"].to_numpy())
    ok = po["placed"].to_numpy() & ~np.isnan(d)
    return float((d[ok] > km).mean())


def build_facts(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for r in df.to_dict("records"):
        fid = clean.clean_str(r["unique_id"])
        for f in FACT_FIELDS:
            for pos, text in clean.parse_items(r[f]):
                rows.append((clean.fact_id(fid, f, pos), fid, f, text, clean.normalize_text(text)))
    return pd.DataFrame(rows, columns=FACTS_SCHEMA.names)


def raw_fact_accounting(raw: pd.DataFrame) -> dict:
    """Count raw fact items on all real rows, by why they are dropped."""
    acc = collections.Counter()
    for f in FACT_FIELDS:
        for v in raw[f]:
            if io.is_null(v):
                continue
            items = json.loads(v)
            for x in items:
                acc["total"] += 1
                if x is None:
                    acc["json_null"] += 1
                elif str(x).strip() == "":
                    acc["empty_string"] += 1
                elif clean.is_null_item(x):
                    acc["placeholder"] += 1
    return dict(acc)


# --------------------------------------------------------------------------
# output
# --------------------------------------------------------------------------

def git_info() -> dict:
    root = paths.code_root()
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True
        ).stdout.strip()
    except Exception:
        commit = None
    try:
        dirty_out = subprocess.run(
            ["git", "--no-optional-locks", "status", "--porcelain", "--",
             SCRIPT, "src/vf/clean.py", "src/vf/io.py", "src/vf/geo.py"],
            cwd=root, capture_output=True, text=True, check=True,
        ).stdout.strip()
        dirty = bool(dirty_out)
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


def write_table(df: pd.DataFrame, schema: pa.Schema, path: Path, meta: dict) -> None:
    table = pa.Table.from_pandas(df, schema=schema, preserve_index=False)
    write_atomic(path, lambda p: pq.write_table(table, p))
    sidecar = dict(meta, rows=table.num_rows, columns=schema.names)
    body = json.dumps(sidecar, indent=2, default=str) + "\n"
    meta_path = path.with_name(path.name + ".meta.json")
    write_atomic(meta_path, lambda p: p.write_text(body))


# --------------------------------------------------------------------------
# acceptance checks
# --------------------------------------------------------------------------

def null_like_cells(df: pd.DataFrame) -> dict:
    """Cells (and list items) that are the string "null" in any case, or blank."""
    out = {}
    for c in df.columns:
        s = df[c].dropna()
        if s.empty or not isinstance(s.iloc[0], (str, list)):
            continue
        vals = [x for v in s for x in v] if isinstance(s.iloc[0], list) else list(s)
        n = sum(1 for x in vals if not isinstance(x, str) or x.strip() == "" or x.strip().lower() == "null")
        if n:
            out[c] = n
    return out


def run_checks(raw, fac, facts, n_removed, coord_valid_on_removed, acc, facts_on_removed, raw_spec,
               coord_stats) -> list:
    checks = []

    def check(name, passed, value):
        checks.append({"check": name, "passed": bool(passed), "value": value})

    check("facility_id is unique", fac["facility_id"].is_unique,
          f"{fac['facility_id'].nunique():,} distinct of {len(fac):,} rows")

    n_trunc = int((fac["row_status"] == "truncated").sum())
    check("rows = 10,000 minus duplicates removed; 30 truncated",
          len(fac) == len(raw) - n_removed and n_trunc == 30,
          f"{len(fac):,} rows = {len(raw):,} - {n_removed}; truncated {n_trunc}")

    bad = {**{f"facilities.{k}": v for k, v in null_like_cells(fac).items()},
           **{f"facts.{k}": v for k, v in null_like_cells(facts).items()}}
    check('no string cell or list item equals "null" (any case) or is blank', not bad,
          "0 cells" if not bad else bad)

    n_box = int(fac["lat_raw"].notna().sum())
    check("scraped coordinates inside India for about 9,964 rows (lat_raw, lon_raw)",
          abs(n_box - PROFILE_COORD_VALID) <= 0.005 * PROFILE_COORD_VALID,
          f"{n_box:,} (profile {PROFILE_COORD_VALID:,} on 10,000 rows, minus "
          f"{coord_valid_on_removed} on removed duplicates = {PROFILE_COORD_VALID - coord_valid_on_removed:,})")

    cc = fac["coord_check"]
    both_null = fac["lat"].isna() == fac["lon"].isna()
    valid_ok = (fac["coord_valid"] == fac["lat"].notna()).all() and both_null.all()
    nulled = cc.isin(["excluded_unresolved", "missing"])
    kept = cc.isin(["ok", "ok_same_district", "kept_city_matches_coords", "kept_unresolved", "no_pin_check"])
    same = ((fac["lat"] == fac["lat_raw"]) & (fac["lon"] == fac["lon_raw"]))
    near = fac["coord_pin_km"] <= geo.FACILITY_PIN_CHECK_KM
    over = fac["coord_pin_km"] > geo.FACILITY_PIN_CHECK_KM
    far = fac["coord_pin_km"] > geo.FACILITY_PIN_KM
    consistent = (
        cc.isin(list(geo.COORD_CHECKS)).all()
        and fac.loc[nulled, "lat"].isna().all()
        and same[kept].all()
        and (~same[cc == "moved_to_pin"]).all()
        and near[cc == "ok"].all()
        and over[cc.isin(["ok_same_district", "moved_to_pin", "kept_city_matches_coords", "kept_unresolved",
                          "excluded_unresolved"])].all()
        and (~far[cc == "kept_unresolved"]).all()
        and far[cc == "excluded_unresolved"].all()
    )
    check("coordinate check consistent: coord_valid = lat/lon present; ok rows within "
          f"{geo.FACILITY_PIN_CHECK_KM:.0f} km of their PIN; kept_unresolved within {geo.FACILITY_PIN_KM:.0f} km, "
          f"excluded_unresolved beyond; moved rows changed, kept rows unchanged, excluded rows null",
          valid_ok and consistent, coord_stats["coord_check_counts"])

    expected_facts = (acc["total"] - acc["empty_string"] - acc.get("json_null", 0)
                      - acc.get("placeholder", 0) - facts_on_removed)
    check("facts.parquet about 450,000 rows, matching the raw accounting",
          len(facts) == expected_facts and 440_000 <= len(facts) <= 460_000,
          f"{len(facts):,} = {acc['total']:,} raw - {acc['empty_string']:,} empty - "
          f"{acc.get('json_null', 0)} JSON null - {acc.get('placeholder', 0)} placeholder (N/A, Null) - "
          f"{facts_on_removed} on removed duplicates")

    spec = collections.Counter(s for lst in fac["specialties"] for s in lst)
    ok = all(abs(spec[k] - v) <= 0.01 * v for k, v in PROFILE_SPECIALTIES.items())
    check("deduped specialty counts match the profile",
          ok and raw_spec == PROFILE_SPECIALTIES,
          "; ".join(f"{k} {spec[k]:,} (profile {v:,}, same method on all 10,000 rows {raw_spec[k]:,})"
                    for k, v in PROFILE_SPECIALTIES.items()))

    # Contract shape: read the files back and compare the Arrow schemas.
    silver = paths.silver_dir()
    for fname, schema in (("facilities.parquet", FACILITIES_SCHEMA), ("facts.parquet", FACTS_SCHEMA)):
        got = pq.read_schema(silver / fname).remove_metadata()
        check(f"{fname} schema equals the contract (names, order, Arrow types)",
              got.equals(schema), "match" if got.equals(schema) else str(got))

    check("fact_id unique; every fact's facility_id is in facilities",
          facts["fact_id"].is_unique and facts["facility_id"].isin(fac["facility_id"]).all(),
          f"{facts['fact_id'].nunique():,} distinct fact_id")

    per_field = facts["field"].value_counts().to_dict()
    same = all(per_field.get(f, 0) == int(fac[f].map(len).sum()) for f in FACT_FIELDS)
    check("facts per field equal the list lengths in facilities", same, per_field)
    return checks


# --------------------------------------------------------------------------

def main() -> int:
    raw = io.read_facilities()  # 10,000 real rows, row_status ok/truncated
    raw_spec = collections.Counter(
        s for v in raw["specialties"] for s in io.parse_list(v, dedupe=True) if not clean.is_null_item(s)
    )
    raw_spec = {k: raw_spec[k] for k in PROFILE_SPECIALTIES}
    acc = raw_fact_accounting(raw)

    dedup, removed, pairs = resolve_duplicates(raw)
    n_removed = len(removed)
    facts_on_removed = sum(count_facts(r) for r in removed.to_dict("records"))
    coord_valid_on_removed = sum(
        io.in_india(io.to_float(a), io.to_float(b)) for a, b in zip(removed["latitude"], removed["longitude"])
    )

    fac, stats = build_facilities(dedup)
    fac, coord_stats = check_coordinates(fac)
    facts = build_facts(dedup)

    meta_common = {
        "created_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        **git_info(),
        "recipe_version": RECIPE_VERSION,
        "script": SCRIPT,
        "inputs": {
            "data/raw/facilities.csv": {"sha256": sha256(io.raw_path("facilities"))},
            "data/raw/india_post_pincode_directory.csv": {"sha256": sha256(io.raw_path("pincodes"))},
        },
    }
    silver = paths.silver_dir()
    write_table(fac, FACILITIES_SCHEMA, silver / "facilities.parquet", {
        "output": "silver/facilities.parquet",
        **meta_common,
        "duplicate_rule": "keep the row with the most non-empty facts; first in file order on a tie",
        "duplicates": pairs,
        "cleaning": stats,
        "coordinate_check": coord_stats,
    })
    write_table(facts, FACTS_SCHEMA, silver / "facts.parquet", {
        "output": "silver/facts.parquet",
        **meta_common,
        "fact_id": "sha1('facility_id|field|position')[:16], position = index in the raw JSON array",
        "text_norm": "vf.clean.normalize_text: whitespace collapsed, stripped, lowercased",
        "raw_fact_accounting": dict(acc, on_removed_duplicates=facts_on_removed),
    })

    checks = run_checks(raw, fac, facts, n_removed, coord_valid_on_removed, acc, facts_on_removed, raw_spec,
                        coord_stats)

    # ---------------------------------------------------------------- summary
    nd = fac["n_source_domains"]
    print(f"WP1 silver facilities ({RECIPE_VERSION}) -> {silver}")
    print(f"  facilities.parquet  {len(fac):,} rows   facts.parquet  {len(facts):,} rows")
    print(f"  duplicates: {len(pairs)} ids, {n_removed} rows removed, "
          f"{sum(p['rows_identical'] for p in pairs)} of {len(pairs)} pairs byte-identical")
    print(f"  row_status: {fac['row_status'].value_counts().to_dict()}")
    print(f"  coord_valid: {int(fac['coord_valid'].sum()):,} (scraped inside India {coord_stats['scraped_in_india']:,})"
          f"   pin: {int(fac['pin'].notna().sum()):,} ({int(fac['pin'].nunique()):,} distinct)")
    print(f"  coordinate check vs own PIN ({coord_stats['with_pin_location']:,} with a PIN location; "
          f"{coord_stats['over_check_km']:,} over {geo.FACILITY_PIN_CHECK_KM:.0f} km, "
          f"{coord_stats['over_far_km']:,} over {geo.FACILITY_PIN_KM:.0f} km, {coord_stats['over_200km']:,} over 200 km, "
          f"{coord_stats['over_500km']:,} over 500 km): {coord_stats['coord_check_counts']}")
    print(f"  operator_type: {fac['operator_type'].value_counts(dropna=False).to_dict()}")
    print(f"  facility_type: {fac['facility_type'].value_counts(dropna=False).to_dict()}")
    print(f"  n_doctors {int(fac['n_doctors'].notna().sum()):,}  n_beds {int(fac['n_beds'].notna().sum()):,}  "
          f"year_established {int(fac['year_established'].notna().sum()):,}  "
          f"page_updated {int(fac['page_updated'].notna().sum()):,}  "
          f"staff_named true {int(fac['staff_named'].sum()):,}")
    print(f"  n_source_domains (ok rows): 1:{int((nd == 1).sum())} 2:{int((nd == 2).sum())} "
          f"3:{int((nd == 3).sum())} 4:{int((nd == 4).sum())} 5:{int((nd == 5).sum())} "
          f"6+:{int((nd >= 6).sum())}  0:{int((nd == 0).sum())}")
    print(f"  facts by field: {facts['field'].value_counts().to_dict()}")
    print(f"  distinct text_norm: {facts['text_norm'].nunique():,} "
          f"({facts.groupby('field')['text_norm'].nunique().to_dict()})")
    print(f"  cleaning: {stats}")
    print("Acceptance checks")
    for c in checks:
        print(f"  [{'PASS' if c['passed'] else 'FAIL'}] {c['check']}: {c['value']}")
    return 0 if all(c["passed"] for c in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
