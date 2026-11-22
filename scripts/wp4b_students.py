#!/usr/bin/env python
"""WP4b retrain stage (D050): student 1b on the merged labels, scored with the encoders against the shipped tagger.

Candidates (round 2, all trained on labels/wp4b/merged_labels.parquet):

  1b_r2  student 1b, the WP4 code unchanged (scripts/wp4_student.py Student, use_regex=True):
         TF-IDF word 1-2 and char 2-5 grams plus the 24 regex v0.2 tags as features,
         one logistic regression per evidence tag, C and threshold tuned on validation F1.
  2a_r2  frozen sentence embeddings + logistic regression, data/models/wp4b_encoder/r2_2a
  2b_r2  fine-tuned bge-small, data/models/wp4b_encoder/r2_2b
         (both from: .venv-ml/bin/python scripts/wp4b_encoder.py --labels <merged> --run r2)

Split: the WP4 split, as scripts/wp4b_encoder.py applies it. Validation is every merged-label
text in the frozen WP4 validation list (data/models/wp4b_encoder/wp4_split.json, 5,859 texts,
seed 20261121); every other merged-label text trains. So all three candidates are tuned and
compared on the same validation texts and the same labels.

Current method: model_version in gold/fact_tags.parquet.meta.json (WP4 shipped mix: WP4
student 1b on 4 tags, regex v0.2 on the rest). Its tags are read from gold/fact_tags.parquet
itself (per distinct text_norm), and checked against a rebuild from the saved WP4 student and
the regex.

Scores, per candidate and evidence tag:
  - validation F1 against the merged labels;
  - gold F1 with the stratified bootstrap of scripts/wp3_score.py (2,000 resamples);
  - the paired stratified bootstrap 95% interval of F1(candidate) - F1(current) on gold
    (scripts/wp4_student.py paired_delta, same resamples for every candidate).
For every (candidate, tag) whose paired interval lies entirely above 0: 25 random distinct
non-gold texts from all 275,095 distinct fact texts where the candidate's tag differs from the
current method's (additions and removals both), written blinded for readers who did not build
the candidate.

The gold set (labels/gold_facts.csv) is used for scoring only: never for training, C,
thresholds or encoder choice, and its texts (and near duplicates) are excluded from the draws.

Writes:
  data/models/wp4b_student1b/student_1b_r2.pkl (+ .meta.json)  fitted model (plain dict of sklearn objects)
  data/models/wp4b_student1b/scores.parquet (+ .meta.json)     every distinct text: p_<tag>, tags
  data/models/wp4b_student1b/summary.json                      settings, per-tag C/threshold, validation, timings
  data/labels/wp4b/scores.json (+ .meta.json)                  every number above
  data/labels/wp4b/pop_blind_<tag>_<candidate>.csv (+ .meta.json)  item_id, text, tag (shuffled, no model info)
  data/labels/wp4b/pop_key.csv (+ .meta.json)                  item_id, candidate, tag, candidate_says, current_says

Makes no LLM calls.

Run:  .venv/bin/python scripts/wp4b_students.py               # train 1b_r2, score, draw
      .venv/bin/python scripts/wp4b_students.py --reuse-1b    # score and draw from the saved 1b_r2
      .venv/bin/python scripts/wp4b_students.py --dry-run     # print only, write nothing
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import pickle
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import scipy.sparse as sp

from vf import paths, recipes

SCRIPT = "scripts/wp4b_students.py"
VERSION = "wp4b-students-1"
TAGS = list(recipes.EVIDENCE_TAGS)
ROOT = paths.code_root()
DATA = paths.data_dir()
MERGED = paths.labels_dir() / "wp4b" / "merged_labels.parquet"
OUT_DIR = paths.labels_dir() / "wp4b"
S1B_DIR = DATA / "models" / "wp4b_student1b"
ENC_DIR = DATA / "models" / "wp4b_encoder"
WP4_DIR = DATA / "models" / "wp4_student"
FACT_TAGS = paths.gold_dir() / "fact_tags.parquet"
WP4_REPORT = paths.gold_dir() / "model_a_report.json"
LEDGERS = {
    "teacher_cache (WP3 Haiku teacher)": paths.labels_dir() / "teacher_cache" / "spend.jsonl",
    "eval_sealed (judges)": paths.labels_dir() / "eval_sealed" / "spend.jsonl",
    "wp4b (this run)": paths.labels_dir() / "wp4b" / "spend.jsonl",
}
N_DRAW = 25
DRAW_SEED = 20261124
CANDIDATES = {
    "1b_r2": {"what": "student 1b retrained on the merged labels: TF-IDF (word 1-2, char 2-5) + the 24 regex v0.2 "
                      "tags as features, one logistic regression per tag (scripts/wp4_student.py code)",
              "dir": S1B_DIR},
    "2a_r2": {"what": "frozen sentence embeddings + logistic regression per tag, trained on the merged labels "
                      "(scripts/wp4b_encoder.py --run r2)", "dir": ENC_DIR / "r2_2a"},
    "2b_r2": {"what": "BAAI/bge-small-en-v1.5 fine-tuned with a multi-label head, trained on the merged labels "
                      "(scripts/wp4b_encoder.py --run r2)", "dir": ENC_DIR / "r2_2b"},
}
#: Reported for context only: not candidates in this stage, no draws.
REFERENCES = {
    "regex_v0.2": "recipe v0.2 regexes (src/vf/recipes.py)",
    "wp4_student_1b": "WP4 student 1b (data/models/wp4_student/student_1b.pkl), trained on the Haiku labels",
    "r1_2a": "round 1 encoder 2a (data/models/wp4b_encoder/r1_2a), trained on the Haiku labels (setup round)",
    "r1_2b": "round 1 encoder 2b (data/models/wp4b_encoder/r1_2b), trained on the Haiku labels (setup round)",
}


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


W4 = _load_script("wp4_student")   # Student, best_threshold, val_scores, Y_of, sets_of, paired_delta
WS = W4.WS                          # wp3_score: load_gold, score_tags, _prf, N_BOOT, BOOT_SEED
RB = W4.RB                          # wp3_regex_baseline: write_atomic, sha256, git_info


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def rel(p: Path) -> str:
    p = Path(p).resolve()
    if p.is_relative_to(DATA.resolve()):
        return str(p.relative_to(DATA.resolve()))
    if p.is_relative_to(ROOT.resolve()):
        return str(p.relative_to(ROOT.resolve()))
    return str(p)


def write_json(path: Path, obj) -> None:
    body = json.dumps(obj, indent=2, ensure_ascii=False, default=str) + "\n"
    RB.write_atomic(path, lambda p: p.write_text(body, encoding="utf-8"))


def write_csv(path: Path, df: pd.DataFrame, meta: dict) -> None:
    RB.write_atomic(path, lambda p: df.to_csv(p, index=False))
    write_json(path.with_name(path.name + ".meta.json"),
               dict(meta, output=rel(path), rows=len(df), columns=list(df.columns), sha256=RB.sha256(path)))


def git_info() -> dict:
    import subprocess
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                                check=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "--no-optional-locks", "status", "--porcelain", "--", SCRIPT,
                                     "scripts/wp4_student.py", "scripts/wp4b_encoder.py", "src/vf/recipes.py"],
                                    cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip())
    except Exception:
        commit, dirty = None, None
    return {"git_commit": commit, "code_uncommitted": dirty}


def near_key(t: str) -> str:
    """Lowercase, punctuation dropped, spaces collapsed (the near-duplicate rule of merged_labels)."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", t.lower())).strip()


