#!/usr/bin/env python
"""WP12: the app bundle, the small set of files the Databricks App loads at start.

Reads silver and gold under vf.paths.data_dir() and writes dbapp/bundle/
(docs/07_data_contracts.md, "The app bundle"):

  facilities.parquet           facility index: location with precision and a plain note, sources, trust
  grades.parquet               gold/capability_grades.parquet, all columns
  flags.parquet                gold/facility_flags.parquet (WP11)            optional
  duplicates.parquet           gold/facility_duplicates.parquet (WP11)       optional
  places.parquet               silver/places.parquet (WP2 B)                 optional
  post_offices.parquet         lat, lon, pin of non-suspect geocoded offices, one row per placeholder point
  service_probability.parquet  gold/service_probability.parquet (WP13)       optional
  keyword_index/               vf.rank.build_keyword_index, when vf.rank has it (WP5)   optional
  manifest.json                row counts, grade counts, versions, git commit, defaults, file sizes

It also copies src/vf to dbapp/vf so the app runs the tested code.

Missing optional inputs are skipped with a message and listed under "missing" in
the manifest; nothing is faked. Facilities and grades are required.

The bundle is built in a temp folder next to the target and swapped in, so a
reader never sees half a bundle. Databricks Apps refuse any file over 10 MB
(docs.databricks.com/aws/en/dev-tools/databricks-apps/app-development), so the
script exits 1 when a bundle file is over that, unless --allow-large.

The manifest's grade_source says what made the grades (method, model, the
tags the student ships, the model's sha256, and regex v0.2's counts), so the
Databricks notebook can apply the same model and compare regex and student
counts separately (apply_student, below).

  .venv/bin/python scripts/wp12_bundle.py
  .venv/bin/python scripts/wp12_bundle.py --out /tmp/vf_bundle --no-copy-vf
  .venv/bin/python scripts/wp12_bundle.py --stage /tmp/vf_app       # build, then a deployable copy of dbapp/
  .venv/bin/python scripts/wp12_bundle.py --check                   # before a deploy: dbapp/vf == src/vf?
  .venv/bin/python scripts/wp12_bundle.py --apply-student data/models/wp4_student/student_1b.pkl --apply-out /tmp/g
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.util
import inspect
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import pandas as pd

from vf import geo, paths, recipes

BUNDLE_VERSION = "wp12-bundle-v1"
SCRIPT = "scripts/wp12_bundle.py"

#: Databricks Apps: "App files can't exceed 10 MB" (app-development docs, Nov 21 2026).
APP_FILE_LIMIT_BYTES = 10 * 1024 * 1024
#: Contract: commit the bundle only under about 50 MB and with no raw text dumps.
COMMIT_LIMIT_BYTES = 50 * 1024 * 1024

#: Decision (WP12): the bundle is built at deploy time, not committed. Measured
#: Nov 21: 12.7 MB without the keyword index, which the contract alone would
#: allow. Not committed because the final bundle carries the keyword index (every
#: distinct fact text, a text dump the contract excludes; a TF-IDF matrix over
#: the 275,095 distinct fact texts measured 10 to 19 MB as one .npz), because
#: places.parquet alone is about 7 MB and grows with WP2 B, and because the
#: facilities table's reuse terms are unchecked.
COMMIT_BUNDLE = False
COMMIT_WHY = ("the final bundle holds the keyword index (all distinct fact texts, a text dump), "
              "it changes on every upstream rerun, and the facilities table's reuse terms are unchecked")

#: A post office further than this from its PIN-directory district's centre is
#: suspect (same rule as the retired WP5 access table, docs/07 "coord_suspect").
SUSPECT_KM = 200.0

FACILITY_COLUMNS = [
    "facility_id", "name", "facility_type", "operator_type", "lat", "lon",
    "location_precision_km", "location_note", "city_raw", "state_raw", "pin",
    "official_website", "source_domains", "n_source_domains", "source_confidence",
]

#: Notes for the two checks that leave no usable point. The others come from vf.services.
UNUSABLE_NOTES = {
    "excluded_unresolved": "No usable location: the record's location is far from its own PIN code's area "
                           "and the address does not settle which is right.",
    "missing": "No usable location in the record.",
}

#: Fallbacks when vf.services is missing (it holds the real values; these match it on Nov 21).
_FALLBACK_PRECISION_KM = {"ok": 1.0, "ok_same_district": 2.0, "kept_city_matches_coords": 2.0,
                          "no_pin_check": 2.0, "kept_unresolved": 25.0, "moved_to_pin": 10.0}


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def git_info(root: Path) -> dict:
    def run(*args):
        try:
            return subprocess.run(["git", "--no-optional-locks", *args], cwd=root,
                                  capture_output=True, text=True, check=True).stdout.strip()
        except Exception:
            return None
    commit = run("rev-parse", "HEAD")
    dirty = run("status", "--porcelain", "--", "src/vf", SCRIPT) if commit else None
    return {"git_commit": commit or None, "code_uncommitted": (bool(dirty) if dirty is not None else None)}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_meta(path: Path) -> dict:
    m = Path(str(path) + ".meta.json")
    if not m.exists():
        return {}
    try:
        return json.loads(m.read_text())
    except ValueError:
        return {}


def _try_import(name: str):
    if importlib.util.find_spec(name) is None:
        return None, f"{name} does not exist yet"
    try:
        return importlib.import_module(name), None
    except Exception as e:  # a half-written module from another agent
        return None, f"{name} failed to import: {e!r}"


def swap_dir(tmp: Path, dest: Path) -> None:
    """Put tmp at dest. The old folder is moved aside first, then removed."""
    old = dest.with_name(f".{dest.name}.old-{os.getpid()}")
    if dest.exists():
        os.replace(dest, old)
    try:
        os.replace(tmp, dest)
    except OSError:
        # some mounted file systems (Databricks workspace files) refuse directory renames
        shutil.copytree(tmp, dest)
        shutil.rmtree(tmp, ignore_errors=True)
    shutil.rmtree(old, ignore_errors=True)


def mb(n: int) -> str:
    return f"{n / 1024 / 1024:.1f} MB"


# --------------------------------------------------------------------------
# parts
# --------------------------------------------------------------------------

def services_defaults() -> tuple[dict, Optional[str]]:
    svc, err = _try_import("vf.services")
    if svc is None:
        return {"source": None, "weights": None, "radii_km": None}, err
    out = {"source": "vf.services"}
    for key, attr in (("weights", "WEIGHTS"), ("radii_km", "RADII_KM"), ("penalties", "PENALTIES"),
                      ("grade_evidence", "GRADE_EVIDENCE"), ("strength_adjust", "STRENGTH_ADJUST"),
                      ("reach_factor", "REACH_FACTOR"), ("max_candidates", "MAX_CANDIDATES"),
                      ("location_precision_km", "LOCATION_PRECISION_KM")):
        v = getattr(svc, attr, None)
        out[key] = ({str(k): v2 for k, v2 in v.items()} if isinstance(v, dict) else v)
    out["service_lines"] = list(getattr(svc, "SERVICE_LINES", ()) or ())
    return out, None


def pin_precision(places: Optional[pd.DataFrame]) -> dict:
    """PIN -> precision_km from the gazetteer's pin rows, when the gazetteer exists."""
    if places is None or not {"kind", "pin", "precision_km"} <= set(places.columns):
        return {}
    p = places[(places["kind"] == "pin") & places["pin"].notna()]
    return dict(zip(p["pin"].astype(str), p["precision_km"].astype(float)))


