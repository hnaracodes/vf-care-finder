#!/usr/bin/env python
"""WP3 part 1: regex baseline tags and grades.

Reads silver/facts.parquet and silver/facilities.parquet and writes, under
vf.paths.gold_dir():

  fact_tags.parquet          one row per fact, tags from the recipe regexes (method = regex)
  capability_grades.parquet  one row per facility per service line (method = regex)
  <file>.meta.json           row count, created time, git commit, recipe version, stats

The taxonomy, regexes, grade rules, evidence picking and source confidence all
live in src/vf/recipes.py (recipe v0.1, docs/06_capability_recipes.md). WP4 reuses
them and overwrites capability_grades.parquet with method = student. When an
earlier regex grades file exists, the script prints and records how many
facility grades moved against it (for example after a recipe bump).

Rerun end to end with

  .venv/bin/python scripts/wp3_regex_baseline.py

It prints a summary and the acceptance checks, and exits 1 if any check fails.
"""
from __future__ import annotations

import collections
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from vf import paths, recipes

SCRIPT = "scripts/wp3_regex_baseline.py"
METHOD = "regex"
#: fact_tags.model_version for the regex tagger is the recipe version.
MODEL_VERSION = recipes.RECIPE_VERSION

#: Facility counts from docs/06_capability_recipes.md and docs/03_data_profile.md.
#: The profile searched facts AND description on all 10,000 raw rows, with some
#: narrower patterns than recipe v0. See the gap notes printed at the end.
PROFILE = {
    "obstetric specialty": 4585,
    "nephrology specialty": 2156,
    "critical care specialty": 1677,
    "C-section mention": 443,
    "dialysis mention": 1046,
    "ICU mention": 2048,
    "ICU and ventilator": 380,
}
#: Allowed relative gap between our facts-only count and the profile count.
PROFILE_TOLERANCE = 0.05

_LIST = pa.list_(pa.string())
FACT_TAGS_SCHEMA = pa.schema([
    pa.field("fact_id", pa.string(), nullable=False),
    pa.field("facility_id", pa.string(), nullable=False),
    pa.field("tags", _LIST, nullable=False),
    pa.field("method", pa.string(), nullable=False),
    pa.field("model_version", pa.string(), nullable=False),
])

GRADES_SCHEMA = pa.schema([
    pa.field("facility_id", pa.string(), nullable=False),
    pa.field("service_line", pa.string(), nullable=False),
    pa.field("grade", pa.int64(), nullable=False),
    pa.field("grade_label", pa.string(), nullable=False),
    pa.field("tags_present", _LIST, nullable=False),
    pa.field("evidence", _LIST, nullable=False),
    pa.field("source_confidence", pa.float64(), nullable=False),
    pa.field("method", pa.string(), nullable=False),
    pa.field("recipe_version", pa.string(), nullable=False),
])


# --------------------------------------------------------------------------
# steps
# --------------------------------------------------------------------------

def load_previous_grades() -> pd.DataFrame | None:
    """The regex grades file this run will replace, if any (read before writing)."""
    p = paths.gold_dir() / "capability_grades.parquet"
    if not p.exists():
        return None
    prev = pd.read_parquet(p, columns=["facility_id", "service_line", "grade", "method", "recipe_version"])
    return prev if (prev["method"] == METHOD).all() else None


def load_inputs() -> tuple[pd.DataFrame, pd.DataFrame]:
    silver = paths.silver_dir()
    need = [silver / "facilities.parquet", silver / "facts.parquet"]
    missing = [str(p) for p in need if not p.exists()]
    if missing:
        raise SystemExit(f"missing input(s): {missing}. Run scripts/wp1_silver_facilities.py first.")
    fac = pq.read_table(need[0], columns=[
        "facility_id", "name", "row_status", "specialties", "official_website",
        "n_source_domains", "page_updated", "staff_named",
    ]).to_pandas()
    # Arrow lists come back as numpy arrays; make them plain lists.
    fac["specialties"] = fac["specialties"].map(list)
    facts = pq.read_table(need[1], columns=["fact_id", "facility_id", "field", "text", "text_norm"]).to_pandas()
    return fac, facts