# --------------------------------------------------------------------------
# data
# --------------------------------------------------------------------------

def load_merged() -> pd.DataFrame:
    m = pd.read_parquet(MERGED)
    m["tags"] = m["tags"].map(lambda xs: [x for x in xs if x in TAGS])
    if not m["text_norm"].is_unique:
        raise SystemExit("merged labels: text_norm not unique")
    return m.reset_index(drop=True)


def wp4_val_texts() -> list[str]:
    sp_ = json.loads((ENC_DIR / "wp4_split.json").read_text())
    h = hashlib.sha256()
    for t in sp_["val_texts"]:
        h.update(t.encode("utf-8")); h.update(b"\x00")
    if h.hexdigest() != sp_["val_sha256"]:
        raise SystemExit("wp4_split.json is corrupt")
    return sp_["val_texts"]


def distinct_texts() -> list[str]:
    t = pq.read_table(paths.silver_dir() / "facts.parquet", columns=["text_norm"]).column(0).to_pylist()
    return sorted(set(t))


def current_lookup(distinct: list[str]) -> tuple[dict, dict]:
    """Evidence tags of the shipped tagger per distinct text_norm, read from gold/fact_tags.parquet."""
    meta = json.loads((FACT_TAGS.with_name(FACT_TAGS.name + ".meta.json")).read_text())
    ft = pq.read_table(FACT_TAGS, columns=["fact_id", "tags", "model_version"]).to_pandas()
    facts = pq.read_table(paths.silver_dir() / "facts.parquet", columns=["fact_id", "text_norm"]).to_pandas()
    m = facts.merge(ft, on="fact_id", how="left", validate="one_to_one")
    if m["tags"].isna().any():
        raise SystemExit(f"{int(m['tags'].isna().sum())} silver facts have no row in gold/fact_tags.parquet")
    m["key"] = m["tags"].map(lambda xs: "|".join(x for x in xs if x in TAGS))
    per = m.groupby("text_norm")["key"].nunique()
    if (per > 1).any():
        raise SystemExit(f"{int((per > 1).sum())} texts carry different tags in gold/fact_tags.parquet")
    look = m.drop_duplicates("text_norm").set_index("text_norm")["key"]
    lut = {t: [x for x in look[t].split("|") if x] for t in distinct}
    versions = sorted(m["model_version"].unique().tolist())
    info = {"file": rel(FACT_TAGS), "sha256": RB.sha256(FACT_TAGS), "model_version": meta.get("model_version"),
            "model_versions_in_rows": versions, "tags_from_student": meta.get("tags_from_student"),
            "tags_from_regex": meta.get("tags_from_regex"), "created_at_utc": meta.get("created_at_utc"),
            "rows": int(len(ft)), "distinct_texts": len(lut)}
    return lut, info


# --------------------------------------------------------------------------
# student 1b
# --------------------------------------------------------------------------

