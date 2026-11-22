#!/usr/bin/env python
"""WP3 part 2 (v0.2): merge the four labeled chunks into labels/gold_facts.csv.

Inputs (all in labels/):

  gold_facts.csv               the v0.1 gold set (429 rows), or, on a rerun,
  gold_facts.v01.bak.csv       its backup, which is then the source of truth
  gold_v02_candidates.csv      the 348 new candidate texts, with their chunk
  gold_v02_relabel_old.csv     the 429 old texts, with their chunk
  gold_v02_part{1..4}.csv      labels: text_norm, source, tags, note
                               (source is "candidates" or "relabel_old")

What it does:

  1. Backs up the v0.1 file as gold_facts.v01.bak.csv (and its sidecar as
     gold_facts.v01.bak.csv.meta.json), once. A rerun reads the backup, so the
     merge is idempotent. The v0.1 file's sha256 must match the one recorded
     in gold_v02_relabel_old.csv.meta.json, so the merge never runs on a file
     the labelers did not see.
  2. Old rows: adds the labeled v0.2 tags to the existing tags. v0.1 tags are
     never dropped or edited. A non-empty relabel note is appended to the old
     note as " [v0.2: ...]".
  3. Candidate rows: appended after the old rows, in gold_v02_candidates.csv
     order, with labeled_by = agent and the labeler's note.
  4. Every row's tags are written in taxonomy order: v0.1 evidence tags, v0.2
     evidence tags, noise tags. Old rows that gained no tag keep their tags
     string byte for byte.

Validation (the script exits non-zero and leaves gold_facts.csv alone if any check fails):

  - each part covers exactly its chunk of candidates and relabel rows
  - no duplicate text_norm in the parts or in the output
  - every tag is in the v0.2 taxonomy
  - relabel rows carry only v0.2 tags (the v0.1 tags are not relabeled)
  - for every old row, dropping the v0.2 tags gives back the v0.1 tags string
    byte for byte, and text_norm and labeled_by are unchanged

Run:

  .venv/bin/python scripts/wp3_gold_v02_merge.py

Read the output with ``pd.read_csv(path, dtype=str, keep_default_na=False)``.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from vf import paths

SCRIPT = "scripts/wp3_gold_v02_merge.py"
GOLD_FILE = "gold_facts.csv"
BACKUP_FILE = "gold_facts.v01.bak.csv"
CANDIDATES_FILE = "gold_v02_candidates.csv"
RELABEL_FILE = "gold_v02_relabel_old.csv"
PART_FILES = [f"gold_v02_part{i}.csv" for i in range(1, 5)]
TARGET_POSITIVES = 25

#: Tag taxonomy, from docs/06_capability_recipes.md. Hardcoded here, not read
#: from vf.recipes, so the merge does not depend on the state of the v0.2
#: recipe code.
V01_EVIDENCE = ("ob_delivery", "csection", "blood", "anesthesia", "ot", "h24", "nicu",
                "dialysis", "dialysis_unit", "icu", "ventilator", "emergency", "scheme")
V02_EVIDENCE = ("neonatal_resp", "cardiac_surgery", "cath_lab", "ccu", "mri", "ct", "trauma",
                "neurosurgery", "burns", "chemo", "radiotherapy")
NOISE = ("reputation", "location", "staff_name", "accessibility", "photo_artifact",
         "web_presence", "other_clinical")
TAXONOMY = V01_EVIDENCE + V02_EVIDENCE + NOISE
V01_TAXONOMY = V01_EVIDENCE + NOISE
_ORDER = {t: i for i, t in enumerate(TAXONOMY)}

#: Starter patterns from the recipes doc, used only for the per-chunk
#: consistency diagnostic below (how often a starter hit was labeled positive,
#: per labeler). They do not affect any label.
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


# --------------------------------------------------------------------------
# helpers (same conventions as scripts/wp3_gold_v02_sample.py)
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


def split_tags(s: str) -> list[str]:
    return [t for t in s.split("|") if t]


def join_tags(tags) -> str:
    return "|".join(sorted(set(tags), key=_ORDER.__getitem__))


class MergeError(Exception):
    pass


def check(cond: bool, msg: str, errors: list[str]) -> None:
    if not cond:
        errors.append(msg)


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main() -> int:
    d = paths.labels_dir()
    gold_path, backup_path = d / GOLD_FILE, d / BACKUP_FILE
    relabel_meta = json.loads((d / (RELABEL_FILE + ".meta.json")).read_text())
    expected_sha = relabel_meta["inputs"]["labels/gold_facts.csv"]["sha256"]

    # 1. backup (once), then read the v0.1 file from the backup
    if not backup_path.exists():
        if sha256(gold_path) != expected_sha:
            raise MergeError(f"{GOLD_FILE} sha256 differs from the one the relabel sheet was drawn from "
                             f"({expected_sha}); refusing to merge")
        tmp = backup_path.with_name(f".{backup_path.name}.{os.getpid()}.tmp")
        shutil.copy2(gold_path, tmp)
        os.replace(tmp, backup_path)
        gold_meta = gold_path.with_name(GOLD_FILE + ".meta.json")
        if gold_meta.exists():
            tmp = backup_path.with_name(f".{BACKUP_FILE}.meta.json.{os.getpid()}.tmp")
            shutil.copy2(gold_meta, tmp)
            os.replace(tmp, backup_path.with_name(BACKUP_FILE + ".meta.json"))
        print(f"backup written: {backup_path}")
    else:
        print(f"backup exists, merging from it: {backup_path}")
    if sha256(backup_path) != expected_sha:
        raise MergeError(f"{BACKUP_FILE} sha256 differs from {expected_sha}; refusing to merge")

    old = read_csv(backup_path)
    cand = read_csv(d / CANDIDATES_FILE)
    rel = read_csv(d / RELABEL_FILE)
    parts = []
    for i, name in enumerate(PART_FILES, start=1):
        p = read_csv(d / name)
        p["chunk"] = str(i)
        parts.append(p)
    lab = pd.concat(parts, ignore_index=True)

    errors: list[str] = []

    # 2. structural checks on the labels
    check(list(old.columns) == ["text_norm", "tags", "labeled_by", "note"],
          f"unexpected v0.1 columns {list(old.columns)}", errors)
    check(not old.text_norm.duplicated().any(), "duplicate text_norm in the v0.1 file", errors)
    check(set(lab.source) <= {"candidates", "relabel_old"}, f"unknown source values {set(lab.source)}", errors)
    dups = lab.text_norm[lab.text_norm.duplicated(keep=False)].unique()
    check(len(dups) == 0, f"{len(dups)} texts labeled more than once across parts: {list(dups)[:5]}", errors)
    for i in range(1, 5):
        c = str(i)
        got_c = set(lab.text_norm[(lab.chunk == c) & (lab.source == "candidates")])
        got_r = set(lab.text_norm[(lab.chunk == c) & (lab.source == "relabel_old")])
        exp_c = set(cand.text_norm[cand.chunk == c])
        exp_r = set(rel.text_norm[rel.chunk == c])
        check(got_c == exp_c, f"part {i}: candidates differ from chunk {i} "
                              f"(missing {len(exp_c - got_c)}, extra {len(got_c - exp_c)})", errors)
        check(got_r == exp_r, f"part {i}: relabel rows differ from chunk {i} "
                              f"(missing {len(exp_r - got_r)}, extra {len(got_r - exp_r)})", errors)
    check(set(rel.text_norm) == set(old.text_norm), "relabel sheet does not cover the v0.1 file exactly", errors)
    check(not (set(cand.text_norm) & set(old.text_norm)), "a candidate text is already in the v0.1 file", errors)

    unknown = Counter(t for s in lab.tags for t in split_tags(s) if t not in _ORDER)
    check(not unknown, f"tags not in the taxonomy: {dict(unknown)}", errors)
    rel_lab = lab[lab.source == "relabel_old"]
    non_v02 = Counter(t for s in rel_lab.tags for t in split_tags(s) if t not in V02_EVIDENCE)
    check(not non_v02, f"relabel rows carry tags outside the 11 v0.2 tags: {dict(non_v02)}", errors)
    in_cell_dups = int(sum(len(split_tags(s)) != len(set(split_tags(s))) for s in lab.tags))
    if errors:
        raise MergeError("; ".join(errors))

    # 3. merge old rows
    new_by_text = dict(zip(rel_lab.text_norm, rel_lab.tags))
    note_by_text = dict(zip(rel_lab.text_norm, rel_lab.note))
    merged_old = old.copy()
    n_gained = 0
    out_tags, out_notes = [], []
    for text, tags, note in zip(old.text_norm, old.tags, old.note):
        add = split_tags(new_by_text[text])
        if add:
            n_gained += 1
            out_tags.append(join_tags(split_tags(tags) + add))
        else:
            out_tags.append(tags)  # untouched, byte for byte
        rnote = note_by_text[text].strip()
        out_notes.append(f"{note} [v0.2: {rnote}]".strip() if rnote else note)
    merged_old["tags"] = out_tags
    merged_old["note"] = out_notes

    # 4. candidate rows, in candidates-file order
    cand_lab = lab[lab.source == "candidates"].set_index("text_norm")
    new_rows = pd.DataFrame({
        "text_norm": cand.text_norm,
        "tags": [join_tags(split_tags(cand_lab.at[t, "tags"])) for t in cand.text_norm],
        "labeled_by": "agent",
        "note": [cand_lab.at[t, "note"] for t in cand.text_norm],
    })
    out = pd.concat([merged_old, new_rows], ignore_index=True)

    # 5. validate the output
    check(not out.text_norm.duplicated().any(), "duplicate text_norm in the merged file", errors)
    check(len(out) == len(old) + len(cand), f"row count {len(out)} != {len(old)} + {len(cand)}", errors)
    bad = Counter(t for s in out.tags for t in split_tags(s) if t not in _ORDER)
    check(not bad, f"merged file has tags outside the taxonomy: {dict(bad)}", errors)
    head = out.iloc[: len(old)]
    v01_mismatch = [
        t for t, before, after in zip(old.text_norm, old.tags, head.tags)
        if "|".join(x for x in after.split("|") if x and x not in V02_EVIDENCE) != before
    ]
    check(not v01_mismatch, f"{len(v01_mismatch)} old rows changed their v0.1 tags", errors)
    check((head.text_norm.values == old.text_norm.values).all(), "old row order or text changed", errors)
    check((head.labeled_by.values == old.labeled_by.values).all(), "old labeled_by changed", errors)
    old_v02 = Counter(t for s in old.tags for t in split_tags(s) if t in V02_EVIDENCE)
    check(not old_v02, f"v0.1 file already had v0.2 tags: {dict(old_v02)}", errors)
    untouched = sum(1 for b, a in zip(old.tags, head.tags) if b == a)
    if errors:
        raise MergeError("; ".join(errors))

    # 6. counts
    is_old = pd.Series([True] * len(old) + [False] * len(cand))
    def positives(mask) -> Counter:
        return Counter(t for s in out.tags[mask] for t in split_tags(s))
    pos_all, pos_old, pos_new = positives(is_old | ~is_old), positives(is_old), positives(~is_old)
    evidence = V01_EVIDENCE + V02_EVIDENCE
    under = [t for t in evidence if pos_all[t] < TARGET_POSITIVES]

    # labeler consistency: per chunk, starter-pattern hits among candidates and
    # how many of them the chunk's labeler tagged positive
    consistency = {}
    for t, pat in STARTER.items():
        rx = re.compile(pat)
        row = {}
        for i in range(1, 5):
            sub = lab[(lab.chunk == str(i)) & (lab.source == "candidates")]
            hit = sub[[bool(rx.search(x)) for x in sub.text_norm]]
            row[str(i)] = f"{int(sum(t in split_tags(s) for s in hit.tags))}/{len(hit)}"
        consistency[t] = row

    meta = {
        "output": "labels/gold_facts.csv",
        "created_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        **git_info(),
        "recipe_version": "v0.2",
        "script": SCRIPT,
        "inputs": {f"labels/{n}": {"sha256": sha256(d / n)}
                   for n in [BACKUP_FILE, CANDIDATES_FILE, RELABEL_FILE, *PART_FILES]},
        "backup_of_v01": f"labels/{BACKUP_FILE}",
        "labeled_by": "agent",
        "labeling": ("v0.1 rows: unchanged v0.1 labels (see the backup's sidecar), plus the 11 v0.2 tags "
                     "added by four agent labelers, one per chunk. Candidate rows: labeled by the same four "
                     "agents against the full v0.2 taxonomy. Labelers did not see the regex output."),
        "use": "scoring only; never used for training, prompt tuning, regex writing, or choosing thresholds",
        "rows_v01": int(len(old)),
        "rows_added": int(len(new_rows)),
        "old_rows_gaining_v02_tags": n_gained,
        "old_rows_tags_byte_identical": int(untouched),
        "positives_per_evidence_tag": {t: int(pos_all[t]) for t in evidence},
        "positives_per_v02_tag_split": {t: {"old_rows": int(pos_old[t]), "candidates": int(pos_new[t])}
                                        for t in V02_EVIDENCE},
        "evidence_tags_under_25": under,
        "positives_per_noise_tag": {t: int(pos_all[t]) for t in NOISE},
        "texts_with_no_tag": int((out.tags == "").sum()),
        "texts_with_an_evidence_tag": int(sum(any(x in evidence for x in split_tags(s)) for s in out.tags)),
        "starter_hit_positive_rate_by_chunk": consistency,
        "known_issues": [
            "Four labelers, one per chunk; they may differ on specialty or department names that are the "
            "service itself (neurosurgery, radiation oncology, cardiothoracic surgery in lists). See "
            "starter_hit_positive_rate_by_chunk.",
            "Agent-labeled, so weaker evidence than human labels.",
        ],
        "partial": False,
    }
    write_csv(out, gold_path, meta)

    # 7. summary
    print(f"wrote {gold_path}: {len(out)} rows ({len(old)} v0.1 + {len(new_rows)} candidates)")
    print(f"old rows gaining a v0.2 tag: {n_gained}; old rows with tags byte-identical: {untouched}/{len(old)}")
    print(f"validation: no duplicate text_norm, all tags in taxonomy, v0.1 tags unchanged on all {len(old)} "
          f"old rows; in-cell duplicate tags collapsed: {in_cell_dups}")
    print("positives per evidence tag (all / old rows / candidates):")
    for t in evidence:
        flag = "  < 25" if pos_all[t] < TARGET_POSITIVES else ""
        print(f"  {t:16s} {pos_all[t]:4d} {pos_old[t]:4d} {pos_new[t]:4d}{flag}")
    print("noise tags:", {t: int(pos_all[t]) for t in NOISE})
    print(f"texts with no tag: {meta['texts_with_no_tag']}")
    print(f"evidence tags under {TARGET_POSITIVES}: {under or 'none'}")
    print("starter hits labeled positive, by chunk (1, 2, 3, 4):")
    for t, row in consistency.items():
        print(f"  {t:16s} " + "  ".join(row[str(i)] for i in range(1, 5)))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except MergeError as e:
        print(f"merge failed, gold_facts.csv not changed: {e}", file=sys.stderr)
        sys.exit(1)
