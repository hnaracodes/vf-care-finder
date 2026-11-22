#!/usr/bin/env python
"""WP3 part 1: regex baseline tags and grades (recipe v0.2, contract v2).

Reads silver/facts.parquet and silver/facilities.parquet and writes, under
vf.paths.gold_dir() (or --out-dir):

  fact_tags.parquet          one row per fact, tags from the recipe regexes (method = regex)
  capability_grades.parquet  one row per facility per service line (method = regex), with
                             structured evidence, missing_for_next, n_supporting_facts and
                             evidence_strength
  <file>.meta.json           row count, created time, git commit, recipe version, stats

The taxonomy, regexes, grade rules, evidence picking, evidence strength and source
confidence all live in src/vf/recipes.py (docs/06_capability_recipes.md). WP4 reuses
them and overwrites capability_grades.parquet with method = student.

The run also grades every facility with recipe v0.1 in memory and checks that the
three v0.1 lines (emoc, dialysis, icu) come out identical under v0.2: same grade,
same tags_present, same evidence. When an earlier regex grades file exists, it
prints and records how many facility grades moved against it.

Rerun end to end with

  .venv/bin/python scripts/wp3_regex_baseline.py               # recipe v0.2, the default
  .venv/bin/python scripts/wp3_regex_baseline.py --spot 10     # also print 10 grade 2+ facilities per new line
  .venv/bin/python scripts/wp3_regex_baseline.py --version v0.1 --out-dir /tmp/v01   # reproduce v0.1

It prints a summary and the acceptance checks, and exits 1 if any check fails.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from vf import paths, recipes

SCRIPT = "scripts/wp3_regex_baseline.py"
METHOD = "regex"

#: Facility counts from docs/06_capability_recipes.md and docs/03_data_profile.md,
#: for the v0.1 tags. The profile searched facts AND description on all 10,000 raw
#: rows, with some narrower patterns than recipe v0.
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
#: WP3 acceptance: every line should have at least this many facilities at grade 2+.
MIN_GRADE2 = 50

_LIST = pa.list_(pa.string())
EVIDENCE_ITEM = pa.struct([
    pa.field("requirement", pa.string()),
    pa.field("tag", pa.string()),
    pa.field("fact_id", pa.string()),
    pa.field("field", pa.string()),
    pa.field("text", pa.string()),
])

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
    pa.field("evidence_items", pa.list_(EVIDENCE_ITEM), nullable=False),
    pa.field("missing_for_next", _LIST, nullable=False),
    pa.field("n_supporting_facts", pa.int64(), nullable=False),
    pa.field("evidence_strength", pa.string(), nullable=False),
    pa.field("source_confidence", pa.float64(), nullable=False),
    pa.field("method", pa.string(), nullable=False),
    pa.field("recipe_version", pa.string(), nullable=False),
])

STRENGTHS = ("none", "thin", "moderate", "strong")


# --------------------------------------------------------------------------
# steps
# --------------------------------------------------------------------------

def load_previous_grades(out_dir: Path) -> pd.DataFrame | None:
    """The regex grades file this run will replace, if any (read before writing)."""
    p = out_dir / "capability_grades.parquet"
    if not p.exists():
        return None
    prev = pd.read_parquet(p, columns=["facility_id", "service_line", "grade", "tags_present", "evidence",
                                       "method", "recipe_version"])
    return prev if (prev["method"] == METHOD).all() else None


def load_inputs() -> tuple[pd.DataFrame, pd.DataFrame]:
    silver = paths.silver_dir()
    need = [silver / "facilities.parquet", silver / "facts.parquet"]
    missing = [str(p) for p in need if not p.exists()]
    if missing:
        raise SystemExit(f"missing input(s): {missing}. Run scripts/wp1_silver_facilities.py first.")
    fac = pq.read_table(need[0], columns=[
        "facility_id", "name", "facility_type", "row_status", "specialties", "official_website",
        "n_source_domains", "page_updated", "staff_named",
    ]).to_pandas()
    # Arrow lists come back as numpy arrays; make them plain lists.
    fac["specialties"] = fac["specialties"].map(list)
    facts = pq.read_table(need[1], columns=["fact_id", "facility_id", "field", "text", "text_norm"]).to_pandas()
    return fac, facts


def tag_all(facts: pd.DataFrame, version: str) -> tuple[pd.Series, int]:
    """Tags per fact, computed once per distinct text_norm. Returns (tags, n distinct)."""
    distinct = facts["text_norm"].unique()
    lookup = {t: recipes.tag_fact(t, version) for t in distinct}
    return facts["text_norm"].map(lookup), len(distinct)


def facts_by_facility(facts: pd.DataFrame, tags: pd.Series) -> dict:
    per_fac: dict[str, list] = collections.defaultdict(list)
    for fid, text, tg, fld, fact_id in zip(facts["facility_id"], facts["text"], tags, facts["field"],
                                           facts["fact_id"]):
        per_fac[fid].append((text, tg, fld, fact_id))
    return per_fac


def build_grades(fac: pd.DataFrame, per_fac: dict, version: str) -> pd.DataFrame:
    rows = []
    for r in fac.itertuples(index=False):
        conf = recipes.source_confidence(
            r.n_source_domains, r.official_website, r.page_updated, r.staff_named
        )
        for lg in recipes.facility_line_grades(per_fac.get(r.facility_id, []), r.specialties, version=version,
                                               n_source_domains=r.n_source_domains,
                                               official_website=r.official_website):
            rows.append({
                "facility_id": r.facility_id,
                "service_line": lg.service_line,
                "grade": lg.grade,
                "grade_label": lg.grade_label,
                "tags_present": lg.tags_present,
                "evidence": lg.evidence,
                "evidence_items": lg.evidence_items,
                "missing_for_next": lg.missing_for_next,
                "n_supporting_facts": lg.n_supporting_facts,
                "evidence_strength": lg.evidence_strength,
                "source_confidence": conf,
                "method": METHOD,
                "recipe_version": version,
            })
    out = pd.DataFrame.from_records(rows, columns=GRADES_SCHEMA.names)
    out["grade"] = out["grade"].astype("int64")
    out["n_supporting_facts"] = out["n_supporting_facts"].astype("int64")
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


def rules_description(version: str) -> dict:
    out = {}
    fmt = lambda groups, names: " and ".join(
        f"{name} [" + " or ".join(sorted(g)) + "]" for g, name in zip(groups, names))
    for line, rule in recipes.RULES_BY_VERSION[version].items():
        corr = fmt(rule.corroborated, rule.corroborated_names)
        if rule.has_repeat:
            corr += (f", and {rule.repeat_name} (at least {rule.min_distinct_facts} distinct fact texts, or "
                     f"{rule.min_distinct_fields} different fields)")
        out[line] = {
            "claimed": f"specialty in {sorted(rule.claim_specialties)}"
                       + (f" or {rule.claim_name} [{' or '.join(sorted(rule.claim_tags))}]" if rule.claim_tags else ""),
            "supported": fmt(rule.supported, rule.supported_names),
            "corroborated": corr,
            "bonus_tags": list(rule.bonus_tags),
        }
    return out


def tagger_description(version: str) -> dict:
    d = {
        "function": f"vf.recipes.tag_fact(text, version={version!r}) on vf.clean.normalize_text",
        "v0_1_tag_patterns": recipes.TAG_PATTERNS,
        "negation_pattern (all tags)": recipes.NEGATION_PATTERN,
        "vbac_pattern (removed before csection)": recipes.VBAC_PATTERN,
    }
    if version == "v0.2":
        d.update({
            "v0_2_tag_rules": {t: {k: v for k, v in vars(r).items() if v} for t, r in recipes.NEW_TAG_RULES.items()},
            "v0_2_context_veto (refer, outsource, plan, need, absence)": recipes.NEW_TAG_CONTEXT_VETO,
            "v0_2_except_mask": recipes.EXCEPT_MASK,
            "v0_2_specialty_list_cue (name_pattern only)": recipes.SPECIALTY_LIST_CUE,
            "v0_2_staff_cue (name_pattern only)": recipes.STAFF_CUE,
            "note": "v0.2 = the v0.1 tags exactly, plus the 11 v0.2 tags; a v0.2 tag fires when, after the mask and "
                    "the except clause are removed, its pattern matches, or its name_pattern matches in a fact that "
                    "is neither a specialty list nor a staff bio; and neither its veto nor the context veto matches",
        })
    return d


# --------------------------------------------------------------------------
# stats and checks
# --------------------------------------------------------------------------

def grade_movement(prev: pd.DataFrame, grades: pd.DataFrame, fac_names: pd.Series) -> dict:
    """How facility grades moved against an earlier grades file (lines in both)."""
    m = prev.merge(grades[["facility_id", "service_line", "grade", "tags_present", "evidence"]],
                   on=["facility_id", "service_line"], how="inner", suffixes=("_prev", ""))
    out = {"previous_recipe_version": ",".join(sorted(map(str, prev["recipe_version"].unique()))),
           "facilities_compared": int(m["facility_id"].nunique()), "lines": {}}
    for line in sorted(set(m["service_line"]), key=list(recipes.SERVICE_LINES).index):
        ml = m[m["service_line"] == line]
        moved = ml[ml["grade_prev"] != ml["grade"]]
        trans = moved.groupby(["grade_prev", "grade"]).size()
        lost2 = moved[(moved["grade_prev"] >= 2) & (moved["grade"] < 2)]
        same_ev = sum(list(a) == list(b) and list(c) == list(d) for a, b, c, d in
                      zip(ml["evidence_prev"], ml["evidence"], ml["tags_present_prev"], ml["tags_present"]))
        out["lines"][line] = {
            "changed": int(len(moved)),
            "evidence_or_tags_present_changed": int(len(ml) - same_ev),
            "transitions": {f"{int(a)}->{int(b)}": int(n) for (a, b), n in trans.items()},
            "grade_ge2_before": int((ml["grade_prev"] >= 2).sum()),
            "grade_ge2_after": int((ml["grade"] >= 2).sum()),
            "grade_3_before": int((ml["grade_prev"] == 3).sum()),
            "grade_3_after": int((ml["grade"] == 3).sum()),
            "lost_grade_ge2": sorted(f"{fac_names.get(f, f)} ({f})" for f in lost2["facility_id"]),
        }
    return out


def grade_table(grades: pd.DataFrame, lines) -> dict:
    """{line: {label: facilities}} in grade order."""
    out = {}
    for line in lines:
        vc = grades.loc[grades["service_line"] == line, "grade"].value_counts()
        out[line] = {recipes.GRADE_LABELS[g]: int(vc.get(g, 0)) for g in range(4)}
    return out


def strength_table(grades: pd.DataFrame, lines) -> dict:
    """{line: {strength: facilities at grade 2+}}."""
    out = {}
    for line in lines:
        s = grades.loc[(grades["service_line"] == line) & (grades["grade"] >= 2), "evidence_strength"]
        vc = s.value_counts()
        out[line] = {k: int(vc.get(k, 0)) for k in STRENGTHS[1:]}
    return out


def tag_table(facts: pd.DataFrame, tags) -> dict:
    """{tag: {facts, distinct_texts, facilities}}."""
    ex = facts[["facility_id", "text_norm", "tags"]].explode("tags").dropna(subset=["tags"])
    out = {}
    for t in tags:
        s = ex[ex["tags"] == t]
        out[t] = {
            "facts": int(len(s)),
            "distinct_texts": int(s["text_norm"].nunique()),
            "facilities": int(s["facility_id"].nunique()),
        }
    return out


def suppression_table(facts: pd.DataFrame) -> dict:
    """{v0.2 tag: {reason: distinct texts}}: how often each v0.2 tag fired, and why it was suppressed."""
    out = {t: collections.Counter() for t in recipes.NEW_TAGS_V02}
    for s in facts["text_norm"].unique():
        for t, why in recipes.tag_reasons(s).items():
            out[t][why] += 1
    return {t: dict(sorted(c.items())) for t, c in out.items()}


def facility_tag_sets(facts: pd.DataFrame) -> dict:
    sets = collections.defaultdict(set)
    for fid, tags in zip(facts["facility_id"], facts["tags"]):
        sets[fid].update(tags)
    return sets


def profile_comparison(fac: pd.DataFrame, tagsets: dict) -> dict:
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


def v01_identity(fac: pd.DataFrame, facts: pd.DataFrame, grades: pd.DataFrame) -> dict:
    """Grade every facility with recipe v0.1 in memory and compare the three v0.1
    lines with this run's rows: grade, tags_present and evidence must be identical."""
    tags01, _ = tag_all(facts, "v0.1")
    g01 = build_grades(fac, facts_by_facility(facts, tags01), "v0.1")
    m = g01.merge(grades, on=["facility_id", "service_line"], how="left", suffixes=("_v01", ""))
    out = {"rows_compared": int(len(m))}
    for col in ("grade", "tags_present", "evidence"):
        diff = sum(list(a) != list(b) if col != "grade" else a != b
                   for a, b in zip(m[f"{col}_v01"], m[col]))
        out[f"{col}_differs"] = int(diff)
    out["fact_tags_differ_on_v0_1_tags"] = int(sum(
        a != [t for t in b if t in recipes.EVIDENCE_TAGS_V01] for a, b in zip(tags01, facts["tags"])))
    return out


