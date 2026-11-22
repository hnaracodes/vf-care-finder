#!/usr/bin/env python
"""WP4: student fact tagger (Model A), scored against regex v0.2 and the teacher.

Steps (tasks/WP4_fact_tagger.md):

1. Split the teacher labels (labels/teacher_facts.parquet) into train and
   validation by text_norm. The gold set (labels/gold_facts.csv) shares no text
   with the teacher set and is never used for training, thresholds or choosing
   between students.
2. Student 1a: TF-IDF (word 1-2 grams + character 2-5 grams) and one logistic
   regression per evidence tag. Student 1b: the same plus the 24 regex v0.2 tag
   indicators as features. Per tag, C and the decision threshold are tuned on
   validation F1 (teacher labels). The ship candidate is the variant with the
   higher validation macro F1, chosen before the gold set is scored.
3. Student 2 (fine-tuned encoder) is not run: torch is not installed and the
   linear student already matches the teacher on validation (see the report).
4. Score regex v0.2, teacher and both students on the gold set with the same
   stratified bootstrap as scripts/wp3_score.py, plus a paired bootstrap of
   student F1 minus regex F1 per tag.
5. Ship per tag: a tag uses the student only when the paired 95% interval of
   (student F1 - regex F1) on gold lies entirely above 0. Every other tag keeps
   regex v0.2. The stricter rule (student F1 interval entirely above the regex
   F1 interval) is reported next to it.
6. If any tag ships: tag every fact, regrade with the v0.2 rules, overwrite
   gold/fact_tags.parquet and gold/capability_grades.parquet (method = student,
   model_version names the tags per method), and print grade counts per line
   against regex. The regex v0.2 files stay reproducible with
   scripts/wp3_regex_baseline.py, and a snapshot of them is kept under
   data/models/wp4_student/regex_v0.2/.

Writes:
  data/models/wp4_student/student_1a.pkl, student_1b.pkl   fitted models
  data/models/wp4_student/regex_v0.2/                      snapshot of the regex files replaced
  gold/model_a_report.json (+ .meta.json)                  every number below
  gold/fact_tags.parquet, gold/capability_grades.parquet   only if some tag ships
  reports/model_a_results.md                               student rows and a WP4 section

Run:  .venv/bin/python scripts/wp4_student.py            # everything
      .venv/bin/python scripts/wp4_student.py --no-write # score only, write nothing in gold/
"""
from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import os
import pickle
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import scipy.sparse as sp
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression

from vf import paths, recipes

SCRIPT = "scripts/wp4_student.py"
STUDENT_VERSION = "s1"
SEED = 20261121
VAL_FRAC = 0.2
C_GRID = (1.0, 4.0, 16.0)
REGEX_FEATURE_WEIGHT = 1.0
TAGS = recipes.EVIDENCE_TAGS
ROOT = paths.code_root()
MODEL_DIR = paths.data_dir() / "models" / "wp4_student"
REPORT_JSON = paths.gold_dir() / "model_a_report.json"
REPORT_MD = ROOT / "reports" / "model_a_results.md"
TEACHER_CACHE = paths.labels_dir() / "teacher_cache"
#: Hand-read sample of student additions per tag (written once by the WP4 agent; see the report).
POPULATION_CHECK = MODEL_DIR / "population_check.json"
POP_MIN_SHARE = 0.5
STUDENT_NAMES = {
    "student_1a": "TF-IDF (word 1-2, char 2-5) + logistic regression per tag",
    "student_1b": "student 1a features + the 24 regex v0.2 tags as inputs",
}


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


WS = _load_script("wp3_score")           # load_gold, score_tags (same bootstrap as the regex page)
RB = _load_script("wp3_regex_baseline")  # load_inputs, build_grades, write_table, schemas


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_text_atomic(path: Path, text: str) -> None:
    RB.write_atomic(path, lambda p: p.write_text(text, encoding="utf-8"))


# --------------------------------------------------------------------------
# data
# --------------------------------------------------------------------------

def load_teacher() -> pd.DataFrame:
    t = pd.read_parquet(paths.labels_dir() / "teacher_facts.parquet")
    t["tags"] = t["tags"].map(lambda xs: [x for x in xs if x in TAGS])
    return t.reset_index(drop=True)


def split(teacher: pd.DataFrame, gold_texts: set) -> tuple[np.ndarray, np.ndarray]:
    if teacher["text_norm"].isin(gold_texts).any():
        raise SystemExit("teacher labels contain gold texts; refusing to train")
    if not teacher["text_norm"].is_unique:
        raise SystemExit("teacher text_norm not unique")
    rng = np.random.default_rng(SEED)
    is_val = rng.random(len(teacher)) < VAL_FRAC
    return np.flatnonzero(~is_val), np.flatnonzero(is_val)


def teacher_gold_labels(gold_texts: list[str]) -> list[list[str]]:
    """Teacher tags for the gold texts, read from the p2 cache (scored only, never trained on)."""
    idx = {}
    for p in sorted((TEACHER_CACHE / "p2").glob("*.json")):
        rec = json.loads(p.read_text())
        if rec.get("recipe_version") != recipes.RECIPE_VERSION:
            continue
        for it in rec["items"]:
            idx[it["text_norm"]] = it["tags"]
    missing = [t for t in gold_texts if t not in idx]
    if missing:
        raise SystemExit(f"{len(missing)} gold texts have no teacher label in the cache")
    return [[x for x in idx[t] if x in TAGS] for t in gold_texts]


def Y_of(tag_lists) -> np.ndarray:
    return np.array([[t in set(s) for t in TAGS] for s in tag_lists], dtype=bool)