def build_facilities(fac: pd.DataFrame, places: Optional[pd.DataFrame]) -> tuple[pd.DataFrame, dict]:
    svc, _ = _try_import("vf.services")
    precision = dict(getattr(svc, "LOCATION_PRECISION_KM", None) or _FALLBACK_PRECISION_KM)
    notes = dict(getattr(svc, "LOCATION_NOTES", None) or {})
    pin_prec = pin_precision(places)

    out = pd.DataFrame({c: fac[c] for c in FACILITY_COLUMNS if c in fac.columns})
    check = fac["coord_check"].astype(str)
    usable = fac["lat"].notna() & fac["lon"].notna()

    prec = check.map(precision).astype("float64")
    moved = check == "moved_to_pin"
    from_places = moved & fac["pin"].astype(object).map(lambda p: p in pin_prec if isinstance(p, str) else False)
    prec[from_places] = fac.loc[from_places, "pin"].map(pin_prec).astype("float64")
    # a kept point that conflicts with its PIN is good to no better than the conflict itself
    kept = check == "kept_unresolved"
    if "coord_pin_km" in fac:
        prec[kept] = np.fmax(prec[kept], fac.loc[kept, "coord_pin_km"].astype("float64"))
    prec[~usable] = np.nan
    out["location_precision_km"] = prec.round(1)

    note = check.map(notes).astype(object)
    note = note.where(note.notna(), check.map(UNUSABLE_NOTES))
    out["location_note"] = note.where(note.notna(), None)

    out["source_confidence"] = [
        recipes.source_confidence(n, w, d, s)
        for n, w, d, s in zip(fac["n_source_domains"], fac["official_website"],
                              fac["page_updated"], fac["staff_named"])
    ]
    out = out[FACILITY_COLUMNS].reset_index(drop=True)
    stats = {
        "with_location": int(usable.sum()),
        "without_location": int((~usable).sum()),
        "by_coord_check": {k: int(v) for k, v in check.value_counts().items()},
        "moved_to_pin_precision_from_places": int(from_places.sum()),
        "moved_to_pin_precision_default": int((moved & ~from_places & usable).sum()),
        "unknown_coord_check_values": sorted(set(check[usable]) - set(precision)),
    }
    return out, stats


def wp2b_office_flags(data: Path, root: Path) -> tuple[Optional[pd.DataFrame], Optional[str]]:
    """WP2 B's own office flags (scripts/wp2_places.py load_offices), so the
    bundle's post offices and the gazetteer share one suspect and placeholder
    set. Returns (frame with po_id, coord_suspect, coord_placeholder, or None; why)."""
    script = root / "scripts" / "wp2_places.py"
    if not script.exists():
        return None, "scripts/wp2_places.py not found"
    old = os.environ.get("VF_DATA_DIR")
    os.environ["VF_DATA_DIR"] = str(data)
    try:
        spec = importlib.util.spec_from_file_location("_wp2_places", script)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        offices, _ = mod.load_offices()
        return offices[["po_id", "coord_suspect", "coord_placeholder"]], None
    except Exception as e:
        return None, f"wp2_places.load_offices failed: {e!r}"
    finally:
        if old is None:
            os.environ.pop("VF_DATA_DIR", None)
        else:
            os.environ["VF_DATA_DIR"] = old


