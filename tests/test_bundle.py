"""WP12: the Databricks source path in vf.io, and the app bundle (scripts/wp12_bundle.py)."""
from __future__ import annotations

import importlib.util
import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from vf import io, paths

ROOT = Path(__file__).resolve().parents[1]


def _load_bundle_script():
    spec = importlib.util.spec_from_file_location("wp12_bundle", ROOT / "scripts" / "wp12_bundle.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _reset_source(monkeypatch):
    monkeypatch.delenv(io.SOURCE_ENV, raising=False)
    monkeypatch.delenv(io.DATABRICKS_SCHEMA_ENV, raising=False)
    io.use_source()
    yield
    io.use_source()


# --------------------------------------------------------------------------
# vf.io databricks path
# --------------------------------------------------------------------------

COLS = ["unique_id", "name", "description", "address_countryCode", "source", "latitude",
        "acceptsVolunteers", "specialties"]

# What the SQL editor's CSV export holds (strings; SQL NULL written as "null").
CSV_ROWS = [
    ["a1", "Alpha Hospital", "Has a NICU", "IN", "web", "28.6139", "true", '["pediatrics","pediatrics"]'],
    ["a2", "Beta Clinic", "Text with a NUL\x00 byte", "IN", "web", "null", "false", "null"],
    ["a3", "Gamma", "cut off", "IN", "null", "null", "null", "null"],        # truncated
    ["frag", "of a description", "null", "null", "null", "null", "null", "null"],  # junk
]


def _write_csv(raw: Path, rows) -> None:
    raw.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows, columns=COLS)
    text = df.to_csv(index=False)
    (raw / io.RAW_FILES["facilities"]).write_bytes(text.encode("utf-8"))


def _spark_shaped(rows) -> pd.DataFrame:
    """The same table as Spark's toPandas() would give it: SQL NULL as None."""
    df = pd.DataFrame(rows, columns=COLS, dtype=object)
    return df.map(lambda v: None if v == "null" else v)


def test_databricks_path_matches_csv_path(tmp_path, monkeypatch):
    monkeypatch.setenv("VF_DATA_DIR", str(tmp_path))
    _write_csv(tmp_path / "raw", CSV_ROWS)
    from_csv = io.read_facilities_raw()
    real_csv = io.read_facilities()

    io.use_source("databricks", schema="cat.sch", reader=lambda fqn: _spark_shaped(CSV_ROWS))
    from_tbl = io.read_facilities_raw()
    real_tbl = io.read_facilities()

    pd.testing.assert_frame_equal(from_tbl, from_csv)
    pd.testing.assert_frame_equal(real_tbl, real_csv)
    assert list(from_tbl.dtypes) == list(from_csv.dtypes)
    assert from_tbl.loc[1, "description"] == "Text with a NUL byte"  # NUL removed on both paths
    assert real_tbl["row_status"].tolist() == ["ok", "ok", "truncated"]
    assert (from_tbl == "null").sum().sum() == (from_csv == "null").sum().sum()


def test_typed_columns_are_spelled_like_the_export():
    # if a reader does not cast to string, numbers, booleans and NaN still come out as strings
    df = pd.DataFrame({"lat": [28.5, np.nan], "flag": [True, False], "n": pd.array([3, None], dtype="Int64"),
                       "s": ["x\x00y", None]})
    out = io.normalize_table_frame(df)
    assert out.to_dict("list") == {"lat": ["28.5", "null"], "flag": ["true", "false"],
                                   "n": ["3", "null"], "s": ["xy", "null"]}
    assert all(pd.api.types.is_string_dtype(t) for t in out.dtypes)


def test_table_names_and_switches(monkeypatch):
    assert io.current_source() == "csv"
    assert io.table_name("facilities") == io.DEFAULT_DATABRICKS_SCHEMA + ".facilities"
    assert io.table_name("pincodes").endswith(".india_post_pincode_directory")
    assert io.table_name("nfhs").endswith(".nfhs_5_district_health_indicators")
    monkeypatch.setenv(io.DATABRICKS_SCHEMA_ENV, "workspace.vf")
    assert io.table_name("facilities") == "workspace.vf.facilities"
    io.use_source("databricks", schema="c.s")
    assert io.table_name("facilities") == "c.s.facilities"
    io.use_source("csv")
    monkeypatch.setenv(io.SOURCE_ENV, "databricks")
    assert io.current_source() == "csv"  # an explicit choice wins over the env var
    io.use_source()
    assert io.current_source() == "databricks"
    monkeypatch.setenv(io.SOURCE_ENV, "parquet")
    with pytest.raises(ValueError):
        io.current_source()
    with pytest.raises(ValueError):
        io.use_source("s3")


def test_tables_are_read_once_and_copied():
    calls = []

    def reader(fqn):
        calls.append(fqn)
        return _spark_shaped(CSV_ROWS)

    io.use_source("databricks", schema="c.s", reader=reader)
    a = io.read_facilities_raw()
    a.loc[0, "name"] = "changed"
    b = io.read_facilities_raw()
    assert calls == ["c.s.facilities"]
    assert b.loc[0, "name"] == "Alpha Hospital"