# --------------------------------------------------------------------------
# student
# --------------------------------------------------------------------------

class Student:
    """TF-IDF + one logistic regression per tag, with per-tag C and threshold."""

    def __init__(self, name: str, use_regex: bool):
        self.name, self.use_regex = name, use_regex
        self.word = TfidfVectorizer(analyzer="word", ngram_range=(1, 2), min_df=2, sublinear_tf=True,
                                    token_pattern=r"(?u)\b\w[\w/+-]*\b")
        self.char = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 5), min_df=3, sublinear_tf=True,
                                    max_features=300_000)
        self.models: dict[str, LogisticRegression | None] = {}
        self.thresholds: dict[str, float] = {}
        self.C: dict[str, float] = {}
        self.const: dict[str, bool] = {}

    def _regex(self, texts) -> sp.csr_matrix:
        R = Y_of(recipes.tag_facts(texts)).astype(np.float64) * REGEX_FEATURE_WEIGHT
        return sp.csr_matrix(R)

    def features(self, texts, fit: bool = False) -> sp.csr_matrix:
        if fit:
            parts = [self.word.fit_transform(texts), self.char.fit_transform(texts)]
        else:
            parts = [self.word.transform(texts), self.char.transform(texts)]
        if self.use_regex:
            parts.append(self._regex(texts))
        return sp.hstack(parts, format="csr")

    def fit(self, tr_texts, Ytr, va_texts, Yva) -> dict:
        Xtr = self.features(tr_texts, fit=True)
        Xva = self.features(va_texts)
        val = {}
        for j, t in enumerate(TAGS):
            y, yv = Ytr[:, j], Yva[:, j]
            if y.sum() == 0:
                self.models[t], self.thresholds[t], self.C[t] = None, 1.0, float("nan")
                val[t] = {"f1": 0.0, "support": int(yv.sum()), "C": None, "threshold": None}
                continue
            best = None
            for C in C_GRID:
                m = LogisticRegression(C=C, solver="liblinear", max_iter=2000)
                m.fit(Xtr, y)
                p = m.predict_proba(Xva)[:, 1]
                th, f1, prec, rec = best_threshold(p, yv)
                if best is None or f1 > best[1] + 1e-9:
                    best = (C, f1, th, m, prec, rec)
            C, f1, th, m, prec, rec = best
            self.models[t], self.thresholds[t], self.C[t] = m, th, C
            val[t] = {"f1": round(f1, 4), "precision": round(prec, 4), "recall": round(rec, 4),
                      "support": int(yv.sum()), "C": C, "threshold": round(th, 4)}
        return val

    def proba(self, texts) -> np.ndarray:
        X = self.features(texts)
        out = np.zeros((X.shape[0], len(TAGS)))
        for j, t in enumerate(TAGS):
            m = self.models[t]
            if m is not None:
                out[:, j] = m.predict_proba(X)[:, 1]
        return out

    def predict(self, texts) -> np.ndarray:
        P = self.proba(texts)
        th = np.array([self.thresholds[t] for t in TAGS])
        return P >= th


def best_threshold(p: np.ndarray, y: np.ndarray) -> tuple[float, float, float, float]:
    """Threshold maximizing F1 on (p, y); ties go to the threshold nearest 0.5."""
    if y.sum() == 0:
        return 1.0, 0.0, 0.0, 0.0
    best = (0.5, -1.0, 0.0, 0.0)
    for th in np.arange(0.05, 0.951, 0.01):
        pr = p >= th
        tp = float((pr & y).sum()); fp = float((pr & ~y).sum()); fn = float((~pr & y).sum())
        f1 = 2 * tp / (2 * tp + fp + fn) if tp + fp + fn else 0.0
        if f1 > best[1] + 1e-9 or (abs(f1 - best[1]) <= 1e-9 and abs(th - 0.5) < abs(best[0] - 0.5)):
            best = (float(th), f1, tp / (tp + fp) if tp + fp else 0.0, tp / (tp + fn) if tp + fn else 0.0)
    return best


def val_scores(Y: np.ndarray, P: np.ndarray) -> dict:
    tp = (Y & P).sum(0); fp = (~Y & P).sum(0); fn = (Y & ~P).sum(0)
    p, r, f = WS._prf(tp, fp, fn)
    has = Y.sum(0) > 0
    return {"macro_f1": round(float(f[has].mean()), 4), "macro_precision": round(float(p[has].mean()), 4),
            "macro_recall": round(float(r[has].mean()), 4),
            "micro_f1": round(float(WS._prf(tp.sum(), fp.sum(), fn.sum())[2]), 4),
            "per_tag_f1": {t: round(float(f[j]), 4) for j, t in enumerate(TAGS)}}


def sets_of(P: np.ndarray) -> list[list[str]]:
    return [[t for j, t in enumerate(TAGS) if row[j]] for row in P]


# --------------------------------------------------------------------------
# gold comparison
# --------------------------------------------------------------------------