def build_post_offices(po: pd.DataFrame, flags: Optional[pd.DataFrame] = None) -> tuple[pd.DataFrame, dict]:
    """Non-suspect geocoded offices, one row per placeholder point.

    The suspect and placeholder flags come, in order of preference, from
    columns in silver/post_offices.parquet, from `flags` (WP2 B's
    load_offices, matched on po_id; offices it drops for having no district
    are dropped here too), or are computed here: suspect is over SUSPECT_KM
    from its PIN-directory district's centre (vf.geo.group_centres: head and
    sub offices), placeholder is vf.geo.placeholder_points.
    """
    geoc = po[po["coord_valid"].astype(bool) & po["lat"].notna() & po["lon"].notna()].reset_index(drop=True)
    if {"coord_suspect", "coord_placeholder"} <= set(geoc.columns):
        rule = "columns in silver/post_offices.parquet"
    elif flags is not None:
        geoc = geoc.merge(flags, on="po_id", how="inner", validate="one_to_one")
        rule = "scripts/wp2_places.py load_offices (the gazetteer's own flags)"
    else:
        group = geo.pin_district_group(geoc["state_pin"], geoc["district_pin"]).to_numpy()
        is_town = (geoc["office_type"].astype(str) != "BO").to_numpy()
        centres = geo.group_centres(geoc["lat"], geoc["lon"], is_town, group)
        c = centres.reindex(group)
        d = geo.haversine_km(geoc["lat"].to_numpy(), geoc["lon"].to_numpy(),
                             c["lat"].to_numpy(dtype=float), c["lon"].to_numpy(dtype=float))
        geoc["coord_suspect"] = np.nan_to_num(d, nan=0.0) > SUSPECT_KM
        geoc["coord_placeholder"] = geo.placeholder_points(geoc["lat"], geoc["lon"], geoc["pin"])["placeholder"].to_numpy()
        rule = (f"computed here: over {SUSPECT_KM:.0f} km from the PIN-directory district centre; "
                "vf.geo.placeholder_points")
    suspect = geoc["coord_suspect"].astype(bool).to_numpy()
    ph = geoc["coord_placeholder"].astype(bool).to_numpy()

    keep = geoc[~suspect].copy()
    keep["placeholder"] = ph[~suspect]
    normal = keep[~keep["placeholder"]]
    pts = keep[keep["placeholder"]]
    if len(pts):
        pts = pts.groupby(["lat", "lon"], as_index=False).agg(n_offices=("pin", "size"))
        pts["pin"] = None
        pts["placeholder"] = True
    else:
        pts = pd.DataFrame(columns=["lat", "lon", "pin", "placeholder", "n_offices"])
    normal = normal.assign(n_offices=1)[["lat", "lon", "pin", "placeholder", "n_offices"]]
    out = pd.concat([normal, pts[["lat", "lon", "pin", "placeholder", "n_offices"]]], ignore_index=True)
    out["lat"] = out["lat"].astype("float64")
    out["lon"] = out["lon"].astype("float64")
    out["pin"] = out["pin"].astype(object).where(out["pin"].notna(), None)
    out["placeholder"] = out["placeholder"].astype(bool)
    out["n_offices"] = out["n_offices"].astype("int64")
    stats = {
        "geocoded_offices": int(len(geoc)),
        "suspect_dropped": int(suspect.sum()),
        "flags_from": rule,
        "placeholder_offices": int(keep["placeholder"].sum()),
        "placeholder_points": int(len(pts)),
        "rows": int(len(out)),
    }
    return out, stats


def build_keyword_index(dest: Path, facts: Callable[[], pd.DataFrame],
                        facilities: Callable[[], pd.DataFrame]) -> tuple[Optional[dict], Optional[str]]:
    """Call vf.rank.build_keyword_index when it exists. Returns (info, skip reason).

    The function's parameters are matched by name: an output folder
    (out_dir, dest, path, bundle_dir, index_dir), and the facts and facilities
    frames (facts, facilities). If it returns an object with .save, it is saved
    into dest.
    """
    rank, err = _try_import("vf.rank")
    if rank is None:
        return None, err
    fn = getattr(rank, "build_keyword_index", None)
    if fn is None:
        return None, "vf.rank has no build_keyword_index yet"
    kwargs, wrote_itself = {}, False
    for name, p in inspect.signature(fn).parameters.items():
        if name in ("out_dir", "dest", "path", "index_dir", "out"):
            kwargs[name] = dest
            wrote_itself = True
        elif name == "bundle_dir":
            kwargs[name] = dest.parent
            wrote_itself = True
        elif name in ("facts", "facts_df"):
            kwargs[name] = facts()
        elif name in ("facilities", "facilities_df", "fac"):
            kwargs[name] = facilities()
        elif p.default is inspect.Parameter.empty and p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            return None, f"vf.rank.build_keyword_index needs {name!r}, which this script does not know how to pass"
    dest.mkdir(parents=True, exist_ok=True)
    result = fn(**kwargs)
    if not wrote_itself:
        if result is not None and hasattr(result, "save"):
            result.save(dest)
        else:
            return None, "vf.rank.build_keyword_index took no output folder and returned nothing savable"
    files = [p for p in dest.rglob("*") if p.is_file()]
    if not files:
        return None, "vf.rank.build_keyword_index wrote no files"
    # same tables, smaller files: the Apps limit is 10 MB per file
    for f in files:
        if f.suffix == ".parquet":
            t = pd.read_parquet(f)
            t.to_parquet(f.with_suffix(".tmp"), index=False, compression="zstd")
            os.replace(f.with_suffix(".tmp"), f)
    return {"files": sorted(str(p.relative_to(dest)) for p in files),
            "version": getattr(rank, "KEYWORD_INDEX_VERSION", None)}, None


