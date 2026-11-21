#!/usr/bin/env python3
"""Check that data/raw is present and matches the export recorded in MANIFEST.json.

Run this first in every new session:

    python scripts/verify_data.py

Exit code 0 means the three raw files parse and match the expected shape.
A checksum difference alone is only a warning (a re-export can reorder rows);
a shape difference is an error.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from vf import io, paths  # noqa: E402


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    raw = paths.raw_dir()
    manifest_path = raw / "MANIFEST.json"
    print(f"data dir : {paths.data_dir()}")
    if not manifest_path.exists():
        print(f"ERROR: {manifest_path} not found")
        return 2
    manifest = json.loads(manifest_path.read_text())
    errors, warnings = [], []

    def check(label, actual, expected):
        ok = actual == expected
        print(f"  {'ok ' if ok else 'BAD'} {label}: {actual}" + ("" if ok else f" (expected {expected})"))
        if not ok:
            errors.append(f"{label}: got {actual}, expected {expected}")

    for name, spec in manifest["files"].items():
        path = raw / name
        print(f"\n{name}")
        if not path.exists():
            errors.append(f"{name} is missing")
            print("  BAD file missing")
            continue
        digest = sha256(path)
        if digest != spec["sha256"]:
            warnings.append(f"{name}: sha256 differs from the manifest (re-export?)")
            print("  warn sha256 differs from manifest")
        else:
            print("  ok  sha256")

    # facilities
    spec = manifest["files"]["facilities.csv"]["expect"]
    if (raw / "facilities.csv").exists():
        print("\nfacilities.csv contents")
        df = io.read_facilities_raw()
        status = io.facility_row_status(df).value_counts().to_dict()
        check("rows", len(df), spec["rows"])
        check("columns", df.shape[1], spec["columns"])
        check("ok rows", status.get("ok", 0), spec["ok_rows"])
        check("truncated rows", status.get("truncated", 0), spec["truncated_rows"])
        check("junk rows", status.get("junk", 0), spec["junk_rows"])

    # pincodes
    spec = manifest["files"]["india_post_pincode_directory.csv"]["expect"]
    if (raw / "india_post_pincode_directory.csv").exists():
        print("\nindia_post_pincode_directory.csv contents")
        df = io.read_pincodes_raw()
        check("rows", len(df), spec["rows"])
        check("columns", df.shape[1], spec["columns"])
        check("distinct pincodes", df["pincode"].nunique(), spec["distinct_pincodes"])

    # nfhs
    spec = manifest["files"]["nfhs5_district_health_indicators.csv"]["expect"]
    if (raw / "nfhs5_district_health_indicators.csv").exists():
        print("\nnfhs5_district_health_indicators.csv contents")
        df = io.read_nfhs_raw()
        check("rows", len(df), spec["rows"])
        check("columns", df.shape[1], spec["columns"])

    print()
    for w in warnings:
        print(f"WARNING: {w}")
    for e in errors:
        print(f"ERROR: {e}")
    if errors:
        return 1
    print("raw data verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