def run_checks(fac, facts, fact_tags, grades, version, out_dir, ours, v01) -> list:
    checks = []

    def check(name, passed, value):
        checks.append({"check": name, "passed": bool(passed), "value": value})

    lines = recipes.SERVICE_LINES_BY_VERSION[version]
    rules = recipes.RULES_BY_VERSION[version]
    for fname, schema in (("fact_tags.parquet", FACT_TAGS_SCHEMA), ("capability_grades.parquet", GRADES_SCHEMA)):
        got = pq.read_schema(out_dir / fname).remove_metadata()
        check(f"{fname} schema equals the contract (names, order, Arrow types)",
              got.equals(schema), "match" if got.equals(schema) else str(got))

    check("fact_tags: one row per fact, same fact_ids as facts.parquet",
          len(fact_tags) == len(facts) and fact_tags["fact_id"].is_unique
          and set(fact_tags["fact_id"]) == set(facts["fact_id"]),
          f"{len(fact_tags):,} rows, {fact_tags['fact_id'].nunique():,} distinct fact_id")

    bad_tags = {t for ts in fact_tags["tags"] for t in ts} - set(recipes.EVIDENCE_TAGS_BY_VERSION[version])
    check(f"fact_tags: every tag is a {version} evidence tag", not bad_tags, sorted(bad_tags) or "ok")

    n_fac, n_lines = len(fac), len(lines)
    pairs_ok = (len(grades) == n_lines * n_fac
                and not grades.duplicated(["facility_id", "service_line"]).any()
                and set(grades["facility_id"]) == set(fac["facility_id"])
                and set(grades["service_line"]) == set(lines)
                and (grades.groupby("facility_id")["service_line"].nunique() == n_lines).all())
    check(f"capability_grades: one row per facility per service line, all {n_lines} lines",
          pairs_ok, f"{len(grades):,} rows = {n_lines} x {n_fac:,} facilities")

    label_ok = (grades["grade"].between(0, 3).all()
                and (grades["grade"].map(lambda g: recipes.GRADE_LABELS[g]) == grades["grade_label"]).all())
    check("grade in 0..3 and grade_label matches grade", label_ok,
          grades["grade_label"].value_counts().to_dict())

    check(f"method = regex and recipe_version = {version} on every row",
          (grades["method"] == METHOD).all() and (grades["recipe_version"] == version).all()
          and (fact_tags["method"] == METHOD).all() and (fact_tags["model_version"] == version).all(),
          f"{METHOD} / {version}")

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

    # Structured evidence: every item points at a real fact of the facility, verbatim.
    by_id = {fid: (fac_id, fld, text, tg) for fid, fac_id, fld, text, tg in
             zip(facts["fact_id"], facts["facility_id"], facts["field"], facts["text"], facts["tags"])}
    n_items = n_bad_item = n_no_items = 0
    for fid, grade, items in zip(grades["facility_id"], grades["grade"], grades["evidence_items"]):
        if grade >= 2 and not any(it["fact_id"] for it in items):
            n_no_items += 1
        for it in items:
            n_items += 1
            ref = by_id.get(it["fact_id"])
            if (ref is None or ref[0] != fid or ref[1] != it["field"] or ref[2] != it["text"]
                    or it["tag"] not in ref[3] or not it["requirement"]):
                n_bad_item += 1
    check("every grade 2+ row has at least one evidence item with a fact_id", n_no_items == 0,
          f"{n_no_items} rows without")
    check("every evidence item: fact_id of that facility, field and text verbatim from facts.parquet, "
          "tag carried by that fact", n_bad_item == 0, f"{n_items:,} items, {n_bad_item} bad")

    # Evidence must justify the grade: the item facts alone must reproduce it.
    n_unjust = 0
    for fid, line, grade, items in zip(grades["facility_id"], grades["service_line"], grades["grade"],
                                       grades["evidence_items"]):
        if grade >= 2:
            fs = [(it["text"], by_id[it["fact_id"]][3], it["field"], it["fact_id"]) for it in items]
            item_grade = next(x.grade for x in recipes.facility_line_grades(fs, version=version)
                              if x.service_line == line)
            n_unjust += item_grade != grade
    check("for grades 2 and 3 the evidence item facts alone reproduce the grade", n_unjust == 0,
          f"{n_unjust} rows where they do not")

    mfn = grades["missing_for_next"].map(len)
    check("missing_for_next: empty at grade 3, non-empty below",
          (mfn[g == 3] == 0).all() and (mfn[g < 3] > 0).all(),
          f"grade 3 non-empty {int((mfn[g == 3] > 0).sum())}; below 3 empty {int((mfn[g < 3] == 0).sum())}")

    es, ns = grades["evidence_strength"], grades["n_supporting_facts"]
    strength_ok = ((es[g == 0] == "none").all() and (es[g == 1] == "thin").all()
                   and es[g >= 2].isin(STRENGTHS[1:]).all() and (g[es == "strong"] == 3).all()
                   and (ns[g >= 2] >= 1).all() and (es[(g >= 2) & (ns == 1)] == "thin").all()
                   and (ns[g == 0] == 0).all())
    check("evidence_strength: none at 0, thin at 1, strong only at 3, a single supporting fact is thin; "
          "n_supporting_facts >= 1 at grade 2+", strength_ok, es.value_counts().to_dict())

    sc = grades["source_confidence"]
    check("source_confidence in [0, 1], no nulls, constant per facility",
          sc.notna().all() and sc.between(0, 1).all()
          and (grades.groupby("facility_id")["source_confidence"].nunique() == 1).all(),
          f"min {sc.min():.3f}, median {sc.median():.3f}, max {sc.max():.3f}")

    # Independent recount of grade 2+ (supported groups hold) and of the imaging
    # repeat condition, from fact_tags without the grade code.
    tagsets = facility_tag_sets(facts)
    gt = grades.set_index("service_line")
    got, want = {}, {}
    for line in lines:
        groups = rules[line].supported
        want[line] = sum(1 for s in tagsets.values() if all(gr & s for gr in groups))
        got[line] = int((gt.loc[line, "grade"] >= 2).sum())
    check("grade >= 2 equals facilities whose tags satisfy the supported rule (independent recount)",
          got == want, "match" if got == want else f"grades {got} vs tags {want}")
    for line in lines:
        rule = rules[line]
        if not rule.has_repeat:
            continue
        tag = next(iter(rule.corroborated[0]))
        sub = facts[facts["tags"].map(lambda ts: tag in ts)]
        per = sub.groupby("facility_id").agg(nt=("text_norm", "nunique"), nf=("field", "nunique"))
        n_want = int(((per["nt"] >= rule.min_distinct_facts) | (per["nf"] >= rule.min_distinct_fields)).sum())
        n_got = int((gt.loc[line, "grade"] == 3).sum())
        check(f"{line} grade 3 equals facilities with {tag} in 2+ distinct texts or 2+ fields (recount)",
              n_got == n_want, f"{n_got} vs {n_want}")
    if "icu" in lines:
        check("icu grade 3 equals facilities with icu and ventilator tags",
              int((gt.loc["icu", "grade"] == 3).sum()) == ours["ICU and ventilator"],
              f"{int((gt.loc['icu', 'grade'] == 3).sum())} vs {ours['ICU and ventilator']}")

    if v01 is not None:
        same = all(v01[k] == 0 for k in ("grade_differs", "tags_present_differs", "evidence_differs",
                                         "fact_tags_differ_on_v0_1_tags"))
        check("v0.1 lines (emoc, dialysis, icu) identical to an in-memory v0.1 run: grade, tags_present, "
              "evidence; and fact tags identical on the 13 v0.1 tags", same, v01)

    gaps = {k: f"{ours[k]:,} vs {v:,} ({(ours[k] - v) / v:+.1%})" for k, v in PROFILE.items()}
    within = all(abs(ours[k] - v) <= PROFILE_TOLERANCE * v for k, v in PROFILE.items())
    check(f"v0.1 facility counts within {PROFILE_TOLERANCE:.0%} of the recipes doc", within, gaps)

    ge2 = {line: int((gt.loc[line, "grade"] >= 2).sum()) for line in lines}
    short = {k: v for k, v in ge2.items() if v < MIN_GRADE2}
    check(f"every line has at least {MIN_GRADE2} facilities at grade 2 or higher", not short,
          short or "all lines")
    return checks