#: Pipeline outputs whose row counts go in the manifest, so a Databricks run can
#: be compared with the local one table by table.
UPSTREAM_TABLES = (
    "silver/facilities.parquet", "silver/facts.parquet", "silver/post_offices.parquet",
    "silver/pin_crosswalk.parquet", "silver/places.parquet", "gold/fact_tags.parquet",
    "gold/capability_grades.parquet", "gold/facility_flags.parquet", "gold/facility_duplicates.parquet",
    "gold/service_probability.parquet",
)


def upstream_row_counts(data: Path) -> dict:
    import pyarrow.parquet as pq

    out = {}
    for rel in UPSTREAM_TABLES:
        p = data / rel
        out[rel] = pq.ParquetFile(p).metadata.num_rows if p.exists() else None
    return out


# --------------------------------------------------------------------------
# the bundle
# --------------------------------------------------------------------------

def _write_parquet(df: pd.DataFrame, path: Path) -> None:
    # zstd: about 20% smaller than the default snappy on places.parquet, which sits near the 10 MB app limit
    df.to_parquet(path, index=False, compression="zstd")


def build_bundle(out: Path, data_dir: Optional[Path] = None, code_root: Optional[Path] = None,
                 with_keyword_index: bool = True, log: Callable[[str], None] = print) -> dict:
    """Write the bundle to `out` (swapped in whole) and return the manifest."""
    data = Path(data_dir) if data_dir else paths.data_dir()
    root = Path(code_root) if code_root else paths.code_root()
    silver, gold = data / "silver", data / "gold"
    out = Path(out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f".{out.name}.tmp-", dir=out.parent))

    files: dict = {}
    missing: dict = {}
    counts: dict = {}

    def add(name: str, df: pd.DataFrame, src: Optional[Path], **extra) -> None:
        _write_parquet(df, tmp / name)
        rel = str(src.relative_to(data)) if src is not None and src.is_relative_to(data) else (str(src) if src else None)
        meta = read_meta(src) if src is not None else {}
        files[name] = {"rows": int(len(df)), "from": rel,
                       "input_created_at_utc": meta.get("created_at_utc"),
                       "input_git_commit": meta.get("git_commit"), **extra}
        counts[name.removesuffix(".parquet")] = int(len(df))
        log(f"  {name:<28} {len(df):>9,} rows")

    try:
        # required: facilities and grades
        fac_path, grades_path = silver / "facilities.parquet", gold / "capability_grades.parquet"
        for p in (fac_path, grades_path):
            if not p.exists():
                raise FileNotFoundError(f"{p} is required for the bundle and is missing. Run WP1 and WP3 first.")
        places_path = silver / "places.parquet"
        places = pd.read_parquet(places_path) if places_path.exists() else None

        fac_silver = pd.read_parquet(fac_path)
        fac, fac_stats = build_facilities(fac_silver, places)
        add("facilities.parquet", fac, fac_path, stats=fac_stats,
            recipe_version=read_meta(fac_path).get("recipe_version"))

        grades = pd.read_parquet(grades_path)
        add("grades.parquet", grades, grades_path)
        lines = sorted(grades["service_line"].unique())
        grade_counts = {
            line: {str(k): int(v) for k, v in g["grade"].value_counts().sort_index().items()}
            for line, g in grades.groupby("service_line")
        }
        defaults, svc_err = services_defaults()
        expected_lines = defaults.get("service_lines") or []
        lines_missing = [x for x in expected_lines if x not in lines]
        if lines_missing:
            log(f"  note: grades cover {len(lines)} of {len(expected_lines)} service lines; "
                f"missing {', '.join(lines_missing)} (WP3 v0.2 not written yet?)")

        # optional parts
        for name, src, owner in (
            ("flags.parquet", gold / "facility_flags.parquet", "WP11"),
            ("duplicates.parquet", gold / "facility_duplicates.parquet", "WP11"),
            ("places.parquet", places_path, "WP2 B"),
            ("service_probability.parquet", gold / "service_probability.parquet", "WP13"),
        ):
            if src.exists():
                df = places if name == "places.parquet" else pd.read_parquet(src)
                add(name, df, src)
            else:
                missing[name] = f"{src.relative_to(data)} not found ({owner} has not written it yet); skipped"
                log(f"  skip {name:<23} {missing[name]}")

        po_path = silver / "post_offices.parquet"
        if po_path.exists():
            po_raw = pd.read_parquet(po_path)
            flags, why = (None, "silver has the flags") if "coord_suspect" in po_raw else wp2b_office_flags(data, root)
            if flags is None and "coord_suspect" not in po_raw:
                log(f"  note: post office flags computed here ({why})")
            po, po_stats = build_post_offices(po_raw, flags)
            add("post_offices.parquet", po, po_path, stats=po_stats)
        else:
            missing["post_offices.parquet"] = "silver/post_offices.parquet not found (WP2); skipped"
            log(f"  skip post_offices.parquet     {missing['post_offices.parquet']}")

        if with_keyword_index:
            facts_path = silver / "facts.parquet"
            info, why = build_keyword_index(
                tmp / "keyword_index",
                facts=lambda: pd.read_parquet(facts_path),
                facilities=lambda: fac_silver,
            )
        else:
            info, why = None, "disabled with --no-keyword-index"
        if info is None:
            shutil.rmtree(tmp / "keyword_index", ignore_errors=True)
            missing["keyword_index/"] = f"{why}; skipped (the app's search fallback needs it)"
            log(f"  skip keyword_index/          {missing['keyword_index/']}")
        else:
            files["keyword_index/"] = info
            log(f"  keyword_index/               {len(info['files'])} files")

        # sizes, per file and total
        sizes = {str(p.relative_to(tmp)): p.stat().st_size for p in sorted(tmp.rglob("*")) if p.is_file()}
        for name, info in files.items():
            if name in sizes:
                info["bytes"] = sizes[name]
                info["sha256"] = sha256(tmp / name)
        total = sum(sizes.values())
        over = {k: v for k, v in sizes.items() if v > APP_FILE_LIMIT_BYTES}
        has_text_dump = "keyword_index/" in files
        rule_allows_now = total < COMMIT_LIMIT_BYTES and not has_text_dump
        decision = {
            "committed": COMMIT_BUNDLE,
            "policy": "built at deploy time by scripts/wp12_bundle.py; only manifest.json is versioned",
            "rule": "docs/07: commit only under about 50 MB and with no raw text dump beyond quoted evidence",
            "rule_allows_commit_of_this_build": rule_allows_now,
            "total_bytes": total,
            "keyword_index_present": has_text_dump,
            "why": COMMIT_WHY,
        }
        if not COMMIT_BUNDLE:
            (tmp / ".gitignore").write_text(
                "# Written by scripts/wp12_bundle.py: the bundle is built at deploy time, not committed.\n"
                "# Only the manifest is versioned, as the local reference for the Databricks run.\n"
                "*\n!.gitignore\n!manifest.json\n")

        git = git_info(root)
        manifest = {
            "bundle_version": BUNDLE_VERSION,
            "created_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            **git,
            "script": SCRIPT,
            # no absolute path: the manifest is versioned
            "data_dir": ("VF_DATA_DIR" if os.environ.get("VF_DATA_DIR") else "vf.paths.data_dir()"),
            "pipeline_source": os.environ.get("VF_SOURCE", "csv"),
            "row_counts": counts,
            "upstream_row_counts": upstream_row_counts(data),
            "grade_counts": grade_counts,
            "grade_source": grade_source(data),
            "service_lines": lines,
            "service_lines_missing": lines_missing,
            "versions": {
                "silver_facilities": read_meta(fac_path).get("recipe_version"),
                "recipes": sorted(grades["recipe_version"].astype(str).unique()) if "recipe_version" in grades else None,
                "grade_method": sorted(grades["method"].astype(str).unique()) if "method" in grades else None,
                "checks": (sorted(pd.read_parquet(tmp / "flags.parquet")["check_version"].astype(str).unique())
                           if (tmp / "flags.parquet").exists() else None),
                "places": read_meta(places_path).get("recipe_version") if places is not None else None,
                "probability_model": (sorted(pd.read_parquet(tmp / "service_probability.parquet")["model_version"]
                                             .astype(str).unique())
                                      if (tmp / "service_probability.parquet").exists() else None),
            },
            "defaults": defaults,
            "defaults_note": svc_err,
            "files": files,
            "missing": missing,
            "sizes": {"total_bytes": total, "largest": max(sizes.items(), key=lambda kv: kv[1]) if sizes else None,
                      "app_file_limit_bytes": APP_FILE_LIMIT_BYTES, "over_app_file_limit": over},
            "commit_decision": decision,
        }
        (tmp / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str) + "\n")
        swap_dir(tmp, out)
        return manifest
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