def tag_all(facts: pd.DataFrame) -> tuple[pd.Series, int]:
    """Tags per fact, computed once per distinct text_norm. Returns (tags, n distinct)."""
    distinct = facts["text_norm"].unique()
    lookup = {t: recipes.tag_fact(t) for t in distinct}
    return facts["text_norm"].map(lookup), len(distinct)


def build_grades(fac: pd.DataFrame, facts: pd.DataFrame) -> pd.DataFrame:
    per_fac: dict[str, list] = collections.defaultdict(list)
    for fid, text, tags in zip(facts["facility_id"], facts["text"], facts["tags"]):
        per_fac[fid].append((text, tags))

    rows = []
    for r in fac.itertuples(index=False):
        conf = recipes.source_confidence(
            r.n_source_domains, r.official_website, r.page_updated, r.staff_named
        )
        for lg in recipes.facility_line_grades(per_fac.get(r.facility_id, []), r.specialties):
            rows.append({
                "facility_id": r.facility_id,
                "service_line": lg.service_line,
                "grade": lg.grade,
                "grade_label": lg.grade_label,
                "tags_present": lg.tags_present,
                "evidence": lg.evidence,
                "source_confidence": conf,
                "method": METHOD,
                "recipe_version": recipes.RECIPE_VERSION,
            })
    out = pd.DataFrame.from_records(rows, columns=GRADES_SCHEMA.names)
    out["grade"] = out["grade"].astype("int64")
    out["source_confidence"] = out["source_confidence"].astype("float64")
    return out


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
             SCRIPT, "src/vf/recipes.py", "src/vf/clean.py"],
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
    write_atomic(path.with_name(path.name + ".meta.json"), lambda p: p.write_text(body))


def rules_description() -> dict:
    out = {}
    for line, rule in recipes.GRADE_RULES.items():
        fmt = lambda groups: " and ".join("(" + " or ".join(sorted(g)) + ")" for g in groups)
        out[line] = {
            "claimed": f"specialty in {sorted(rule.claim_specialties)}"
                       + (f" or tag in {sorted(rule.claim_tags)}" if rule.claim_tags else ""),
            "supported": fmt(rule.supported),
            "corroborated": fmt(rule.corroborated),
            "bonus_tags": list(rule.bonus_tags),
        }
    return out


# --------------------------------------------------------------------------
# stats and checks
# --------------------------------------------------------------------------

def grade_movement(prev: pd.DataFrame, grades: pd.DataFrame, fac_names: pd.Series) -> dict:
    """How facility grades moved against an earlier grades file (same facilities)."""
    m = prev.merge(grades[["facility_id", "service_line", "grade", "recipe_version"]],
                   on=["facility_id", "service_line"], how="inner", suffixes=("_prev", ""))
    out = {"previous_recipe_version": ",".join(sorted(map(str, prev["recipe_version"].unique()))),
           "facilities_compared": int(m["facility_id"].nunique()), "lines": {}}
    for line in recipes.SERVICE_LINES:
        ml = m[m["service_line"] == line]
        moved = ml[ml["grade_prev"] != ml["grade"]]
        trans = moved.groupby(["grade_prev", "grade"]).size()
        lost2 = moved[(moved["grade_prev"] >= 2) & (moved["grade"] < 2)]
        out["lines"][line] = {
            "changed": int(len(moved)),
            "transitions": {f"{int(a)}->{int(b)}": int(n) for (a, b), n in trans.items()},
            "grade_ge2_before": int((ml["grade_prev"] >= 2).sum()),
            "grade_ge2_after": int((ml["grade"] >= 2).sum()),
            "grade_3_before": int((ml["grade_prev"] == 3).sum()),
            "grade_3_after": int((ml["grade"] == 3).sum()),
            "lost_grade_ge2": sorted(f"{fac_names.get(f, f)} ({f})" for f in lost2["facility_id"]),
        }
    return out


def grade_table(grades: pd.DataFrame) -> dict:
    """{line: {label: facilities}} in grade order."""
    out = {}
    for line in recipes.SERVICE_LINES:
        vc = grades.loc[grades["service_line"] == line, "grade"].value_counts()
        out[line] = {recipes.GRADE_LABELS[g]: int(vc.get(g, 0)) for g in range(4)}
    return out


