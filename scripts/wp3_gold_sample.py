#!/usr/bin/env python
"""WP3 part 2: draw the gold-set sample, then assemble the hand labels.

The gold set is about 400 distinct facts (``text_norm``) labeled by hand with
the recipe v0 taxonomy (src/vf/recipes.py). It is used only to score taggers
(regex, teacher, student). It is never used for training, prompt tuning, or
choosing thresholds.

Two steps, two subcommands:

  sample    Partition every distinct text_norm in silver/facts.parquet into
            strata, draw a fixed-seed random sample from each, and write
            labels/gold_sample.csv (the design: stratum, population size,
            sample size, weight). With --worksheet PATH it also writes a blind
            worksheet (gold_id and text only, shuffled, no stratum, no regex
            tags) for the labeler.

  assemble  Read the labeler's TSV (gold_id <TAB> tags <TAB> note, tags
            pipe-separated, empty for none), validate it against the sample,
            and write labels/gold_facts.csv in the contract shape:
            text_norm, tags, labeled_by, note.

Strata (a partition: each distinct text is in exactly one, taken in this
order, so stratum weights N_h / n_h give population estimates):

  fm_*     known regex failure modes, whatever else the regex tags
  re_<t>   texts the v0 regex tags, assigned to their rarest tag t
  nm_<t>   texts the regex does not tag at all that contain a near-miss
           keyword for tag t ("kidney", "critical", "round-the-clock", ...)
  random   everything else (no regex tag, no near-miss keyword)

Run:

  .venv/bin/python scripts/wp3_gold_sample.py sample --worksheet /path/to/worksheet.tsv
  .venv/bin/python scripts/wp3_gold_sample.py assemble --labels /path/to/labels.tsv

Read the outputs with ``pd.read_csv(path, dtype=str, keep_default_na=False)``
so that an empty tag list stays "" and texts such as "na" stay strings.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from vf import paths, recipes

SCRIPT = "scripts/wp3_gold_sample.py"
SEED = 20261121
SAMPLE_FILE = "gold_sample.csv"
GOLD_FILE = "gold_facts.csv"
LABELED_BY = "agent"
#: The design was drawn with the v0 tagger. recipes.RECIPE_VERSION moved on
#: (v0.1); the strata must not, or a rerun would no longer reproduce the
#: labeled sample. The tag taxonomy is the same in both versions.
DESIGN_RECIPE = "v0"
GOLD_COLUMNS = ["text_norm", "tags", "labeled_by", "note"]

# --------------------------------------------------------------------------
# stratum definitions
# --------------------------------------------------------------------------

#: Negation cue, looked for within NEG_WINDOW characters of a regex match.
NEG_CUE = re.compile(
    r"\b(no|not|without|unavailable|lacks?|lacking|absence of|absent|neither|nor|never|"
    r"non-availability|nil)\b"
)
NEG_WINDOW = 40

#: A real caesarean term. csection matches without one are word-internal
#: ("paediatric section").
REAL_CSECTION = re.compile(r"\bc[- ]?section|caesarean|cesarean|\blscs\b")
#: dialysis_unit branches other than h(a)emodialysis.
UNIT_WORDS = re.compile(r"dialysis (unit|machine|centre|center)|nephrologist")
#: Neonatal or paediatric context next to an icu match.
NEONATAL = re.compile(r"neonat|\bnicu\b|newborn|\bpicu\b|paediatric intensive|pediatric intensive")
#: ob_delivery branches other than the bare word "obstetric".
OB_NON_OBSTETRIC = re.compile(r"maternity|labou?r (room|ward)|normal delivery|childbirth")

#: Near-miss keywords, tried in this order on texts the regex leaves untagged.
NEAR_MISS: dict[str, str] = {
    "dialysis": r"kidney|renal|nephro|\bcrrt\b|\bcapd\b|peritoneal|fistula|haemodiafiltration|"
                r"hemodiafiltration|\bsled\b",
    "ventilator": r"ventilat|\bbipap\b|\bcpap\b|life support|respiratory support|\bniv\b",
    "nicu": r"neonat|newborn|new-born|new born|incubator|warmer|phototherapy|\bnbsu\b|premature|preterm",
    "icu": r"critical|intensive|\biccu\b|\bccu\b|\bmicu\b|\bsicu\b|\bpicu\b|\bhdu\b|high dependency|"
           r"\bitu\b|coronary care|cardiac care unit",
    "blood": r"blood|transfusion|plasma|platelet|apheresis",
    "anesthesia": r"sedation|epidural|nerve block|boyle|painless|anest|anaest",
    "scheme": r"insurance|cashless|yojana|scheme|\bechs\b|\besi\b|\besic\b|arogya|bhamashah|chiranjeevi|"
              r"swasthya|government|govt|pm-jay|pm jay|health card|\bbpl\b|\btpa\b|empanel",
    "h24": r"round-the-clock|around-the-clock|24\s*[x×\*]\s*7|24 by 7|twenty[- ]four|day and night|"
           r"always open|open all|\bnight|\b24\b|\b365\b",
    "ob_delivery": r"deliver|labou?r\b|birth|midwi|maternal|pregnan|antenatal|caesar|cesar",
    "emergency": r"accident|urgent|ambulance|injur|fracture|first aid|poison|snake ?bite|\bburns?\b",
    "ot": r"operation|operating|theatre|theater|\bot\b|\bots\b|surgical suite|laminar|surger|surgical",
}
_NEAR_MISS_RX = {t: re.compile(p) for t, p in NEAR_MISS.items()}

FM_DEFINITIONS: dict[str, str] = {
    "fm_csection_wordpart": "regex tags csection but no real caesarean term (word-internal 'c section', "
                            "e.g. 'paediatric section')",
    "fm_negation": f"regex tags something and a negation cue ({NEG_CUE.pattern}) occurs within "
                   f"{NEG_WINDOW} characters of a regex match",
    "fm_haemodialysis_only": "regex tags dialysis_unit only through h(a)emodialysis (no unit, machine, "
                             "centre or nephrologist)",
    "fm_neonatal_icu": "regex tags icu and the text has neonatal or paediatric intensive care words",
    "fm_obstetric_only": "regex tags ob_delivery only through the word 'obstetric' (no maternity, labour "
                         "room or ward, normal delivery, childbirth)",
}

#: Sample size per stratum. Rare evidence tags get more, because the regex-tag
#: strata are where most positives come from and each evidence tag needs at
#: least 25 gold positives. Strata smaller than their allocation are taken whole.
ALLOCATION: dict[str, int] = {
    "fm_csection_wordpart": 5,
    "fm_negation": 14,
    "fm_haemodialysis_only": 10,
    "fm_neonatal_icu": 10,
    "fm_obstetric_only": 16,
    "re_csection": 26,
    "re_ventilator": 24,
    "re_blood": 22,
    "re_dialysis_unit": 20,
    "re_nicu": 20,
    "re_scheme": 22,
    "re_anesthesia": 20,
    "re_dialysis": 14,
    "re_icu": 14,
    "re_ot": 14,
    "re_h24": 12,
    "re_emergency": 12,
    "re_ob_delivery": 14,
    "nm_dialysis": 6,
    "nm_ventilator": 8,
    "nm_nicu": 7,
    "nm_icu": 10,
    "nm_blood": 6,
    "nm_anesthesia": 6,
    "nm_scheme": 8,
    "nm_h24": 8,
    "nm_ob_delivery": 6,
    "nm_emergency": 6,
    "nm_ot": 6,
    "random": 30,
}

#: Phase 2 (top-up). After phase 1 was labeled, six evidence tags had fewer than
#: 25 gold positives (blood 22, anesthesia 19, ot 19, h24 24, dialysis_unit 24,
#: scheme 23). Their re_ strata had 90 to 100% positives, so a second random
#: draw from the same strata, excluding phase-1 texts, closes the gap. It uses
#: its own seed so phase 1 is unchanged. Weights use the combined stratum n.
SEED_PHASE2 = 20261122
TOPUP: dict[str, int] = {
    "re_blood": 6,
    "re_anesthesia": 9,
    "re_ot": 8,
    "re_h24": 3,
    "re_dialysis_unit": 3,
    "re_scheme": 4,
}

_TAG_RX = {t: re.compile(p) for t, p in recipes.TAG_PATTERNS_V0.items()}


def _neg_near_match(s: str, tags: list[str]) -> bool:
    for t in tags:
        for m in _TAG_RX[t].finditer(s):
            lo, hi = max(0, m.start() - NEG_WINDOW), min(len(s), m.end() + NEG_WINDOW)
            if NEG_CUE.search(s[lo:hi]):
                return True
    return False


def failure_mode(s: str, tags: list[str]) -> str | None:
    """The first failure-mode stratum a text falls in, or None."""
    if "csection" in tags and not REAL_CSECTION.search(s):
        return "fm_csection_wordpart"
    if tags and _neg_near_match(s, tags):
        return "fm_negation"
    if "dialysis_unit" in tags and not UNIT_WORDS.search(s):
        return "fm_haemodialysis_only"
    if "icu" in tags and NEONATAL.search(s):
        return "fm_neonatal_icu"
    if "ob_delivery" in tags and not OB_NON_OBSTETRIC.search(s):
        return "fm_obstetric_only"
    return None


def near_miss(s: str) -> str | None:
    for t, rx in _NEAR_MISS_RX.items():
        if rx.search(s):
            return f"nm_{t}"
    return None


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def git_info() -> dict:
    root = paths.code_root()
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                                capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        commit = None
    try:
        dirty = bool(subprocess.run(
            ["git", "--no-optional-locks", "status", "--porcelain", "--", SCRIPT,
             "src/vf/recipes.py", "src/vf/clean.py"],
            cwd=root, capture_output=True, text=True, check=True).stdout.strip())
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
    paths.ensure(path.parent)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        write(tmp)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def write_csv(df: pd.DataFrame, path: Path, meta: dict) -> None:
    write_atomic(path, lambda p: df.to_csv(p, index=False, quoting=csv.QUOTE_MINIMAL, lineterminator="\n"))
    sidecar = dict(meta, rows=int(len(df)), columns=list(df.columns))
    body = json.dumps(sidecar, indent=2, ensure_ascii=False, default=str) + "\n"
    write_atomic(path.with_name(path.name + ".meta.json"), lambda p: p.write_text(body))


def read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str, keep_default_na=False)


# --------------------------------------------------------------------------
# sample
# --------------------------------------------------------------------------

def distinct_texts() -> pd.DataFrame:
    """One row per distinct text_norm: display text, main field, counts, regex tags."""
    src = paths.silver_dir() / "facts.parquet"
    if not src.exists():
        raise SystemExit(f"missing input: {src}. Run scripts/wp1_silver_facilities.py first.")
    facts = pq.read_table(src, columns=["facility_id", "field", "text", "text_norm"]).to_pandas()
    g = facts.groupby("text_norm", sort=True)
    d = pd.DataFrame({
        "n_facts": g.size(),
        "n_facilities": g["facility_id"].nunique(),
        # most common original spelling and field, ties broken alphabetically
        "text": g["text"].agg(lambda x: x.value_counts().sort_index().idxmax()),
        "field": g["field"].agg(lambda x: x.value_counts().sort_index().idxmax()),
    }).reset_index()
    d["regex_tags"] = d["text_norm"].map(lambda s: recipes.tag_fact(s, version=DESIGN_RECIPE))
    return d


def assign_strata(d: pd.DataFrame) -> pd.Series:
    stratum = pd.Series([failure_mode(s, t) for s, t in zip(d["text_norm"], d["regex_tags"])],
                        index=d.index, dtype=object)
    # regex-tagged, not a failure mode: rarest tag, counted on what is left
    rest = stratum.isna() & d["regex_tags"].map(bool)
    counts = Counter(t for ts in d.loc[rest, "regex_tags"] for t in ts)
    order = {t: (counts[t], recipes.EVIDENCE_TAGS.index(t)) for t in recipes.EVIDENCE_TAGS}
    stratum[rest] = d.loc[rest, "regex_tags"].map(lambda ts: "re_" + min(ts, key=order.__getitem__))
    untagged = stratum.isna()
    stratum[untagged] = d.loc[untagged, "text_norm"].map(near_miss)
    stratum = stratum.fillna("random")
    unknown = set(stratum) - set(ALLOCATION)
    assert not unknown, unknown
    return stratum


def _draw_phase(d: pd.DataFrame, alloc: dict, rng: np.random.Generator, exclude: set,
                phase: int, first_id: int) -> pd.DataFrame:
    parts = []
    for name, n in alloc.items():
        # d is sorted by text_norm, so this order is stable
        pool = d.index[(d["stratum"] == name) & ~d.index.isin(list(exclude))]
        k = min(n, len(pool))
        pick = rng.choice(pool.to_numpy(), size=k, replace=False) if k else np.array([], dtype=int)
        parts.append(d.loc[np.sort(pick)])
    s = pd.concat(parts)
    # shuffle, then number, so the gold_id order reveals nothing about strata
    s = s.iloc[rng.permutation(len(s))].copy()
    s["phase"] = phase
    s.insert(0, "gold_id", [f"g{i:03d}" for i in range(first_id, first_id + len(s))])
    return s


def draw(d: pd.DataFrame) -> pd.DataFrame:
    s1 = _draw_phase(d, ALLOCATION, np.random.default_rng(SEED), set(), phase=1, first_id=1)
    s2 = _draw_phase(d, TOPUP, np.random.default_rng(SEED_PHASE2), set(s1.index), phase=2,
                     first_id=len(s1) + 1)
    s = pd.concat([s1, s2])
    N = d["stratum"].value_counts()
    n = s["stratum"].value_counts()
    s["stratum_size"] = s["stratum"].map(N).astype(int)
    s["stratum_n"] = s["stratum"].map(n).astype(int)
    s["weight"] = s["stratum_size"] / s["stratum_n"]
    return s.reset_index(drop=True)


def cmd_sample(args) -> int:
    d = distinct_texts()
    d["stratum"] = assign_strata(d)
    s = draw(d)
    assert s["text_norm"].is_unique

    pop = d["stratum"].value_counts().to_dict()
    taken = s["stratum"].value_counts().to_dict()
    out = s[["gold_id", "text_norm", "text", "field", "n_facts", "n_facilities",
             "stratum", "phase", "stratum_size", "stratum_n", "weight"]].copy()
    out["weight"] = out["weight"].round(6)
    src = paths.silver_dir() / "facts.parquet"
    meta = {
        "output": f"labels/{SAMPLE_FILE}",
        "created_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        **git_info(),
        "recipe_version": DESIGN_RECIPE,
        "script": SCRIPT,
        "inputs": {"silver/facts.parquet": {"sha256": sha256(src)}},
        "seed": SEED,
        "seed_phase2": SEED_PHASE2,
        "unit": "distinct text_norm (vf.clean.normalize_text), uniform within each stratum",
        "population_distinct_texts": int(len(d)),
        "strata_order": "fm_* (first match in the order below), then re_<rarest regex tag>, "
                        "then nm_<first near-miss group>, then random",
        "failure_modes": FM_DEFINITIONS,
        "near_miss_patterns": NEAR_MISS,
        "rarest_tag_order": "ascending count of distinct non-failure-mode texts carrying the tag",
        "phase2": "top-up drawn after phase 1 was labeled, because blood, anesthesia, ot, h24, "
                  "dialysis_unit and scheme had fewer than 25 gold positives; random within the "
                  "same strata, phase-1 texts excluded, own seed",
        "strata": {k: {"population": int(pop.get(k, 0)),
                       "sampled_phase1": int(min(n, pop.get(k, 0))),
                       "sampled_phase2": int(TOPUP.get(k, 0)),
                       "sampled": int(taken.get(k, 0))}
                   for k, n in ALLOCATION.items()},
        "rows_phase1": int((out["phase"] == 1).sum()),
        "rows_phase2": int((out["phase"] == 2).sum()),
        "weight": "stratum population / stratum sample size, both phases (for population estimates)",
        "use": "gold set design for scoring only; never for training or tuning",
    }
    path = paths.labels_dir() / SAMPLE_FILE
    write_csv(out, path, meta)

    if args.worksheet:
        ws = Path(args.worksheet)
        ws.parent.mkdir(parents=True, exist_ok=True)
        blind = out[["gold_id", "field", "text"]]
        blind.to_csv(ws, sep="\t", index=False, quoting=csv.QUOTE_NONE, escapechar="\\")
        print(f"blind worksheet: {ws}")

    print(f"gold sample -> {path}  ({len(out)} texts from {len(d):,} distinct; "
          f"phase 1 seed {SEED}: {meta['rows_phase1']}, phase 2 seed {SEED_PHASE2}: {meta['rows_phase2']})")
    print(f"{'stratum':24s} {'population':>10s} {'sampled':>8s}")
    for k in ALLOCATION:
        print(f"{k:24s} {pop.get(k, 0):>10,d} {taken.get(k, 0):>8d}")
    return 0


# --------------------------------------------------------------------------
# assemble
# --------------------------------------------------------------------------

def parse_labels(path: Path) -> pd.DataFrame:
    """Labeler TSV: gold_id, tags (pipe-separated), note. Lines starting with # are comments."""
    rows = []
    with open(path, encoding="utf-8") as fh:
        for ln, line in enumerate(fh, 1):
            line = line.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            cells = line.split("\t")
            if len(cells) < 2 or len(cells) > 3:
                raise SystemExit(f"{path}:{ln}: expected gold_id<TAB>tags[<TAB>note], got {line!r}")
            gid, tags = cells[0].strip(), cells[1].strip()
            note = cells[2].strip() if len(cells) == 3 else ""
            rows.append((ln, gid, tags, note))
    return pd.DataFrame(rows, columns=["line", "gold_id", "tags", "note"])