# --------------------------------------------------------------------------
# the grades' source, and WP4's student applied without retraining
# --------------------------------------------------------------------------

#: Decision (WP12 fix round): on Databricks the notebook applies WP4's saved
#: student model (data/models/wp4_student/<name>.pkl, trained locally by
#: scripts/wp4_student.py) to the Databricks silver facts, instead of
#: retraining. Retraining needs the teacher labels and their cache (git-ignored
#: LLM output) and could pick other C values and thresholds; applying the same
#: fitted model tests what the comparison is about, whether Databricks makes the
#: same silver facts and grades.
APPLY_STEP = "scripts/wp12_bundle.py apply_student"


def _model_file(meta: dict) -> Optional[str]:
    """The model file named in a fact_tags meta: 'data/models/x.pkl (what it is)' -> 'data/models/x.pkl'."""
    if meta.get("student_model_path"):
        return meta["student_model_path"]
    s = meta.get("student")  # wp4_student.py: one string, path first
    return s.split(" ")[0] if isinstance(s, str) and s.strip() else None


def _resolve_model(name: Optional[str], data: Path) -> Optional[Path]:
    if not name:
        return None
    p = Path(name)
    for c in ([p] if p.is_absolute() else [data / name.removeprefix("data/"), paths.code_root() / name]):
        if c.exists():
            return c
    return None