def spot_read(fac: pd.DataFrame, grades: pd.DataFrame, lines, n: int, seed: int) -> None:
    """Print n random grade 2+ facilities per line with their evidence items, for reading by hand."""
    names = fac.set_index("facility_id")[["name", "facility_type"]]
    rng = np.random.default_rng(seed)
    for line in lines:
        sub = grades[(grades["service_line"] == line) & (grades["grade"] >= 2)]
        pick = sub.iloc[rng.permutation(len(sub))[:n]]
        print(f"\n  Spot-read: {line} ({len(sub):,} facilities at grade 2+, showing {len(pick)})")
        for r in pick.itertuples(index=False):
            nm, ft = names.loc[r.facility_id]
            print(f"    - {nm} [{ft}] ({r.facility_id}): {r.grade_label}, {r.evidence_strength}, "
                  f"{r.n_supporting_facts} supporting; missing {list(r.missing_for_next)}")
            for it in r.evidence_items:
                print(f"        {it['requirement']}: \"{it['text'][:150]}\" ({it['field']})")


# --------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--version", default=recipes.RECIPE_VERSION, choices=recipes.RULE_VERSIONS,
                    help="recipe version to run (default: %(default)s)")
    ap.add_argument("--out-dir", type=Path, default=None, help="output folder (default: vf.paths.gold_dir())")
    ap.add_argument("--spot", type=int, default=0, help="print N random grade 2+ facilities per new line")
    ap.add_argument("--seed", type=int, default=20261121)
    args = ap.parse_args(argv)
    version = args.version
    out_dir = args.out_dir or paths.gold_dir()
    lines = recipes.SERVICE_LINES_BY_VERSION[version]

    t0 = time.time()
    prev = load_previous_grades(out_dir)
    fac, facts = load_inputs()
    facts["tags"], n_distinct = tag_all(facts, version)

    fact_tags = pd.DataFrame({
        "fact_id": facts["fact_id"],
        "facility_id": facts["facility_id"],
        "tags": facts["tags"],
        "method": METHOD,
        "model_version": version,
    })
    grades = build_grades(fac, facts_by_facility(facts, facts["tags"]), version)

    tagsets = facility_tag_sets(facts)
    ours = profile_comparison(fac, tagsets)
    gtab = grade_table(grades, lines)
    stab = strength_table(grades, lines)
    fac_names = fac.set_index("facility_id")["name"]
    movement = grade_movement(prev, grades, fac_names) if prev is not None else None
    ttab = tag_table(facts, recipes.EVIDENCE_TAGS_BY_VERSION[version])
    supp = suppression_table(facts) if version == "v0.2" else None
    v01 = v01_identity(fac, facts, grades) if version == "v0.2" else None

    silver = paths.silver_dir()
    meta_common = {
        "created_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        **git_info(),
        "recipe_version": version,
        "method": METHOD,
        "script": SCRIPT,
        "inputs": {
            f"silver/{n}": {"sha256": sha256(silver / n)} for n in ("facilities.parquet", "facts.parquet")
        },
    }
    write_table(fact_tags, FACT_TAGS_SCHEMA, out_dir / "fact_tags.parquet", {
        "output": "gold/fact_tags.parquet",
        **meta_common,
        "model_version": version,
        "tagger": tagger_description(version),
        "distinct_texts_tagged": n_distinct,
        "facts_with_any_tag": int(fact_tags["tags"].map(len).gt(0).sum()),
        "tags": ttab,
        "v0_2_tag_outcomes_distinct_texts": supp,
    })
    write_table(grades, GRADES_SCHEMA, out_dir / "capability_grades.parquet", {
        "output": "gold/capability_grades.parquet",
        **meta_common,
        "service_lines": list(lines),
        "grade_rules": rules_description(version),
        "evidence": "vf.recipes.select_evidence: up to 5 fact texts (original text), covering the "
                    "grade's requirements first (and, for corroborated mri and ct, a second mention), then "
                    "round-robin over the rule's tags; none for grade 0",
        "evidence_items": "one {requirement, tag, fact_id, field, text} per satisfied requirement of the grade "
                          "reached (claim tags at grade 1; nothing for a specialty-only claim), plus the second "
                          "mention for corroborated mri and ct; text verbatim from silver/facts.parquet",
        "missing_for_next": "plain-language requirements of the next grade up that no fact satisfies",
        "n_supporting_facts": "distinct normalized fact texts carrying a tag of the reached grade's required "
                              "groups (one text in two fields counts once); 0 at grade 0 and for a "
                              "specialty-only claim",
        "evidence_strength": "none at grade 0; thin at grade 1, or at grade 2+ on 1 supporting fact or on a record "
                             "with <= 1 source domain and no official website; strong at grade 3 with >= 3 "
                             "supporting facts and (>= 2 source domains or an official website); else moderate",
        "description_as_fact_source": False,
        "tags_present": "the line's tags found on the facility, bonus tags (emoc h24, nicu) included",
        "source_confidence": {
            "formula": "0.4*min(n_source_domains,6)/6 + 0.2*has_official_website "
                       "+ 0.2*recency + 0.2*staff_named",
            "recency": "1 if page_updated within 1 year of 2026-11-21, linear to 0 at 5 years; 0 if missing",
            "calibrated": False,
        },
        "facilities_per_grade": gtab,
        "evidence_strength_at_grade_2_plus": stab,
        "v0_1_identity": v01,
        "grade_movement_vs_previous_file": movement,
    })

    checks = run_checks(fac, facts, fact_tags, grades, version, out_dir, ours, v01)

    # ---------------------------------------------------------------- summary
    print(f"WP3 regex baseline (recipe {version}) -> {out_dir}  [{time.time() - t0:.1f}s]")
    print(f"  fact_tags.parquet  {len(fact_tags):,} rows ({n_distinct:,} distinct texts tagged; "
          f"{int(fact_tags['tags'].map(len).gt(0).sum()):,} facts with any tag)")
    print(f"  capability_grades.parquet  {len(grades):,} rows ({len(fac):,} facilities x {len(lines)} lines)")
    print("  Facilities per grade (none / claimed / supported / corroborated) | grade 2+ by strength "
          "(thin / moderate / strong):")
    for line in lines:
        d, s = gtab[line], stab[line]
        print(f"    {line:16s}" + "".join(f" {v:>5,}" for v in d.values())
              + f"  | 2+: {d['supported'] + d['corroborated']:>5,}  ({s['thin']:,} / {s['moderate']:,} / {s['strong']:,})")
    print("  Tags (facts / distinct texts / facilities):")
    for t, d in ttab.items():
        print(f"    {t:16s} {d['facts']:>7,} {d['distinct_texts']:>7,} {d['facilities']:>6,}")
    if supp:
        print("  v0.2 tags, distinct texts by outcome (fires: pattern, name_pattern; suppressed: the rest):")
        for t, c in supp.items():
            print(f"    {t:16s} " + ", ".join(f"{k} {v:,}" for k, v in c.items()))
    sc = grades.drop_duplicates("facility_id")["source_confidence"]
    print(f"  source_confidence per facility: quartiles {sc.quantile([.25, .5, .75]).round(3).tolist()}, "
          f"mean {sc.mean():.3f}")
    print("  v0.1 counts compared with the recipes doc (ours: facts only, deduped facilities; "
          "doc: facts + description, 10,000 raw rows):")
    for k, v in PROFILE.items():
        print(f"    {k:24s} {ours[k]:>6,}  doc {v:>6,}  ({(ours[k] - v) / v:+.1%})")
    if movement is not None:
        print(f"  Grade movement against the previous regex grades (recipe {movement['previous_recipe_version']} "
              f"-> {version}), lines in both:")
        for line, mv in movement["lines"].items():
            print(f"    {line:9s} changed {mv['changed']:>4,}  evidence/tags_present changed "
                  f"{mv['evidence_or_tags_present_changed']:,}  grade>=2 {mv['grade_ge2_before']:,} -> "
                  f"{mv['grade_ge2_after']:,}  grade 3 {mv['grade_3_before']:,} -> {mv['grade_3_after']:,}")
    if args.spot:
        new_lines = [l for l in lines if l not in recipes.SERVICE_LINES_V01]
        spot_read(fac, grades, new_lines, args.spot, args.seed)
    print("Acceptance checks")
    for c in checks:
        print(f"  [{'PASS' if c['passed'] else 'FAIL'}] {c['check']}: {c['value']}")
    return 0 if all(c["passed"] for c in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
