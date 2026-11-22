# Databricks notebook source
# MAGIC %md
# MAGIC # Care finder pipeline on Databricks (WP12)
# MAGIC
# MAGIC Runs the same pipeline code as the local checkout, against the Marketplace tables:
# MAGIC
# MAGIC 1. Checks the Marketplace tables against the local CSV export (row counts, columns, `null` cells, NUL bytes).
# MAGIC 2. Reads them with Spark into pandas through `vf.io` (`source="databricks"`), so the NUL-byte and `null` handling is the tested code.
# MAGIC 3. Runs WP1 (silver facilities and facts), WP2 Part A (post offices) and Part B (places), WP3 (regex tags and grades), WP11 (trust checks), and the WP12 bundle.
# MAGIC 4. Prints the Databricks counts next to the local run's (from the committed `dbapp/bundle/manifest.json`).
# MAGIC 5. Writes Delta tables `facilities_silver`, `facts_silver`, `capability_grades`, `facility_flags`, `places`, and the app bundle into `<repo>/dbapp/bundle`.
# MAGIC
# MAGIC Free Edition: serverless only, so packages come in with `%pip`. The pipeline calls no external API.
# MAGIC
# MAGIC **Before Run all:** set the widgets at the top. The defaults are the catalog and schema the listing landed in on Nov 21 (`data/raw/MANIFEST.json`) and the Free Edition default catalog `workspace` for outputs. See `dbapp/README.md`.

# COMMAND ----------

# MAGIC %pip install rapidfuzz==3.14.6 --quiet

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

dbutils.widgets.text("source_catalog", "databricks_virtue_foundation_dataset_dais_2026", "1 Marketplace catalog")
dbutils.widgets.text("source_schema", "virtue_foundation_dataset", "2 Marketplace schema")
dbutils.widgets.text("output_catalog", "workspace", "3 Output catalog")
dbutils.widgets.text("output_schema", "vf_datathon", "4 Output schema")
dbutils.widgets.text("repo_path", "", "5 Repo folder (blank: this notebook's parent folder)")
dbutils.widgets.text("steps", "check,wp1,wp2a,wp2b,wp3,wp11,bundle,delta", "6 Steps")

# COMMAND ----------

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

SOURCE_SCHEMA = f"{dbutils.widgets.get('source_catalog').strip()}.{dbutils.widgets.get('source_schema').strip()}"
OUT_CATALOG = dbutils.widgets.get("output_catalog").strip()
OUT_SCHEMA = dbutils.widgets.get("output_schema").strip()
STEPS = [s.strip() for s in dbutils.widgets.get("steps").split(",") if s.strip()]


def _repo_from_notebook() -> Path:
    ctx = dbutils.notebook.entry_point.getDbutils().notebook().getContext()
    nb = ctx.notebookPath().get()                      # /Users/<me>/<repo>/notebooks/01_pipeline
    return Path("/Workspace" + nb).parent.parent


REPO = Path(dbutils.widgets.get("repo_path").strip() or _repo_from_notebook())
assert (REPO / "src" / "vf" / "io.py").exists(), f"vf package not found under {REPO}; set the repo_path widget"

# The pipeline works on local disk (fast, and renames are atomic there); results are copied out at the end.
WORK = Path(tempfile.gettempdir()) / "vf_pipeline"
DATA = WORK / "data"
for sub in ("raw", "silver", "gold", "labels", "external"):
    (DATA / sub).mkdir(parents=True, exist_ok=True)
os.environ["VF_DATA_DIR"] = str(DATA)
os.environ["VF_SOURCE"] = "databricks"
os.environ["VF_DATABRICKS_SCHEMA"] = SOURCE_SCHEMA

# hand-made label files in the repo (the gold set, resolver cases): WP3 and WP2 B score against them
for f in sorted((REPO / "data" / "labels").glob("*.csv")):
    shutil.copy(f, DATA / "labels" / f.name)
    print("label file  ", f.name)

sys.path.insert(0, str(REPO / "src"))
from vf import io, paths  # noqa: E402

io.use_source("databricks", schema=SOURCE_SCHEMA, reader=io.spark_table_reader(spark))

import numpy as np, pandas as pd, pyarrow, sklearn  # noqa: E401,E402
print("repo        ", REPO)
print("source      ", SOURCE_SCHEMA, "->", [io.table_name(k) for k in io.RAW_FILES])
print("work dir    ", DATA)
print("output      ", f"{OUT_CATALOG}.{OUT_SCHEMA}")
print("versions    ", f"python {sys.version.split()[0]}, pandas {pd.__version__}, numpy {np.__version__}, "
      f"pyarrow {pyarrow.__version__}, scikit-learn {sklearn.__version__}")