def grade_source(data: Path) -> dict:
    """What made gold/capability_grades: method, model, shipped tags, and regex v0.2's counts
    when the grades are not regex (from the meta sidecars WP3, WP4 or apply_student write)."""
    gm = read_meta(data / "gold" / "capability_grades.parquet")
    tm = read_meta(data / "gold" / "fact_tags.parquet")
    name = _model_file(tm)
    model = _resolve_model(name, data)
    regex = gm.get("facilities_per_grade_regex_v0_2")
    labels = list(recipes.GRADE_LABELS)
    return {
        "script": gm.get("script"),
        "method": gm.get("method"),
        "model_version": gm.get("model_version"),
        "tags_from_student": gm.get("tags_from_student") or [],
        "student_model": name,
        "student_model_sha256": sha256(model) if model is not None else None,
        "regex_grade_counts": ({line: {str(labels.index(k)): int(v) for k, v in d.items()}
                                for line, d in regex.items()} if isinstance(regex, dict) else None),
    }


class _DataDirEnv:
    """Point vf.paths at `data` for the duration (the WP3 helpers read paths.silver_dir())."""

    def __init__(self, data: Path):
        self.data, self.old = str(data), None

    def __enter__(self):
        self.old = os.environ.get("VF_DATA_DIR")
        os.environ["VF_DATA_DIR"] = self.data

    def __exit__(self, *exc):
        if self.old is None:
            os.environ.pop("VF_DATA_DIR", None)
        else:
            os.environ["VF_DATA_DIR"] = self.old