@pytest.mark.skipif(not io.raw_path("facilities").exists(), reason="data/raw not present")
def test_real_tables_round_trip():
    # the full raw tables, with "null" turned back into SQL NULL as Spark returns them
    for read in (io.read_facilities_raw, io.read_pincodes_raw):
        csv = read()
        spark_like = csv.astype(object).where(csv != "null", None)
        pd.testing.assert_frame_equal(io.normalize_table_frame(spark_like), csv)


# --------------------------------------------------------------------------
# the bundle
# --------------------------------------------------------------------------

def _synthetic_data(d: Path) -> None:
    (d / "silver").mkdir(parents=True)
    (d / "gold").mkdir(parents=True)
    checks = ["ok", "moved_to_pin", "kept_unresolved", "excluded_unresolved", "missing", "no_pin_check"]
    n = len(checks)
    fac = pd.DataFrame({
        "facility_id": [f"f{i}" for i in range(n)],
        "name": [f"Facility {i}" for i in range(n)],
        "facility_type": ["hospital"] * n,
        "operator_type": ["private"] * n,
        "lat": [26.9, 26.8, 26.7, None, None, 26.5],
        "lon": [75.8, 75.7, 75.6, None, None, 75.4],
        "coord_check": checks,
        "coord_pin_km": [1.0, 60.0, 70.0, 140.0, None, None],
        "pin": ["302001", "302002", "302003", "302004", None, None],
        "city_raw": ["Jaipur"] * n,
        "state_raw": ["Rajasthan"] * n,
        "official_website": ["https://a.example", None, None, None, None, None],
        "source_domains": [["a.example", "b.example"]] + [[]] * (n - 1),
        "n_source_domains": [2] + [0] * (n - 1),
        "page_updated": [date(2026, 1, 1)] + [None] * (n - 1),
        "staff_named": [True] + [None] * (n - 1),
        "description": ["long raw text that must not reach the bundle"] * n,
    })
    fac.to_parquet(d / "silver" / "facilities.parquet")
    (d / "silver" / "facilities.parquet.meta.json").write_text(json.dumps({"recipe_version": "wp1-test"}))
    grades = pd.DataFrame({
        "facility_id": [f"f{i}" for i in range(n)] * 2,
        "service_line": ["emoc"] * n + ["dialysis"] * n,
        "grade": [3, 2, 1, 0, 0, 0] * 2,
        "grade_label": ["corroborated", "supported", "claimed", "none", "none", "none"] * 2,
        "method": ["regex"] * (2 * n),
        "recipe_version": ["v0.1"] * (2 * n),
    })
    grades.to_parquet(d / "gold" / "capability_grades.parquet")
    # 12 offices on one point from 3 PINs (a placeholder), 2 ordinary ones, 1 far outlier, 1 not geocoded
    lat = [26.9] * 12 + [26.95, 26.85, 12.0, None]
    lon = [75.8] * 12 + [75.85, 75.75, 77.0, None]
    pins = ["302001"] * 4 + ["302005"] * 4 + ["302006"] * 4 + ["302001", "302002", "302009", "302010"]
    po = pd.DataFrame({
        "po_id": range(len(lat)), "office_name": "x", "office_type": ["SO"] * 3 + ["BO"] * (len(lat) - 3),
        "delivery": "Delivery", "pin": pins, "state_pin": "RAJASTHAN", "district_pin": "JAIPUR",
        "lat": lat, "lon": lon, "coord_valid": [v is not None for v in lat], "coord_swap_repaired": False,
        "district_key": None,
    })
    po.to_parquet(d / "silver" / "post_offices.parquet")