def normalize_tags(raw: str, where: str) -> str:
    tags = [t.strip() for t in raw.split("|") if t.strip()] if raw not in ("", "-") else []
    bad = [t for t in tags if t not in recipes.ALL_TAGS]
    if bad:
        raise SystemExit(f"{where}: unknown tag(s) {bad}")
    if len(set(tags)) != len(tags):
        raise SystemExit(f"{where}: repeated tag in {raw!r}")
    # canonical order: the taxonomy order
    return "|".join(t for t in recipes.ALL_TAGS if t in tags)


def cmd_assemble(args) -> int:
    sample_path = paths.labels_dir() / SAMPLE_FILE
    if not sample_path.exists():
        raise SystemExit(f"missing {sample_path}; run the sample step first")
    sample = read_csv(sample_path)
    labels_path = Path(args.labels)
    lab = parse_labels(labels_path)

    dup = lab["gold_id"][lab["gold_id"].duplicated()].tolist()
    if dup:
        raise SystemExit(f"gold_id labeled more than once: {dup}")
    unknown = sorted(set(lab["gold_id"]) - set(sample["gold_id"]))
    if unknown:
        raise SystemExit(f"labels for gold_ids not in the sample: {unknown}")
    missing = sorted(set(sample["gold_id"]) - set(lab["gold_id"]))
    if missing and not args.allow_partial:
        raise SystemExit(f"{len(missing)} sampled gold_ids have no label, e.g. {missing[:10]}")

    lab["tags"] = [normalize_tags(t, f"{labels_path}:{ln}") for ln, t in zip(lab["line"], lab["tags"])]
    gold = sample[["gold_id", "text_norm"]].merge(lab[["gold_id", "tags", "note"]], on="gold_id", how="inner")
    gold["labeled_by"] = LABELED_BY
    gold = gold[GOLD_COLUMNS]
    assert gold["text_norm"].is_unique

    pos = Counter(t for ts in gold["tags"] for t in ts.split("|") if t)
    ev_pos = {t: int(pos.get(t, 0)) for t in recipes.EVIDENCE_TAGS}
    meta = {
        "output": f"labels/{GOLD_FILE}",
        "created_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        **git_info(),
        "recipe_version": DESIGN_RECIPE,
        "script": SCRIPT,
        "inputs": {
            f"labels/{SAMPLE_FILE}": {"sha256": sha256(sample_path)},
            "hand_labels_tsv": {"path": str(labels_path), "sha256": sha256(labels_path)},
        },
        "labeled_by": LABELED_BY,
        "labeling": "each text labeled by meaning against the recipe v0 tag definitions, from a "
                    "blind worksheet (no stratum, no regex output); see reports/model_a_results.md "
                    "for the labeling rules",
        "use": "scoring only; never used for training, prompt tuning, or choosing thresholds",
        "positives_per_evidence_tag": ev_pos,
        "evidence_tags_under_25": [t for t, n in ev_pos.items() if n < 25],
        "positives_per_noise_tag": {t: int(pos.get(t, 0)) for t in recipes.NOISE_TAGS},
        "texts_with_no_tag": int((gold["tags"] == "").sum()),
        "texts_with_an_evidence_tag": int(gold["tags"].map(
            lambda s: any(t in recipes.EVIDENCE_TAGS for t in s.split("|") if t)).sum()),
        "partial": bool(missing),
    }
    path = paths.labels_dir() / GOLD_FILE
    write_csv(gold, path, meta)
    print(f"gold facts -> {path}  ({len(gold)} rows{', PARTIAL' if missing else ''})")
    print("evidence positives:", ", ".join(f"{t} {n}" for t, n in ev_pos.items()))
    short = meta["evidence_tags_under_25"]
    print("under 25:", ", ".join(short) if short else "none")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("sample", help="draw the stratified sample")
    p.add_argument("--worksheet", help="also write a blind worksheet TSV here (keep it out of the repo)")
    p.set_defaults(func=cmd_sample)
    p = sub.add_parser("assemble", help="validate hand labels and write gold_facts.csv")
    p.add_argument("--labels", required=True, help="labeler TSV: gold_id<TAB>tags<TAB>note")
    p.add_argument("--allow-partial", action="store_true", help="write even if some gold_ids are unlabeled")
    p.set_defaults(func=cmd_assemble)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