def tag_table(facts: pd.DataFrame) -> dict:
    """{tag: {facts, distinct_texts, facilities}}."""
    ex = facts[["facility_id", "text_norm", "tags"]].explode("tags").dropna(subset=["tags"])
    out = {}
    for t in recipes.EVIDENCE_TAGS:
        s = ex[ex["tags"] == t]
        out[t] = {
            "facts": int(len(s)),
            "distinct_texts": int(s["text_norm"].nunique()),
            "facilities": int(s["facility_id"].nunique()),
        }
    return out


def facility_tag_sets(facts: pd.DataFrame) -> dict:
    sets = collections.defaultdict(set)
    for fid, tags in zip(facts["facility_id"], facts["tags"]):
        sets[fid].update(tags)
    return sets


def profile_comparison(fac: pd.DataFrame, facts: pd.DataFrame) -> dict:
    tagsets = facility_tag_sets(facts)
    has = lambda *ts: sum(1 for s in tagsets.values() if all(t in s for t in ts))
    spec = lambda names: int(fac["specialties"].map(lambda xs: bool(set(xs) & set(names))).sum())
    ob = recipes.GRADE_RULES["emoc"].claim_specialties
    return {
        "obstetric specialty": spec(ob),
        "nephrology specialty": spec({"nephrology"}),
        "critical care specialty": spec({"criticalCareMedicine"}),
        "C-section mention": has("csection"),
        "dialysis mention": has("dialysis"),
        "ICU mention": has("icu"),
        "ICU and ventilator": has("icu", "ventilator"),
    }