def _load_wp3(root: Path):
    spec = importlib.util.spec_from_file_location("_wp3_regex_baseline", root / "scripts" / "wp3_regex_baseline.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def student_predict(blob: dict, texts: list[str], chunk: int = 20000) -> np.ndarray:
    """Tags per text from a WP4 student blob, as its 'usage' note says (bool, texts x blob['tags'])."""
    import scipy.sparse as sp

    tags = list(blob["tags"])
    out = []
    for i in range(0, len(texts), chunk):
        t = texts[i:i + chunk]
        parts = [blob["word_vectorizer"].transform(t), blob["char_vectorizer"].transform(t)]
        if blob["use_regex_features"]:
            rx = recipes.tag_facts(t, blob["regex_recipe_version"])
            R = np.array([[x in set(s) for x in tags] for s in rx], dtype=np.float64) * blob["regex_feature_weight"]
            parts.append(sp.csr_matrix(R))
        X = sp.hstack(parts, format="csr")
        P = np.zeros((X.shape[0], len(tags)), dtype=bool)
        for j, tag in enumerate(tags):
            m = blob["models"].get(tag)
            if m is not None:
                P[:, j] = m.predict_proba(X)[:, 1] >= blob["thresholds"][tag]
        out.append(P)
    return np.vstack(out) if out else np.zeros((0, len(tags)), dtype=bool)


def apply_student(model_path: Path, ship: list[str], data_dir: Optional[Path] = None,
                  out_dir: Optional[Path] = None, model_version: Optional[str] = None,
                  code_root: Optional[Path] = None, log: Callable[[str], None] = print) -> dict:
    """Regrade with WP4's saved student on the tags it ships, regex v0.2 on the rest.

    Same steps as scripts/wp4_student.py after training: tag every distinct
    fact text, take the student's tags for `ship` and the regex's for the
    others, regrade with the WP3 rules, and write fact_tags.parquet and
    capability_grades.parquet (method = student) with meta sidecars into
    out_dir (default: gold/ under data_dir). Returns a summary.
    """
    import pickle

    data = Path(data_dir) if data_dir else paths.data_dir()
    root = Path(code_root) if code_root else paths.code_root()
    out = Path(out_dir) if out_dir else data / "gold"
    model_path = Path(model_path)
    with open(model_path, "rb") as fh:
        blob = pickle.load(fh)
    tags = list(recipes.EVIDENCE_TAGS)
    if list(blob["tags"]) != tags:
        raise ValueError(f"{model_path.name} was trained on other tags than vf.recipes.EVIDENCE_TAGS")
    if blob["regex_recipe_version"] != recipes.RECIPE_VERSION:
        raise ValueError(f"{model_path.name} uses regex {blob['regex_recipe_version']}, "
                         f"vf.recipes is {recipes.RECIPE_VERSION}")
    unknown = sorted(set(ship) - set(tags))
    if unknown or not ship:
        raise ValueError(f"ship tags must be a non-empty subset of the evidence tags; unknown {unknown}")
    version = recipes.RECIPE_VERSION
    mv = model_version or f"student:s1-{blob['name'].split('_')[-1]}[{','.join(ship)}]+regex:{version}[rest]"

    RB = _load_wp3(root)
    with _DataDirEnv(data):
        fac, facts = RB.load_inputs()
    distinct = facts["text_norm"].unique().tolist()
    regex_lookup = {t: recipes.tag_fact(t) for t in distinct}
    P = student_predict(blob, distinct)
    ship_idx = [tags.index(t) for t in ship]
    stud = {txt: {tags[j] for j in ship_idx if P[i, j]} for i, txt in enumerate(distinct)}
    keep_rx = set(tags) - set(ship)
    mix = {t: [x for x in tags if (x in keep_rx and x in regex_lookup[t]) or x in stud[t]] for t in distinct}
    facts["tags"] = facts["text_norm"].map(mix)
    changed = sum(set(regex_lookup[t]) != set(mix[t]) for t in distinct)

    grades = RB.build_grades(fac, RB.facts_by_facility(facts, facts["tags"]), version)
    grades["method"] = "student"
    grades["recipe_version"] = version
    fact_tags = pd.DataFrame({"fact_id": facts["fact_id"], "facility_id": facts["facility_id"],
                              "tags": facts["tags"], "method": "student", "model_version": mv})
    counts = {line: {lab: int((g["grade"] == i).sum()) for i, lab in enumerate(recipes.GRADE_LABELS)}
              for line, g in grades.groupby("service_line")}
    try:
        model_rel = str(model_path.resolve().relative_to(data.resolve()))
        model_rel = "data/" + model_rel
    except ValueError:
        model_rel = str(model_path)
    meta = {"created_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            **git_info(root), "script": APPLY_STEP, "recipe_version": version, "model_version": mv,
            "method": "student", "tags_from_student": list(ship),
            "student_model_path": model_rel,
            "student_model": f"{model_rel} (trained by scripts/wp4_student.py, applied here without retraining)",
            "inputs": {"silver/facts.parquet": {"sha256": sha256(data / "silver" / "facts.parquet")},
                       "student_model": {"sha256": sha256(model_path)}}}
    RB.write_table(fact_tags, RB.FACT_TAGS_SCHEMA, out / "fact_tags.parquet",
                   dict(meta, output="gold/fact_tags.parquet",
                        tags_from_regex=[t for t in tags if t not in ship],
                        distinct_texts_whose_tags_differ_from_regex=int(changed)))
    RB.write_table(grades, RB.GRADES_SCHEMA, out / "capability_grades.parquet",
                   dict(meta, output="gold/capability_grades.parquet", grade_rules=RB.rules_description(version),
                        description_as_fact_source=False, facilities_per_grade=counts))
    log(f"  student {blob['name']} on {ship}: {len(distinct):,} distinct texts, {changed:,} change tags; "
        f"wrote {len(fact_tags):,} fact tags and {len(grades):,} grade rows to {out}")
    return {"model_version": mv, "distinct_texts": len(distinct), "texts_changed": int(changed),
            "rows": int(len(grades)), "facilities_per_grade": counts, "model_sha256": meta["inputs"]["student_model"]["sha256"]}


# --------------------------------------------------------------------------
# deploy guards: drift between src/vf and dbapp/vf, and a deployable copy
# --------------------------------------------------------------------------

_VF_SKIP = {"GENERATED.txt"}


def vf_drift(src: Path, dest: Path) -> list[str]:
    """Files that differ between src/vf and its copy (missing, extra or changed), ignoring caches."""
    def files(d: Path) -> dict:
        if not d.exists():
            return {}
        return {str(p.relative_to(d)): p for p in d.rglob("*")
                if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
                and p.name not in _VF_SKIP and p.name != ".DS_Store"}
    a, b = files(Path(src)), files(Path(dest))
    out = [f"missing in copy: {k}" for k in sorted(set(a) - set(b))]
    out += [f"only in copy: {k}" for k in sorted(set(b) - set(a))]
    out += [f"differs: {k}" for k in sorted(set(a) & set(b)) if a[k].read_bytes() != b[k].read_bytes()]
    return out


#: Never part of a deployed app: git metadata and caches. .gitignore files are
#: left out so the question of whether `databricks apps deploy` reads them
#: (its docs do not say) cannot come up.
STAGE_IGNORE = (".gitignore", "__pycache__", "*.pyc", ".DS_Store", "manifest.local.json")


def stage_app(dbapp: Path, dest: Path) -> int:
    """Copy dbapp/ to dest (swapped in whole) without STAGE_IGNORE. Returns the file count."""
    dest = Path(dest).resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f".{dest.name}.tmp-", dir=dest.parent))
    os.rmdir(tmp)
    shutil.copytree(dbapp, tmp, ignore=shutil.ignore_patterns(*STAGE_IGNORE))
    swap_dir(tmp, dest)
    return sum(1 for p in dest.rglob("*") if p.is_file())