def blob_of(s, name: str) -> dict:
    return {"name": name, "what": CANDIDATES["1b_r2"]["what"], "tags": list(TAGS), "use_regex_features": s.use_regex,
            "regex_feature_weight": W4.REGEX_FEATURE_WEIGHT, "regex_recipe_version": recipes.RECIPE_VERSION,
            "word_vectorizer": s.word, "char_vectorizer": s.char, "models": s.models,
            "thresholds": s.thresholds, "C": s.C,
            "usage": "X = hstack([word.transform(t), char.transform(t)] + ([regex tag indicators in "
                     "tags order * weight] if use_regex_features)); tag j fires when "
                     "models[tag].predict_proba(X)[:,1] >= thresholds[tag]"}


def blob_proba(blob: dict, texts: list[str], R: np.ndarray | None = None, chunk: int = 20000) -> np.ndarray:
    """Scores of a saved WP4-style student (float64), in chunks; R = regex indicators if precomputed."""
    out = np.zeros((len(texts), len(TAGS)))
    for s in range(0, len(texts), chunk):
        tt = texts[s:s + chunk]
        parts = [blob["word_vectorizer"].transform(tt), blob["char_vectorizer"].transform(tt)]
        if blob["use_regex_features"]:
            Rc = W4.Y_of(recipes.tag_facts(tt)) if R is None else R[s:s + chunk]
            parts.append(sp.csr_matrix(Rc.astype(np.float64) * blob["regex_feature_weight"]))
        X = sp.hstack(parts, format="csr")
        for j, t in enumerate(TAGS):
            m = blob["models"][t]
            if m is not None:
                out[s:s + len(tt), j] = m.predict_proba(X)[:, 1]
    return out


def blob_fire(blob: dict, P: np.ndarray) -> np.ndarray:
    return P >= np.array([blob["thresholds"][t] for t in TAGS])


def write_scores_parquet(path: Path, texts: list[str], P: np.ndarray, fired: np.ndarray, meta: dict) -> None:
    cols = {"text_norm": pa.array(texts, pa.string())}
    for j, t in enumerate(TAGS):
        cols[f"p_{t}"] = pa.array(P[:, j].astype(np.float32), pa.float32())
    cols["tags"] = pa.array([[TAGS[j] for j in np.flatnonzero(r)] for r in fired], pa.list_(pa.string()))
    table = pa.table(cols)
    RB.write_atomic(path, lambda p: pq.write_table(table, p))
    write_json(path.with_name(path.name + ".meta.json"),
               dict(meta, output=rel(path), rows=table.num_rows, columns=table.column_names,
                    texts_with_tag={t: int(fired[:, j].sum()) for j, t in enumerate(TAGS)}))


def train_1b(merged: pd.DataFrame, tr: np.ndarray, va: np.ndarray, distinct: list[str], R_all: np.ndarray,
             base_meta: dict, write: bool) -> dict:
    texts = merged["text_norm"].tolist()
    Y = W4.Y_of(merged["tags"])
    tr_t = [texts[i] for i in tr]; va_t = [texts[i] for i in va]
    t0 = time.time()
    s = W4.Student("student_1b_r2", True)
    per = s.fit(tr_t, Y[tr], va_t, Y[va])
    fit_secs = time.time() - t0
    blob = blob_of(s, "student_1b_r2")
    Pva_student = s.predict(va_t)
    t1 = time.time()
    P = blob_proba(blob, distinct, R_all)
    inf_secs = time.time() - t1
    fired = blob_fire(blob, P)
    idx = {t: i for i, t in enumerate(distinct)}
    same = bool((fired[[idx[t] for t in va_t]] == Pva_student).all())
    if not same:
        raise SystemExit("1b_r2: chunked inference disagrees with Student.predict on validation")
    vs = W4.val_scores(Y[va], Pva_student)
    print(f"  1b_r2: validation macro F1 {vs['macro_f1']:.4f} (micro {vs['micro_f1']:.4f}) vs merged labels; "
          f"fit {fit_secs:.0f}s; inference {len(distinct):,} texts {inf_secs:.0f}s")
    summary = {
        "variant": "1b_r2", "what": CANDIDATES["1b_r2"]["what"], "created_at_utc": now(),
        "settings": {"C_grid": list(W4.C_GRID), "solver": "liblinear, max_iter 2000",
                     "regex_feature_weight": W4.REGEX_FEATURE_WEIGHT,
                     "threshold_grid": "0.05 to 0.95 step 0.01, ties nearest 0.5 (wp4_student.best_threshold)",
                     "choice": "per tag C and threshold by validation F1 against the merged labels"},
        "validation": {k: vs[k] for k in ("macro_f1", "micro_f1", "macro_precision", "macro_recall")},
        "per_tag": per,
        "word_vocab": len(s.word.vocabulary_), "char_vocab": len(s.char.vocabulary_),
        "timing": {"train_seconds": round(fit_secs, 1), "inference_seconds": round(inf_secs, 1),
                   "inference_texts": len(distinct),
                   "inference_texts_per_second": round(len(distinct) / inf_secs, 0),
                   "hardware": "local CPU (Apple M5 Pro), single process"},
        "inference_check": "chunked inference equals Student.predict on all validation texts",
    }
    if write:
        paths.ensure(S1B_DIR)
        pk = S1B_DIR / "student_1b_r2.pkl"
        RB.write_atomic(pk, lambda p: p.write_bytes(pickle.dumps(blob)))
        write_json(pk.with_name(pk.name + ".meta.json"),
                   dict(base_meta, output=rel(pk), sha256=RB.sha256(pk), what=blob["what"]))
        write_scores_parquet(S1B_DIR / "scores.parquet", distinct, P, fired,
                             dict(base_meta, variant="1b_r2", what=blob["what"],
                                  thresholds={t: round(float(blob["thresholds"][t]), 4) for t in TAGS}))
        write_json(S1B_DIR / "summary.json", dict(summary, **{k: base_meta[k] for k in ("labels", "split")}))
    return {"summary": summary, "fired": fired}


