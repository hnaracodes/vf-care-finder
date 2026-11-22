#!/usr/bin/env python
"""WP3 part 2 (v0.2): draw the candidate facts for the gold set extension.

Recipe v0.2 adds 11 evidence tags (docs/06_capability_recipes.md, section
"v0.2: twelve service lines"). This script picks which new facts get hand
labels. It labels nothing.

The strata are drawn with the loose *starter* patterns copied from the recipes
doc, on purpose not with the final v0.2 regexes in src/vf/recipes.py: the gold
set must not be shaped by the regex it will score.

Outputs (both in labels/, each with a .meta.json sidecar):

  gold_v02_candidates.csv   text_norm, stratum, chunk
      Distinct text_norm values from silver/facts.parquet that are not
      already in labels/gold_facts.csv. Strata, drawn in this order, each
      excluding texts already drawn:
        hit_<tag>   the tag's starter pattern matches (about 20 per tag;
                    rarest tag drawn first)
        nm_<tag>    a near-miss keyword for the tag matches and the tag's
                    starter pattern does not (about 8 per tag)
        random      uniform over all remaining distinct texts (40)
      chunk is 1 to 4: rows are shuffled with a fixed seed and dealt
      round-robin within each stratum, so every chunk gets a near-equal
      share of every stratum and chunk sizes differ by at most one.

  gold_v02_relabel_old.csv  text_norm, chunk
      The existing gold rows, so labelers can add the 11 new tags to them.
      Shuffled with a fixed seed and dealt into chunks 1 to 4.

Run:

  .venv/bin/python scripts/wp3_gold_v02_sample.py

Read the outputs with ``pd.read_csv(path, dtype=str, keep_default_na=False)``.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from vf import paths

SCRIPT = "scripts/wp3_gold_v02_sample.py"
SEED = 20261123
N_CHUNKS = 4
N_HIT = 20
N_NEAR = 8
N_RANDOM = 40
CANDIDATES_FILE = "gold_v02_candidates.csv"
RELABEL_FILE = "gold_v02_relabel_old.csv"
GOLD_FILE = "gold_facts.csv"

#: Starter patterns, copied verbatim from docs/06_capability_recipes.md (v0.2,
#: "New evidence tags"), with the table's escaped pipes written as plain pipes.
#: Used only to draw strata. Not the final v0.2 regexes.
STARTER: dict[str, str] = {
    "neonatal_resp": r"\bcpap\b|bubble cpap|neonatal ventilat|infant ventilat",
    "cardiac_surgery": r"\bcabg\b|bypass surgery|open heart|valve (replacement|repair)|cardiac surg|"
                       r"cardiothoracic surg|\bctvs\b",
    "cath_lab": r"cath ?lab|angioplast|coronary angiograph|\bptca\b|\bpci\b|(coronary|cardiac) stent",
    "ccu": r"\bccu\b|\biccu\b|cardiac (intensive|critical) care|coronary care unit",
    "mri": r"\bmri\b|magnetic resonance",
    "ct": r"\bct ?scan|computed tomograph|\d+[- ]?slice|\bmdct\b|\bhrct\b",
    "trauma": r"trauma (cent(re|er)|care|unit|ward|team|surgery|services)|polytrauma|"
              r"accident (and|&) emergency",
    "neurosurgery": r"neurosurg|craniotom|brain tumou?r (surgery|removal)|(spine|spinal) surgery|"
                    r"head injur(y|ies) (management|surgery|care)",
    "burns": r"\bburns? (unit|ward|icu|care|centre|center|management|treatment)|burn injur",
    "chemo": r"chemotherap|\bchemo\b|day care chemo",
    "radiotherapy": r"radiotherap|radiation (therapy|oncology)|\blinac\b|linear accelerator|brachytherap|"
                    r"\bcobalt[- ]?60\b",
}

#: Near-miss keywords: related words a labeler must judge. A text is a near
#: miss for a tag when one of these matches and the tag's starter pattern does
#: not. Written from the tag definitions and the "watch for" column, not from
#: fact texts or labels.
NEAR_MISS: dict[str, str] = {
    "neonatal_resp": r"neonat|newborn|new-born|new born|infant|preterm|premature|\bnicu\b|\bsncu\b|"
                     r"surfactant|high[- ]flow|\bhfnc\b|oxygen hood|\bbipap\b|\bniv\b|sleep apn",
    "cardiac_surgery": r"cardiac|cardio|heart|bypass|valve|thoracic|pacemaker|bariatric",
    "cath_lab": r"cardiology|angiogra|stent|catheter|coronary|heart attack|\becho|\btmt\b",
    "ccu": r"cardiac|coronary|heart|cardiology|critical care|intensive care|\bicu\b",
    "mri": r"scan|radiolog|imaging|magnetic|\bmr\b|tesla|diagnostic",
    "ct": r"scan|radiolog|imaging|tomograph|\bct\b|slice|\bpet\b",
    "trauma": r"trauma|accident|injur|fracture|emergency|casualty",
    "neurosurgery": r"neuro|brain|spine|spinal|head injur|skull|stroke",
    "burns": r"burn|plastic surg|skin graft|scald",
    "chemo": r"cancer|oncolog|tumou?r|malignan|carcinoma|cytotoxic|infusion",
    "radiotherapy": r"radiolog|radiation|cancer|oncolog|tumou?r|cobalt|gamma knife|\bimrt\b|\bigrt\b|"
                    r"cyberknife|tomotherapy",
}

NEW_TAGS = list(STARTER)
_STARTER_RX = {t: re.compile(p) for t, p in STARTER.items()}
_NEAR_RX = {t: re.compile(p) for t, p in NEAR_MISS.items()}


# --------------------------------------------------------------------------
# helpers (same conventions as scripts/wp3_gold_sample.py)
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
            ["git", "--no-optional-locks", "status", "--porcelain", "--", SCRIPT],
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


def deal_chunks(strata: pd.Series, rng: np.random.Generator) -> np.ndarray:
    """Chunk 1..N_CHUNKS per row: shuffle, then deal round-robin stratum by stratum.

    Dealing continues from where the previous stratum stopped, so each stratum
    is split as evenly as possible and chunk totals differ by at most one.
    """
    order = rng.permutation(len(strata))
    # stable sort by stratum keeps the random order within each stratum
    by_stratum = sorted(order, key=lambda i: strata.iloc[i])
    chunk = np.empty(len(strata), dtype=int)
    for k, i in enumerate(by_stratum):
        chunk[i] = k % N_CHUNKS + 1
    return chunk


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main() -> int:
    src = paths.silver_dir() / "facts.parquet"
    gold_path = paths.labels_dir() / GOLD_FILE
    for p in (src, gold_path):
        if not p.exists():
            raise SystemExit(f"missing input: {p}")

    facts = pq.read_table(src, columns=["text_norm"]).to_pandas()
    texts = pd.Series(sorted(facts["text_norm"].dropna().unique()), name="text_norm")
    gold = read_csv(gold_path)
    gold_set = set(gold["text_norm"])
    pool = texts[~texts.isin(gold_set)].reset_index(drop=True)

    hit = pd.DataFrame({t: pool.map(lambda s, rx=rx: bool(rx.search(s))) for t, rx in _STARTER_RX.items()})
    near = pd.DataFrame({t: pool.map(lambda s, rx=rx: bool(rx.search(s))) for t, rx in _NEAR_RX.items()})
    near = near & ~hit

    rng = np.random.default_rng(SEED)
    taken: set[int] = set()
    picks: list[tuple[int, str]] = []
    population: dict[str, int] = {}
    stratum_order: list[str] = []

    def draw(mask: pd.Series, name: str, n: int) -> None:
        idx = np.array([i for i in np.flatnonzero(mask.to_numpy()) if i not in taken], dtype=int)
        population[name] = int(mask.sum())
        k = min(n, len(idx))
        chosen = rng.choice(idx, size=k, replace=False) if k else np.array([], dtype=int)
        for i in np.sort(chosen):
            taken.add(int(i))
            picks.append((int(i), name))
        stratum_order.append(name)

    # hits first, rarest tag first, so small strata are not eaten by large ones
    hit_order = sorted(NEW_TAGS, key=lambda t: (int(hit[t].sum()), NEW_TAGS.index(t)))
    for t in hit_order:
        draw(hit[t], f"hit_{t}", N_HIT)
    near_order = sorted(NEW_TAGS, key=lambda t: (int(near[t].sum()), NEW_TAGS.index(t)))
    for t in near_order:
        draw(near[t], f"nm_{t}", N_NEAR)
    draw(pd.Series(True, index=pool.index), "random", N_RANDOM)

    cand = pd.DataFrame({"text_norm": [pool.iloc[i] for i, _ in picks],
                         "stratum": [s for _, s in picks]})
    assert cand["text_norm"].is_unique
    assert not cand["text_norm"].isin(gold_set).any()
    cand["chunk"] = deal_chunks(cand["stratum"], rng)
    # final order: by chunk, shuffled within chunk
    cand = cand.iloc[rng.permutation(len(cand))].sort_values("chunk", kind="stable").reset_index(drop=True)

    old = gold[["text_norm"]].copy()
    old["chunk"] = deal_chunks(pd.Series([""] * len(old)), np.random.default_rng(SEED + 1))
    old = old.sort_values("chunk", kind="stable").reset_index(drop=True)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    gi = git_info()
    inputs = {"silver/facts.parquet": {"sha256": sha256(src)},
              f"labels/{GOLD_FILE}": {"sha256": sha256(gold_path)}}
    taken_counts = cand["stratum"].value_counts().to_dict()
    hits_in_sample = {t: int(cand["text_norm"].map(lambda s, rx=_STARTER_RX[t]: bool(rx.search(s))).sum())
                      for t in NEW_TAGS}
    chunk_counts = {int(k): int(v) for k, v in cand["chunk"].value_counts().sort_index().items()}

    meta = {
        "output": f"labels/{CANDIDATES_FILE}",
        "created_at_utc": now,
        **gi,
        "recipe_version": "v0.2 starter patterns (docs/06_capability_recipes.md), not the final v0.2 regexes",
        "script": SCRIPT,
        "inputs": inputs,
        "seed": SEED,
        "unit": "distinct text_norm in silver/facts.parquet, excluding labels/gold_facts.csv",
        "population_distinct_texts": int(len(texts)),
        "pool_distinct_texts": int(len(pool)),
        "starter_patterns": STARTER,
        "near_miss_patterns": NEAR_MISS,
        "strata_order": stratum_order,
        "strata_rule": "hit_<tag>: starter pattern matches; nm_<tag>: near-miss keyword matches and the "
                       "tag's starter pattern does not; random: uniform over the rest. Drawn in "
                       "strata_order, each excluding texts already drawn. Not a partition, so there are "
                       "no design weights: use for scoring and positive counts, not population estimates.",
        "strata": {k: {"eligible": population[k], "sampled": int(taken_counts.get(k, 0))}
                   for k in stratum_order},
        "starter_hits_in_sample_any_stratum": hits_in_sample,
        "chunks": chunk_counts,
        "labeled": False,
        "use": "candidates for hand labels; the gold set is for scoring only, never training or regex writing",
    }
    write_csv(cand, paths.labels_dir() / CANDIDATES_FILE, meta)

    meta_old = {
        "output": f"labels/{RELABEL_FILE}",
        "created_at_utc": now,
        **gi,
        "script": SCRIPT,
        "inputs": {f"labels/{GOLD_FILE}": inputs[f"labels/{GOLD_FILE}"]},
        "seed": SEED + 1,
        "purpose": "existing gold rows, to be relabeled for the 11 v0.2 tags only; their v0.1 tags stay as is",
        "new_tags": NEW_TAGS,
        "chunks": {int(k): int(v) for k, v in old["chunk"].value_counts().sort_index().items()},
    }
    write_csv(old, paths.labels_dir() / RELABEL_FILE, meta_old)

    print(f"candidates -> {paths.labels_dir() / CANDIDATES_FILE}  ({len(cand)} texts from a pool of "
          f"{len(pool):,} distinct, {len(texts) - len(pool)} gold texts excluded; seed {SEED})")
    print(f"{'stratum':24s} {'eligible':>9s} {'sampled':>8s}")
    for k in stratum_order:
        print(f"{k:24s} {population[k]:>9,d} {taken_counts.get(k, 0):>8d}")
    print("chunks:", chunk_counts)
    print("starter hits in sample (any stratum):", ", ".join(f"{t} {n}" for t, n in hits_in_sample.items()))
    print(f"relabel old -> {paths.labels_dir() / RELABEL_FILE}  ({len(old)} rows), chunks:",
          meta_old["chunks"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