print("  (local run: python 3.12, pandas 3.1.1, numpy 2.5.5, pyarrow 26.0.1, scikit-learn 1.9.1)")
print("steps       ", STEPS)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 1: do the Marketplace tables match the local CSV export?
# MAGIC
# MAGIC The local numbers were measured on `data/raw` on Nov 21. In the CSV export a SQL NULL and the string `null` look the same, so the table's two are added up for the comparison.

# COMMAND ----------

from pyspark.sql import functions as F  # noqa: E402

LOCAL_RAW = {
    "facilities": {"rows": 10088, "columns": 51, "null_cells": 103723, "rows_with_nul": 2,
                   "row_status": {"ok": 9970, "truncated": 30, "junk": 88}},
    "pincodes": {"rows": 165627, "columns": 11, "null_cells": 0, "rows_with_nul": 0,
                 "na_cells": {"latitude": 12007, "longitude": 12002, "district": 715, "statename": 715,
                              "regionname": 315}},
    "nfhs": {"rows": 706, "columns": 109, "null_cells": 0, "rows_with_nul": 0,
             "star_cells": 4125, "paren_cells": 5068},
}
LOCAL_COLUMNS = {
    "facilities": ["unique_id", "source_types", "source_ids", "source_content_id", "name", "organization_type",
                   "content_table_id", "phone_numbers", "officialPhone", "email", "websites", "officialWebsite",
                   "yearEstablished", "acceptsVolunteers", "facebookLink", "address_line1", "address_line2",
                   "address_line3", "address_city", "address_stateOrRegion", "address_zipOrPostcode",
                   "address_country", "address_countryCode", "countries", "facilityTypeId", "operatorTypeId",
                   "affiliationTypeIds", "description", "area", "numberDoctors", "capacity", "specialties",
                   "procedure", "equipment", "capability", "recency_of_page_update",
                   "distinct_social_media_presence_count", "affiliated_staff_presence", "custom_logo_presence",
                   "number_of_facts_about_the_organization", "post_metrics_most_recent_social_media_post_date",
                   "post_metrics_post_count", "engagement_metrics_n_followers", "engagement_metrics_n_likes",
                   "engagement_metrics_n_engagements", "source", "coordinates", "latitude", "longitude",
                   "cluster_id", "source_urls"],
    "pincodes": ["circlename", "regionname", "divisionname", "officename", "pincode", "officetype", "delivery",
                 "district", "statename", "latitude", "longitude"],
}


def _count_if(cond):
    return F.when(cond, 1).otherwise(0)


def table_profile(kind: str) -> dict:
    """Row-level equivalents of the file checksums, in one Spark job per table."""
    sdf = spark.table(io.table_name(kind))
    names = sdf.columns
    s = [F.col(f"`{c}`").cast("string") for c in names]
    aggs = [
        F.count(F.lit(1)).alias("rows"),
        F.sum(sum(_count_if(c.isNull()) for c in s)).alias("sql_nulls"),
        F.sum(sum(_count_if(c == "null") for c in s)).alias("null_strings"),
        F.sum(_count_if(F.greatest(*[F.coalesce(F.instr(c, "\x00"), F.lit(0)) for c in s]) > 0)).alias("rows_with_nul"),
    ]
    if kind == "pincodes":
        for c in LOCAL_RAW["pincodes"]["na_cells"]:
            col = s[names.index(c)]
            aggs += [F.sum(_count_if(col == "NA")).alias(f"na__{c}"), F.sum(_count_if(col.isNull())).alias(f"nn__{c}")]
    if kind == "nfhs":
        aggs += [F.sum(sum(_count_if(F.trim(c) == "*") for c in s)).alias("star_cells"),
                 F.sum(sum(_count_if(c.startswith("(")) for c in s)).alias("paren_cells")]
    r = sdf.agg(*aggs).first().asDict()
    out = {"rows": r["rows"], "columns": len(names), "column_names": names,
           "types": {f.name: f.dataType.simpleString() for f in sdf.schema.fields if f.dataType.simpleString() != "string"},
           "sql_nulls": r["sql_nulls"], "null_strings": r["null_strings"],
           "null_cells": r["sql_nulls"] + r["null_strings"], "rows_with_nul": r["rows_with_nul"]}
    if kind == "pincodes":
        out["na_cells"] = {c: r[f"na__{c}"] for c in LOCAL_RAW["pincodes"]["na_cells"]}
        out["null_cells_in_na_columns"] = {c: r[f"nn__{c}"] for c in LOCAL_RAW["pincodes"]["na_cells"]}
    if kind == "nfhs":
        out["star_cells"], out["paren_cells"] = r["star_cells"], r["paren_cells"]
    return out


def side_by_side(rows):
    w = max(len(r[0]) for r in rows)
    print(f"{'measure':<{w}}  {'local':>12}  {'databricks':>12}  same")
    for name, a, b in rows:
        print(f"{name:<{w}}  {str(a):>12}  {str(b):>12}  {'yes' if a == b else 'NO'}")