def load_scores_dir(d: Path, distinct: list[str]) -> np.ndarray:
    sc = pq.read_table(d / "scores.parquet", columns=["text_norm", "tags"]).to_pandas()
    if len(sc) != len(distinct) or not sc["text_norm"].is_unique:
        raise SystemExit(f"{d}/scores.parquet does not cover the {len(distinct):,} distinct texts")
    look = dict(zip(sc["text_norm"], sc["tags"]))
    if set(look) != set(distinct):
        raise SystemExit(f"{d}/scores.parquet texts differ from the distinct silver texts")
    return W4.Y_of([list(look[t]) for t in distinct])


# --------------------------------------------------------------------------
# scoring
# --------------------------------------------------------------------------

def prf_cols(Y: np.ndarray, P: np.ndarray) -> dict:
    tp = (Y & P).sum(0); fp = (~Y & P).sum(0); fn = (Y & ~P).sum(0)
    p, r, f = WS._prf(tp, fp, fn)
    has = Y.sum(0) > 0
    per = {t: {"f1": round(float(f[j]), 4), "precision": round(float(p[j]), 4), "recall": round(float(r[j]), 4),
               "support": int(Y[:, j].sum()), "predicted": int(P[:, j].sum())} for j, t in enumerate(TAGS)}
    return {"macro_f1": round(float(f[has].mean()), 4), "macro_precision": round(float(p[has].mean()), 4),
            "macro_recall": round(float(r[has].mean()), 4),
            "micro_f1": round(float(WS._prf(tp.sum(), fp.sum(), fn.sum())[2]), 4), "per_tag": per}


def gold_block(res: dict) -> dict:
    sm = res["summary"]
    return {"macro_f1": sm["macro"]["f1"], "macro_f1_ci95": sm["macro"]["f1_ci95"],
            "micro_f1": sm["micro"]["f1"], "exact_match": sm.get("exact_match_evidence_tags"),
            "per_tag": {t: {k: res["per_tag"][t][k] for k in ("f1", "f1_ci95", "precision", "recall", "support",
                                                               "predicted", "tp", "fp", "fn")} for t in TAGS}}