def run_checks(fac, facts, fact_tags, grades, ours) -> list:
    checks = []

    def check(name, passed, value):
        checks.append({"check": name, "passed": bool(passed), "value": value})

    gold = paths.gold_dir()
    for fname, schema in (("fact_tags.parquet", FACT_TAGS_SCHEMA), ("capability_grades.parquet", GRADES_SCHEMA)):
        got = pq.read_schema(gold / fname).remove_metadata()
        check(f"{fname} schema equals the contract (names, order, Arrow types)",
              got.equals(schema), "match" if got.equals(schema) else str(got))

    check("fact_tags: one row per fact, same fact_ids as facts.parquet",
          len(fact_tags) == len(facts) and fact_tags["fact_id"].is_unique
          and set(fact_tags["fact_id"]) == set(facts["fact_id"]),
          f"{len(fact_tags):,} rows, {fact_tags['fact_id'].nunique():,} distinct fact_id")

    bad_tags = {t for ts in fact_tags["tags"] for t in ts} - set(recipes.EVIDENCE_TAGS)
    check("fact_tags: every tag is an evidence tag", not bad_tags, sorted(bad_tags) or "ok")

    n_fac = len(fac)
    pairs_ok = (len(grades) == 3 * n_fac
                and not grades.duplicated(["facility_id", "service_line"]).any()
                and set(grades["facility_id"]) == set(fac["facility_id"])
                and (grades.groupby("facility_id")["service_line"].nunique() == 3).all())
    check("capability_grades: one row per facility per service line, all three lines",
          pairs_ok, f"{len(grades):,} rows = 3 x {n_fac:,} facilities")

    label_ok = (grades["grade"].between(0, 3).all()
                and (grades["grade"].map(lambda g: recipes.GRADE_LABELS[g]) == grades["grade_label"]).all())
    check("grade in 0..3 and grade_label matches grade", label_ok,
          grades["grade_label"].value_counts().to_dict())

    check(f"method = regex and recipe_version = {recipes.RECIPE_VERSION} on every row",
          (grades["method"] == METHOD).all() and (grades["recipe_version"] == recipes.RECIPE_VERSION).all()
          and (fact_tags["method"] == METHOD).all() and (fact_tags["model_version"] == MODEL_VERSION).all(),
          f"{METHOD} / {recipes.RECIPE_VERSION}")

    ev_len = grades["evidence"].map(len)
    g = grades["grade"]
    check("evidence: at most 5; non-empty for every grade >= 2; empty for grade 0",
          (ev_len <= recipes.MAX_EVIDENCE).all() and (ev_len[g >= 2] > 0).all() and (ev_len[g == 0] == 0).all(),
          f"max {int(ev_len.max())}; grade>=2 empty {int((ev_len[g >= 2] == 0).sum())}; "
          f"grade 0 non-empty {int((ev_len[g == 0] > 0).sum())}; grade 1 empty {int((ev_len[g == 1] == 0).sum())}")

    texts = collections.defaultdict(set)
    for fid, text in zip(facts["facility_id"], facts["text"]):
        texts[fid].add(text)
    n_bad = sum(1 for fid, ev in zip(grades["facility_id"], grades["evidence"]) for e in ev if e not in texts[fid])
    check("every evidence text is a fact of that facility", n_bad == 0, f"{n_bad} not found")

    # Evidence must justify the grade: the quoted facts alone must reproduce it.
    tag_of = dict(zip(zip(facts["facility_id"], facts["text"]), facts["tags"]))
    n_unjust = 0
    for fid, line, grade, ev in zip(grades["facility_id"], grades["service_line"], grades["grade"], grades["evidence"]):
        if grade >= 2:
            ev_tags = set().union(*(tag_of[(fid, e)] for e in ev))
            if recipes.grade_line(line, ev_tags) != grade:
                n_unjust += 1
    check("for grades 2 and 3 the quoted evidence alone reproduces the grade", n_unjust == 0,
          f"{n_unjust} rows where it does not")

    sc = grades["source_confidence"]
    check("source_confidence in [0, 1], no nulls, constant per facility",
          sc.notna().all() and sc.between(0, 1).all()
          and (grades.groupby("facility_id")["source_confidence"].nunique() == 1).all(),
          f"min {sc.min():.3f}, median {sc.median():.3f}, max {sc.max():.3f}")

    # Independent recount of supported+ from the fact tags.
    gt = grades.set_index(["service_line"])
    recount = {
        "emoc": ours["C-section mention"],
        "dialysis": ours["dialysis mention"],
        "icu": ours["ICU mention"],
    }
    got = {line: int((gt.loc[line, "grade"] >= 2).sum()) for line in recipes.SERVICE_LINES}
    check("grade >= 2 equals facilities with the defining tag (independent recount)",
          got == recount, f"grades {got} vs tags {recount}")
    check("icu grade 3 equals facilities with icu and ventilator tags",
          int((gt.loc["icu", "grade"] == 3).sum()) == ours["ICU and ventilator"],
          f"{int((gt.loc['icu', 'grade'] == 3).sum())} vs {ours['ICU and ventilator']}")

    gaps = {k: f"{ours[k]:,} vs {v:,} ({(ours[k] - v) / v:+.1%})" for k, v in PROFILE.items()}
    within = all(abs(ours[k] - v) <= PROFILE_TOLERANCE * v for k, v in PROFILE.items())
    check(f"facility counts within {PROFILE_TOLERANCE:.0%} of the recipes doc", within, gaps)
    return checks


# --------------------------------------------------------------------------