CHECK = {}
if "check" in STEPS:
    for kind in io.RAW_FILES:
        p = table_profile(kind)
        CHECK[kind] = p
        loc = LOCAL_RAW[kind]
        print(f"\n== {io.table_name(kind)}")
        rows = [("rows", loc["rows"], p["rows"]), ("columns", loc["columns"], p["columns"]),
                ("null cells (SQL NULL + 'null')", loc["null_cells"], p["null_cells"]),
                ("rows with NUL bytes", loc["rows_with_nul"], p["rows_with_nul"])]
        for k, v in loc.get("na_cells", {}).items():
            rows.append((f"'NA' cells in {k}", v, p["na_cells"][k]))
        for k in ("star_cells", "paren_cells"):
            if k in loc:
                rows.append((k, loc[k], p[k]))
        side_by_side(rows)
        print(f"  of the nulls: {p['sql_nulls']:,} SQL NULL, {p['null_strings']:,} the string 'null'")
        if p["types"]:
            print("  non-string columns (cast to string on read):", p["types"])
        if kind in LOCAL_COLUMNS and p["column_names"] != LOCAL_COLUMNS[kind]:
            print("  COLUMN NAMES DIFFER:",
                  "missing", sorted(set(LOCAL_COLUMNS[kind]) - set(p["column_names"])),
                  "extra", sorted(set(p["column_names"]) - set(LOCAL_COLUMNS[kind])),
                  "order differs" if set(p["column_names"]) == set(LOCAL_COLUMNS[kind]) else "")
        if kind == "pincodes":
            print("  SQL NULL in the 'NA' columns:", p["null_cells_in_na_columns"],
                  "(the loaders treat 'NA' and 'null' alike for coordinates)")

    fac = io.read_facilities_raw()
    status = io.facility_row_status(fac).value_counts().to_dict()
    print("\n== facilities through vf.io (normalized)")
    side_by_side([(f"row_status {k}", v, int(status.get(k, 0))) for k, v in LOCAL_RAW["facilities"]["row_status"].items()]
                 + [("unique_id values seen twice", 11,
                     int((fac.loc[fac['address_countryCode'] == 'IN', 'unique_id'].value_counts() == 2).sum()))])

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 2: run the pipeline scripts
# MAGIC
# MAGIC Each script is loaded as a module and its `main()` is called in this Python process, so it reads through the same `vf.io` (Databricks source). A script's file checksum of `data/raw` would fail here because there are no files: that one helper is wrapped to record the table name instead.

# COMMAND ----------

PIPELINE = [
    ("wp1", ["scripts/wp1_silver_facilities.py"]),
    ("wp2a", ["scripts/wp2_geography_nfhs.py"]),
    ("wp2b", ["scripts/wp2_places.py"]),
    ("wp3", ["scripts/wp3_regex_baseline.py"]),
    ("wp11", ["scripts/wp11_trust_checks.py", "scripts/wp11_trust.py", "scripts/wp11_flags.py"]),
]
RAW_TO_TABLE = {str(io.raw_path(k)): io.table_name(k) for k in io.RAW_FILES}