@pytest.mark.parametrize("flags_from", ["wp2b", "computed"])
def test_bundle_from_synthetic_inputs(tmp_path, flags_from):
    wp12 = _load_bundle_script()
    data = tmp_path / "data"
    _synthetic_data(data)
    out = tmp_path / "dbapp" / "bundle"
    out.mkdir(parents=True)
    (out / "stale.parquet").write_text("left over from an old build")
    # with the repo root the WP2 B flags are used; with an empty root the script computes its own
    root = ROOT if flags_from == "wp2b" and (ROOT / "scripts" / "wp2_places.py").exists() else tmp_path

    m = wp12.build_bundle(out, data_dir=data, code_root=root, with_keyword_index=False, log=lambda s: None)
    rule = m["files"]["post_offices.parquet"]["stats"]["flags_from"]
    assert rule.startswith("scripts/wp2_places.py" if root == ROOT else "computed here")

    assert not (out / "stale.parquet").exists()  # swapped in whole
    assert not list(out.parent.glob(".bundle.*"))  # no temp or old folders left behind
    assert m["row_counts"]["facilities"] == 6 and m["row_counts"]["grades"] == 12
    assert m["grade_counts"]["emoc"] == {"0": 3, "1": 1, "2": 1, "3": 1}
    assert set(m["missing"]) == {"flags.parquet", "duplicates.parquet", "places.parquet",
                                 "service_probability.parquet", "keyword_index/"}
    assert m["versions"]["silver_facilities"] == "wp1-test"
    assert json.loads((out / "manifest.json").read_text())["row_counts"] == m["row_counts"]
    assert (out / ".gitignore").exists() and not m["commit_decision"]["committed"]

    fac = pd.read_parquet(out / "facilities.parquet")
    assert list(fac.columns) == wp12.FACILITY_COLUMNS
    assert "description" not in fac.columns
    by = fac.set_index("facility_id")
    assert by.loc["f0", "location_precision_km"] == 1.0
    assert by.loc["f1", "location_precision_km"] == 10.0  # no gazetteer: the vf.services default
    assert by.loc["f2", "location_precision_km"] == 70.0  # no better than its PIN conflict
    assert np.isnan(by.loc["f3", "location_precision_km"]) and np.isnan(by.loc["f3", "lat"])
    assert by.loc["f3", "location_note"].startswith("No usable location")
    assert by[by["lat"].notna()]["location_note"].notna().all()
    assert 0 < by.loc["f0", "source_confidence"] <= 1 and by.loc["f1", "source_confidence"] == 0

    po = pd.read_parquet(out / "post_offices.parquet")
    assert list(po.columns) == ["lat", "lon", "pin", "placeholder", "n_offices"]
    assert po["placeholder"].sum() == 1 and po.loc[po["placeholder"], "n_offices"].item() == 12
    assert (po["lat"] < 20).sum() == 0  # the far outlier is suspect and dropped
    assert len(po) == 3


def test_bundle_uses_gazetteer_precision_and_optional_parts(tmp_path):
    wp12 = _load_bundle_script()
    data = tmp_path / "data"
    _synthetic_data(data)
    pd.DataFrame({"place_id": ["pin:302002"], "kind": ["pin"], "name": ["302002"], "name_norm": ["302002"],
                  "state": ["RAJASTHAN"], "district": ["JAIPUR"], "pin": ["302002"], "lat": [26.8], "lon": [75.7],
                  "precision_km": [4.5], "n_offices": [3], "note": [None]}).to_parquet(data / "silver" / "places.parquet")
    pd.DataFrame({"facility_id": ["f1"], "flag": ["location_conflict"], "service_line": [None], "severity": ["minor"],
                  "signal": ["coord_moved"], "reason": ["r"], "quote": [None], "check_version": ["c0"]}
                 ).to_parquet(data / "gold" / "facility_flags.parquet")
    out = tmp_path / "bundle"
    m = wp12.build_bundle(out, data_dir=data, code_root=ROOT, with_keyword_index=False, log=lambda s: None)
    fac = pd.read_parquet(out / "facilities.parquet").set_index("facility_id")
    assert fac.loc["f1", "location_precision_km"] == 4.5
    assert m["versions"]["checks"] == ["c0"]
    assert "places.parquet" not in m["missing"] and "flags.parquet" not in m["missing"]
    assert (out / "places.parquet").exists() and (out / "flags.parquet").exists()


def test_bundle_fails_without_required_inputs(tmp_path):
    wp12 = _load_bundle_script()
    (tmp_path / "silver").mkdir()
    with pytest.raises(FileNotFoundError):
        wp12.build_bundle(tmp_path / "bundle", data_dir=tmp_path, code_root=ROOT,
                          with_keyword_index=False, log=lambda s: None)
    assert not (tmp_path / "bundle").exists()


@pytest.mark.skipif(not (paths.silver_dir() / "facilities.parquet").exists()
                    or not (paths.gold_dir() / "capability_grades.parquet").exists(),
                    reason="silver and gold outputs not present")
def test_bundle_on_real_outputs(tmp_path):
    wp12 = _load_bundle_script()
    m = wp12.build_bundle(tmp_path / "bundle", with_keyword_index=False, log=lambda s: None)
    fac = pd.read_parquet(tmp_path / "bundle" / "facilities.parquet")
    assert list(fac.columns) == wp12.FACILITY_COLUMNS
    assert fac["facility_id"].is_unique
    assert m["row_counts"]["facilities"] == len(pd.read_parquet(paths.silver_dir() / "facilities.parquet"))
    assert not m["sizes"]["over_app_file_limit"], m["sizes"]
    usable = fac["lat"].notna()
    assert fac.loc[usable, "location_precision_km"].notna().all()
    assert fac.loc[~usable, "location_precision_km"].isna().all()


def test_copy_vf(tmp_path):
    wp12 = _load_bundle_script()
    dest = tmp_path / "vf"
    n = wp12.copy_vf(ROOT / "src" / "vf", dest)
    assert n >= 3 and (dest / "io.py").exists() and (dest / "GENERATED.txt").exists()
    assert not list(dest.rglob("__pycache__"))