def main() -> int:
    t0 = time.time()
    prev = load_previous_grades()
    fac, facts = load_inputs()
    facts["tags"], n_distinct = tag_all(facts)

    fact_tags = pd.DataFrame({
        "fact_id": facts["fact_id"],
        "facility_id": facts["facility_id"],
        "tags": facts["tags"],
        "method": METHOD,
        "model_version": MODEL_VERSION,
    })
    grades = build_grades(fac, facts)

    ours = profile_comparison(fac, facts)
    gtab = grade_table(grades)
    movement = grade_movement(prev, grades, fac.set_index("facility_id")["name"]) if prev is not None else None
    ttab = tag_table(facts)
    silver = paths.silver_dir()
    meta_common = {
        "created_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        **git_info(),
        "recipe_version": recipes.RECIPE_VERSION,
        "method": METHOD,
        "script": SCRIPT,
        "inputs": {
            f"silver/{n}": {"sha256": sha256(silver / n)} for n in ("facilities.parquet", "facts.parquet")
        },
    }
    gold = paths.gold_dir()
    write_table(fact_tags, FACT_TAGS_SCHEMA, gold / "fact_tags.parquet", {
        "output": "gold/fact_tags.parquet",
        **meta_common,
        "model_version": MODEL_VERSION,
        "tagger": f"vf.recipes.tag_fact ({recipes.RECIPE_VERSION} regexes on vf.clean.normalize_text; a fact "
                  "matching negation_pattern gets no tag; vbac_pattern is removed before csection runs)",
        "tag_patterns": recipes.TAG_PATTERNS,
        "negation_pattern": recipes.NEGATION_PATTERN,
        "vbac_pattern": recipes.VBAC_PATTERN,
        "distinct_texts_tagged": n_distinct,
        "facts_with_any_tag": int(fact_tags["tags"].map(len).gt(0).sum()),
        "tags": ttab,
    })
    write_table(grades, GRADES_SCHEMA, gold / "capability_grades.parquet", {
        "output": "gold/capability_grades.parquet",
        **meta_common,
        "grade_rules": rules_description(),
        "evidence": "vf.recipes.select_evidence: up to 5 fact texts (original text), covering the "
                    "grade's requirements first, then round-robin over the rule's tags; none for grade 0",
        "tags_present": "the line's tags found on the facility, bonus tags (emoc h24, nicu) included",
        "source_confidence": {
            "formula": "0.4*min(n_source_domains,6)/6 + 0.2*has_official_website "
                       "+ 0.2*recency + 0.2*staff_named",
            "recency": "1 if page_updated within 1 year of 2026-11-21, linear to 0 at 5 years; 0 if missing",
            "calibrated": False,
        },
        "facilities_per_grade": gtab,
        "grade_movement_vs_previous_file": movement,
    })

    checks = run_checks(fac, facts, fact_tags, grades, ours)

    # ---------------------------------------------------------------- summary
    print(f"WP3 regex baseline (recipe {recipes.RECIPE_VERSION}) -> {gold}  [{time.time() - t0:.1f}s]")
    print(f"  fact_tags.parquet  {len(fact_tags):,} rows ({n_distinct:,} distinct texts tagged; "
          f"{int(fact_tags['tags'].map(len).gt(0).sum()):,} facts with any tag)")
    print(f"  capability_grades.parquet  {len(grades):,} rows ({len(fac):,} facilities x 3 lines)")
    print("  Facilities per grade (none / claimed / supported / corroborated):")
    for line, d in gtab.items():
        print(f"    {line:9s}" + "".join(f" {k} {v:>5,}" for k, v in d.items()))
    print("  Tags (facts / distinct texts / facilities):")
    for t, d in ttab.items():
        print(f"    {t:14s} {d['facts']:>7,} {d['distinct_texts']:>7,} {d['facilities']:>6,}")
    sc = grades.drop_duplicates("facility_id")["source_confidence"]
    print(f"  source_confidence per facility: quartiles {sc.quantile([.25, .5, .75]).round(3).tolist()}, "
          f"mean {sc.mean():.3f}")
    print("  Compared with the recipes doc (ours: facts only, 9,989 deduped facilities; "
          "doc: facts + description, 10,000 raw rows):")
    for k, v in PROFILE.items():
        print(f"    {k:24s} {ours[k]:>6,}  doc {v:>6,}  ({(ours[k] - v) / v:+.1%})")
    if movement is not None:
        print(f"  Grade movement against the previous regex grades (recipe {movement['previous_recipe_version']} "
              f"-> {recipes.RECIPE_VERSION}):")
        for line, mv in movement["lines"].items():
            print(f"    {line:9s} changed {mv['changed']:>4,}  grade>=2 {mv['grade_ge2_before']:,} -> "
                  f"{mv['grade_ge2_after']:,}  grade 3 {mv['grade_3_before']:,} -> {mv['grade_3_after']:,}  "
                  f"{mv['transitions']}")
            if mv["lost_grade_ge2"]:
                print(f"      lost grade >= 2: {'; '.join(mv['lost_grade_ge2'][:12])}"
                      + (" ..." if len(mv["lost_grade_ge2"]) > 12 else ""))
    print("Acceptance checks")
    for c in checks:
        print(f"  [{'PASS' if c['passed'] else 'FAIL'}] {c['check']}: {c['value']}")
    return 0 if all(c["passed"] for c in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