def paired_delta(gold: pd.DataFrame, a_sets, b_sets, n_boot=WS.N_BOOT, seed=WS.BOOT_SEED + 1) -> dict:
    """Stratified paired bootstrap of F1(a) - F1(b), per tag and macro (same strata as wp3_score)."""
    G = Y_of(gold["gold"]); A = Y_of(a_sets); B = Y_of(b_sets)
    rng = np.random.default_rng(seed)
    groups = [np.flatnonzero(gold["stratum"].to_numpy() == s) for s in sorted(gold["stratum"].unique())]
    per = np.empty((n_boot, len(TAGS))); mac = np.empty(n_boot)
    for k in range(n_boot):
        c = np.zeros(len(gold))
        for idx in groups:
            np.add.at(c, rng.choice(idx, size=len(idx), replace=True), 1)
        fa = WS._prf(c @ (G & A), c @ (~G & A), c @ (G & ~A))[2]
        fb = WS._prf(c @ (G & B), c @ (~G & B), c @ (G & ~B))[2]
        per[k] = fa - fb
        mac[k] = fa.mean() - fb.mean()
    ci = lambda a: [round(float(x), 4) for x in np.percentile(a, [2.5, 97.5])]  # noqa: E731
    return {"macro_f1_delta_ci95": ci(mac), "per_tag_delta_ci95": {t: ci(per[:, j]) for j, t in enumerate(TAGS)}}


def gold_errors(gold: pd.DataFrame, pred_sets, tags) -> dict:
    out = {}
    for t in tags:
        fps = [x for x, g, p in zip(gold["text_norm"], gold["gold"], pred_sets) if t in p and t not in g]
        fns = [x for x, g, p in zip(gold["text_norm"], gold["gold"], pred_sets) if t in g and t not in p]
        out[t] = {"false_positives": fps, "false_negatives": fns}
    return out


def slim(res: dict) -> dict:
    res = dict(res); res.pop("_matrices", None)
    return res


# --------------------------------------------------------------------------
# grades
# --------------------------------------------------------------------------

def grade_counts(grades: pd.DataFrame) -> dict:
    out = {}
    for line in recipes.SERVICE_LINES:
        vc = grades.loc[grades["service_line"] == line, "grade"].value_counts()
        out[line] = {recipes.GRADE_LABELS[g]: int(vc.get(g, 0)) for g in range(4)}
    return out


def grade_changes(before: pd.DataFrame, after: pd.DataFrame) -> dict:
    m = before[["facility_id", "service_line", "grade"]].merge(
        after[["facility_id", "service_line", "grade"]], on=["facility_id", "service_line"],
        suffixes=("_regex", "_student"), validate="one_to_one")
    cb, ca = grade_counts(before), grade_counts(after)
    out = {}
    for line in recipes.SERVICE_LINES:
        ml = m[m["service_line"] == line]
        mv = ml[ml["grade_regex"] != ml["grade_student"]]
        rel = {}
        for lab in recipes.GRADE_LABELS:
            b, a = cb[line][lab], ca[line][lab]
            rel[lab] = None if b == 0 else round((a - b) / b, 4)
        ge2b = int((ml["grade_regex"] >= 2).sum()); ge2a = int((ml["grade_student"] >= 2).sum())
        out[line] = {
            "regex": cb[line], "student": ca[line], "relative_change": rel,
            "grade_ge2_regex": ge2b, "grade_ge2_student": ge2a,
            "facilities_changed": int(len(mv)),
            "transitions": {f"{a}->{b}": int(n) for (a, b), n in
                            mv.groupby(["grade_regex", "grade_student"]).size().items()},
            "shift_over_10pct": [lab for lab, v in rel.items() if v is not None and abs(v) > 0.10
                                 and lab != "none"],
        }
    return out


# --------------------------------------------------------------------------
# report page
# --------------------------------------------------------------------------

MD_START, MD_END = "<!-- wp4:start -->", "<!-- wp4:end -->"


def _row(name, what, sm, status) -> str:
    ex = sm.get("exact_match_evidence_tags")
    return (f"| {name} | {what} | {sm['tags']} | {sm['rows']} | {sm['macro']['precision']:.2f} | "
            f"{sm['macro']['recall']:.2f} | {WS.ci_str(sm['macro']['f1'], sm['macro']['f1_ci95'])} | "
            f"{sm['micro']['f1']:.2f} | {'' if ex is None else f'{ex:.2f}'} | {status} |")


def patch_md(rep: dict) -> None:
    text = REPORT_MD.read_text(encoding="utf-8")
    gs = rep["gold_scores"]
    cand = rep["ship_candidate"]
    ship = set(rep["shipped_tags"])
    # summary table: replace the student row(s)
    rows = []
    for s in ("student_1a", "student_1b"):
        what = STUDENT_NAMES[s] + (", ship candidate (best validation macro F1)" if s == cand else "")
        rows.append(_row(s.replace("_", " "), what, gs[s]["summary"], "scored"))
    rows.append(_row("shipped mix", f"student {cand[-2:]} on {len(ship)} tags "
                     f"({', '.join(sorted(ship)) or 'none'}), regex v0.2 on the rest", gs["shipped"]["summary"],
                     "shipped" if ship else "not shipped"))
    lines = text.split("\n")
    out, done = [], False
    for ln in lines:
        if re.match(r"\| (student|student 1a|student 1b|shipped mix) \|", ln):
            if not done:
                out.extend(rows); done = True
            continue
        out.append(ln)
    if not done:
        raise SystemExit("no student row found in reports/model_a_results.md")
    # per-tag table: last cell is the student F1
    sp_ = gs[cand]["per_tag"]
    res = []
    for ln in out:
        m = re.match(r"\| `(\w+)` \|", ln)
        if m and m.group(1) in sp_ and ln.count("|") == 11:
            t = m.group(1)
            cell = f"{sp_[t]['f1']:.2f}" + (" (ships)" if t in ship else "")
            ln = ln[: ln.rstrip().rstrip("|").rstrip().rfind("|") + 1] + f" {cell} |"
        res.append(ln)
    text = "\n".join(res)
    text = text.replace("| Teacher F1 | Student F1 |", f"| Teacher F1 | Student F1 ({cand.replace('_', ' ')}) |")
    section = render_section(rep)
    if MD_START in text:
        text = re.sub(re.escape(MD_START) + r".*?" + re.escape(MD_END), section, text, flags=re.S)
    else:
        text = text.replace("\n## Caveats\n", "\n" + section + "\n\n## Caveats\n", 1)
    write_text_atomic(REPORT_MD, text)