def ledger_totals() -> dict:
    out, total = {}, 0.0
    for name, p in LEDGERS.items():
        if not p.exists():
            out[name] = {"file": rel(p), "responses": 0, "usd": 0.0, "exists": False}
            continue
        rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
        usd = sum(float(r.get("cost_usd", 0) or 0) for r in rows)
        out[name] = {"file": rel(p), "responses": len(rows), "usd": round(usd, 4), "exists": True,
                     "last_at": max((r.get("at", "") for r in rows), default=None)}
        total += usd
    return {"ledgers": out, "project_total_usd": round(total, 4), "this_stage_usd": 0.0, "read_at_utc": now(),
            "note": "this script makes no LLM calls; other workflows may be spending concurrently"}


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--reuse-1b", action="store_true", help="load the saved 1b_r2 scores instead of retraining")
    ap.add_argument("--candidates", default=",".join(CANDIDATES), help="comma list of candidates to score")
    ap.add_argument("--dry-run", action="store_true", help="print only; write nothing")
    args = ap.parse_args(argv)
    write = not args.dry_run
    t_all = time.time()
    cands = [c.strip() for c in args.candidates.split(",") if c.strip()]
    for c in cands:
        if c not in CANDIDATES:
            raise SystemExit(f"unknown candidate {c}")

    # ---------------------------------------------------------------- inputs and split
    gold = WS.load_gold()
    gold_texts = gold["text_norm"].tolist()
    gold_set = set(gold_texts)
    gold_near = {near_key(t) for t in gold_texts}
    merged = load_merged()
    if merged["text_norm"].isin(gold_set).any():
        raise SystemExit("merged labels contain gold texts; refusing to train")
    val_list = wp4_val_texts()
    is_val = merged["text_norm"].isin(set(val_list)).to_numpy()
    tr, va = np.flatnonzero(~is_val), np.flatnonzero(is_val)
    mtexts = merged["text_norm"].tolist()
    va_t = [mtexts[i] for i in va]
    Yva = W4.Y_of(merged["tags"].iloc[va])
    mm = json.loads(MERGED.with_name(MERGED.name + ".meta.json").read_text())
    split_info = {
        "rule": "validation = merged-label texts in the frozen WP4 validation list (data/models/wp4b_encoder/"
                "wp4_split.json: scripts/wp4_student.py split(), rng(20261121).random(n) < 0.2 over "
                "labels/teacher_facts.parquet, by text_norm); every other merged-label text trains. This is the "
                "rule scripts/wp4b_encoder.py applies, so every candidate uses the same validation texts.",
        "train": int(len(tr)), "validation": int(len(va)), "wp4_validation_texts": len(val_list),
        "wp4_validation_texts_missing_from_merged": len(val_list) - int(len(va)),
        "validation_by_label_source": merged.iloc[va]["source"].value_counts().to_dict(),
        "train_by_label_source": merged.iloc[tr]["source"].value_counts().to_dict(),
        "merged_split_column_not_used": int(((merged["split"] == "validation") & ~is_val).sum()),
        "merged_split_column_note": "merged_labels.parquet also marks new texts as validation by a sha256 rule; "
                                    "scripts/wp4b_encoder.py trains on them, so they train here too",
        "gold_texts_in_labels": 0,
    }
    labels_meta = {"file": rel(MERGED), "sha256": RB.sha256(MERGED), "rows": len(merged),
                   "chosen_teacher": mm.get("chosen_teacher", {}).get("model"),
                   "texts_with_any_evidence_tag": int(merged["tags"].map(len).gt(0).sum())}
    print(f"WP4b retrain: {len(merged):,} merged-label texts -> train {len(tr):,}, validation {len(va):,} "
          f"(WP4 validation texts present {len(va):,} of {len(val_list):,}); gold {len(gold)} rows, no overlap")

    distinct = distinct_texts()
    idx = {t: i for i, t in enumerate(distinct)}
    t0 = time.time()
    R_all = W4.Y_of(recipes.tag_facts(distinct))
    regex_secs = time.time() - t0
    cur_lut, cur_info = current_lookup(distinct)
    C_all = W4.Y_of([cur_lut[t] for t in distinct])
    print(f"  current method: {cur_info['model_version']} ({len(distinct):,} distinct texts; regex {regex_secs:.0f}s)")

    # rebuild check: WP4 student 1b on its shipped tags, regex v0.2 on the rest
    wp4_blob = pickle.loads((WP4_DIR / "student_1b.pkl").read_bytes())
    t0 = time.time()
    W_all = blob_fire(wp4_blob, blob_proba(wp4_blob, distinct, R_all))
    wp4_secs = time.time() - t0
    stud_tags = cur_info["tags_from_student"] or []
    js = [TAGS.index(t) for t in stud_tags]
    rebuilt = R_all.copy()
    rebuilt[:, js] = W_all[:, js]
    diff_rows = int((rebuilt != C_all).any(1).sum())
    rebuild = {"rule": "WP4 student 1b on tags_from_student, regex v0.2 on the rest",
               "distinct_texts_differing": diff_rows,
               "per_tag_cells_differing": {t: int((rebuilt[:, j] != C_all[:, j]).sum()) for j, t in enumerate(TAGS)
                                           if (rebuilt[:, j] != C_all[:, j]).any()},
               "recipes_py_sha256": RB.sha256(ROOT / "src" / "vf" / "recipes.py"),
               "wp4_student_1b_seconds_all_texts": round(wp4_secs, 1)}
    print(f"  rebuild check (WP4 1b on {stud_tags} + regex): {diff_rows} distinct texts differ from gold/fact_tags")

    sil_sha = RB.sha256(paths.silver_dir() / "facts.parquet")
    base_meta = {"created_at_utc": now(), "script": SCRIPT, "version": VERSION, **git_info(),
                 "labels": labels_meta, "split": split_info, "recipe_version": recipes.RECIPE_VERSION, "tags": TAGS,
                 "candidate_only": "D050: candidate files only, not a shipped tagger",
                 "inputs": {"silver/facts.parquet": {"sha256": sil_sha}}}

    # ---------------------------------------------------------------- candidates
    fired, summaries = {}, {}
    for c in cands:
        if c == "1b_r2" and not args.reuse_1b:
            out = train_1b(merged, tr, va, distinct, R_all, base_meta, write)
            fired[c], summaries[c] = out["fired"], out["summary"]
        else:
            d = CANDIDATES[c]["dir"]
            if not (d / "scores.parquet").exists():
                raise SystemExit(f"{c}: {d}/scores.parquet not found (run the encoder round 2 first)")
            fired[c] = load_scores_dir(d, distinct)
            summaries[c] = json.loads((d / "summary.json").read_text())
            lab = summaries[c].get("labels", {}).get("sha256")
            if lab and lab != labels_meta["sha256"]:
                raise SystemExit(f"{c}: trained on labels {lab[:12]}, not the current merged labels")
    refs = {"regex_v0.2": R_all, "wp4_student_1b": W_all}
    for r in ("r1_2a", "r1_2b"):
        d = ENC_DIR / r
        if (d / "scores.parquet").exists():
            refs[r] = load_scores_dir(d, distinct)

    # ---------------------------------------------------------------- validation (merged labels)
    vi = np.array([idx[t] for t in va_t])
    val = {c: prf_cols(Yva, fired[c][vi]) for c in cands}
    val_ref = {"current": prf_cols(Yva, C_all[vi]), **{k: prf_cols(Yva, v[vi]) for k, v in refs.items()}}
    src = merged.iloc[va]["source"].to_numpy()
    val_by_source = {}
    for s_ in sorted(set(src)):
        msk = src == s_
        val_by_source[s_] = {"texts": int(msk.sum()),
                             **{k: prf_cols(Yva[msk], M[vi][msk])["macro_f1"]
                                for k, M in [*[(c, fired[c]) for c in cands], ("current", C_all),
                                             *refs.items()]}}
    for c in cands:
        print(f"  validation vs merged labels: {c} macro F1 {val[c]['macro_f1']:.4f} (micro {val[c]['micro_f1']:.4f})")
    print(f"  validation vs merged labels: current {val_ref['current']['macro_f1']:.4f}, regex "
          f"{val_ref['regex_v0.2']['macro_f1']:.4f}, WP4 1b {val_ref['wp4_student_1b']['macro_f1']:.4f}")

    # ---------------------------------------------------------------- gold
    gi = [idx[t] for t in gold_texts]
    sets = lambda M: W4.sets_of(M[gi])  # noqa: E731
    cur_sets = sets(C_all)
    g_cur = WS.score_tags(gold, cur_sets, tags=TAGS)
    wp4_rep = json.loads(WP4_REPORT.read_text())
    shipped_ref = wp4_rep["gold_scores"]["shipped"]["summary"]["macro"]["f1"]
    gold_check = {"current_macro_f1_here": g_cur["summary"]["macro"]["f1"],
                  "shipped_macro_f1_in_model_a_report": shipped_ref,
                  "equal": abs(g_cur["summary"]["macro"]["f1"] - shipped_ref) < 1e-9}
    print(f"  gold current macro F1 {g_cur['summary']['macro']['f1']:.4f} (model_a_report shipped {shipped_ref:.4f})")
    g = {c: WS.score_tags(gold, sets(fired[c]), tags=TAGS) for c in cands}
    g_ref = {k: WS.score_tags(gold, sets(v), tags=TAGS) for k, v in refs.items()}
    pd_ = {c: W4.paired_delta(gold, sets(fired[c]), cur_sets) for c in cands}
    pd_ref = {k: W4.paired_delta(gold, sets(v), cur_sets) for k, v in refs.items()}

    # ---------------------------------------------------------------- population disagreements
    non_gold = np.array([(t not in gold_set) and (near_key(t) not in gold_near) for t in distinct])
    mset = set(mtexts)
    in_labels = np.array([t in mset for t in distinct])
    pop = {}
    for c in cands:
        pop[c] = {}
        for j, t in enumerate(TAGS):
            a = fired[c][:, j]; b = C_all[:, j]
            add = a & ~b & non_gold; rem = ~a & b & non_gold
            pop[c][t] = {"candidate_tags": int(a.sum()), "current_tags": int(b.sum()),
                         "added_vs_current": int(add.sum()), "removed_vs_current": int(rem.sum()),
                         "disagreements_non_gold": int(add.sum() + rem.sum()),
                         "disagreements_in_merged_labels": int(((add | rem) & in_labels).sum())}

    passing = [(c, t) for c in cands for t in TAGS if pd_[c]["per_tag_delta_ci95"][t][0] > 0]
    print(f"  (candidate, tag) with paired interval entirely above 0: {passing or 'none'}")

    # ---------------------------------------------------------------- draws
    key_rows, blind_files, draws = [], [], {}
    used_ids: set[str] = set()
    id_rng = np.random.default_rng(DRAW_SEED + 1)
    Ymerged = dict(zip(mtexts, merged["tags"]))
    for c, t in passing:
        j = TAGS.index(t)
        ti_ = TAGS.index(t)
        a = fired[c][:, j]; b = C_all[:, j]
        pool = np.flatnonzero((a != b) & non_gold)
        rng = np.random.default_rng([DRAW_SEED, list(CANDIDATES).index(c), ti_])
        pick = np.sort(rng.choice(pool, size=min(N_DRAW, len(pool)), replace=False))
        order = rng.permutation(len(pick))
        rows = []
        for k in pick[order]:
            while True:
                iid = "".join("0123456789ABCDEF"[x] for x in id_rng.integers(0, 16, size=6))
                if iid not in used_ids:
                    used_ids.add(iid)
                    break
            rows.append({"item_id": iid, "text": distinct[k], "tag": t})
            key_rows.append({"item_id": iid, "candidate": c, "tag": t, "candidate_says": int(a[k]),
                             "current_says": int(b[k])})
        fn = f"pop_blind_{t}_{c}.csv"
        blind_files.append(fn)
        n_add = int((a[pick] & ~b[pick]).sum())
        draws[f"{c}|{t}"] = {
            "candidate": c, "tag": t, "file": rel(OUT_DIR / fn), "drawn": int(len(pick)),
            "pool_non_gold_disagreements": int(len(pool)), "drawn_additions": n_add,
            "drawn_removals": int(len(pick) - n_add),
            "drawn_in_merged_labels": int(in_labels[pick].sum()),
            "drawn_where_merged_label_agrees_with_candidate": int(sum(
                (t in Ymerged[distinct[k]]) == bool(a[k]) for k in pick if distinct[k] in Ymerged)),
            "seed": [DRAW_SEED, list(CANDIDATES).index(c), ti_],
            "_rows": pd.DataFrame(rows, columns=["item_id", "text", "tag"]),
        }
        print(f"  draw {c} {t}: {len(pick)} of {len(pool):,} disagreements ({n_add} additions, "
              f"{len(pick) - n_add} removals; {int(in_labels[pick].sum())} in the merged labels)")

    # ---------------------------------------------------------------- scores.json
    per_tag = {}
    for j, t in enumerate(TAGS):
        cm = "WP4 student 1b" if t in stud_tags else "regex v0.2"
        entry = {"current_method": cm, "gold_support": g_cur["per_tag"][t]["support"],
                 "current": {"validation": val_ref["current"]["per_tag"][t], "gold": gold_block(g_cur)["per_tag"][t]},
                 "candidates": {}}
        for c in cands:
            d = pd_[c]["per_tag_delta_ci95"][t]
            gb = gold_block(g[c])["per_tag"][t]
            entry["candidates"][c] = {
                "validation": val[c]["per_tag"][t],
                "gold": gb,
                "paired_delta_vs_current": {"point": round(gb["f1"] - g_cur["per_tag"][t]["f1"], 4), "ci95": d},
                "paired_interval_entirely_above_0": bool(d[0] > 0),
                "population": pop[c][t],
                "blind_file": f"pop_blind_{t}_{c}.csv" if (c, t) in passing else None,
            }
        entry["reference"] = {k: {"validation_f1": val_ref[k]["per_tag"][t]["f1"],
                                  "gold_f1": g_ref[k]["per_tag"][t]["f1"],
                                  "gold_f1_ci95": g_ref[k]["per_tag"][t]["f1_ci95"],
                                  "paired_delta_vs_current_ci95": pd_ref[k]["per_tag_delta_ci95"][t]}
                              for k in refs}
        per_tag[t] = entry
    tie = {}
    for t in TAGS:
        ps = [c for c in cands if (c, t) in passing]
        if len(ps) > 1:
            tie[t] = {"passing_candidates": ps,
                      "validation_f1_vs_merged_labels": {c: val[c]["per_tag"][t]["f1"] for c in ps},
                      "highest_validation_f1": max(ps, key=lambda c: val[c]["per_tag"][t]["f1"]),
                      "note": "D050 tie-break, applied only to candidates that also pass the hand read"}
    spend = ledger_totals()
    rep = {
        "created_at_utc": now(), "script": SCRIPT, "version": VERSION, **git_info(), "stage": "WP4b retrain",
        "decision_ref": "D050 (protocol and ship rule). This file makes no ship decision: the hand read and the "
                        "decision belong to the next stage.",
        "ship_rule_d050": "a candidate replaces the current method on a tag only if (1) its paired 95% bootstrap "
                          "interval of gold F1 minus the current method's gold F1 lies entirely above 0, and (2) a "
                          "blinded hand read of 25 random texts where it disagrees with the current method, by an "
                          "agent that did not build it, finds at least 20 of 25 in the candidate's favour. If several "
                          "candidates pass, the tie-break is validation F1 against teacher labels, not gold.",
        "gold_use": "The gold set (labels/gold_facts.csv, 777 texts) shares no text with the merged labels and was "
                    "used for none of: training, C, thresholds, epochs, encoder choice. Its texts and near "
                    "duplicates are excluded from the draws. Gold decides only the paired-interval gate.",
        "labels": labels_meta, "split": split_info, "tags": TAGS, "recipe_version": recipes.RECIPE_VERSION,
        "current_method": {**cur_info, "rebuild_check": rebuild, "gold_check": gold_check},
        "candidates": {c: {"what": CANDIDATES[c]["what"], "dir": rel(CANDIDATES[c]["dir"]),
                           "validation_vs_merged_labels": {k: val[c][k] for k in
                                                           ("macro_f1", "micro_f1", "macro_precision",
                                                            "macro_recall")},
                           "gold": {k: v for k, v in gold_block(g[c]).items() if k != "per_tag"},
                           "gold_macro_f1_paired_delta_vs_current_ci95": pd_[c]["macro_f1_delta_ci95"],
                           "tags_with_paired_interval_above_0": [t for t in TAGS if (c, t) in passing],
                           "summary": {k: summaries[c].get(k) for k in
                                       ("encoder_kept", "best_epoch", "stopped", "converged", "epochs", "timing",
                                        "validation_macro_f1_at_0.5") if summaries[c].get(k) is not None}}
                       for c in cands},
        "current": {"validation_vs_merged_labels": {k: val_ref["current"][k] for k in
                                                    ("macro_f1", "micro_f1", "macro_precision", "macro_recall")},
                    "gold": {k: v for k, v in gold_block(g_cur).items() if k != "per_tag"}},
        "references_not_candidates": {k: {"what": REFERENCES[k],
                                          "validation_vs_merged_labels_macro_f1": val_ref[k]["macro_f1"],
                                          "gold_macro_f1": g_ref[k]["summary"]["macro"]["f1"],
                                          "gold_macro_f1_ci95": g_ref[k]["summary"]["macro"]["f1_ci95"]}
                                      for k in refs},
        "validation_macro_f1_by_label_source": val_by_source,
        "per_tag": per_tag,
        "passing_paired_gate": [{"candidate": c, "tag": t, "paired_delta_ci95": pd_[c]["per_tag_delta_ci95"][t],
                                 "gold_f1": g[c]["per_tag"][t]["f1"], "current_gold_f1": g_cur["per_tag"][t]["f1"],
                                 "validation_f1": val[c]["per_tag"][t]["f1"], "blind_file": f"pop_blind_{t}_{c}.csv"}
                                for c, t in passing],
        "tie_break_info": tie,
        "draws": {k: {kk: vv for kk, vv in v.items() if kk != "_rows"} for k, v in draws.items()},
        "blind_files": [rel(OUT_DIR / f) for f in blind_files],
        "key_file": rel(OUT_DIR / "pop_key.csv") if passing else None,
        "draw_rule": f"{N_DRAW} random distinct texts (or all, if fewer) from the 275,095 distinct silver fact texts, "
                     "excluding gold texts and their near duplicates, where the candidate's tag differs from the "
                     "current method's (additions and removals both, sampled together); training texts are not "
                     "excluded. Rows shuffled; item ids random; the blind files carry no model information.",
        "reader_instructions": "for each row, does the text state the tag under the recipe v0.2 TAG_DEFINITIONS and "
                               "TAGGING_RULES (src/vf/recipes.py)? Readers must not see this file or pop_key.csv.",
        "timing": {"regex_all_texts_seconds": round(regex_secs, 1), "total_seconds": None},
        "spend": spend,
        "notes": [
            "Validation F1 uses thresholds (and C, and for 2a the encoder) tuned on the same validation texts, as in "
            "WP4, so it is optimistic in absolute terms; all candidates are on the same footing. The current method "
            "was tuned on the Haiku labels, so its validation F1 against the merged labels is not tuned to them.",
            "Validation labels: 1,017 of the 5,857 validation texts carry Opus labels (relabeled), the rest Haiku. "
            "Validation measures agreement with the teacher labels, not correctness.",
            "Gold is agent-labeled, 777 texts, 16 to 74 positives per tag; per-tag differences of a few points are "
            "noise, and only the paired intervals gate a tag. Several candidates and tags are tested at once, so a "
            "few passes can be chance; the hand read is the second gate.",
            "2b runs on the MPS GPU, which is not bit-for-bit deterministic.",
        ],
    }
    rep["timing"]["total_seconds"] = round(time.time() - t_all, 1)

    # print a compact table
    print(f"  {'tag':16s} cur  gold_cur  " + "  ".join(f"{c:>7s} gold  dCI95           val" for c in cands))
    for t in TAGS:
        e = per_tag[t]
        line = f"  {t:16s} {'s1b' if e['current_method'].startswith('WP4') else 'rx '}  {e['current']['gold']['f1']:.2f}     "
        for c in cands:
            x = e["candidates"][c]
            d = x["paired_delta_vs_current"]["ci95"]
            line += (f"  {x['gold']['f1']:.2f} [{d[0]:+.2f},{d[1]:+.2f}]{'*' if d[0] > 0 else ' '} "
                     f"{x['validation']['f1']:.2f}     ")
        print(line)
    for c in cands:
        gm = rep["candidates"][c]["gold"]
        print(f"  {c}: gold macro F1 {gm['macro_f1']:.4f} ({gm['macro_f1_ci95'][0]:.3f} to {gm['macro_f1_ci95'][1]:.3f}); "
              f"paired macro delta vs current {pd_[c]['macro_f1_delta_ci95']}")
    print(f"  project LLM spend ${spend['project_total_usd']:.4f} (this stage $0.00)")

    if not write:
        print("  --dry-run: nothing written")
        return 0

    # ---------------------------------------------------------------- write
    for old in OUT_DIR.glob("pop_blind_*.csv"):
        if old.name not in blind_files:
            old.unlink()
            m = old.with_name(old.name + ".meta.json")
            if m.exists():
                m.unlink()
    for k, v in draws.items():
        fn = OUT_DIR / f"pop_blind_{v['tag']}_{v['candidate']}.csv"
        write_csv(fn, v["_rows"], {
            "created_at_utc": rep["created_at_utc"], "script": SCRIPT,
            "blinded": "no model names, no votes, rows shuffled, random item ids; the key is pop_key.csv",
            "task_for_the_reader": rep["reader_instructions"].split(" Readers")[0]})
    key_path = OUT_DIR / "pop_key.csv"
    if passing:
        write_csv(key_path, pd.DataFrame(key_rows, columns=["item_id", "candidate", "tag", "candidate_says",
                                                            "current_says"]),
                  {"created_at_utc": rep["created_at_utc"], "script": SCRIPT, **git_info(),
                   "blind_files": blind_files,
                   "columns_meaning": {"candidate_says": "1 if the candidate tags the text with the tag",
                                       "current_says": "1 if the current shipped tagger does "
                                                       f"({cur_info['model_version']})"},
                   "not_for_readers": True})
    elif key_path.exists():
        key_path.unlink()
        m = key_path.with_name(key_path.name + ".meta.json")
        if m.exists():
            m.unlink()
    sj = OUT_DIR / "scores.json"
    write_json(sj, rep)
    write_json(sj.with_name(sj.name + ".meta.json"), {
        "output": rel(sj), "created_at_utc": rep["created_at_utc"], "script": SCRIPT, **git_info(),
        "labels_sha256": labels_meta["sha256"], "fact_tags_sha256": cur_info["sha256"],
        "silver_facts_sha256": sil_sha, "gold_facts_sha256": RB.sha256(paths.labels_dir() / "gold_facts.csv"),
        "candidate_dirs": {c: rel(CANDIDATES[c]["dir"]) for c in cands}, "gold_use": "scoring only",
        "not_for_readers": True})
    print(f"  wrote {rel(sj)}, {len(blind_files)} blind files, "
          f"{'pop_key.csv' if passing else 'no key'}  [{(time.time() - t_all) / 60:.1f} min]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
