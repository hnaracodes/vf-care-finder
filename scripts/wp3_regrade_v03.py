#!/usr/bin/env python
"""WP3: recipe v0.3, a rule-level regrade of the frozen tags (D048).

Reads the frozen gold/fact_tags.parquet (tags are not recomputed: no regex, no
student) with silver/facts.parquet and silver/facilities.parquet, and writes

  gold/capability_grades_v03.parquet (+ .meta.json)

with the capability_grades schema plus two columns (level_note, level_note_items)
and recipe_version = v0.3. gold/capability_grades.parquet (v0.2) is never
written. The v0.3 rules live in src/vf/recipes.py (GRADE_RULES_V03,
QUALIFIERS_V03) and docs/06_capability_recipes.md ("v0.3").

The run first regrades the same tags under v0.2 in memory and checks that the
result equals gold/capability_grades.parquet cell for cell, so every v0.2 to
v0.3 difference comes from the rule change alone.

  .venv/bin/python scripts/wp3_regrade_v03.py
  .venv/bin/python scripts/wp3_regrade_v03.py --changes-csv <path>   # also dump every changed record
  .venv/bin/python scripts/wp3_regrade_v03.py --no-write              # checks only

Prints a summary and the acceptance checks, and exits 1 if any check fails.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa

from vf import paths, recipes

SCRIPT = "scripts/wp3_regrade_v03.py"
VERSION = recipes.RULE_VERSION_V03
OUT_NAME = "capability_grades_v03.parquet"

#: Prototype counts from scripts/research/score_v1/f2_v03.log (facilities, all records).
PROTOTYPE = {
    ("nicu", "g2+"): 694, ("nicu", "g3"): 113, ("nicu", "sncu_only"): 12,
    ("emoc", "g3"): 59, ("emoc", "fru_note"): 14,
    ("cath_lab", "g3"): 220,
    ("dialysis", "g3"): 456, ("dialysis", "demoted"): 24,
}


def _load_rb():
    spec = importlib.util.spec_from_file_location("wp3_regex_baseline",
                                                  paths.code_root() / "scripts" / "wp3_regex_baseline.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


RB = _load_rb()

GRADES_V03_SCHEMA = pa.schema(list(RB.GRADES_SCHEMA) + [
    pa.field("level_note", pa.string(), nullable=True),
    pa.field("level_note_items", pa.list_(RB.EVIDENCE_ITEM), nullable=False),
])


def build(fac: pd.DataFrame, per_fac: dict, version: str, method: str) -> pd.DataFrame:
    rows = []
    for r in fac.itertuples(index=False):
        conf = recipes.source_confidence(r.n_source_domains, r.official_website, r.page_updated, r.staff_named)
        for lg in recipes.facility_line_grades(per_fac.get(r.facility_id, []), r.specialties, version=version,
                                               n_source_domains=r.n_source_domains,
                                               official_website=r.official_website):
            rows.append({
                "facility_id": r.facility_id, "service_line": lg.service_line, "grade": lg.grade,
                "grade_label": lg.grade_label, "tags_present": lg.tags_present, "evidence": lg.evidence,
                "evidence_items": lg.evidence_items, "missing_for_next": lg.missing_for_next,
                "n_supporting_facts": lg.n_supporting_facts, "evidence_strength": lg.evidence_strength,
                "source_confidence": conf, "method": method, "recipe_version": version,
                "level_note": lg.level_note, "level_note_items": lg.level_note_items,
            })
    out = pd.DataFrame.from_records(rows, columns=GRADES_V03_SCHEMA.names)
    for c in ("grade", "n_supporting_facts"):
        out[c] = out[c].astype("int64")
    out["source_confidence"] = out["source_confidence"].astype("float64")
    return out


def _cell_eq(x, y) -> bool:
    if isinstance(x, (list, np.ndarray)) or isinstance(y, (list, np.ndarray)):
        return len(x) == len(y) and all(_cell_eq(a, b) for a, b in zip(x, y))
    if isinstance(x, dict) and isinstance(y, dict):
        return x.keys() == y.keys() and all(_cell_eq(x[k], y[k]) for k in x)
    if isinstance(x, float) and isinstance(y, float):
        return x == y or (x != x and y != y)
    return x == y


def frame_diff(a: pd.DataFrame, b: pd.DataFrame, cols) -> dict:
    """{column: number of differing cells} for rows in the same order."""
    out = {}
    for c in cols:
        out[c] = int(sum(not _cell_eq(x, y) for x, y in zip(a[c], b[c])))
    return out


def rules_text(version: str) -> dict:
    """Plain rule text per line, with every corroborated option and note."""
    def fmt(groups, names):
        return " and ".join(f"{n} [" + " or ".join(sorted(g)) + "]" for g, n in zip(groups, names))
    out = {}
    for line, rule in recipes.RULES_BY_VERSION[version].items():
        d = {
            "claimed": f"specialty in {sorted(rule.claim_specialties)}"
                       + (f" or {rule.claim_name} [{' or '.join(sorted(rule.claim_tags))}]" if rule.claim_tags else ""),
            "supported": fmt(rule.supported, rule.supported_names),
            "corroborated": " OR ".join(f"({fmt(g, n)})" for g, n in rule.corroborated_options),
            "bonus_tags": list(rule.bonus_tags),
        }
        if rule.has_repeat:
            d["corroborated"] += f", and {rule.repeat_name}"
        if rule.note_groups:
            d["level_note"] = {"when": "grade 3 and " + fmt(rule.note_groups, rule.note_names), "text": rule.note_text}
        out[line] = d
    return out


def counts(g: pd.DataFrame, lines) -> dict:
    t = g[g.service_line.isin(lines)].groupby(["service_line", "grade"]).size().unstack(fill_value=0)
    return {line: {str(k): int(t.loc[line].get(k, 0)) for k in range(4)} for line in lines}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--changes-csv", type=Path, help="write every record whose grade changed, with its facts")
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args(argv)
    t0 = time.time()
    gold = paths.gold_dir()
    tags_path, v02_path = gold / "fact_tags.parquet", gold / "capability_grades.parquet"

    fac, facts = RB.load_inputs()
    ft = pd.read_parquet(tags_path)
    checks = []

    def check(name, passed, value):
        checks.append((name, bool(passed), value))

    aligned = len(ft) == len(facts) and bool((ft["fact_id"].values == facts["fact_id"].values).all())
    check("fact_tags rows align with silver/facts by fact_id", aligned, f"{len(ft):,} rows")
    if not aligned:
        ft = facts[["fact_id"]].merge(ft, on="fact_id", how="left")
    tags = ft["tags"].map(lambda x: list(x) if x is not None else [])
    methods = ft["method"].dropna().unique().tolist()
    method = methods[0] if len(methods) == 1 else "mixed"
    model_version = ft["model_version"].dropna().unique().tolist()
    per_fac = RB.facts_by_facility(facts, tags)

    # 1. v0.2 reproduces the frozen grades
    g02 = build(fac, per_fac, "v0.2", method)
    old = pd.read_parquet(v02_path)
    base_cols = RB.GRADES_SCHEMA.names
    same_shape = len(old) == len(g02) and (old[["facility_id", "service_line"]].values
                                           == g02[["facility_id", "service_line"]].values).all()
    d02 = frame_diff(old.reset_index(drop=True), g02.reset_index(drop=True), base_cols) if same_shape else None
    check("v0.2 regrade of the frozen tags equals gold/capability_grades.parquet, every cell",
          same_shape and not any(d02.values()), "0 differing cells" if same_shape and not any(d02.values())
          else d02)

    # 2. v0.3
    g03 = build(fac, per_fac, VERSION, method)
    unchanged = [l for l in recipes.SERVICE_LINES if l not in recipes.CHANGED_LINES_V03]
    m_un = g02.service_line.isin(unchanged).values
    d_un = frame_diff(g02[m_un].reset_index(drop=True), g03[m_un].reset_index(drop=True),
                      [c for c in base_cols if c != "recipe_version"])
    check("unchanged lines identical between v0.2 and v0.3", not any(d_un.values()), f"{len(unchanged)} lines")
    check("schema: capability_grades columns + level_note, level_note_items",
          list(g03.columns) == GRADES_V03_SCHEMA.names, len(g03.columns))
    check("one row per facility per line", len(g03) == len(fac) * len(recipes.SERVICE_LINES)
          and not g03.duplicated(["facility_id", "service_line"]).any(), f"{len(g03):,}")

    # evidence texts verbatim in silver/facts
    fact_text = dict(zip(facts["fact_id"], facts["text"]))
    bad = 0
    n_items = 0
    for col in ("evidence_items", "level_note_items"):
        for items in g03[col]:
            for it in items:
                n_items += 1
                bad += fact_text.get(it["fact_id"]) != it["text"]
    all_texts = set(facts["text"])
    bad_ev = sum(t not in all_texts for ev in g03["evidence"] for t in ev)
    check("every evidence_items / level_note_items text equals its fact in silver/facts", bad == 0,
          f"{n_items:,} items, {bad} mismatches")
    check("every evidence text is a silver/facts text", bad_ev == 0, f"{bad_ev} not found")
    real = set(recipes.EVIDENCE_TAGS)
    check("tags_present and item tags are real tags (no qualifier names leak)",
          all(set(tp) <= real for tp in g03["tags_present"])
          and all(it["tag"] in real for items in g03["evidence_items"] for it in items), "")
    g3_items_ok = all(len(items) > 0 for gr, items in zip(g03["grade"], g03["evidence_items"]) if gr >= 2)
    check("every grade 2+ record has evidence items", g3_items_ok, "")

    # 3. counts and changes
    lines = recipes.CHANGED_LINES_V03
    c02, c03 = counts(g02, recipes.SERVICE_LINES), counts(g03, recipes.SERVICE_LINES)
    mrg = g02[["facility_id", "service_line", "grade"]].merge(
        g03[["facility_id", "service_line", "grade", "level_note"]], on=["facility_id", "service_line"],
        suffixes=("_v02", "_v03"))
    ch = mrg[mrg.grade_v02 != mrg.grade_v03]
    trans = {line: {f"{a}->{b}": int(n) for (a, b), n in
                    ch[ch.service_line == line].groupby(["grade_v02", "grade_v03"]).size().items()}
             for line in lines}
    got = {
        ("nicu", "g2+"): int(((mrg.service_line == "nicu") & (mrg.grade_v03 >= 2)).sum()),
        ("nicu", "g3"): c03["nicu"]["3"],
        ("nicu", "sncu_only"): int(((mrg.service_line == "nicu") & (mrg.grade_v02 >= 2) & (mrg.grade_v03 == 1)).sum()),
        ("emoc", "g3"): c03["emoc"]["3"],
        ("emoc", "fru_note"): int(mrg.level_note.notna().sum()),
        ("cath_lab", "g3"): c03["cath_lab"]["3"],
        ("dialysis", "g3"): c03["dialysis"]["3"],
        ("dialysis", "demoted"): int(((mrg.service_line == "dialysis") & (mrg.grade_v02 == 3)
                                      & (mrg.grade_v03 == 2)).sum()),
    }
    proto = {f"{l}.{k}": {"prototype": PROTOTYPE[(l, k)], "v0.3": got[(l, k)]} for (l, k) in PROTOTYPE}
    check("only nicu, emoc, cath_lab, dialysis grades change",
          set(ch.service_line) <= set(lines), sorted(set(ch.service_line)))

    print(f"recipe {VERSION}: rule-level regrade of {tags_path.name} (method {method}, {model_version})")
    print(f"  {len(fac):,} facilities x {len(recipes.SERVICE_LINES)} lines = {len(g03):,} rows; "
          f"{len(ch):,} grades changed")
    print("  facilities per grade, v0.2 -> v0.3 (grades 0/1/2/3):")
    for line in lines:
        a, b = c02[line], c03[line]
        print(f"    {line:9s} " + "  ".join(f"g{k}: {a[str(k)]:5d} -> {b[str(k)]:5d}" for k in range(4)))
        print(f"              transitions {trans[line]}")
    print("  against the research prototype (f2_v03.log):")
    for k, v in proto.items():
        print(f"    {k:20s} prototype {v['prototype']:5d}   v0.3 {v['v0.3']:5d}")

    if args.changes_csv:
        fx = facts.assign(tags=tags.values)
        name = dict(zip(fac.facility_id, fac.name))
        rows = []
        k = ch.merge(g02[["facility_id", "service_line", "evidence_items", "missing_for_next"]],
                     on=["facility_id", "service_line"]).merge(
            g03[["facility_id", "service_line", "evidence_items", "missing_for_next", "level_note"]],
            on=["facility_id", "service_line"], suffixes=("_v02", "_v03"))
        rel = {l: set(recipes.GRADE_RULES[l].tags) | set(recipes.GRADE_RULES_V03[l].tags) for l in lines}
        byfac = {fid: d for fid, d in fx.groupby("facility_id")}
        for r in k.itertuples(index=False):
            d = byfac.get(r.facility_id)
            want = {recipes.base_tag(t) for t in rel[r.service_line]}
            fl = [] if d is None else [f"[{','.join(sorted(set(t) & want))}] {txt}"
                                       for txt, t in zip(d.text, d.tags) if set(t) & want]
            rows.append({
                "facility_id": r.facility_id, "name": name.get(r.facility_id), "service_line": r.service_line,
                "grade_v02": r.grade_v02, "grade_v03": r.grade_v03, "level_note": r.level_note_v03,
                "items_v02": json.dumps([[i["requirement"], i["text"]] for i in r.evidence_items_v02],
                                        ensure_ascii=False),
                "items_v03": json.dumps([[i["requirement"], i["text"]] for i in r.evidence_items_v03],
                                        ensure_ascii=False),
                "missing_v03": json.dumps(list(r.missing_for_next_v03), ensure_ascii=False),
                "line_facts": " || ".join(dict.fromkeys(fl)),
            })
        paths.ensure(args.changes_csv.parent)
        pd.DataFrame(rows).to_csv(args.changes_csv, index=False)
        print(f"  wrote {len(rows):,} changed records to {args.changes_csv}")

    failed = [c for c in checks if not c[1]]
    print("acceptance checks:")
    for n, ok, v in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {n}: {v}")

    if args.no_write or failed:
        print("  nothing written" + (" (a check failed)" if failed else ""))
        return 1 if failed else 0

    meta = {
        "output": f"gold/{OUT_NAME}",
        "created_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        **RB.git_info(), "script": SCRIPT,
        "recipe_version": VERSION, "decision": "D048 (score spec v1-final)",
        "what": "rule-level regrade of the frozen fact tags: tags, taggers and tag definitions unchanged",
        "method": method, "model_version": model_version,
        "inputs": {
            "gold/fact_tags.parquet": {"sha256": RB.sha256(tags_path)},
            "silver/facts.parquet": {"sha256": RB.sha256(paths.silver_dir() / "facts.parquet")},
            "silver/facilities.parquet": {"sha256": RB.sha256(paths.silver_dir() / "facilities.parquet")},
            "gold/capability_grades.parquet (v0.2, reproduced, not written)": {"sha256": RB.sha256(v02_path)},
        },
        "changed_lines": list(lines),
        "v0_3_rules": {l: rules_text(VERSION)[l] for l in lines},
        "v0_3_qualifiers": {n: {"base_tag": q.base, "pattern": q.pattern, "veto": q.veto, "meaning": q.meaning}
                            for n, q in recipes.QUALIFIERS_V03.items()},
        "v0_2_rules_replaced": {l: rules_text("v0.2")[l] for l in lines},
        "citations": {
            "emoc": "WHO 2009, Monitoring emergency obstetric care: a handbook, Table 4, signal functions 8 and 9; "
                    "FRU 2025 (NHM Revised Guidelines for Operationalizing First Referral Units) s2.2",
            "nicu": "FBNC 2025 (MoHFW Facility Based Newborn Care Operational Guidelines)",
            "cath_lab": "PM-JAY Hospital Empanelment Criteria, advanced criteria V.A Cardiology/CTVS",
            "dialysis": "PM-JAY Hospital Empanelment Criteria, V.H Nephrology item 1; PMNDP Operational Guidelines",
            "docs": "docs/06_capability_recipes.md, section v0.3",
        },
        "level_note": "emoc only: set at grade 3 when anaesthesia and h24 tags are also present; never changes a grade",
        "facilities_per_grade_v02": {l: c02[l] for l in lines},
        "facilities_per_grade_v03": {l: c03[l] for l in lines},
        "transitions_v02_to_v03": trans,
        "prototype_comparison": proto,
        "checks": [{"check": n, "passed": ok, "value": str(v)} for n, ok, v in checks],
    }
    RB.write_table(g03, GRADES_V03_SCHEMA, gold / OUT_NAME, meta)
    print(f"  wrote gold/{OUT_NAME} ({len(g03):,} rows) in {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
