#!/usr/bin/env python
"""WP4b promotion (D059): install the candidate fact tags and regrade recipe v0.2.

D050 froze WP4b's output as a candidate file. D059 records that one (candidate,
tag) pair passed the declared ship rule (ob_delivery with student 2b_r2) and that
the promotion happens in the final assembly. This script does exactly that:

1. Check gold/fact_tags_candidate_wp4b.parquet is the reviewed file (sha256
   1bc6faf5..., as decision.json and reports/model_a_wp4b.md record) and that its
   schema and fact_id order equal gold/fact_tags.parquet.
2. Back up the current tags and grades under data/models/wp4b/
   (fact_tags_pre_wp4b.parquet, capability_grades_pre_wp4b.parquet,
   capability_grades_v03_pre_wp4b.parquet, facility_flags_pre_wp4b.parquet,
   each with its .meta.json).
3. Install the candidate as gold/fact_tags.parquet (new meta sidecar, status
   promoted).
4. Regrade recipe v0.2 from the promoted tags with the same code WP4 used
   (scripts/wp3_regex_baseline.py build_grades) into gold/capability_grades.parquet,
   and print the change against the backed-up v0.2 grades, with the grade 2 gate.

Then run scripts/wp3_regrade_v03.py (v0.3) and scripts/wp11_trust_checks.py.

  .venv/bin/python scripts/wp4b_promote.py
  .venv/bin/python scripts/wp4b_promote.py --no-write    # checks and the v0.2 diff only
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from vf import paths, recipes

SCRIPT = "scripts/wp4b_promote.py"
EXPECTED_SHA = "1bc6faf5da8df74ece73edcc50a1bb95d3dcb029770f7fd1a1f84f0b8645f982"
GATE_LINES = ("emoc", "dialysis", "icu", "trauma", "neurosurgery")


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, paths.code_root() / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def counts(g: pd.DataFrame) -> dict:
    out = {}
    for line, d in g.groupby("service_line"):
        vc = d["grade"].value_counts()
        out[line] = {str(k): int(vc.get(k, 0)) for k in range(4)}
    return out


def diff(before: pd.DataFrame, after: pd.DataFrame) -> dict:
    m = before[["facility_id", "service_line", "grade"]].merge(
        after[["facility_id", "service_line", "grade"]], on=["facility_id", "service_line"],
        suffixes=("_before", "_after"), validate="one_to_one")
    ch = m[m.grade_before != m.grade_after]
    per = {}
    for line, d in ch.groupby("service_line"):
        per[line] = {
            "changed": int(len(d)),
            "transitions": {f"{a}->{b}": int(n) for (a, b), n in d.groupby(["grade_before", "grade_after"]).size().items()},
            "cross_gate_up": int(((d.grade_before < 2) & (d.grade_after >= 2)).sum()),
            "cross_gate_down": int(((d.grade_before >= 2) & (d.grade_after < 2)).sum()),
        }
    gate = int(((ch.grade_before >= 2) != (ch.grade_after >= 2)).sum())
    return {"rows": int(len(m)), "changed": int(len(ch)), "per_line": per, "grade2_gate_crossings_all_lines": gate}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args(argv)
    t0 = time.time()
    gold = paths.gold_dir()
    cand, cur = gold / "fact_tags_candidate_wp4b.parquet", gold / "fact_tags.parquet"
    g02_path, g03_path, flags_path = (gold / "capability_grades.parquet", gold / "capability_grades_v03.parquet",
                                      gold / "facility_flags.parquet")
    backup = paths.data_dir() / "models" / "wp4b"
    checks = []

    def check(name, ok, value=""):
        checks.append((name, bool(ok), value))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {value}")

    cand_sha = sha256(cand)
    cur_sha = sha256(cur)
    print(f"WP4b promotion (D059): {cand.name} -> {cur.name}")
    check("candidate sha256 is the reviewed file", cand_sha == EXPECTED_SHA, cand_sha[:16])
    already = cur_sha == EXPECTED_SHA
    if already:
        print("  gold/fact_tags.parquet already is the candidate; the backup is kept as it is")
    ct, ft = pd.read_parquet(cand), pd.read_parquet(cur)
    check("same columns", list(ct.columns) == list(ft.columns), list(ct.columns))
    check("same rows and fact_id order", len(ct) == len(ft) and bool((ct.fact_id.values == ft.fact_id.values).all()),
          f"{len(ct):,}")
    cmeta = json.loads((gold / "fact_tags_candidate_wp4b.parquet.meta.json").read_text())
    mv = cmeta["model_version"]
    check("candidate model_version names 2b_r2 for ob_delivery", "wp4b-2b_r2[ob_delivery]" in mv, mv)
    if not already:
        n_diff = int(sum(set(a) != set(b) for a, b in zip(ct.tags, ft.tags)))
        check("rows whose tags differ equal the WP4b record (1,625)", n_diff == cmeta["rows_whose_tags_differ_from_gold_fact_tags"],
              f"{n_diff:,}")
    if any(not ok for _, ok, _ in checks):
        print("a check failed; nothing written")
        return 1

    RB = _load("wp3_regex_baseline")
    fac, facts = RB.load_inputs()
    aligned = bool((ct.fact_id.values == facts.fact_id.values).all())
    check("candidate rows align with silver/facts", aligned, f"{len(facts):,}")
    tags = ct["tags"].map(lambda x: list(x) if x is not None else [])
    g02_new = RB.build_grades(fac, RB.facts_by_facility(facts, tags), "v0.2")
    g02_new["method"] = "student"
    g02_new["recipe_version"] = recipes.RECIPE_VERSION
    old_g02 = pd.read_parquet(backup / "capability_grades_pre_wp4b.parquet") if already and \
        (backup / "capability_grades_pre_wp4b.parquet").exists() else pd.read_parquet(g02_path)
    d02 = diff(old_g02, g02_new)
    print(f"  v0.2 regrade: {d02['changed']} of {d02['rows']:,} facility-lines change; "
          f"grade 2 gate crossings (all lines) {d02['grade2_gate_crossings_all_lines']}")
    for line, d in d02["per_line"].items():
        print(f"    {line:16s} {d}")
    if args.no_write:
        print("  --no-write: nothing written")
        return 0

    if not already:
        paths.ensure(backup)
        for src, name in ((cur, "fact_tags_pre_wp4b.parquet"), (g02_path, "capability_grades_pre_wp4b.parquet"),
                          (g03_path, "capability_grades_v03_pre_wp4b.parquet"),
                          (flags_path, "facility_flags_pre_wp4b.parquet")):
            shutil.copy2(src, backup / name)
            sm = src.with_name(src.name + ".meta.json")
            if sm.exists():
                shutil.copy2(sm, backup / (name + ".meta.json"))
        check("backup of fact_tags equals the replaced file", sha256(backup / "fact_tags_pre_wp4b.parquet") == cur_sha,
              cur_sha[:16])

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    git = RB.git_info()
    if not already:
        tmp = cur.with_name(f".{cur.name}.tmp")
        shutil.copy2(cand, tmp)
        tmp.replace(cur)
        meta = {**{k: v for k, v in cmeta.items() if k not in ("output", "status")},
                "output": "gold/fact_tags.parquet",
                "status": "promoted (D059): the WP4b candidate installed by the final assembly",
                "promoted_at_utc": now, "promoted_by": SCRIPT, **{f"promote_{k}": v for k, v in git.items()},
                "promoted_from": {"file": "gold/fact_tags_candidate_wp4b.parquet", "sha256": cand_sha},
                "replaced": {"file": "gold/fact_tags.parquet", "sha256": cur_sha,
                             "model_version": json.loads((backup / "fact_tags_pre_wp4b.parquet.meta.json").read_text())
                             .get("model_version"),
                             "backup": "data/models/wp4b/fact_tags_pre_wp4b.parquet"},
                # vf.rank / wp12 grade_source read these keys (WP4 meta layout)
                "tags_from_student": ["ob_delivery", "h24", "icu", "ventilator"],
                "tags_from_regex": cmeta["tags_by_method"]["regex:v0.2"],
                "student": "data/models/wp4_student/student_1b.pkl (h24, icu, ventilator); ob_delivery from "
                           "the fine-tuned bge-small encoder 2b_r2 (data/models/wp4b_encoder/r2_2b)"}
        RB.write_atomic(cur.with_name(cur.name + ".meta.json"),
                        lambda p: p.write_text(json.dumps(meta, indent=2, default=str) + "\n"))
    check("gold/fact_tags.parquet sha256 is the candidate's", sha256(cur) == EXPECTED_SHA, sha256(cur)[:16])

    gmeta = {
        "output": "gold/capability_grades.parquet", "created_at_utc": now, **git, "script": SCRIPT,
        "recipe_version": recipes.RECIPE_VERSION, "model_version": mv, "method": "student",
        "tags_from_student": ["ob_delivery", "h24", "icu", "ventilator"],
        "decision": "D059 (WP4b promotion, final assembly)",
        "grade_rules": RB.rules_description("v0.2"), "description_as_fact_source": False,
        "inputs": {"gold/fact_tags.parquet": {"sha256": sha256(cur)},
                   "silver/facts.parquet": {"sha256": RB.sha256(paths.silver_dir() / "facts.parquet")},
                   "silver/facilities.parquet": {"sha256": RB.sha256(paths.silver_dir() / "facilities.parquet")}},
        "facilities_per_grade": counts(g02_new),
        "changes_vs_pre_wp4b": d02,
        "pre_wp4b_backup": "data/models/wp4b/capability_grades_pre_wp4b.parquet",
    }
    old_meta_p = backup / "capability_grades_pre_wp4b.parquet.meta.json"
    if old_meta_p.exists():
        om = json.loads(old_meta_p.read_text())
        if om.get("facilities_per_grade_regex_v0_2"):
            gmeta["facilities_per_grade_regex_v0_2"] = om["facilities_per_grade_regex_v0_2"]
    RB.write_table(g02_new, RB.GRADES_SCHEMA, g02_path, gmeta)
    print(f"  wrote gold/capability_grades.parquet ({len(g02_new):,} rows) in {time.time() - t0:.0f}s")
    print("  next: scripts/wp3_regrade_v03.py, then scripts/wp11_trust_checks.py")
    return 1 if any(not ok for _, ok, _ in checks) else 0


if __name__ == "__main__":
    sys.exit(main())