def load_script(path: Path):
    spec = importlib.util.spec_from_file_location(f"vf_step_{path.stem}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_step(step: str, candidates: list[str]) -> dict:
    found = [REPO / c for c in candidates if (REPO / c).exists()]
    if step == "wp11" and not found:
        found = sorted((REPO / "scripts").glob("wp11_*.py"))[:1]
    if not found:
        print(f"[{step}] SKIPPED: none of {candidates} exists in the repo yet")
        return {"step": step, "status": "skipped"}
    path = found[0]
    print(f"[{step}] {path.relative_to(REPO)}")
    mod = load_script(path)
    if hasattr(mod, "sha256"):
        real = mod.sha256

        def sha256(p, _real=real):
            p = Path(p)
            if p.exists():
                return _real(p)
            return f"no file: read from Databricks table {RAW_TO_TABLE.get(str(p), '?')}"

        mod.sha256 = sha256
    argv, sys.argv = sys.argv, [str(path)]
    t0 = time.time()
    try:
        rc = mod.main()
    except SystemExit as e:
        rc = e.code
    finally:
        sys.argv = argv
    status = "ok" if rc in (0, None) else f"exit {rc}"
    print(f"[{step}] {status} in {time.time() - t0:.0f} s\n")
    return {"step": step, "status": status, "script": str(path.relative_to(REPO))}


RESULTS = [run_step(s, c) for s, c in PIPELINE if s in STEPS]
for r in RESULTS:
    print(f"{r['step']:<6} {r['status']:<10} {r.get('script', '')}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 3: the app bundle, and the counts next to the local run

# COMMAND ----------

BUNDLE_LOCAL_TMP = WORK / "bundle"
REPO_BUNDLE = REPO / "dbapp" / "bundle"
LOCAL_MANIFEST = None
if (REPO_BUNDLE / "manifest.json").exists():
    LOCAL_MANIFEST = json.loads((REPO_BUNDLE / "manifest.json").read_text())

M = None
if "bundle" in STEPS:
    wp12 = load_script(REPO / "scripts" / "wp12_bundle.py")
    M = wp12.build_bundle(BUNDLE_LOCAL_TMP, data_dir=DATA, code_root=REPO)
    over = M["sizes"]["over_app_file_limit"]
    if over:
        print("WARNING: files over the 10 MB Databricks Apps limit; the deploy will fail until they are split:", over)
    # publish into the repo folder the app deploys from (workspace files: copy, not rename)
    if LOCAL_MANIFEST is not None:
        (WORK / "manifest.local.json").write_text(json.dumps(LOCAL_MANIFEST, indent=2))
    shutil.rmtree(REPO_BUNDLE, ignore_errors=True)
    shutil.copytree(BUNDLE_LOCAL_TMP, REPO_BUNDLE)
    if LOCAL_MANIFEST is not None:
        (REPO_BUNDLE / "manifest.local.json").write_text(json.dumps(LOCAL_MANIFEST, indent=2))
    shutil.rmtree(REPO / "dbapp" / "vf", ignore_errors=True)
    shutil.copytree(REPO / "src" / "vf", REPO / "dbapp" / "vf",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    print(f"bundle written to {REPO_BUNDLE} ({M['sizes']['total_bytes'] / 1e6:.1f} MB); vf copied to {REPO / 'dbapp' / 'vf'}")

if M is not None and LOCAL_MANIFEST is not None:
    L = LOCAL_MANIFEST
    print(f"local run: {L.get('created_at_utc')} commit {L.get('git_commit')}; databricks run: {M['created_at_utc']}")
    rows = [(f"bundle {k}", L["row_counts"].get(k), v) for k, v in M["row_counts"].items()]
    rows += [(k, L.get("upstream_row_counts", {}).get(k), v) for k, v in M["upstream_row_counts"].items()]
    for line in sorted(set(L["grade_counts"]) | set(M["grade_counts"])):
        for g in ("0", "1", "2", "3"):
            rows.append((f"{line} grade {g}", L["grade_counts"].get(line, {}).get(g), M["grade_counts"].get(line, {}).get(g)))
    side_by_side(rows)
    n_diff = sum(a != b for _, a, b in rows)
    print(f"\n{n_diff} of {len(rows)} counts differ" + ("" if n_diff == 0 else ": every difference is a bug to explain"))
elif M is not None:
    print("No local manifest at dbapp/bundle/manifest.json in the repo folder, so no side-by-side. Upload it (see dbapp/README.md).")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 4: Delta tables
# MAGIC
# MAGIC The parquet outputs are staged in a Unity Catalog volume and saved as managed tables, so they can be browsed and used by a Genie space.

# COMMAND ----------

DELTA = {
    "facilities_silver": "silver/facilities.parquet",
    "facts_silver": "silver/facts.parquet",
    "capability_grades": "gold/capability_grades.parquet",
    "facility_flags": "gold/facility_flags.parquet",
    "places": "silver/places.parquet",
}
if "delta" in STEPS:
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS `{OUT_CATALOG}`.`{OUT_SCHEMA}`")
    spark.sql(f"CREATE VOLUME IF NOT EXISTS `{OUT_CATALOG}`.`{OUT_SCHEMA}`.`pipeline`")
    stage = Path(f"/Volumes/{OUT_CATALOG}/{OUT_SCHEMA}/pipeline/run")
    stage.mkdir(parents=True, exist_ok=True)
    for table, rel in DELTA.items():
        src = DATA / rel
        if not src.exists():
            print(f"{table:<20} SKIPPED: {rel} was not produced")
            continue
        dst = stage / src.name
        shutil.copy(src, dst)
        fqn = f"`{OUT_CATALOG}`.`{OUT_SCHEMA}`.`{table}`"
        (spark.read.parquet(str(dst)).write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(fqn))
        n = spark.table(fqn).count()
        print(f"{table:<20} {n:>9,} rows -> {OUT_CATALOG}.{OUT_SCHEMA}.{table}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Summary

# COMMAND ----------

print("source:", SOURCE_SCHEMA)
for r in RESULTS:
    print(f"  {r['step']:<6} {r['status']}")
if M is not None:
    print("bundle:", {k: v for k, v in M["row_counts"].items()}, "missing:", list(M["missing"]))
    print("next: deploy the app from", REPO / "dbapp", "(dbapp/README.md, section 'Deploy')")