def render_section(rep: dict) -> str:
    gs, ship, cand = rep["gold_scores"], rep["shipped_tags"], rep["ship_candidate"]
    rx, tc, s1a, s1b = (gs[k]["per_tag"] for k in ("regex", "teacher", "student_1a", "student_1b"))
    dl = rep["paired_delta_vs_regex"][cand]["per_tag_delta_ci95"]
    v = rep["validation"]
    L = [MD_START, "## WP4: student tagger", ""]
    L.append(rep["headline"] + "\n")
    L.append(f"Training: {rep['split']['train']:,} teacher-labeled texts, validation {rep['split']['val']:,} "
             f"(random split by `text_norm`, seed {SEED}). The gold set shares no text with the teacher set; "
             "it was not used for training, for the per-tag C and thresholds, or for choosing between "
             "students (the ship candidate is the one with the higher validation macro F1 on teacher labels, "
             f"student 1a {v['student_1a']['macro_f1']:.3f}, student 1b {v['student_1b']['macro_f1']:.3f}). "
             "The gold set decides only which tags ship, by the rule below. Because that choice is made on "
             "gold, the shipped mix's gold score is slightly optimistic.\n")
    L.append("Ship rule: a tag uses the student only when the paired bootstrap 95% interval of student F1 "
             "minus regex F1 on gold (2,000 stratified resamples, same rows) lies entirely above 0. "
             "The stricter column asks the student's own F1 interval to sit entirely above the regex's. "
             "A second gate, added after the dry run showed the student tagging plastic surgery as burns, can "
             "only remove tags: the gold set is built around regex hits and near misses and cannot see what the "
             "student adds elsewhere, so for each tag that passes the gold rule 25 random texts the student tags "
             "and the regex does not (outside the teacher set) were read by hand, and the tag ships only if at "
             f"least half are right (`data/models/wp4_student/population_check.json`).\n")
    gate = rep["population_check"]["per_tag"]
    L.append("| Tag | Gold positives | Regex F1 (95% CI) | Teacher F1 | Student 1a F1 | Student 1b F1 (95% CI) | "
             f"{cand.replace('_', ' ')} minus regex (95% CI) | Strict rule | Additions right (hand read) | Ships |")
    L.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for t in TAGS:
        strict = rep["strict_rule_pass"].get(t, False)
        g = gate.get(t)
        gtxt = (f"{g['right']}/{g['read']} of {g.get('added_texts_all', 0):,} added" if g else "")
        L.append(f"| `{t}` | {rx[t]['support']} | {WS.ci_str(rx[t]['f1'], rx[t]['f1_ci95'])} | "
                 f"{tc[t]['f1']:.2f} | {s1a[t]['f1']:.2f} | {WS.ci_str(s1b[t]['f1'], s1b[t]['f1_ci95'])} | "
                 f"{dl[t][0]:+.2f} to {dl[t][1]:+.2f} | {'pass' if strict else ''} | {gtxt} | "
                 f"{'student' if t in ship else 'regex'} |")
    L.append("")
    L.append("Grade counts per line (none / claimed / supported / corroborated), regex v0.2 against the shipped "
             "mix:\n")
    L.append("| Line | Regex v0.2 | Shipped mix | Grade 2+ regex | Grade 2+ mix | Facilities changed | Shift over 10% |")
    L.append("| --- | --- | --- | --- | --- | --- | --- |")
    for line, d in rep["grade_changes"].items():
        fmt = lambda c: " / ".join(f"{c[k]:,}" for k in recipes.GRADE_LABELS)  # noqa: E731
        L.append(f"| `{line}` | {fmt(d['regex'])} | {fmt(d['student'])} | {d['grade_ge2_regex']:,} | "
                 f"{d['grade_ge2_student']:,} | {d['facilities_changed']:,} | "
                 f"{', '.join(d['shift_over_10pct']) or ''} |")
    L.append("")
    for e in rep.get("grade_shift_explanations", []):
        L.append(f"- {e}")
    L.append("")
    c = rep["cost_and_throughput"]
    L.append("Cost and throughput: " + c["plain"] + "\n")
    for n in rep["notes"]:
        L.append(f"- {n}")
    L.append("")
    L.append("Reproduce: `.venv/bin/python scripts/wp4_student.py` (regex v0.2 grades: "
             "`.venv/bin/python scripts/wp3_regex_baseline.py --out-dir <folder>`). "
             f"Generated {rep['created_at_utc']}.")
    L.append(MD_END)
    return "\n".join(L)


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def teacher_throughput() -> dict:
    rows = [json.loads(l) for l in open(TEACHER_CACHE / "spend.jsonl")]
    tr = [r for r in rows if r["purpose"] == "train" and r["prompt_version"] == "p2"]
    t0 = datetime.strptime(min(r["at"] for r in tr), "%Y-%m-%dT%H:%M:%SZ")
    t1 = datetime.strptime(max(r["at"] for r in tr), "%Y-%m-%dT%H:%M:%SZ")
    n = sum(r["n_texts"] for r in tr); usd = sum(r["cost_usd"] for r in tr)
    secs = (t1 - t0).total_seconds()
    return {"texts": n, "calls": len(tr), "usd": round(usd, 4), "wall_seconds_first_to_last_call": secs,
            "texts_per_second": round(n / secs, 1), "usd_per_1000_texts": round(1000 * usd / n, 4),
            "parallel_requests": 8, "all_runs_usd": round(sum(r["cost_usd"] for r in rows), 4)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--no-write", action="store_true", help="score only; write nothing under gold/ or reports/")
    ap.add_argument("--patch-md", action="store_true",
                    help="only re-render the WP4 part of reports/model_a_results.md from gold/model_a_report.json "
                         "and data/models/wp4_student/grade_shift_explanations.json")
    args = ap.parse_args(argv)
    t_start = time.time()
    if args.patch_md:
        rep = json.loads(REPORT_JSON.read_text())
        expl_path = MODEL_DIR / "grade_shift_explanations.json"
        if expl_path.exists():
            rep["grade_shift_explanations"] = json.loads(expl_path.read_text())
            write_text_atomic(REPORT_JSON, json.dumps(rep, indent=2, ensure_ascii=False, default=str) + "\n")
        patch_md(rep)
        print("patched reports/model_a_results.md from gold/model_a_report.json")
        return 0

    gold = WS.load_gold()
    gold_texts = gold["text_norm"].tolist()
    teacher = load_teacher()
    tr, va = split(teacher, set(gold_texts))
    texts = teacher["text_norm"].tolist()
    Y = Y_of(teacher["tags"])
    tr_texts = [texts[i] for i in tr]; va_texts = [texts[i] for i in va]
    print(f"WP4 student: {len(teacher):,} teacher texts -> train {len(tr):,}, validation {len(va):,}; "
          f"gold {len(gold)} rows (no overlap)")

    students, val, fit_secs = {}, {}, {}
    for name, use_rx in (("student_1a", False), ("student_1b", True)):
        t0 = time.time()
        s = Student(name, use_rx)
        per = s.fit(tr_texts, Y[tr], va_texts, Y[va])
        fit_secs[name] = round(time.time() - t0, 1)
        Pva = s.predict(va_texts)
        vs = val_scores(Y[va], Pva)
        vs["per_tag_tuning"] = per
        val[name] = vs
        students[name] = s
        print(f"  {name}: validation macro F1 {vs['macro_f1']:.3f} (micro {vs['micro_f1']:.3f}) "
              f"vs teacher labels; fit {fit_secs[name]}s")
    # regex against teacher labels on validation, for reference
    Rva = Y_of(recipes.tag_facts(va_texts))
    val["regex_v0.2_vs_teacher"] = val_scores(Y[va], Rva)
    print(f"  regex v0.2 vs teacher labels on validation: macro F1 {val['regex_v0.2_vs_teacher']['macro_f1']:.3f}")
    cand = max(("student_1a", "student_1b"), key=lambda k: val[k]["macro_f1"])
    print(f"  ship candidate (validation only): {cand}")

    # ---------------------------------------------------------------- gold
    pred = {"regex": recipes.tag_facts(gold_texts), "teacher": teacher_gold_labels(gold_texts)}
    for name, s in students.items():
        pred[name] = sets_of(s.predict(gold_texts))
    gs = {k: slim(WS.score_tags(gold, p, tags=TAGS)) for k, p in pred.items()}
    deltas = {k: paired_delta(gold, pred[k], pred["regex"]) for k in ("teacher", "student_1a", "student_1b")}
    dl = deltas[cand]["per_tag_delta_ci95"]
    ship_gold = [t for t in TAGS if dl[t][0] > 0]
    # population gate (hand-read; can only remove a tag): see POPULATION_CHECK
    pop = json.loads(POPULATION_CHECK.read_text())["tags"] if POPULATION_CHECK.exists() else {}
    gate = {}
    for t in ship_gold:
        c = pop.get(t)
        if c is None:
            gate[t] = {"read": 0, "right": 0, "pass": False, "why": "not read: does not ship until read"}
        else:
            frac = c["added_right"] / c["added_read"]
            gate[t] = {"read": c["added_read"], "right": c["added_right"], "share_right": round(frac, 3),
                       "added_texts_all": c["added_texts_all"], "removed_texts_all": c["removed_texts_all"],
                       "pass": frac >= POP_MIN_SHARE}
    ship = [t for t in ship_gold if gate[t]["pass"]]
    strict = {t: gs[cand]["per_tag"][t]["f1_ci95"][0] > gs["regex"]["per_tag"][t]["f1_ci95"][1] for t in TAGS}
    mix = [[t for t in TAGS if (t in set(c) if t in ship else t in set(r))]
           for r, c in zip(pred["regex"], pred[cand])]
    gs["shipped"] = slim(WS.score_tags(gold, mix, tags=TAGS))
    deltas["shipped"] = paired_delta(gold, mix, pred["regex"])
    for k in ("regex", "teacher", "student_1a", "student_1b", "shipped"):
        sm = gs[k]["summary"]
        print(f"  gold {k:11s} macro F1 {sm['macro']['f1']:.3f} ({sm['macro']['f1_ci95'][0]:.3f} to "
              f"{sm['macro']['f1_ci95'][1]:.3f})  micro {sm['micro']['f1']:.3f}  exact {sm['exact_match_evidence_tags']:.3f}")
    print(f"  {'tag':16s} pos  regex  teach  s1a    s1b    {cand[-2:]}-regex CI      strict ships")
    for t in TAGS:
        print(f"  {t:16s} {gs['regex']['per_tag'][t]['support']:>3}  " + "  ".join(
            f"{gs[k]['per_tag'][t]['f1']:.2f} " for k in ("regex", "teacher", "student_1a", "student_1b"))
            + f"  {dl[t][0]:+.2f} to {dl[t][1]:+.2f}   {'pass' if strict[t] else '    '}   "
              f"{'student' if t in ship else 'regex'}"
            + (f"   population check {gate[t]['right']}/{gate[t]['read']}"
               f" {'pass' if gate[t]['pass'] else 'FAIL'}" if t in gate else ""))
    print(f"  passes the gold rule: {ship_gold}; ships after the population check: {ship or 'none'}")

    # ---------------------------------------------------------------- all facts
    fac, facts = RB.load_inputs()
    distinct = facts["text_norm"].unique().tolist()
    t0 = time.time()
    regex_lookup = {t: recipes.tag_fact(t) for t in distinct}
    regex_secs = time.time() - t0
    t0 = time.time()
    s = students[cand]
    chunks = []
    for i in range(0, len(distinct), 20000):
        chunks.append(s.predict(distinct[i:i + 20000]))
    Pall = np.vstack(chunks)
    infer_secs = time.time() - t0
    print(f"  student inference: {len(distinct):,} distinct texts in {infer_secs:.1f}s "
          f"({len(distinct) / infer_secs:,.0f} texts/s); regex {regex_secs:.1f}s")
    ship_idx = [TAGS.index(t) for t in ship]
    stud_lookup = {txt: {TAGS[j] for j in ship_idx if Pall[i, j]} for i, txt in enumerate(distinct)}
    student_all_counts = {t: int(Pall[:, j].sum()) for j, t in enumerate(TAGS)}
    regex_all_counts = collections.Counter(x for v in regex_lookup.values() for x in v)

    def mixed(txt):
        r = set(regex_lookup[txt]) - set(ship)
        return [t for t in TAGS if t in r or t in stud_lookup[txt]]

    mix_lookup = {t: mixed(t) for t in distinct}
    facts["tags_regex"] = facts["text_norm"].map(regex_lookup)
    facts["tags"] = facts["text_norm"].map(mix_lookup)
    changed_texts = sum(set(regex_lookup[t]) != set(mix_lookup[t]) for t in distinct)
    per_tag_text_changes = {}
    for t in ship:
        add = sum(t in mix_lookup[x] and t not in regex_lookup[x] for x in distinct)
        drop = sum(t not in mix_lookup[x] and t in regex_lookup[x] for x in distinct)
        per_tag_text_changes[t] = {"added": add, "removed": drop}

    grades_regex = RB.build_grades(fac, RB.facts_by_facility(facts, facts["tags_regex"]), "v0.2")
    if ship:
        grades_mix = RB.build_grades(fac, RB.facts_by_facility(facts, facts["tags"]), "v0.2")
        grades_mix["method"] = "student"
    else:
        grades_mix = grades_regex
    gch = grade_changes(grades_regex, grades_mix)
    print("  Grades per line, none / claimed / supported / corroborated (regex v0.2 -> shipped mix):")
    for line, d in gch.items():
        f = lambda c: "/".join(f"{c[k]:,}" for k in recipes.GRADE_LABELS)  # noqa: E731
        rel = ", ".join(f"{k} {v:+.1%}" for k, v in d["relative_change"].items() if v)
        print(f"    {line:16s} {f(d['regex']):>24s} -> {f(d['student']):<24s} changed {d['facilities_changed']:>4,}"
              f"  {rel}" + (f"  SHIFT>10%: {d['shift_over_10pct']}" if d["shift_over_10pct"] else ""))

    # examples for lines that moved over 10%
    fac_names = fac.set_index("facility_id")["name"]
    examples = {}
    m = grades_regex[["facility_id", "service_line", "grade"]].merge(
        grades_mix[["facility_id", "service_line", "grade"]], on=["facility_id", "service_line"],
        suffixes=("_r", "_s"))
    diff_facts = facts[[set(a) != set(b) for a, b in zip(facts["tags_regex"], facts["tags"])]]
    for line, d in gch.items():
        if not d["facilities_changed"]:
            continue
        mv = m[(m["service_line"] == line) & (m["grade_r"] != m["grade_s"])]
        ex = []
        for r in mv.head(6).itertuples():
            df = diff_facts[diff_facts["facility_id"] == r.facility_id]
            ex.append({"facility_id": r.facility_id, "name": fac_names.get(r.facility_id),
                       "grade_regex": int(r.grade_r), "grade_student": int(r.grade_s),
                       "facts_changed": [{"text": x, "regex": list(a), "mix": list(b)}
                                         for x, a, b in zip(df["text"], df["tags_regex"], df["tags"])][:4]})
        examples[line] = ex

    # ---------------------------------------------------------------- cost
    tth = teacher_throughput()
    stud_rate = len(distinct) / infer_secs
    cost = {
        "teacher": tth,
        "teacher_estimate_all_distinct_texts": {
            "texts": len(distinct), "usd": round(tth["usd_per_1000_texts"] * len(distinct) / 1000, 2),
            "minutes_at_observed_rate": round(len(distinct) / tth["texts_per_second"] / 60, 1)},
        "student": {"train_seconds": fit_secs, "inference_texts": len(distinct),
                    "inference_seconds": round(infer_secs, 1), "texts_per_second": round(stud_rate, 0),
                    "usd": 0.0, "hardware": "local CPU (Apple M5 Pro), single process"},
        "regex": {"seconds_all_distinct_texts": round(regex_secs, 1)},
    }
    cost["plain"] = (
        f"the teacher labeled {tth['texts']:,} texts in about {tth['wall_seconds_first_to_last_call'] / 60:.0f} "
        f"minutes ({tth['texts_per_second']:.0f} texts per second with 8 parallel requests) for "
        f"${tth['usd']:.2f}, ${tth['usd_per_1000_texts']:.4f} per 1,000 texts; labeling all "
        f"{len(distinct):,} distinct fact texts that way would cost about "
        f"${cost['teacher_estimate_all_distinct_texts']['usd']:.2f} and "
        f"{cost['teacher_estimate_all_distinct_texts']['minutes_at_observed_rate']:.0f} minutes. The ship "
        f"candidate trained in {fit_secs[cand]:.0f} seconds and tagged all {len(distinct):,} texts in "
        f"{infer_secs:.0f} seconds ({stud_rate:,.0f} texts per second) on a laptop CPU, at no API cost. "
        f"The regex takes {regex_secs:.0f} seconds for the same texts.")

    # ---------------------------------------------------------------- headline and notes
    sm = {k: gs[k]["summary"]["macro"] for k in gs}
    if ship:
        headline = (f"The student ships on {len(ship)} of {len(TAGS)} evidence tags "
                    f"({', '.join(f'`{t}`' for t in ship)}); regex v0.2 stays on the other {len(TAGS) - len(ship)}. "
                    f"Over all 24 tags the shipped mix scores macro F1 {sm['shipped']['f1']:.2f} "
                    f"({sm['shipped']['f1_ci95'][0]:.2f} to {sm['shipped']['f1_ci95'][1]:.2f}) against regex "
                    f"{sm['regex']['f1']:.2f} ({sm['regex']['f1_ci95'][0]:.2f} to {sm['regex']['f1_ci95'][1]:.2f}); "
                    f"paired difference {deltas['shipped']['macro_f1_delta_ci95'][0]:+.3f} to "
                    f"{deltas['shipped']['macro_f1_delta_ci95'][1]:+.3f}. The ship candidate alone "
                    f"({cand.replace('_', ' ')}) scores {sm[cand]['f1']:.2f}, the teacher {sm['teacher']['f1']:.2f}: "
                    "the student learned the teacher, mistakes included, and the regex remains better on the "
                    "tags where the teacher is worse.")
    else:
        headline = (f"The student wins on no tag, so regex v0.2 ships unchanged. The ship candidate "
                    f"({cand.replace('_', ' ')}) scores macro F1 {sm[cand]['f1']:.2f} against regex "
                    f"{sm['regex']['f1']:.2f}.")
    notes = [
        "The gold set is agent-labeled (four labelers who split on specialty names in lists), and each tag has "
        "16 to 74 positives. A difference of a few points means nothing; only the paired intervals decide.",
        "The student learns the teacher's reading of each tag. Where the teacher is clearly worse than the regex "
        "on gold (`anesthesia`, `emergency`, `ccu` in the teacher report), the student is too, and those tags "
        "keep the regex.",
        "The teacher labeled a selection that over-draws regex hits and near misses (21,874 hits, 3,426 near "
        "misses, 2,000 common-tag hits, 2,000 random). The student is applied to all 275,095 distinct texts, "
        "most of which look like the random stratum; its precision there is not measured by the gold set "
        "beyond the gold set's own random rows.",
        "Student 2 (a fine-tuned encoder) was not run: torch is not installed in the project environment, the "
        "linear student already reproduces the teacher labels at validation macro F1 0.92, and an encoder would "
        "learn the same teacher, which does not beat the regex on most tags. It remains the next step for "
        "`neurosurgery`, `trauma` and `radiotherapy`, where no tagger is good.",
    ]
    if ship and examples:
        notes.append("Facility examples for every line whose grades moved are in gold/model_a_report.json "
                     "under grade_change_examples.")

    rep = {
        "created_at_utc": now(),
        "script": SCRIPT,
        **RB.git_info(),
        "recipe_version": recipes.RECIPE_VERSION,
        "student_version": STUDENT_VERSION,
        "gold_set_use": "The gold set (labels/gold_facts.csv, 777 texts) shares no text with the teacher labels "
                        "and was used for none of: training, per-tag C, thresholds, choosing between students. "
                        "It decides only which tags ship, by the paired-interval rule.",
        "split": {"train": int(len(tr)), "val": int(len(va)), "seed": SEED, "by": "text_norm (distinct)",
                  "val_frac": VAL_FRAC},
        "students": {k: {"what": STUDENT_NAMES[k], "C_grid": list(C_GRID), "per_tag_C": students[k].C,
                         "per_tag_threshold": students[k].thresholds,
                         "word_vocab": len(students[k].word.vocabulary_),
                         "char_vocab": len(students[k].char.vocabulary_)} for k in students},
        "validation": val,
        "ship_candidate": cand,
        "ship_candidate_rule": "higher validation macro F1 on teacher labels (gold not consulted)",
        "ship_rule": "a tag ships from the student when the stratified paired bootstrap 95% CI of "
                     "F1(student) - F1(regex) on gold lies entirely above 0",
        "tags_passing_gold_rule": ship_gold,
        "population_check": {"file": "data/models/wp4_student/population_check.json",
                             "rule": f"a tag passing the gold rule ships only if at least {POP_MIN_SHARE:.0%} of "
                                     "25 random student-added texts (outside the teacher set) are right on a "
                                     "hand read",
                             "added_after": "reading the burns additions in the dry run; it can only remove tags",
                             "per_tag": gate},
        "shipped_tags": ship,
        "strict_rule_pass": strict,
        "strict_rule": "student F1 95% CI lower bound above the regex F1 95% CI upper bound",
        "gold_scores": gs,
        "paired_delta_vs_regex": deltas,
        "gold_errors_candidate": gold_errors(gold, pred[cand], TAGS),
        "model_version": None,
        "fact_level_counts_all_distinct_texts": {"regex": dict(regex_all_counts),
                                                 "student_candidate": student_all_counts,
                                                 "distinct_texts": len(distinct),
                                                 "texts_whose_tags_change": int(changed_texts),
                                                 "shipped_tag_changes": per_tag_text_changes},
        "grade_changes": gch,
        "grade_change_examples": examples,
        "cost_and_throughput": cost,
        "headline": headline,
        "notes": notes,
    }
    mv = (f"student:{STUDENT_VERSION}-{cand.split('_')[1]}[{','.join(ship)}]+regex:{recipes.RECIPE_VERSION}[rest]"
          if ship else recipes.RECIPE_VERSION)
    rep["model_version"] = mv
    expl_path = MODEL_DIR / "grade_shift_explanations.json"
    if expl_path.exists():
        rep["grade_shift_explanations"] = json.loads(expl_path.read_text())

    if args.no_write:
        print("  --no-write: nothing written")
        print(json.dumps({l: examples.get(l, [])[:3] for l, d in gch.items() if d["shift_over_10pct"]},
                         indent=1, ensure_ascii=False)[:6000])
        return 0

    # ---------------------------------------------------------------- write
    paths.ensure(MODEL_DIR)
    for name, s in students.items():
        # plain dict of sklearn objects, so loading needs no class from this script
        blob = {"name": name, "what": STUDENT_NAMES[name], "tags": list(TAGS), "use_regex_features": s.use_regex,
                "regex_feature_weight": REGEX_FEATURE_WEIGHT, "regex_recipe_version": recipes.RECIPE_VERSION,
                "word_vectorizer": s.word, "char_vectorizer": s.char, "models": s.models,
                "thresholds": s.thresholds, "C": s.C,
                "usage": "X = hstack([word.transform(t), char.transform(t)] + ([regex tag indicators in "
                         "tags order * weight] if use_regex_features)); tag j fires when "
                         "models[tag].predict_proba(X)[:,1] >= thresholds[tag]"}
        RB.write_atomic(MODEL_DIR / f"{name}.pkl", lambda p, b=blob: p.write_bytes(pickle.dumps(b)))
    meta_common = {"created_at_utc": rep["created_at_utc"], **RB.git_info(), "script": SCRIPT,
                   "recipe_version": recipes.RECIPE_VERSION, "model_version": mv,
                   "inputs": {"silver/facts.parquet": {"sha256": RB.sha256(paths.silver_dir() / "facts.parquet")},
                              "labels/teacher_facts.parquet": {
                                  "sha256": RB.sha256(paths.labels_dir() / "teacher_facts.parquet")}}}
    if ship:
        gdir = paths.gold_dir()
        snap = paths.ensure(MODEL_DIR / "regex_v0.2")
        for n in ("fact_tags.parquet", "capability_grades.parquet"):
            src = gdir / n
            meta = gdir / (n + ".meta.json")
            if src.exists() and json.loads(meta.read_text()).get("method") == "regex":
                shutil.copy2(src, snap / n); shutil.copy2(meta, snap / (n + ".meta.json"))
        fact_tags = pd.DataFrame({"fact_id": facts["fact_id"], "facility_id": facts["facility_id"],
                                  "tags": facts["tags"], "method": "student", "model_version": mv})
        RB.write_table(fact_tags, RB.FACT_TAGS_SCHEMA, gdir / "fact_tags.parquet", {
            "output": "gold/fact_tags.parquet", **meta_common, "method": "student",
            "tags_from_student": ship, "tags_from_regex": [t for t in TAGS if t not in ship],
            "student": f"data/models/wp4_student/{cand}.pkl ({STUDENT_NAMES[cand]})",
            "facts_with_any_tag": int(fact_tags["tags"].map(len).gt(0).sum()),
            "distinct_texts_whose_tags_differ_from_regex": int(changed_texts),
            "regex_v0_2_reproduce": "scripts/wp3_regex_baseline.py --out-dir <folder>; snapshot in "
                                    "data/models/wp4_student/regex_v0.2/"})
        grades_mix["recipe_version"] = recipes.RECIPE_VERSION
        RB.write_table(grades_mix, RB.GRADES_SCHEMA, gdir / "capability_grades.parquet", {
            "output": "gold/capability_grades.parquet", **meta_common, "method": "student",
            "tags_from_student": ship,
            "grade_rules": RB.rules_description("v0.2"),
            "description_as_fact_source": False,
            "facilities_per_grade": grade_counts(grades_mix),
            "facilities_per_grade_regex_v0_2": grade_counts(grades_regex),
            "grade_changes_vs_regex_v0_2": gch,
            "regex_v0_2_reproduce": "scripts/wp3_regex_baseline.py --out-dir <folder>; snapshot in "
                                    "data/models/wp4_student/regex_v0.2/"})
        print(f"  wrote gold/fact_tags.parquet ({len(fact_tags):,} rows) and gold/capability_grades.parquet "
              f"({len(grades_mix):,} rows), method = student, model_version {mv}")
    body = json.dumps(rep, indent=2, ensure_ascii=False, default=str) + "\n"
    write_text_atomic(REPORT_JSON, body)
    write_text_atomic(REPORT_JSON.with_name(REPORT_JSON.name + ".meta.json"),
                      json.dumps({**meta_common, "output": "gold/model_a_report.json", "rows": len(TAGS)},
                                 indent=2, default=str) + "\n")
    patch_md(rep)
    print(f"  wrote gold/model_a_report.json, reports/model_a_results.md, models in {MODEL_DIR}  "
          f"[{time.time() - t_start:.0f}s]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