def copy_vf(src: Path, dest: Path) -> int:
    """Copy src/vf to dest (swapped in whole), without caches. Returns the file count."""
    dest = Path(dest).resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f".{dest.name}.tmp-", dir=dest.parent))
    os.rmdir(tmp)
    shutil.copytree(src, tmp, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"))
    (tmp / "GENERATED.txt").write_text(
        "Generated copy of src/vf by scripts/wp12_bundle.py. Do not edit here; edit src/vf and rerun.\n")
    swap_dir(tmp, dest)
    return sum(1 for p in dest.rglob("*.py"))


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=None, help="bundle folder (default dbapp/bundle in this checkout)")
    ap.add_argument("--no-copy-vf", action="store_true", help="do not copy src/vf to dbapp/vf")
    ap.add_argument("--no-keyword-index", action="store_true", help="skip the keyword index")
    ap.add_argument("--allow-large", action="store_true", help="do not fail on files over the 10 MB app limit")
    ap.add_argument("--stage", type=Path, default=None,
                    help="after the build, copy dbapp/ here without .gitignore files and caches (deploy from it)")
    ap.add_argument("--check", action="store_true",
                    help="build nothing; exit 1 if dbapp/vf differs from src/vf or the bundle is older than gold")
    ap.add_argument("--apply-student", type=Path, default=None, metavar="PKL",
                    help="build nothing; regrade with this WP4 student model into --apply-out")
    ap.add_argument("--apply-out", type=Path, default=None, help="output folder for --apply-student (required)")
    ap.add_argument("--ship", default=None,
                    help="comma-separated tags the student ships (default: tags_from_student in gold's meta)")
    args = ap.parse_args(argv)

    root = paths.code_root()
    if args.apply_student:
        if args.apply_out is None:
            ap.error("--apply-student needs --apply-out (gold/ belongs to WP4; write somewhere else)")
        src = grade_source(paths.data_dir())
        ship = args.ship.split(",") if args.ship else src["tags_from_student"]
        r = apply_student(args.apply_student, ship, out_dir=args.apply_out,
                          model_version=None if args.ship else src["model_version"])
        print("Summary")
        print(f"  model_version {r['model_version']}; {r['texts_changed']:,} of {r['distinct_texts']:,} texts change tags")
        for line, d in r["facilities_per_grade"].items():
            print(f"    {line:<16} " + "  ".join(f"{k}={v:,}" for k, v in d.items()))
        return 0
    if args.check:
        return check(root)
    out = args.out or (root / "dbapp" / "bundle")
    print(f"WP12 bundle -> {out}")
    print(f"  data from {paths.data_dir()}")
    m = build_bundle(out, with_keyword_index=not args.no_keyword_index)
    if not args.no_copy_vf:
        n = copy_vf(root / "src" / "vf", root / "dbapp" / "vf")
        print(f"  copied src/vf -> dbapp/vf ({n} .py files)")

    s = m["sizes"]
    print("Summary")
    print(f"  total {mb(s['total_bytes'])}; largest {s['largest'][0]} {mb(s['largest'][1])}")
    print(f"  grades: {len(m['service_lines'])} service lines; missing lines: {m['service_lines_missing'] or 'none'}")
    for line, gc in m["grade_counts"].items():
        print(f"    {line:<16} " + "  ".join(f"g{k}={v:,}" for k, v in gc.items()))
    print(f"  skipped parts: {', '.join(m['missing']) or 'none'}")
    d = m["commit_decision"]
    print(f"  commit decision: {'commit dbapp/bundle' if d['committed'] else 'built at deploy time, manifest.json versioned'}"
          f" (size rule alone would {'allow' if d['rule_allows_commit_of_this_build'] else 'refuse'} committing this build)")
    print(f"  git commit {m['git_commit']} (uncommitted code: {m['code_uncommitted']})")
    if s["over_app_file_limit"]:
        print("  ERROR: files over the 10 MB Databricks Apps limit: "
              + ", ".join(f"{k} {mb(v)}" for k, v in s["over_app_file_limit"].items()))
        if not args.allow_large:
            return 1
    if args.stage:
        if args.out is not None or args.no_copy_vf:
            ap.error("--stage copies dbapp/ as built; use it without --out and --no-copy-vf")
        n = stage_app(root / "dbapp", args.stage)
        print(f"  staged dbapp/ -> {args.stage} ({n} files, no .gitignore); deploy from there")
    return 0


def check(root: Path) -> int:
    """Deploy-time guard: dbapp/vf equals src/vf, and the bundle was built from the current gold grades."""
    problems = vf_drift(root / "src" / "vf", root / "dbapp" / "vf")
    for p in problems:
        print(f"  vf: {p}")
    mpath = root / "dbapp" / "bundle" / "manifest.json"
    if not mpath.exists():
        problems.append("no bundle")
        print("  bundle: dbapp/bundle/manifest.json missing")
    else:
        m = json.loads(mpath.read_text())
        built = m["files"].get("grades.parquet", {}).get("input_created_at_utc")
        now_ = read_meta(paths.gold_dir() / "capability_grades.parquet").get("created_at_utc")
        if now_ and built != now_:
            problems.append("bundle grades stale")
            print(f"  bundle: grades built from gold of {built}, gold is now {now_}")
        if not (root / "dbapp" / "bundle" / "grades.parquet").exists():
            problems.append("bundle files missing")
            print("  bundle: manifest present but grades.parquet missing (rebuild)")
    print("check: " + ("ok, dbapp/ matches src/vf and gold" if not problems
                       else f"{len(problems)} problem(s): rerun scripts/wp12_bundle.py"))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
