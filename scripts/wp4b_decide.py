#!/usr/bin/env python
"""WP4b decide stage (D050): unblind the population reads, apply the ship rule per tag,
build the candidate tag mix, score it on gold, and measure what it does to grades.

Ship rule (D050, declared before any result). A candidate replaces the current shipped
method on a tag only if
  (1) its paired 95% bootstrap interval of gold F1 minus the current method's gold F1 lies
      entirely above 0 (labels/wp4b/scores.json, from scripts/wp4b_students.py), and
  (2) a blinded hand read of 25 random texts where it disagrees with the current method, by
      an agent that did not build it, finds at least 20 of 25 in the candidate's favour
      (labels/wp4b/pop_read_<tag>_<candidate>.csv, unblinded with labels/wp4b/pop_key.csv).
If several candidates pass a tag, the one with the highest validation F1 against the teacher
labels wins (not gold). Otherwise the tag keeps the current method.

Freeze (D050): candidate files only. This script never writes gold/fact_tags.parquet or
gold/capability_grades*.parquet. Promotion, if any, belongs to the integration pass.

Reads (data folder unless noted):
  labels/wp4b/scores.json, pop_key.csv, pop_blind_*.csv, pop_read_*.csv, pilot_result.json,
  merged_labels.parquet, teacher_strong_facts.parquet.meta.json
  gold/fact_tags.parquet (the current shipped tags; its sha256 must equal the one scores.json used)
  models/wp4b_*/.../scores.parquet (saved candidate tags for every distinct text)
  silver/facts.parquet, silver/facilities.parquet, labels/gold_facts.csv (scoring only)
  gold/capability_grades.parquet, gold/capability_grades_v03.parquet (read for a consistency check)
  the three LLM spend ledgers (labels/teacher_cache, labels/eval_sealed, labels/wp4b)
  labels/wp4b/teacher_cache/prompt_p2.json, pilot_read_*.csv, the previous decision.json (audits and provenance)

Disclosure blocks (wp4b-decide-2; none feeds the decision): how firm each passing gold gate is (the declared
resampling run longer, and at other seeds), a full re-score of every paired interval, gold texts in the teacher prompt
and looser near duplicates in the labels, and what the files show about the reads.

Writes:
  gold/fact_tags_candidate_wp4b.parquet (+ .meta.json)  same schema as gold/fact_tags.parquet
  labels/wp4b/decision.json (+ .meta.json)              unblinded reads, both gates, decision per tag
  labels/wp4b/impact.json (+ .meta.json)                gold scores of the mix, grade impact, spend
  reports/model_a_wp4b.md (code root)                   the write-up

Makes no LLM calls.

Run:  .venv/bin/python scripts/wp4b_decide.py
      .venv/bin/python scripts/wp4b_decide.py --dry-run        # print only, write nothing
      .venv/bin/python scripts/wp4b_decide.py --reads labels/wp4b/pop_read_burns_2b_r2.csv,...
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import importlib.util
import json
import math
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from vf import paths, recipes

SCRIPT = "scripts/wp4b_decide.py"
VERSION = "wp4b-decide-2"  # 2: gold-gate sensitivity, gold-use audit, read provenance (review fixes); same rule
TAGS = list(recipes.EVIDENCE_TAGS)
ROOT = paths.code_root()
DATA = paths.data_dir()
WP4B = paths.labels_dir() / "wp4b"
SCORES = WP4B / "scores.json"
KEY = WP4B / "pop_key.csv"
PILOT = WP4B / "pilot_result.json"
STRONG_META = WP4B / "teacher_strong_facts.parquet.meta.json"
MERGED = WP4B / "merged_labels.parquet"
FACT_TAGS = paths.gold_dir() / "fact_tags.parquet"
GRADES_V02 = paths.gold_dir() / "capability_grades.parquet"
GRADES_V03 = paths.gold_dir() / "capability_grades_v03.parquet"
OUT_TAGS = paths.gold_dir() / "fact_tags_candidate_wp4b.parquet"
DECISION = WP4B / "decision.json"
IMPACT = WP4B / "impact.json"
REPORT = ROOT / "reports" / "model_a_wp4b.md"

PROMPT_SNAPSHOT = WP4B / "teacher_cache" / "prompt_p2.json"   # the frozen WP3 p2 prompt the WP4b teachers got
PILOT_READS = ("pilot_read_1.csv", "pilot_read_2.csv")

N_READ = 25
MIN_FAVOUR = 20
#: Sensitivity of the gold gate (disclosure only; the gate itself is the declared interval in scores.json).
#: The declared gate is 2,000 stratified resamples at seed BOOT_SEED + 1 (scripts/wp4_student.py paired_delta).
SENS_LONG_N_BOOT = 50_000      # the declared seed's resampling, extended: its first 2,000 are the declared run
SENS_ALT_SEEDS = 100           # other seeds, BOOT_SEED + 2 onward, each running the declared 2,000-resample procedure
#: Reporting threshold for "templated near-copy" of a gold text in the training labels (taken from the review).
NEAR_COPY_RATIO = 0.85
#: Lines whose grade 2 gate the task asks about (D048 gate: grade 2+ under recipe v0.3).
GATE_LINES = ("emoc", "dialysis", "icu", "trauma", "neurosurgery")
RUN_CAP_USD = 12.0
PROJECT_CAP_USD = 30.0
SEALED_RESERVE_USD = 5.0
LEDGERS = {
    "WP3 Haiku teacher": paths.labels_dir() / "teacher_cache" / "spend.jsonl",
    "sealed evaluation judges": paths.labels_dir() / "eval_sealed" / "spend.jsonl",
    "WP4b (this run)": WP4B / "spend.jsonl",
}
#: USD per million tokens (input, output, cache write 5 min, cache read), from the claude-api
#: skill's model table (cached 2026-11-03): Opus 5.5 $4/$20, cache reads $0.20; Sonnet 5.5 $2/$10,
#: cache reads $0.10; Haiku 5.5 $0.10/$0.50 (prompts up to 100K). Cache writes are 1.25x input;
#: Haiku cache reads 0.1x input. Used only to recompute each ledger line as a check.
PRICES = {
    "claude-opus-5-5": (4.0, 20.0, 5.0, 0.20),
    "claude-sonnet-5-5": (2.0, 10.0, 2.5, 0.10),
    "claude-haiku-5-5": (0.10, 0.50, 0.125, 0.01),
}
CURRENT_METHOD_ID = {"WP4 student 1b": "student:s1-1b", "regex v0.2": f"regex:{recipes.RECIPE_VERSION}"}
CANDIDATE_LABEL = {
    "1b_r2": "student 1b, retrained (TF-IDF + regex features + logistic regression)",
    "2a_r2": "student 2a (frozen sentence embeddings + logistic regression)",
    "2b_r2": "student 2b (fine-tuned bge-small encoder)",
}


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


S = _load_script("wp4b_students")    # current_lookup, load_scores_dir, distinct_texts, near_key, CANDIDATES
W4 = S.W4                             # Y_of, sets_of, paired_delta
WS = S.WS                             # load_gold, score_tags, _prf, ci_str
RB = S.RB                             # write_atomic, write_table, sha256, load_inputs, build_grades, schemas
V3 = _load_script("wp3_regrade_v03")  # build (v0.3 grades, same code as gold/capability_grades_v03.parquet)


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def rel(p: Path) -> str:
    return S.rel(p)


def write_json(path: Path, obj) -> None:
    S.write_json(path, obj)


def git_info() -> dict:
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                                check=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "--no-optional-locks", "status", "--porcelain", "--", SCRIPT,
                                     "scripts/wp4b_students.py", "scripts/wp4_student.py",
                                     "scripts/wp3_regrade_v03.py", "src/vf/recipes.py"],
                                    cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip())
    except Exception:
        commit, dirty = None, None
    return {"git_commit": commit, "code_uncommitted": dirty}


def wilson(k: int, n: int, z: float = 1.959964) -> list:
    if n == 0:
        return [None, None]
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(c - h, 4), round(c + h, 4)]


def f2(x) -> str:
    return "" if x is None else f"{x:.2f}"


def f3(x) -> str:
    return "" if x is None else f"{x:.3f}"


def sgn(x, nd=3) -> str:
    return f"{x:+.{nd}f}"


def ci_txt(ci, nd=3, signed=True) -> str:
    if signed:
        return f"{ci[0]:+.{nd}f} to {ci[1]:+.{nd}f}"
    return f"{ci[0]:.{nd}f} to {ci[1]:.{nd}f}"


def pct(k, n) -> str:
    return f"{100 * k / n:.0f}%" if n else ""


def no_dash(s: str) -> str:
    """House style: no em or en dashes in anything a human reads."""
    return s.replace("\u2014", ", ").replace("\u2013", " to ")


# --------------------------------------------------------------------------
# inputs
# --------------------------------------------------------------------------

def check_sidecar_sha(path: Path) -> str:
    meta = json.loads(path.with_name(path.name + ".meta.json").read_text())
    got = RB.sha256(path)
    if meta.get("sha256") and meta["sha256"] != got:
        raise SystemExit(f"{rel(path)}: sha256 {got[:12]} differs from its sidecar {meta['sha256'][:12]}")
    return got


def find_reads(arg: str | None, blind_files: list[str]) -> list[Path]:
    if arg:
        out = []
        for s in arg.split(","):
            p = Path(s.strip())
            out.append(p if p.is_absolute() else (DATA / p if (DATA / p).exists() else WP4B / p.name))
        return out
    out = []
    for b in blind_files:
        p = WP4B / Path(b).name.replace("pop_blind_", "pop_read_")
        if p.exists():
            out.append(p)
    return out


def load_read(path: Path, blind: pd.DataFrame) -> pd.DataFrame:
    r = pd.read_csv(path, dtype={"item_id": str}, keep_default_na=False)
    need = {"item_id", "present"}
    if not need <= set(r.columns):
        raise SystemExit(f"{rel(path)}: needs columns {sorted(need)}, has {list(r.columns)}")
    if "note" not in r.columns:
        r["note"] = ""
    r["present"] = pd.to_numeric(r["present"], errors="raise").astype(int)
    if not r["present"].isin([0, 1]).all():
        raise SystemExit(f"{rel(path)}: present must be 0 or 1")
    if r["item_id"].duplicated().any():
        raise SystemExit(f"{rel(path)}: duplicate item_id")
    if set(r["item_id"]) != set(blind["item_id"]):
        raise SystemExit(f"{rel(path)}: item ids differ from its blind file")
    return r


# --------------------------------------------------------------------------
# grades
# --------------------------------------------------------------------------

def grade_frame(g: pd.DataFrame) -> pd.DataFrame:
    return g[["facility_id", "service_line", "grade"]].reset_index(drop=True)


def spliced(cur: pd.DataFrame, part: pd.DataFrame) -> pd.Series:
    """Grades of the mix: the current grade, replaced by the regraded value for affected facilities."""
    look = dict(zip(zip(part["facility_id"], part["service_line"]), part["grade"]))
    return pd.Series([look.get((f, l), g) for f, l, g in zip(cur["facility_id"], cur["service_line"],
                                                              cur["grade"])], index=cur.index)


def line_counts(lines: pd.Series, grades: pd.Series) -> dict:
    out = {}
    for line in recipes.SERVICE_LINES:
        vc = grades[lines == line].value_counts()
        out[line] = [int(vc.get(k, 0)) for k in range(4)]
    return out


def grade_impact(cur: pd.DataFrame, mix_grade: pd.Series) -> dict:
    lines = cur["service_line"]
    a, b = cur["grade"], mix_grade
    ca, cb = line_counts(lines, a), line_counts(lines, b)
    out = {}
    for line in recipes.SERVICE_LINES:
        m = (lines == line).to_numpy()
        aa, bb = a[m].to_numpy(), b[m].to_numpy()
        ch = aa != bb
        trans = {}
        for x, y in zip(aa[ch], bb[ch]):
            trans[f"{x}->{y}"] = trans.get(f"{x}->{y}", 0) + 1
        out[line] = {
            "current": ca[line], "candidate": cb[line],
            "grade_ge2_current": int((aa >= 2).sum()), "grade_ge2_candidate": int((bb >= 2).sum()),
            "facilities_changed": int(ch.sum()),
            "transitions": dict(sorted(trans.items())),
            "cross_gate_up": int(((aa < 2) & (bb >= 2)).sum()),
            "cross_gate_down": int(((aa >= 2) & (bb < 2)).sum()),
        }
    return out


# --------------------------------------------------------------------------
# spend
# --------------------------------------------------------------------------

def price_of(row: dict) -> float | None:
    p = PRICES.get(row.get("served_model") or row.get("model"))
    u = row.get("usage") or {}
    if p is None or not u:
        return None
    return (u.get("input_tokens", 0) * p[0] + u.get("output_tokens", 0) * p[1]
            + u.get("cache_creation_input_tokens", 0) * p[2] + u.get("cache_read_input_tokens", 0) * p[3]) / 1e6


def ledger_summary() -> dict:
    out, total = {}, 0.0
    for name, p in LEDGERS.items():
        if not p.exists():
            out[name] = {"file": rel(p), "exists": False, "responses": 0, "usd": 0.0}
            continue
        rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
        usd = sum(float(r.get("cost_usd") or 0) for r in rows)
        rec = [price_of(r) for r in rows]
        diffs = [abs(x - float(r.get("cost_usd") or 0)) for x, r in zip(rec, rows) if x is not None]
        by_model, by_purpose = {}, {}
        for r in rows:
            m = r.get("served_model") or r.get("model") or "unknown"
            d = by_model.setdefault(m, {"responses": 0, "usd": 0.0})
            d["responses"] += 1; d["usd"] += float(r.get("cost_usd") or 0)
            k = f"{r.get('purpose', '')} | {m}"
            d = by_purpose.setdefault(k, {"responses": 0, "usd": 0.0})
            d["responses"] += 1; d["usd"] += float(r.get("cost_usd") or 0)
        for d in (*by_model.values(), *by_purpose.values()):
            d["usd"] = round(d["usd"], 4)
        out[name] = {
            "file": rel(p), "exists": True, "responses": len(rows), "usd": round(usd, 4),
            "first_at": min((r.get("at", "") for r in rows), default=None),
            "last_at": max((r.get("at", "") for r in rows), default=None),
            "recomputed_usd": round(sum(x for x in rec if x is not None), 4),
            "lines_recomputed": len(diffs), "lines_without_price": len(rows) - len(diffs),
            "max_abs_line_diff_usd": round(max(diffs), 8) if diffs else None,
            "by_model": dict(sorted(by_model.items())), "by_purpose": dict(sorted(by_purpose.items())),
        }
        total += usd
    wp4b = out["WP4b (this run)"]["usd"]
    return {"ledgers": out, "project_total_usd": round(total, 4), "this_stage_usd": 0.0,
            "wp4b_run_cap_usd": RUN_CAP_USD, "wp4b_run_remaining_usd": round(RUN_CAP_USD - wp4b, 4),
            "project_cap_usd": PROJECT_CAP_USD, "project_remaining_usd": round(PROJECT_CAP_USD - total, 4),
            "sealed_eval_reserve_usd": SEALED_RESERVE_USD,
            "prices_per_million": {m: dict(zip(("input", "output", "cache_write", "cache_read"), p))
                                   for m, p in PRICES.items()},
            "prices_source": "claude-api skill model table (cached 2026-11-03); cache write 1.25x input",
            "read_at_utc": now(),
            "note": "this stage makes no LLM calls; other workflows may be spending concurrently"}


# --------------------------------------------------------------------------
# how firm the gold gate is (disclosure only: the gate is the declared interval in scores.json)
# --------------------------------------------------------------------------

def boot_arrays(gold: pd.DataFrame, a_sets, b_sets, n_boot: int, seed: int, chunk: int = 5000):
    """The resampling of scripts/wp4_student.py paired_delta (same strata, the same rng calls in the same order),
    returning raw per-tag and macro F1 deltas (a minus b) instead of rounded percentiles. At n_boot=2,000 and seed
    BOOT_SEED + 1 it is the declared gate; a longer run at that seed starts with the declared resamples."""
    G = W4.Y_of(gold["gold"]); A = W4.Y_of(a_sets); B = W4.Y_of(b_sets)
    m_tp = [(G & X).astype(float) for X in (A, B)]
    m_fp = [(~G & X).astype(float) for X in (A, B)]
    m_fn = [(G & ~X).astype(float) for X in (A, B)]
    rng = np.random.default_rng(seed)
    groups = [np.flatnonzero(gold["stratum"].to_numpy() == s) for s in sorted(gold["stratum"].unique())]
    n = len(gold)
    per = np.empty((n_boot, len(TAGS))); mac = np.empty(n_boot)
    for s0 in range(0, n_boot, chunk):
        m = min(chunk, n_boot - s0)
        cnt = np.empty((m, n))
        for k in range(m):
            cnt[k] = np.bincount(np.concatenate([rng.choice(ix, size=len(ix), replace=True) for ix in groups]),
                                 minlength=n)
        fa = WS._prf(cnt @ m_tp[0], cnt @ m_fp[0], cnt @ m_fn[0])[2]
        fb = WS._prf(cnt @ m_tp[1], cnt @ m_fp[1], cnt @ m_fn[1])[2]
        per[s0:s0 + m] = fa - fb
        mac[s0:s0 + m] = fa.mean(1) - fb.mean(1)
    return per, mac


def r4(v) -> float:
    return round(float(v), 4) + 0.0   # + 0.0 turns -0.0 into 0.0


def ci4(x: np.ndarray) -> list:
    return [r4(v) for v in np.percentile(x, [2.5, 97.5])]


def dist_summary(x: np.ndarray) -> dict:
    lo, hi = np.percentile(x, [2.5, 97.5])
    p = float((x <= 0).mean())
    return {"resamples": int(len(x)), "ci95": [r4(lo), r4(hi)],
            "ci95_full_precision": [float(lo), float(hi)],
            "p_delta_le_0": round(p, 4), "p_delta_lt_0": round(float((x < 0).mean()), 4),
            "monte_carlo_se_of_p_delta_le_0": round(math.sqrt(p * (1 - p) / len(x)), 4)}


def sensitivity(gold: pd.DataFrame, a_sets, b_sets, j: int | None) -> dict:
    """The declared seed extended to SENS_LONG_N_BOOT resamples, and SENS_ALT_SEEDS other seeds each running the
    declared 2,000-resample procedure. j = tag column, or None for macro F1."""
    pick = (lambda per, mac: mac) if j is None else (lambda per, mac: per[:, j])  # noqa: E731
    per, mac = boot_arrays(gold, a_sets, b_sets, SENS_LONG_N_BOOT, WS.BOOT_SEED + 1)
    x = pick(per, mac)
    declared = x[:WS.N_BOOT]
    lows, pooled = [], []
    for i in range(SENS_ALT_SEEDS):
        p_, m_ = boot_arrays(gold, a_sets, b_sets, WS.N_BOOT, WS.BOOT_SEED + 2 + i)
        y = pick(p_, m_)
        lows.append(float(np.percentile(y, 2.5))); pooled.append(y)
    lows = np.array(lows)
    d_low = float(np.percentile(declared, 2.5))
    return {
        "declared": {"seed": WS.BOOT_SEED + 1, "resamples": WS.N_BOOT, "ci95": ci4(declared),
                     "lower_bound_full_precision": d_low},
        "declared_seed_extended": {"seed": WS.BOOT_SEED + 1, **dist_summary(x),
                                   "note": f"the same rng stream run to {SENS_LONG_N_BOOT:,} resamples; its first "
                                           f"{WS.N_BOOT:,} are the declared run"},
        "other_seeds": {"seeds": [WS.BOOT_SEED + 2, WS.BOOT_SEED + 1 + SENS_ALT_SEEDS], "runs": SENS_ALT_SEEDS,
                        "resamples_each": WS.N_BOOT,
                        "lower_bound_min": r4(lows.min()), "lower_bound_median": r4(np.median(lows)),
                        "lower_bound_max": r4(lows.max()),
                        "runs_with_lower_bound_above_0": int((lows > 0).sum()),
                        "runs_with_lower_bound_at_or_above_declared": int((lows >= d_low).sum()),
                        "pooled": dist_summary(np.concatenate(pooled))},
    }


def seed_provenance() -> dict:
    """When the gate's seed line and D050 entered git (to show the seed was fixed before the protocol)."""
    def first(pattern: str, path: str):
        try:
            out = subprocess.run(["git", "log", "-S", pattern, "--format=%h %aI", "--", path], cwd=ROOT,
                                 capture_output=True, text=True, check=True).stdout.strip().splitlines()
            return dict(zip(("commit", "at"), out[-1].split(" ", 1))) if out else None
        except Exception:
            return None
    seed = first("seed=WS.BOOT_SEED + 1", "scripts/wp4_student.py")
    n_boot = first("N_BOOT = 2000", "scripts/wp3_score.py")
    d050 = first("D050 |", "docs/08_decisions_log.md")
    try:
        dirty = bool(subprocess.run(["git", "status", "--porcelain", "--", "scripts/wp4_student.py",
                                     "scripts/wp3_score.py"], cwd=ROOT, capture_output=True, text=True,
                                    check=True).stdout.strip())
    except Exception:
        dirty = None
    when = lambda x: datetime.fromisoformat(x["at"]) if x else None  # noqa: E731
    return {"seed_line_first_commit": seed, "n_boot_line_first_commit": n_boot, "d050_first_commit": d050,
            "bootstrap_files_uncommitted_changes": dirty,
            "seed_and_n_boot_before_d050": bool(seed and n_boot and d050 and when(seed) < when(d050)
                                                and when(n_boot) < when(d050))}


def firmness_text(t: str, c: str, s: dict, macro: dict | None, rd: dict) -> str:
    """One paragraph on how firm a shipped tag's gold gate is (Result section and Disclosures)."""
    d, e, o = s["declared"], s["declared_seed_extended"], s["other_seeds"]
    hc = rd["hard_calls"]
    txt = (f"The `{t}` gold gate passes at the declared seed ({d['ci95'][0]:+.4f} to {d['ci95'][1]:+.4f}; seed "
           f"{d['seed']}, {d['resamples']:,} resamples, committed in scripts/wp4_student.py before D050), but only just. Run "
           f"to {e['resamples']:,} resamples, the same resampling puts the lower bound at {e['ci95'][0]:+.4f}, with "
           f"P(gain <= 0) = {e['p_delta_le_0']:.1%} (Monte Carlo SE {e['monte_carlo_se_of_p_delta_le_0']:.1%}). Across "
           f"{o['runs']} other seeds, each running the declared {o['resamples_each']:,}-resample procedure, the lower "
           f"bound ranged from {o['lower_bound_min']:+.4f} to {o['lower_bound_max']:+.4f} (median "
           f"{o['lower_bound_median']:+.4f}) and was above 0 in {o['runs_with_lower_bound_above_0']} of {o['runs']}; the "
           f"declared run's lower bound is higher than {o['runs'] - o['runs_with_lower_bound_at_or_above_declared']} of "
           f"them. Pooled over those {o['pooled']['resamples']:,} resamples: {o['pooled']['ci95'][0]:+.4f} to "
           f"{o['pooled']['ci95'][1]:+.4f}, P(gain <= 0) = {o['pooled']['p_delta_le_0']:.1%}.")
    if macro:
        me = macro["declared_seed_extended"]
        txt += (f" The candidate mix's macro gain touches 0 the same way: {macro['declared']['ci95'][0]:+.4f} to "
                f"{macro['declared']['ci95'][1]:+.4f} declared, {me['ci95'][0]:+.4f} to {me['ci95'][1]:+.4f} at "
                f"{me['resamples']:,} resamples, P(gain <= 0) = {me['p_delta_le_0']:.1%}.")
    txt += (f" The gold evidence is marginal; the change rests mainly on the blinded read ({rd['favours_candidate']} of "
            f"{rd['n']}, {hc['favour_if_all_hard_calls_went_to_current']} to "
            f"{hc['favour_if_all_hard_calls_went_to_candidate']} if every hard call flipped). The decision is unchanged "
            "and the gate was not rerun with another seed: D050 fixes the declared interval. The integration pass "
            "should weigh this before promoting.")
    return txt


# --------------------------------------------------------------------------
# gold separation audits (disclosure only)
# --------------------------------------------------------------------------

def _alnum(t: str) -> str:
    return re.sub(r"[^a-z0-9]", "", t.lower())


def _wordset(t: str) -> str:
    return " ".join(sorted(set(S.near_key(t).split())))


MATCH_KEYS = {"exact": lambda t: t.lower().strip(), "near_key": S.near_key,
              "letters_and_digits_only": _alnum, "same_word_set": _wordset}
MATCH_KEY_NOTE = {"exact": "identical text", "near_key": "the declared near-duplicate rule (lowercase, punctuation "
                  "dropped, spaces collapsed)", "letters_and_digits_only": "lowercase, everything but a-z and 0-9 "
                  "dropped ('neuro critical care' = 'neurocritical care')", "same_word_set": "the same set of words "
                  "under near_key, in any order"}


def prompt_gold_audit(gold: pd.DataFrame) -> dict:
    """Gold texts inside the frozen WP3 p2 teacher prompt (its 70 labeled examples and the facts quoted in its rules)."""
    snap = json.loads(PROMPT_SNAPSHOT.read_text())
    sp = snap["system_prompt"]
    sha = hashlib.sha256(sp.encode("utf-8")).hexdigest()
    W3 = _load_script("wp3_teacher")
    head = sp.partition("## Examples")[0]
    facts = [(e, list(tg), "labeled example") for e, tg in W3.EXAMPLES] + \
            [(q, None, "quoted in the rules (no label)") for q in re.findall(r'"([^"]+)"', head)]
    gold_tags = dict(zip(gold["text_norm"], gold["gold"]))
    gk = {k: {} for k in MATCH_KEYS}
    for g in gold["text_norm"]:
        for k, f in MATCH_KEYS.items():
            gk[k].setdefault(f(g), []).append(g)
    multi = [(g, " " + S.near_key(g) + " ") for g in gold["text_norm"] if len(S.near_key(g).split()) >= 2]
    one = [(g, " " + S.near_key(g) + " ") for g in gold["text_norm"] if len(S.near_key(g).split()) == 1]
    hits, one_word = [], []
    for q, tg, where in facts:
        matched = set()
        for k, f in MATCH_KEYS.items():
            for g in gk[k].get(f(q), []):
                if g not in matched:
                    matched.add(g)
                    hits.append({"prompt_text": q, "prompt_tags": tg, "where": where, "gold_text": g,
                                 "gold_tags": sorted(gold_tags[g]), "match": k})
        nq = " " + S.near_key(q) + " "
        for g, ng in multi:
            if g not in matched and ng in nq:
                matched.add(g)
                hits.append({"prompt_text": q, "prompt_tags": tg, "where": where, "gold_text": g,
                             "gold_tags": sorted(gold_tags[g]), "match": "prompt text contains the gold text (2+ words)"})
        ow = [g for g, ng in one if ng in nq and g not in matched]
        if ow:
            one_word.append({"prompt_text": q, "gold_texts": ow})
    equal = [h for h in hits if h["match"] != "prompt text contains the gold text (2+ words)"]
    contains = [h for h in hits if h["match"] == "prompt text contains the gold text (2+ words)"]
    return {
        "file": rel(PROMPT_SNAPSHOT), "prompt_version": snap.get("prompt_version"), "sha256": sha,
        "sha256_equals_snapshot_record": sha == snap.get("sha256"),
        "labeled_examples": len(W3.EXAMPLES), "labeled_examples_verbatim_in_prompt": sum(e in sp for e, _ in W3.EXAMPLES),
        "facts_quoted_in_rules": len(facts) - len(W3.EXAMPLES),
        "rule": "each prompt fact against each gold text: equal under " + ", ".join(MATCH_KEYS)
                + "; or the prompt fact contains a gold text of 2+ words as whole words under near_key",
        "hits": hits, "equal_after_normalisation": len(equal), "contain_a_gold_text": len(contains),
        "gold_tags_involved": sorted({t for h in hits for t in h["gold_tags"]}),
        "one_word_gold_texts_inside_prompt_facts": {
            "prompt_facts": len(one_word), "gold_texts": sorted({g for x in one_word for g in x["gold_texts"]}),
            "items": one_word, "note": "a one-word gold text (a bare tag word) inside a longer example; not counted "
                                       "as a copy of a gold text"},
        "why_it_is_there": "the p2 prompt was written in WP3 and frozen; D050 required WP4b to reuse it unchanged",
    }


def near_dup_audit(gold: pd.DataFrame, merged: pd.DataFrame, val_texts: set, draws: dict, teacher: str,
                   changed: list, cur_g: np.ndarray, mix_g: np.ndarray) -> dict:
    """Gold texts under looser keys than the declared near-duplicate rule, in the labels and the population draws;
    and near copies (difflib) of the gold rows the mix fixes, among the chosen teacher's training labels."""
    gold_texts = gold["text_norm"].tolist()
    gk = {k: {} for k in MATCH_KEYS}
    for g in gold_texts:
        for k, f in MATCH_KEYS.items():
            gk[k].setdefault(f(g), []).append(g)
    m = merged.assign(split_used=np.where(merged["text_norm"].isin(val_texts), "validation", "train"))
    out_keys = {}
    loose_gold = set()
    for k, f in MATCH_KEYS.items():
        hit = m[m["text_norm"].map(lambda t: f(t) in gk[k])]
        if k not in ("exact", "near_key"):
            loose_gold |= {g for t in hit["text_norm"] for g in gk[k][f(t)]}
        out_keys[k] = {
            "merged_texts": int(len(hit)), "in_training": int((hit["split_used"] == "train").sum()),
            "by_source": {s: int(v) for s, v in hit["source"].value_counts().sort_index().items()},
            "draw_texts": {n: int(sum(f(t) in gk[k] for t in ts)) for n, ts in draws.items()},
            "items": [{"text": t, "source": s, "split_used": sp_, "gold_text": gk[k][f(t)][0]}
                      for t, s, sp_ in zip(hit["text_norm"], hit["source"], hit["split_used"])][:40],
            "what": MATCH_KEY_NOTE[k]}
    changed_rows = [i for i in range(len(gold_texts)) if (cur_g[i] != mix_g[i]).any()]
    teacher_train = m[(m["source"] == teacher) & (m["split_used"] == "train")]
    tt_texts = teacher_train["text_norm"].tolist()
    tt_tags = [set(x) for x in teacher_train["tags"]]
    copies = {}
    for t in changed:
        j = TAGS.index(t)
        G = np.array([t in g for g in gold["gold"]])
        rows = []
        for i in changed_rows:
            if cur_g[i, j] == mix_g[i, j]:
                continue
            sm = difflib.SequenceMatcher(None, "", gold_texts[i], autojunk=False)
            best, bi = 0.0, None
            for q, x in enumerate(tt_texts):
                sm.set_seq1(x)
                if sm.real_quick_ratio() <= best or sm.quick_ratio() <= best:
                    continue
                r = sm.ratio()
                if r > best:
                    best, bi = r, q
            rows.append({"gold_text": gold_texts[i], "gold_says": int(G[i]), "current_says": int(cur_g[i, j]),
                         "mix_says": int(mix_g[i, j]), "fixed_by_mix": bool(mix_g[i, j] == G[i]),
                         "best_ratio": round(best, 3), "best_teacher_text": tt_texts[bi] if bi is not None else None,
                         "teacher_says_on_it": (int(t in tt_tags[bi]) if bi is not None else None)})
        fixed = [r for r in rows if r["fixed_by_mix"]]
        near = [r for r in fixed if r["best_ratio"] >= NEAR_COPY_RATIO]
        copies[t] = {"gold_rows_changed": len(rows), "fixed": len(fixed), "broken": len(rows) - len(fixed),
                     "fixed_with_a_near_copy_in_training": len(near),
                     "near_copy_ratio_range": [min(r["best_ratio"] for r in near), max(r["best_ratio"] for r in near)]
                     if near else None,
                     "near_copies_where_teacher_label_equals_gold": sum(r["teacher_says_on_it"] == r["gold_says"] for r in near),
                     "rows": rows}
    return {"keys": out_keys, "gold_rows_changed_by_mix": len(changed_rows),
            "loose_match_gold_texts_among_changed_rows": len(loose_gold & {gold_texts[i] for i in changed_rows}),
            "near_copies_of_changed_rows": copies,
            "near_copy_rule": f"difflib SequenceMatcher ratio of at least {NEAR_COPY_RATIO} between the gold text and "
                              f"a {teacher}-labeled training text (a reporting threshold, from the review)",
            "split_used": "validation = the WP4 validation texts (data/models/wp4b_encoder/wp4_split.json), the "
                          "split every candidate used; merged_labels.parquet's own split column is not used"}


def file_times(p: Path) -> dict:
    st = os.stat(p)
    b = getattr(st, "st_birthtime", None)
    f = lambda x: datetime.fromtimestamp(x, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
    return {"created_utc": f(b) if b else None, "modified_utc": f(st.st_mtime),
            "written_once": None if b is None else bool(abs(st.st_mtime - b) < 1.0)}


def read_provenance(reads: dict, pilot: dict, prior: dict | None, run_at: str) -> dict:
    """What the files can show about the reads: written once, before unblinding, unchanged since."""
    prior_rp = (prior or {}).get("read_provenance", {}).get("population_reads", {})
    out = {"pilot_reads": {}, "population_reads": {}}
    for name in PILOT_READS:
        p = WP4B / name
        if not p.exists():
            continue
        ft = file_times(p)
        rec = pilot.get("inputs", {}).get(name)
        out["pilot_reads"][rel(p)] = {**ft, "sha256": RB.sha256(p), "sha256_at_unblinding": rec,
                                      "unchanged_since_unblinding": RB.sha256(p) == rec,
                                      "unblinded_at_utc": pilot.get("created_at_utc"),
                                      "written_before_unblinding": ft["modified_utc"] < (pilot.get("created_at_utc") or "")}
    for r in reads.values():
        f = r["read_file"]
        p = DATA / f
        ft = file_times(p)
        if f in prior_rp:
            first_at, first_sha = prior_rp[f]["first_unblinded_at_utc"], prior_rp[f]["sha256_at_first_unblinding"]
        elif prior and f in prior.get("inputs", {}):
            first_at, first_sha = prior["created_at_utc"], prior["inputs"][f]["sha256"]
        else:
            first_at, first_sha = run_at, r["read_sha256"]
        out["population_reads"][f] = {**ft, "sha256": r["read_sha256"], "sha256_at_first_unblinding": first_sha,
                                      "unchanged_since_unblinding": r["read_sha256"] == first_sha,
                                      "first_unblinded_at_utc": first_at,
                                      "written_before_unblinding": ft["modified_utc"] < first_at}
    out["reader_identity_recorded"] = False
    out["blind_file_names_carry_candidate_and_tag"] = any(
        r["candidate"] in Path(r["blind_file"]).name and r["tag"] in Path(r["blind_file"]).name for r in reads.values())
    out["key_in_same_folder_as_blind_files"] = all((DATA / r["blind_file"]).parent == KEY.parent for r in reads.values())
    out["note"] = ("first unblinding = the first decide run that wrote decision.json from these reads (carried forward "
                   "across reruns); file times are the data folder's, in UTC")
    return out


FUTURE_RUNS = [
    "Near-duplicate rule: key on letters and digits only (and consider same-word-set equality) at the merge stage "
    "and in the draw exclusion, not only lowercase with punctuation dropped.",
    "Teacher prompt: exclude gold texts from any future prompt revision; check examples against gold before freezing.",
    "Blind files: give them neutral names (for example pop_blind_<random>.csv) and keep the key in a folder readers "
    "are not given.",
    "Reader provenance: each reader writes a sidecar with its agent or session id, start and end time, and the files "
    "it opened.",
    "merged_labels.parquet: drop or rename its split column in a future merge; it is unused and differs from the "
    "split the students used (this stage does not own that file).",
]


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--reads", help="comma list of reader files (default: pop_read_* next to each blind file)")
    ap.add_argument("--dry-run", action="store_true", help="print only; write nothing")
    args = ap.parse_args(argv)
    t0 = time.time()
    checks: list[dict] = []

    def check(name, ok, value=""):
        checks.append({"check": name, "passed": bool(ok), "value": str(value)})
        if not ok:
            print(f"  [FAIL] {name}: {value}")

    # ------------------------------------------------------------ inputs and their hashes
    run_at = now()
    prior = json.loads(DECISION.read_text()) if DECISION.exists() else None   # the previous run, read before any write
    prior_tags_sha = RB.sha256(OUT_TAGS) if OUT_TAGS.exists() else None
    sc = json.loads(SCORES.read_text())
    sc_meta = json.loads(SCORES.with_name(SCORES.name + ".meta.json").read_text())
    key_sha = check_sidecar_sha(KEY)
    key = pd.read_csv(KEY, dtype={"item_id": str})
    blind_files = [Path(b).name for b in sc.get("blind_files", [])]
    blind = {}
    blind_sha = {}
    for b in blind_files:
        p = WP4B / b
        blind_sha[b] = check_sidecar_sha(p)
        blind[b] = pd.read_csv(p, dtype={"item_id": str}, keep_default_na=False)
    reads_paths = find_reads(args.reads, blind_files)
    if not reads_paths:
        raise SystemExit("no reader files found (pop_read_<tag>_<candidate>.csv)")

    ft_sha = RB.sha256(FACT_TAGS)
    if ft_sha != sc["current_method"]["sha256"]:
        raise SystemExit("gold/fact_tags.parquet changed since labels/wp4b/scores.json was written "
                         f"({ft_sha[:12]} vs {sc['current_method']['sha256'][:12]}); rerun scripts/wp4b_students.py")
    check("gold/fact_tags.parquet is the file scores.json scored as current", True, ft_sha[:16])
    gold_sha = RB.sha256(paths.labels_dir() / "gold_facts.csv")
    check("gold_facts.csv is the file scores.json scored", gold_sha == sc_meta.get("gold_facts_sha256"), gold_sha[:16])
    merged_sha = RB.sha256(MERGED)
    check("merged labels are the file the candidates trained on", merged_sha == sc["labels"]["sha256"],
          merged_sha[:16])
    print(f"WP4b decide: {len(reads_paths)} reader file(s); current tagger {sc['current_method']['model_version']}")

    # ------------------------------------------------------------ texts, current tags, candidate tags
    distinct = S.distinct_texts()
    idx = {t: i for i, t in enumerate(distinct)}
    cur_lut, cur_info = S.current_lookup(distinct)
    C_all = W4.Y_of([cur_lut[t] for t in distinct])
    gold = WS.load_gold()
    gold_texts = gold["text_norm"].tolist()
    gold_set = set(gold_texts)
    gold_near = {S.near_key(t) for t in gold_texts}
    non_gold = np.array([(t not in gold_set) and (S.near_key(t) not in gold_near) for t in distinct])
    merged = pd.read_parquet(MERGED, columns=["text_norm", "tags", "source"])
    m_tags = {t: set(x) for t, x in zip(merged["text_norm"], merged["tags"])}
    m_src = dict(zip(merged["text_norm"], merged["source"]))
    fired: dict[str, np.ndarray] = {}
    cand_sha = {}

    def cand(c: str) -> np.ndarray:
        if c not in fired:
            d = S.CANDIDATES[c]["dir"]
            cand_sha[c] = RB.sha256(d / "scores.parquet")
            fired[c] = S.load_scores_dir(d, distinct)
        return fired[c]

    # ------------------------------------------------------------ unblind
    reads = {}
    for rp in reads_paths:
        bname = rp.name.replace("pop_read_", "pop_blind_")
        if bname not in blind:
            raise SystemExit(f"{rel(rp)}: no blind file {bname} listed in scores.json")
        bdf = blind[bname]
        r = load_read(rp, bdf)
        k = key[key["item_id"].isin(set(bdf["item_id"]))]
        if len(k) != len(bdf) or k[["candidate", "tag"]].drop_duplicates().shape[0] != 1:
            raise SystemExit(f"{rel(rp)}: key rows do not map one (candidate, tag) to the blind file")
        c, t = k["candidate"].iloc[0], k["tag"].iloc[0]
        dr = sc["draws"][f"{c}|{t}"]
        check(f"{c} {t}: read rows equal the draw", len(r) == dr["drawn"], f"{len(r)} rows, drawn {dr['drawn']}")
        check(f"{c} {t}: blind tag column is the key's tag", (bdf["tag"] == t).all(), t)
        m = r.merge(k, on="item_id", validate="one_to_one").merge(bdf[["item_id", "text"]], on="item_id",
                                                                 validate="one_to_one")
        check(f"{c} {t}: every drawn text is a disagreement", (m["candidate_says"] != m["current_says"]).all())
        # the key must match the files this stage builds from
        j = TAGS.index(t)
        F = cand(c)
        ii = [idx[x] for x in m["text"]]
        ok_c = bool((F[ii, j].astype(int) == m["candidate_says"].to_numpy()).all())
        ok_k = bool((C_all[ii, j].astype(int) == m["current_says"].to_numpy()).all())
        check(f"{c} {t}: key matches the saved candidate tags and the current tags", ok_c and ok_k,
              f"candidate {ok_c}, current {ok_k}")
        m["favours_candidate"] = m["present"] == m["candidate_says"]
        m["hard_call"] = m["note"].fillna("").str.contains("hard call", case=False)
        m["in_merged_labels"] = m["text"].isin(m_tags)
        m["merged_label_says"] = [(int(t in m_tags[x]) if x in m_tags else None) for x in m["text"]]
        m["merged_label_source"] = [m_src.get(x) for x in m["text"]]
        n = len(m)
        fav = int(m["favours_candidate"].sum())
        add = m["candidate_says"] == 1
        hard = m["hard_call"]
        inl = m["in_merged_labels"]
        need = MIN_FAVOUR if n >= N_READ else math.ceil(MIN_FAVOUR / N_READ * n)
        share_ci = wilson(fav, n)
        pool = dr["pool_non_gold_disagreements"]
        reads[(c, t)] = {
            "candidate": c, "tag": t, "read_file": rel(rp), "read_sha256": RB.sha256(rp),
            "blind_file": rel(WP4B / bname), "n": n, "favours_candidate": fav, "favours_current": n - fav,
            "needed": need, "passes": bool(fav >= need),
            "fewer_than_25": n < N_READ,
            "share_favouring_candidate": round(fav / n, 4), "share_wilson95": share_ci,
            "pool_non_gold_disagreements": pool,
            "pool_estimate_favouring_candidate": [round(share_ci[0] * pool), round(share_ci[1] * pool)],
            "additions": {"n": int(add.sum()), "reader_agrees_with_candidate": int((add & m["favours_candidate"]).sum())},
            "removals": {"n": int((~add).sum()),
                         "reader_agrees_with_candidate": int((~add & m["favours_candidate"]).sum())},
            "hard_calls": {"n": int(hard.sum()), "favour_candidate": int((hard & m["favours_candidate"]).sum()),
                           "favour_if_all_hard_calls_went_to_current": fav - int((hard & m["favours_candidate"]).sum()),
                           "favour_if_all_hard_calls_went_to_candidate": fav + int((hard & ~m["favours_candidate"]).sum())},
            "texts_in_training_or_validation_labels": {
                "n": int(inl.sum()), "favour_candidate": int((inl & m["favours_candidate"]).sum()),
                "merged_label_agrees_with_candidate": int(sum(
                    1 for x, cs in zip(m.loc[inl, "merged_label_says"], m.loc[inl, "candidate_says"]) if x == cs))},
            "texts_not_in_labels": {"n": int((~inl).sum()), "favour_candidate": int((~inl & m["favours_candidate"]).sum())},
            "items": [{"item_id": x.item_id, "text": x.text, "candidate_says": int(x.candidate_says),
                       "current_says": int(x.current_says), "reader_present": int(x.present),
                       "favours": "candidate" if x.favours_candidate else "current",
                       "hard_call": bool(x.hard_call), "note": x.note, "in_merged_labels": bool(x.in_merged_labels),
                       "merged_label_says": x.merged_label_says, "merged_label_source": x.merged_label_source}
                      for x in m.sort_values(["favours_candidate", "candidate_says", "item_id"]).itertuples()],
        }
        print(f"  read {c} {t}: {fav} of {n} in the candidate's favour (needs {need}) -> "
              f"{'PASS' if reads[(c, t)]['passes'] else 'FAIL'}")

    # ------------------------------------------------------------ decision per tag
    per_tag = {}
    for t in TAGS:
        e = sc["per_tag"][t]
        rows = {}
        for c, x in e["candidates"].items():
            ci = x["paired_delta_vs_current"]["ci95"]
            g1 = ci[0] > 0
            if g1 != bool(x["paired_interval_entirely_above_0"]):
                raise SystemExit(f"{c} {t}: paired flag disagrees with its interval")
            rd = reads.get((c, t))
            g2 = bool(rd and rd["passes"])
            if g1 and rd is None:
                why = "passed the gold gate but has no hand read: does not ship"
            elif g1 and not g2:
                why = f"passed the gold gate; hand read {rd['favours_candidate']} of {rd['n']}, needs {rd['needed']}"
            elif g1 and g2:
                why = f"passed both gates (hand read {rd['favours_candidate']} of {rd['n']})"
            else:
                why = "paired interval not entirely above 0"
            rows[c] = {"gold_f1": x["gold"]["f1"], "gold_f1_ci95": x["gold"]["f1_ci95"],
                       "paired_delta_point": x["paired_delta_vs_current"]["point"], "paired_delta_ci95": ci,
                       "passes_gold_gate": g1, "hand_read": None if rd is None else
                       {"favours_candidate": rd["favours_candidate"], "n": rd["n"], "needed": rd["needed"]},
                       "passes_hand_read": g2 if rd is not None else None, "passes_both": g1 and g2,
                       "validation_f1": x["validation"]["f1"], "why": why}
        passing = [c for c in rows if rows[c]["passes_both"]]
        chosen = max(passing, key=lambda c: (rows[c]["validation_f1"], c)) if passing else None
        cm = e["current_method"]
        per_tag[t] = {
            "gold_support": e["gold_support"], "current_method": cm,
            "current_gold_f1": e["current"]["gold"]["f1"], "current_gold_f1_ci95": e["current"]["gold"]["f1_ci95"],
            "current_validation_f1": e["current"]["validation"]["f1"],
            "candidates": rows, "passing_both": passing,
            "tie_break": None if len(passing) < 2 else {
                "rule": "highest validation F1 against the teacher labels",
                "validation_f1": {c: rows[c]["validation_f1"] for c in passing}},
            "decision": f"replace with {chosen}" if chosen else "keep the current method",
            "chosen_candidate": chosen,
            "method_in_mix": CANDIDATE_LABEL[chosen] if chosen else cm,
            "method_id_in_mix": f"student:wp4b-{chosen}" if chosen else CURRENT_METHOD_ID[cm],
        }
    changed = [t for t in TAGS if per_tag[t]["chosen_candidate"]]
    failed_read = [(c, t) for (c, t), r in reads.items() if not r["passes"]]
    moved_txt = ", ".join(f"{t} ({per_tag[t]['chosen_candidate']})" for t in changed)
    print(f"  decision: {('replace on ' + moved_txt) if changed else 'no tag changes method'}")

    # model_version: methods in this order, each with its tags; the last group is [rest]
    groups: dict[str, list[str]] = {}
    for t in TAGS:
        groups.setdefault(per_tag[t]["method_id_in_mix"], []).append(t)
    order = sorted(groups, key=lambda k: (0 if k.startswith("student:wp4b") else 1 if k.startswith("student") else 2, k))
    rest_key = max(groups, key=lambda k: (len(groups[k]), k.startswith("regex")))
    model_version = "+".join(f"{k}[{'rest' if k == rest_key else ','.join(groups[k])}]"
                             for k in order if k != rest_key) + f"+{rest_key}[rest]"
    if not changed:
        check("no change: model_version equals the current one", model_version == sc["current_method"]["model_version"],
              model_version)
    tags_by_method = {k: groups[k] for k in order}

    # ------------------------------------------------------------ the candidate mix over distinct texts
    M = C_all.copy()
    for t in changed:
        j = TAGS.index(t)
        M[:, j] = cand(per_tag[t]["chosen_candidate"])[:, j]
    pop_changes = {}
    for t in changed:
        j = TAGS.index(t); c = per_tag[t]["chosen_candidate"]
        a, b = M[:, j], C_all[:, j]
        exp = sc["per_tag"][t]["candidates"][c]["population"]
        got = {"added_vs_current": int((a & ~b & non_gold).sum()), "removed_vs_current": int((~a & b & non_gold).sum())}
        check(f"{t}: added and removed texts (outside gold) equal scores.json",
              got["added_vs_current"] == exp["added_vs_current"] and got["removed_vs_current"] == exp["removed_vs_current"],
              f"{got} vs {exp['added_vs_current']}/{exp['removed_vs_current']}")
        pop_changes[t] = {"candidate": c, "added_all_texts": int((a & ~b).sum()), "removed_all_texts": int((~a & b).sum()),
                          "added_outside_gold": got["added_vs_current"], "removed_outside_gold": got["removed_vs_current"],
                          "texts_with_tag_current": int(b.sum()), "texts_with_tag_candidate": int(a.sum())}
    other = [j for j, t in enumerate(TAGS) if t not in changed]
    check("tags that keep their method are identical to the current tags", bool((M[:, other] == C_all[:, other]).all()))
    texts_changed = np.flatnonzero((M != C_all).any(1))
    changed_text_set = {distinct[i] for i in texts_changed}

    # ------------------------------------------------------------ gold
    gi = [idx[t] for t in gold_texts]
    cur_sets, mix_sets = W4.sets_of(C_all[gi]), W4.sets_of(M[gi])
    rx_sets = [[x for x in s if x in TAGS] for s in recipes.tag_facts(gold_texts)]
    g_cur = WS.score_tags(gold, cur_sets, tags=TAGS)
    g_mix = WS.score_tags(gold, mix_sets, tags=TAGS)
    g_rx = WS.score_tags(gold, rx_sets, tags=TAGS)
    check("current gold macro F1 equals scores.json", abs(g_cur["summary"]["macro"]["f1"] - sc["current"]["gold"]["macro_f1"]) < 1e-9,
          g_cur["summary"]["macro"]["f1"])
    d_mix_cur = W4.paired_delta(gold, mix_sets, cur_sets)
    d_mix_rx = W4.paired_delta(gold, mix_sets, rx_sets)
    d_cur_rx = W4.paired_delta(gold, cur_sets, rx_sets)
    for t in changed:
        c = per_tag[t]["chosen_candidate"]
        check(f"{t}: mix F1 and paired interval equal the candidate's in scores.json",
              abs(g_mix["per_tag"][t]["f1"] - sc["per_tag"][t]["candidates"][c]["gold"]["f1"]) < 1e-9
              and d_mix_cur["per_tag_delta_ci95"][t] == sc["per_tag"][t]["candidates"][c]["paired_delta_vs_current"]["ci95"],
              f"F1 {g_mix['per_tag'][t]['f1']}, CI {d_mix_cur['per_tag_delta_ci95'][t]}")

    def gsum(g):
        s = g["summary"]
        return {"macro_precision": s["macro"]["precision"], "macro_recall": s["macro"]["recall"],
                "macro_f1": s["macro"]["f1"], "macro_f1_ci95": s["macro"]["f1_ci95"],
                "micro_precision": s["micro"]["precision"], "micro_recall": s["micro"]["recall"],
                "micro_f1": s["micro"]["f1"], "micro_f1_ci95": s["micro"]["f1_ci95"],
                "exact_match": s["exact_match_evidence_tags"], "rows": s["rows"], "tags": s["tags"]}

    def gtag(g, t):
        x = g["per_tag"][t]
        return {k: x[k] for k in ("f1", "f1_ci95", "precision", "recall", "support", "predicted", "tp", "fp", "fn")}

    gold_out = {
        "current": gsum(g_cur), "candidate_mix": gsum(g_mix), "regex_v0.2": gsum(g_rx),
        "paired_macro_f1_delta_mix_minus_current_ci95": d_mix_cur["macro_f1_delta_ci95"],
        "paired_macro_f1_delta_mix_minus_regex_ci95": d_mix_rx["macro_f1_delta_ci95"],
        "paired_macro_f1_delta_current_minus_regex_ci95": d_cur_rx["macro_f1_delta_ci95"],
        "per_tag": {t: {"current": gtag(g_cur, t), "candidate_mix": gtag(g_mix, t),
                        "mix_minus_current_ci95": d_mix_cur["per_tag_delta_ci95"][t],
                        "method_in_mix": per_tag[t]["method_in_mix"]} for t in TAGS},
        "gold_rows_whose_tags_change": int(sum(set(a) != set(b) for a, b in zip(cur_sets, mix_sets))),
    }
    print(f"  gold macro F1: current {g_cur['summary']['macro']['f1']:.4f}, candidate mix "
          f"{g_mix['summary']['macro']['f1']:.4f}; paired {d_mix_cur['macro_f1_delta_ci95']}")

    # ------------------------------------------------------------ re-score every candidate's gold gate
    # Recompute every paired interval from the saved candidate tags with the declared resampling, and check that
    # gate 1 does not depend on scores.json's 4-decimal rounding.
    t_s = time.time()
    regate, flips, mismatch = {}, [], []
    for c in sc["candidates"]:
        per, mac = boot_arrays(gold, W4.sets_of(cand(c)[gi]), cur_sets, WS.N_BOOT, WS.BOOT_SEED + 1)
        if ci4(mac) != sc["candidates"][c]["gold_macro_f1_paired_delta_vs_current_ci95"]:
            mismatch.append(f"{c} macro")
        regate[c] = {}
        for j, t in enumerate(TAGS):
            lo_full = float(np.percentile(per[:, j], 2.5))
            stored = sc["per_tag"][t]["candidates"][c]["paired_delta_vs_current"]["ci95"]
            if ci4(per[:, j]) != stored:
                mismatch.append(f"{c} {t}")
            if (lo_full > 0) != (stored[0] > 0):
                flips.append(f"{c} {t}")
            regate[c][t] = {"ci95": ci4(per[:, j]), "lower_bound_full_precision": lo_full, "passes": lo_full > 0}
    n_iv = len(sc["candidates"]) * (len(TAGS) + 1)
    check("every paired interval recomputed from the saved candidate tags equals scores.json",
          not mismatch, f"{n_iv - len(mismatch)} of {n_iv} equal (per tag and macro)"
          + (f"; differ: {mismatch}" if mismatch else ""))
    check("gate 1 is the same on full-precision lower bounds as on scores.json's 4-decimal ones", not flips,
          f"flips: {flips}" if flips else "0 flips")
    lb0 = [(c, t) for c in sc["candidates"] for t in TAGS
           if sc["per_tag"][t]["candidates"][c]["paired_delta_vs_current"]["ci95"][0] == 0]
    lb00 = [(c, t) for c, t in lb0 if sc["per_tag"][t]["candidates"][c]["paired_delta_vs_current"]["ci95"][1] == 0]
    regate_summary = {
        "rule": "gate 1 = lower bound of the declared paired interval > 0 (strict)",
        "passing": [f"{c}|{t}" for c in regate for t in TAGS if regate[c][t]["passes"]],
        "lower_bound_exactly_0": [f"{c}|{t}" for c, t in lb0],
        "of_which_interval_is_0_to_0": [f"{c}|{t}" for c, t in lb00],
        "lower_bound_exactly_0_full_precision": sum(regate[c][t]["lower_bound_full_precision"] == 0 for c, t in lb0),
        "per_candidate": regate,
    }

    # ------------------------------------------------------------ how firm the passing gold gates are
    gate_sens = {}
    mix_macro_sens = None
    for x in sc["passing_paired_gate"]:
        c, t = x["candidate"], x["tag"]
        j = TAGS.index(t)
        A = C_all[gi].copy(); A[:, j] = cand(c)[gi][:, j]   # per-tag F1 depends only on the tag's own column
        a_sets = W4.sets_of(A)
        gate_sens[f"{c}|{t}"] = sensitivity(gold, a_sets, cur_sets, j)
        check(f"{c} {t}: sensitivity rerun reproduces the declared interval",
              gate_sens[f"{c}|{t}"]["declared"]["ci95"] == sc["per_tag"][t]["candidates"][c]["paired_delta_vs_current"]["ci95"],
              gate_sens[f"{c}|{t}"]["declared"]["ci95"])
        if changed == [t] and per_tag[t]["chosen_candidate"] == c:
            mix_macro_sens = sensitivity(gold, a_sets, cur_sets, None)   # this swap is the candidate mix itself
    if changed and mix_macro_sens is None:
        mix_macro_sens = sensitivity(gold, mix_sets, cur_sets, None)
    if mix_macro_sens is not None:
        check("macro sensitivity reproduces the declared mix-minus-current interval",
              mix_macro_sens["declared"]["ci95"] == d_mix_cur["macro_f1_delta_ci95"], mix_macro_sens["declared"]["ci95"])
    seed_prov = seed_provenance()
    check("the gate's seed and resample count were committed before D050, unchanged since",
          seed_prov["seed_and_n_boot_before_d050"] and seed_prov["bootstrap_files_uncommitted_changes"] is False,
          f"seed {seed_prov['seed_line_first_commit']}, D050 {seed_prov['d050_first_commit']}")
    sens_secs = time.time() - t_s
    for k, v in gate_sens.items():
        e_, o_ = v["declared_seed_extended"], v["other_seeds"]
        print(f"  gate sensitivity {k}: declared {v['declared']['ci95']}; {e_['resamples']:,} resamples {e_['ci95']} "
              f"P(<=0) {e_['p_delta_le_0']}; other seeds lower bound {o_['lower_bound_min']} to {o_['lower_bound_max']}, "
              f"above 0 in {o_['runs_with_lower_bound_above_0']} of {o_['runs']}")
    print(f"  sensitivity and re-score: {sens_secs:.0f}s")

    # ------------------------------------------------------------ gold separation audits
    prompt_audit = prompt_gold_audit(gold)
    check("teacher prompt snapshot is the frozen p2 prompt", prompt_audit["sha256_equals_snapshot_record"],
          prompt_audit["sha256"][:16])
    check("every labeled prompt example appears verbatim in the prompt snapshot",
          prompt_audit["labeled_examples_verbatim_in_prompt"] == prompt_audit["labeled_examples"],
          f"{prompt_audit['labeled_examples_verbatim_in_prompt']} of {prompt_audit['labeled_examples']}")
    merged_full = pd.read_parquet(MERGED, columns=["text_norm", "tags", "source"])
    val_texts = set(S.wp4_val_texts())
    draw_texts = {"population draws": [t for b in blind.values() for t in b["text"]]}
    dup_audit = near_dup_audit(gold, merged_full, val_texts, draw_texts, sc["labels"]["chosen_teacher"], changed,
                               C_all[gi], M[gi])
    check("declared near-duplicate rule: 0 merged-label and draw texts equal a gold text",
          all(dup_audit["keys"][k]["merged_texts"] == 0 and not any(dup_audit["keys"][k]["draw_texts"].values())
              for k in ("exact", "near_key")),
          {k: dup_audit["keys"][k]["merged_texts"] for k in ("exact", "near_key")})
    val_missing = sorted(set(S.wp4_val_texts()) - set(merged_full["text_norm"]))
    dropped_near_gold = {x["text_norm"] for x in json.loads(MERGED.with_name(MERGED.name + ".meta.json").read_text())
                         ["gold_near_duplicates_dropped"]["texts"]}
    check("the WP4 validation texts missing from the merged labels are near-gold texts dropped at the merge",
          len(val_missing) == sc["split"]["wp4_validation_texts_missing_from_merged"]
          and set(val_missing) <= dropped_near_gold, f"{len(val_missing)} missing")
    val_note = {"wp4_validation_texts": sc["split"]["wp4_validation_texts"], "used": sc["split"]["validation"],
                "missing_near_gold_texts_dropped_at_merge": val_missing,
                "merged_split_column_not_used": sc["split"]["merged_split_column_not_used"]}
    print(f"  gold audits: prompt {prompt_audit['equal_after_normalisation']} equal + "
          f"{prompt_audit['contain_a_gold_text']} containing a gold text; merged labels "
          + ", ".join(f"{k} {v['merged_texts']}" for k, v in dup_audit["keys"].items()))

    # ------------------------------------------------------------ what the files show about the reads
    provenance = read_provenance(reads, json.loads(PILOT.read_text()), prior, run_at)
    for f, v in {**provenance["pilot_reads"], **provenance["population_reads"]}.items():
        check(f"{f}: unchanged since unblinding and written before it",
              v["unchanged_since_unblinding"] and v["written_before_unblinding"],
              f"sha {v['sha256'][:12]}, modified {v['modified_utc']}")

    # ------------------------------------------------------------ grades
    fac, facts = RB.load_inputs()
    t_g = time.time()
    tags_cur = facts["text_norm"].map(lambda x: [TAGS[j] for j in np.flatnonzero(C_all[idx[x]])])
    tags_mix = facts["text_norm"].map(lambda x: [TAGS[j] for j in np.flatnonzero(M[idx[x]])])
    fact_changed = facts["text_norm"].isin(changed_text_set).to_numpy()
    aff = set(facts.loc[fact_changed, "facility_id"])
    per_cur = RB.facts_by_facility(facts, tags_cur)
    per_mix = RB.facts_by_facility(facts, tags_mix)
    fac_aff = fac[fac["facility_id"].isin(aff)]
    g02c = grade_frame(RB.build_grades(fac, per_cur, "v0.2"))
    g03c = grade_frame(V3.build(fac, per_cur, recipes.RULE_VERSION_V03, "student"))
    g02m = spliced(g02c, grade_frame(RB.build_grades(fac_aff, per_mix, "v0.2")) if len(fac_aff) else g02c.iloc[:0])
    g03m = spliced(g03c, grade_frame(V3.build(fac_aff, per_mix, recipes.RULE_VERSION_V03, "student"))
                   if len(fac_aff) else g03c.iloc[:0])
    grade_secs = time.time() - t_g
    # the rebuilt current grades must equal the grade files on disk
    for nm, path, g in (("v0.2", GRADES_V02, g02c), ("v0.3", GRADES_V03, g03c)):
        if path.exists():
            disk = pd.read_parquet(path, columns=["facility_id", "service_line", "grade"])
            mm = g.merge(disk, on=["facility_id", "service_line"], how="outer", suffixes=("", "_disk"), indicator=True)
            diff = int((mm["_merge"] != "both").sum() + (mm["grade"] != mm["grade_disk"]).sum())
            check(f"current {nm} grades rebuilt from gold/fact_tags.parquet equal {rel(path)}", diff == 0,
                  f"{diff} differing cells of {len(g):,}")
    imp02 = grade_impact(g02c, g02m)
    imp03 = grade_impact(g03c, g03m)
    gate = {line: {v: {"grade_ge2_current": d[line]["grade_ge2_current"],
                       "grade_ge2_candidate": d[line]["grade_ge2_candidate"],
                       "up": d[line]["cross_gate_up"], "down": d[line]["cross_gate_down"]}
                   for v, d in (("v0.2", imp02), ("v0.3", imp03))} for line in GATE_LINES}
    print(f"  grades ({grade_secs:.0f}s): {len(aff):,} facilities have a changed fact; "
          + "; ".join(f"{l} v0.3 gate +{gate[l]['v0.3']['up']}/-{gate[l]['v0.3']['down']}" for l in GATE_LINES))

    # examples per moved line (v0.3): the changed facts behind each move
    fx = facts.assign(tags_cur=tags_cur.values, tags_mix=tags_mix.values)
    fx = fx[fact_changed]
    by_fac = {f: d for f, d in fx.groupby("facility_id")}
    examples = {}
    rng = np.random.default_rng(20261125)
    for line, d in imp03.items():
        if not d["facilities_changed"]:
            continue
        msk = (g03c["service_line"] == line) & (g03c["grade"] != g03m)
        sub = g03c[msk].assign(grade_mix=g03m[msk])
        ex = {}
        for (a, b), grp in sub.groupby(["grade", "grade_mix"]):
            pick = grp.iloc[np.sort(rng.choice(len(grp), size=min(4, len(grp)), replace=False))]
            ex[f"{a}->{b}"] = [{"facility_id": r.facility_id, "facts_changed": [
                {"text": x, "current": [t for t in tc if t in changed], "candidate": [t for t in tm if t in changed]}
                for x, tc, tm in zip(by_fac[r.facility_id]["text"], by_fac[r.facility_id]["tags_cur"],
                                     by_fac[r.facility_id]["tags_mix"])][:3]} for r in pick.itertuples()]
        examples[line] = ex

    # ------------------------------------------------------------ the candidate tag file's rows
    ft = pq.read_table(FACT_TAGS).to_pandas()
    if not all(set(x) <= set(TAGS) for x in ft["tags"]):
        raise SystemExit("gold/fact_tags.parquet holds non-evidence tags; the candidate would drop them")
    f_text = dict(zip(facts["fact_id"], facts["text_norm"]))
    new_tags = [[TAGS[j] for j in np.flatnonzero(M[idx[f_text[f]]])] for f in ft["fact_id"]]
    differ = sum(list(a) != b for a, b in zip(ft["tags"], new_tags))
    if differ != int(fact_changed.sum()):
        raise SystemExit(f"{differ} rows differ from gold/fact_tags.parquet, expected {int(fact_changed.sum())}")
    # the previous run, for the record (not a gate: a new read would legitimately change the result)
    previous = None
    if prior is not None:
        same_rows = None
        if OUT_TAGS.exists():
            old = pq.read_table(OUT_TAGS, columns=["fact_id", "tags"]).to_pandas()
            same_rows = bool(len(old) == len(ft) and (old["fact_id"].to_numpy() == ft["fact_id"].to_numpy()).all()
                             and all(list(a) == b for a, b in zip(old["tags"], new_tags)))
        previous = {
            "previous_decide_run_utc": prior.get("created_at_utc"), "previous_version": prior.get("version"),
            "tags_changing_method_previous": prior.get("summary", {}).get("tags_changing_method"),
            "same_decision_per_tag": all(prior.get("per_tag", {}).get(t, {}).get("chosen_candidate")
                                         == per_tag[t]["chosen_candidate"] for t in TAGS),
            "same_model_version": prior.get("model_version") == model_version,
            "same_candidate_tags_in_every_row": same_rows,
            "previous_candidate_file_sha256": prior_tags_sha,
        }
        # keep a pointer to the run the review read (wp4b-decide-1) across reruns of this version
        if prior.get("version") == "wp4b-decide-1":
            previous["reviewed_run"] = {k: v for k, v in previous.items()}
        else:
            pp = prior.get("previous_run") or {}
            previous["reviewed_run"] = pp.get("reviewed_run") or (
                {k: v for k, v in pp.items() if k != "reviewed_run"} if pp.get("previous_version") == "wp4b-decide-1"
                else None)
        print(f"  vs the previous run ({previous['previous_decide_run_utc']}): same decision per tag "
              f"{previous['same_decision_per_tag']}, same candidate tags {same_rows}")

    # ------------------------------------------------------------ spend, pilot, teacher scale
    spend = ledger_summary()
    pilot = json.loads(PILOT.read_text())
    strong = json.loads(STRONG_META.read_text())
    mm = json.loads(MERGED.with_name(MERGED.name + ".meta.json").read_text())

    # ------------------------------------------------------------ assemble
    base = {"created_at_utc": now(), "script": SCRIPT, "version": VERSION, **git_info(),
            "decision_ref": "D050 (protocol and ship rule, declared before any result)"}
    inputs = {
        "labels/wp4b/scores.json": {"sha256": RB.sha256(SCORES), "created_at_utc": sc["created_at_utc"]},
        "labels/wp4b/pop_key.csv": {"sha256": key_sha},
        **{f"labels/wp4b/{b}": {"sha256": s} for b, s in blind_sha.items()},
        **{r["read_file"]: {"sha256": r["read_sha256"], "rows": r["n"]} for r in reads.values()},
        "gold/fact_tags.parquet": {"sha256": ft_sha, "model_version": sc["current_method"]["model_version"]},
        "labels/wp4b/merged_labels.parquet": {"sha256": merged_sha},
        "labels/gold_facts.csv": {"sha256": gold_sha, "use": "scoring only"},
        **{f"{rel(S.CANDIDATES[c]['dir'])}/scores.parquet": {"sha256": s} for c, s in cand_sha.items()},
        "silver/facts.parquet": {"sha256": RB.sha256(paths.silver_dir() / "facts.parquet")},
        "silver/facilities.parquet": {"sha256": RB.sha256(paths.silver_dir() / "facilities.parquet")},
        rel(PROMPT_SNAPSHOT): {"sha256": RB.sha256(PROMPT_SNAPSHOT), "use": "gold-use audit only"},
        **{f"labels/wp4b/{n}": {"sha256": RB.sha256(WP4B / n), "use": "provenance check only"}
           for n in PILOT_READS if (WP4B / n).exists()},
    }
    rule = sc["ship_rule_d050"]
    firm = {t: firmness_text(t, per_tag[t]["chosen_candidate"], gate_sens[f"{per_tag[t]['chosen_candidate']}|{t}"],
                             mix_macro_sens, reads[(per_tag[t]["chosen_candidate"], t)])
            for t in changed if f"{per_tag[t]['chosen_candidate']}|{t}" in gate_sens}
    pa = prompt_audit
    p_equal = [h for h in pa["hits"] if h["match"] != "prompt text contains the gold text (2+ words)"]
    p_contain = [h for h in pa["hits"] if h["match"] == "prompt text contains the gold text (2+ words)"]
    p_tags = pa["gold_tags_involved"]
    p_touch_changed = sorted({t for h in pa["hits"] for t in (set(h["gold_tags"]) | set(h["prompt_tags"] or []))
                              if t in changed})
    p_touch_gate1 = sorted({x["tag"] for x in sc["passing_paired_gate"]}
                           & {t for h in pa["hits"] for t in (set(h["gold_tags"]) | set(h["prompt_tags"] or []))})
    def short(s_: str, n: int = 60) -> str:
        """A quoted text, shortened; an en or em dash inside it is shown as a hyphen (house style, no dashes)."""
        s_ = s_.replace("\u2013", "-").replace("\u2014", "-")
        return s_ if len(s_) <= n else s_[:n - 3].rstrip() + "..."
    prompt_text = (
        f"Teacher prompt: gold texts are in it. The frozen WP3 p2 prompt (sha256 {pa['sha256'][:8]}), reused unchanged "
        f"as D050 required, has {len(p_equal)} labeled example{'s' if len(p_equal) != 1 else ''} equal to a gold text "
        "after normalization (" + "; ".join(f"'{short(h['prompt_text'])}' -> {h['prompt_tags']}, gold "
                                             f"'{short(h['gold_text'])}'" for h in p_equal)
        + f") and {len(p_contain)} that contain a gold text (" + "; ".join(
            f"'{short(h['prompt_text'])}' contains gold '{h['gold_text']}'" for h in p_contain) + "). "
        f"Their gold tags are {', '.join(f'`{t}`' for t in p_tags)}"
        + (f"; none involves {', '.join(f'`{t}`' for t in changed)}, the only tag{'s' if len(changed) > 1 else ''} "
           "that change" + ("s" if len(changed) == 1 else "") + "." if not p_touch_changed else
           f"; {', '.join(p_touch_changed)} also changes method, so its gold score may be lifted by this.")
        + (f" `{'`, `'.join(p_touch_gate1)}` passed the gold gate but not the hand read, so no decision rests on it."
           if p_touch_gate1 and not (set(p_touch_gate1) & set(changed)) else "")
        + f" {pa['one_word_gold_texts_inside_prompt_facts']['prompt_facts']} more prompt facts (labeled examples or "
          "facts quoted in the rules) contain a one-word gold text "
          f"({', '.join(pa['one_word_gold_texts_inside_prompt_facts']['gold_texts'])}); a bare tag word is not counted "
          "as a copy. "
          "The earlier statement that no gold text is in any prompt was wrong.")
    dk = dup_audit["keys"]
    nc = {t: v for t, v in dup_audit["near_copies_of_changed_rows"].items()}
    near_bits = []
    for t, v in nc.items():
        near = [r for r in v["rows"] if r["fixed_by_mix"] and r["best_ratio"] >= NEAR_COPY_RATIO]
        if near:
            ex_ = min(near, key=lambda r: len(r["gold_text"]))
            near_bits.append(
                f"{v['fixed_with_a_near_copy_in_training']} of the {v['fixed']} gold rows the mix fixes on `{t}` have "
                f"a {sc['labels']['chosen_teacher']}-labeled training text with a difflib ratio of "
                f"{v['near_copy_ratio_range'][0]:.2f} to {v['near_copy_ratio_range'][1]:.2f} (templated text, e.g. "
                f"'{short(ex_['best_teacher_text'], 70)}' against gold '{short(ex_['gold_text'], 70)}'), and the "
                f"teacher's label on that text equals the gold label in {v['near_copies_where_teacher_label_equals_gold']} "
                f"of {v['fixed_with_a_near_copy_in_training']}. Part of the gold gain may come from near copies rather "
                "than from generalising, one more reason to rest the change on the population read.")
    ad = dk["letters_and_digits_only"]; ws_ = dk["same_word_set"]
    ad_ex = next((x for x in ad["items"] if x["source"] == sc["labels"]["chosen_teacher"]), ad["items"][0] if ad["items"] else None)
    dup_text = (
        f"Gold separation under looser keys: under the declared near-duplicate rule, 0 of the {len(merged_full):,} "
        f"merged-label texts and 0 of the {len(draw_texts['population draws'])} population draws equal a gold text. "
        f"Looser keys find some: {ad['merged_texts']} merged texts equal a gold text on letters and digits only "
        f"({ad['in_training']} in training"
        + (f"; e.g. '{short(ad_ex['text'])}' ({ad_ex['source']}) against gold '{short(ad_ex['gold_text'])}'"
           if ad_ex else "")
        + f"), and {ws_['merged_texts']} have the same set of words as a gold text ({ws_['in_training']} in training). "
        + ("None of these gold texts is among" if dup_audit["loose_match_gold_texts_among_changed_rows"] == 0 else
           f"{dup_audit['loose_match_gold_texts_among_changed_rows']} of these gold texts are among")
        + f" the {dup_audit['gold_rows_changed_by_mix']} gold rows the mix changes. "
        + ("No population draw matches under either looser key. "
           if not any(dk[k]["draw_texts"]["population draws"] for k in ("letters_and_digits_only", "same_word_set"))
           else "Population draws matching: " + ", ".join(
               f"{dk[k]['draw_texts']['population draws']} on {MATCH_KEY_NOTE[k].split(' (')[0]}"
               for k in ("letters_and_digits_only", "same_word_set")) + ". ")
        + " ".join(near_bits))
    pr = provenance
    pil = list(pr["pilot_reads"].values()); pop_ = list(pr["population_reads"].values())
    hm = lambda s_: (s_ or "")[11:16]  # noqa: E731
    prov_text = (
        "Reader independence: D050 asks for a reader that did not build the candidate. No sidecar names the reader, "
        "so the files cannot show who read; the workflow assigned the reads to separate reader agents. The blind files "
        "hold only item id, text and tag, in shuffled order, but their names carry the candidate id and tag "
        + (f"(e.g. `{Path(next(iter(reads.values()))['blind_file']).name}`)" if reads else "")
        + ", and the key (`pop_key.csv`, which holds both taggers' calls) sits in the same folder. What the files do "
        "show: every read file was written once, before its unblinding, and is unchanged since (sha256 equal): pilot "
        f"reads at {' and '.join(sorted(hm(v['modified_utc']) for v in pil))} UTC against unblinding at "
        f"{hm(pil[0]['unblinded_at_utc']) if pil else ''}; population reads at "
        f"{' and '.join(sorted(hm(v['modified_utc']) for v in pop_))} against the first decide run at "
        f"{hm(pop_[0]['first_unblinded_at_utc']) if pop_ else ''}.")
    val_text = (
        f"Validation texts: WP4's {val_note['wp4_validation_texts']:,} validation texts minus "
        f"{len(val_note['missing_near_gold_texts_dropped_at_merge'])} near-gold texts dropped at the merge ("
        + "; ".join(f"'{short(t, 50)}'" for t in val_note["missing_near_gold_texts_dropped_at_merge"])
        + f"), so {val_note['used']:,}. merged_labels.parquet's own split column marks "
          f"{val_note['merged_split_column_not_used']:,} further texts as validation; the students trained on them, and "
          "the column is not used anywhere in WP4b scoring.")
    disclosures = [
        *firm.values(),
        "Gold decides the first gate per tag (as in WP4), so the candidate mix's gold F1 is slightly optimistic. "
        f"{len(sc['candidates'])} candidates x {len(TAGS)} tags = {len(sc['candidates']) * len(TAGS)} paired tests were run. "
        f"At a 2.5% one-sided rate, up to about {round(0.025 * len(sc['candidates']) * len(TAGS))} passes could come from "
        "chance alone if no candidate were better and the tests were independent (many are neither: several tags have "
        f"no gold error to fix). {len(sc['passing_paired_gate'])} passed. The hand read, which does not use gold, is what "
        f"tells a real gain from a lucky one: it passed {sum(r['passes'] for r in reads.values())} and failed "
        f"{sum(not r['passes'] for r in reads.values())}.",
        "Gold labels and hand reads are agent-made, not clinicians' readings. The 777 gold texts were labeled by four "
        "agent labelers who split on specialty names in lists.",
        prov_text,
        prompt_text,
        dup_text,
        val_text,
        "The hand-read draw does not exclude texts the candidate trained on (D050 does not ask it to). Results split by "
        "whether a drawn text is in the merged teacher labels are in decision.json.",
        "Validation F1 (the tie-break) measures agreement with teacher labels, on thresholds tuned on the same texts; "
        "it was not needed this run because no tag had two passing candidates.",
        "Student 2b trains on the Apple MPS GPU, which is not bit-for-bit deterministic. The candidate mix uses the "
        "saved candidate tags (sha256 in the sidecar); a retrain can differ on a few texts.",
        "25 texts per (candidate, tag) is a small read: 23 of 25 has a Wilson 95% interval of about 75% to 98%.",
        "The gold set over-draws regex hits and near misses. Most of a candidate's changes fall on texts the gold set "
        "does not resemble; the population read is the only check on those.",
    ]
    decision = {
        **base, "stage": "WP4b decide",
        "rule": rule, "hand_read_threshold": f"at least {MIN_FAVOUR} of {N_READ} in the candidate's favour",
        "favour_definition": "a drawn text favours the candidate when the reader's present (0 or 1) equals the "
                             "candidate's call; every drawn text is a disagreement, so it favours exactly one side",
        "gold_use": (
            "scoring and the per-tag gold gate only (the gate reads scores.json), with one inherited exception. Under the "
            "declared near-duplicate rule no gold text is in any training or validation label, pilot pool, population "
            "draw, threshold or teacher choice (looser near copies remain in training: gold_use_audit.near_duplicates). "
            f"Exception: the frozen WP3 p2 teacher prompt, reused unchanged as D050 required, holds {len(p_equal)} gold "
            f"text equal after normalization and {len(p_contain)} as substrings, on {', '.join(p_tags)}; "
            + (f"none on {', '.join(changed)}." if not p_touch_changed else f"including {', '.join(p_touch_changed)}.")),
        "gold_use_previous_wording": "scoring only: the paired gate reads scores.json; no gold text is in any prompt, "
                                     "training set, pilot pool, threshold or teacher choice (version wp4b-decide-1; "
                                     "wrong about the prompt)",
        "gold_use_audit": {"teacher_prompt": prompt_audit, "near_duplicates": dup_audit},
        "gold_gate_sensitivity": {
            "what": "disclosure only: how firm each passing gold gate is. The gate itself is the declared interval in "
                    "scores.json (seed BOOT_SEED + 1 = %d, %s stratified resamples, set in scripts/wp4_student.py before "
                    "D050); it is not rerun with another seed and the decision does not read this block."
                    % (WS.BOOT_SEED + 1, f"{WS.N_BOOT:,}"),
            "seed_provenance": seed_prov,
            "per_candidate_tag": gate_sens, "candidate_mix_macro": mix_macro_sens, "seconds": round(sens_secs, 1)},
        "gate1_rescore": regate_summary,
        "validation_texts": val_note,
        "read_provenance": provenance,
        "previous_run": previous,
        "future_runs": FUTURE_RUNS,
        "revision": ("wp4b-decide-2 (review fixes): the same ship rule on the same inputs; adds the gold-gate "
                     "sensitivity, the gold-use audit, read provenance, a full re-score of every paired interval, and "
                     "corrected wording (gold_use, validation count, intervals at 0)"),
        "inputs": inputs,
        "current_method": {"model_version": sc["current_method"]["model_version"],
                           "tags_from_student": sc["current_method"]["tags_from_student"],
                           "tags_from_regex": sc["current_method"]["tags_from_regex"]},
        "reads": {f"{c}|{t}": r for (c, t), r in reads.items()},
        "per_tag": per_tag,
        "summary": {
            "tags_changing_method": {t: per_tag[t]["chosen_candidate"] for t in changed},
            "passed_gold_gate": [{"candidate": x["candidate"], "tag": x["tag"]} for x in sc["passing_paired_gate"]],
            "failed_hand_read": [{"candidate": c, "tag": t, "favours_candidate": reads[(c, t)]["favours_candidate"],
                                  "n": reads[(c, t)]["n"]} for c, t in failed_read],
            "no_tag_passes": not changed,
            "candidates_as_whole_taggers": {c: {"gold_macro_f1": v["gold"]["macro_f1"],
                                                "paired_macro_delta_vs_current_ci95": v["gold_macro_f1_paired_delta_vs_current_ci95"],
                                                "validation_macro_f1": v["validation_vs_merged_labels"]["macro_f1"]}
                                            for c, v in sc["candidates"].items()},
        },
        "model_version": model_version, "tags_by_method": tags_by_method,
        "candidate_file": rel(OUT_TAGS),
        "promotion": "not done here (D050 freeze): gold/fact_tags.parquet and gold/capability_grades*.parquet are "
                     "untouched; promotion, if any, happens in the integration pass before the 04:00 bundle freeze",
        "disclosures": disclosures, "checks": checks,
    }
    impact = {
        **base, "stage": "WP4b decide: impact of the candidate mix",
        "model_version": {"current": sc["current_method"]["model_version"], "candidate": model_version},
        "gold": {**gold_out, "paired_macro_f1_delta_mix_minus_current_sensitivity": mix_macro_sens},
        "fact_level": {"distinct_texts": len(distinct), "distinct_texts_whose_tags_change": int(len(texts_changed)),
                       "fact_rows_whose_tags_change": int(fact_changed.sum()), "fact_rows": int(len(facts)),
                       "facilities_with_a_changed_fact": len(aff), "per_changed_tag": pop_changes},
        "grades": {"v0.2": imp02, "v0.3": imp03,
                   "counts_order": "grade 0 none, 1 claimed, 2 supported, 3 corroborated",
                   "how": "current = the v0.2 and v0.3 grade code (wp3_regex_baseline.build_grades, "
                          "wp3_regrade_v03.build) on gold/fact_tags.parquet; candidate = the same code on the mix, "
                          "rerun for every facility with a changed fact", "seconds": round(grade_secs, 1)},
        "grade2_gate": {"lines": list(GATE_LINES), "per_line": gate,
                        "facility_lines_crossing_v0.3": sum(gate[l]["v0.3"]["up"] + gate[l]["v0.3"]["down"] for l in GATE_LINES),
                        "facility_lines_crossing_v0.2": sum(gate[l]["v0.2"]["up"] + gate[l]["v0.2"]["down"] for l in GATE_LINES),
                        "note": "D048 gates the ranker on grade 2+ under recipe v0.3; v0.2 shown too"},
        "examples_v0.3": examples,
        "spend": spend,
        "checks": checks,
    }
    pc_path = DATA / "models" / "wp4_student" / "population_check.json"
    ctx = {"sc": sc, "pilot": pilot, "strong": strong, "merged_meta": mm, "decision": decision, "impact": impact,
           "firm": firm,
           "wp4_population_check": json.loads(pc_path.read_text()).get("tags", {}) if pc_path.exists() else {}}
    md = no_dash(render_md(ctx))

    failed = [c for c in checks if not c["passed"]]
    print(f"  checks: {len(checks) - len(failed)} of {len(checks)} pass")
    print(f"  model_version {model_version}")
    print(f"  spend: project ${spend['project_total_usd']:.4f}; WP4b ${spend['ledgers']['WP4b (this run)']['usd']:.4f} "
          f"of ${RUN_CAP_USD:.0f}; this stage $0.00")
    if failed:
        print("  a check failed; nothing written")
        return 1
    if args.dry_run:
        print("  --dry-run: nothing written")
        return 0

    # ------------------------------------------------------------ write
    out = pd.DataFrame({"fact_id": ft["fact_id"], "facility_id": ft["facility_id"], "tags": new_tags,
                        "method": "student", "model_version": model_version})
    RB.write_table(out, RB.FACT_TAGS_SCHEMA, OUT_TAGS, {
        "output": rel(OUT_TAGS), **base, "recipe_version": recipes.RECIPE_VERSION,
        "status": "candidate only (D050 freeze): not promoted; gold/fact_tags.parquet is unchanged",
        "method": "student", "model_version": model_version, "tags_by_method": tags_by_method,
        "methods": {"student:s1-1b": "data/models/wp4_student/student_1b.pkl (WP4, Haiku labels)",
                    f"regex:{recipes.RECIPE_VERSION}": "vf.recipes.tag_fact (recipe v0.2 regexes)",
                    **{f"student:wp4b-{c}": f"{rel(S.CANDIDATES[c]['dir'])}/scores.parquet ({CANDIDATE_LABEL[c]}, "
                                            "merged Opus + Haiku labels)" for c in cand_sha}},
        "built_from": {"current tags": "gold/fact_tags.parquet (per distinct text_norm)",
                       "replaced tags": {t: per_tag[t]["chosen_candidate"] for t in changed}},
        "rows_whose_tags_differ_from_gold_fact_tags": differ,
        "facts_with_any_tag": int(out["tags"].map(len).gt(0).sum()),
        "decision": rel(DECISION), "impact": rel(IMPACT),
        "inputs": {k: v for k, v in inputs.items() if k.endswith(".parquet")},
    })
    for path, obj, what in ((DECISION, decision, "decision"), (IMPACT, impact, "impact")):
        write_json(path, obj)
        write_json(path.with_name(path.name + ".meta.json"), {
            "output": rel(path), **base, "what": what, "sha256": RB.sha256(path),
            "inputs": inputs, "candidate_file": rel(OUT_TAGS)})
    RB.write_atomic(REPORT, lambda p: p.write_text(md, encoding="utf-8"))
    print(f"  wrote {rel(OUT_TAGS)} ({len(out):,} rows), {rel(DECISION)}, {rel(IMPACT)}, "
          f"reports/{REPORT.name}  [{time.time() - t0:.0f}s]")
    return 0


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------

def render_md(ctx: dict) -> str:
    sc, pilot, strong, mm = ctx["sc"], ctx["pilot"], ctx["strong"], ctx["merged_meta"]
    dec, imp = ctx["decision"], ctx["impact"]
    pt = dec["per_tag"]
    changed = list(dec["summary"]["tags_changing_method"])
    reads = dec["reads"]
    g = imp["gold"]
    sp = imp["spend"]
    cands = list(sc["candidates"])
    L = []
    w = L.append

    w("# Model A, WP4b: a stronger teacher and two new students")
    w("")
    w(f"Generated by `{SCRIPT}` on {dec['created_at_utc'][:16].replace('T', ' ')} UTC. Numbers come from "
      "`labels/wp4b/decision.json` and `labels/wp4b/impact.json` in the data folder, which read "
      "`labels/wp4b/scores.json` (`scripts/wp4b_students.py`) and `labels/wp4b/pilot_result.json` "
      "(`scripts/wp4b_teacher.py`). Protocol: D050, fixed before any result.")
    w("")
    w("## Result")
    w("")
    if changed:
        for t in changed:
            c = pt[t]["chosen_candidate"]; x = pt[t]["candidates"][c]; r = reads[f"{c}|{t}"]
            against = [it for it in r["items"] if it["favours"] == "current"]
            extra = ""
            if r["removals"]["n"] > r["additions"]["n"]:
                extra = (f" Most of its changes remove the tag: {r['removals']['reader_agrees_with_candidate']} of "
                         f"{r['removals']['n']} sampled removals were right"
                         + (" (specialty lists, antenatal or postnatal care, a dental 'delivery unit')."
                            if t == "ob_delivery" else "."))
            w(f"- **`{t}` changes method: {CANDIDATE_LABEL[c]} replaces {pt[t]['current_method']}.** It passed both "
              f"D050 gates. Gold F1 {x['gold_f1']:.2f} against {pt[t]['current_gold_f1']:.2f} (paired 95% interval of "
              f"the gain {ci_txt(x['paired_delta_ci95'])}), and the blinded hand read found {r['favours_candidate']} of "
              f"{r['n']} disagreements in its favour (it needed {r['needed']})"
              + (f"; the {len(against)} decided against it were both marked hard calls by the reader"
                 if len(against) == 2 and all(it["hard_call"] for it in against) else "")
              + "." + extra)
            s_ = dec["gold_gate_sensitivity"]["per_candidate_tag"].get(f"{c}|{t}")
            if s_:
                e_, o_ = s_["declared_seed_extended"], s_["other_seeds"]
                hc_ = r["hard_calls"]
                w(f"  - **The gold evidence for it is marginal.** The gate passes at the declared seed "
                  f"({ci_txt(s_['declared']['ci95'], 4)}), but run to {e_['resamples']:,} resamples the lower bound is "
                  f"{e_['ci95'][0]:+.4f} with P(gain <= 0) = {e_['p_delta_le_0']:.1%}, and at {o_['runs']} other seeds "
                  f"the lower bound was above 0 in {o_['runs_with_lower_bound_above_0']} of {o_['runs']}. The change rests "
                  f"mainly on the blinded read ({r['favours_candidate']} of {r['n']}, at least "
                  f"{hc_['favour_if_all_hard_calls_went_to_current']} if every hard call flipped). The decision stands "
                  "under D050, which fixes the declared interval (the gate was not rerun with a new seed); the "
                  "integration pass should weigh this before promoting. Details in \"How firm the gold gate is\".")
    for f_ in dec["summary"]["failed_hand_read"]:
        c, t = f_["candidate"], f_["tag"]; x = pt[t]["candidates"][c]; r = reads[f"{c}|{t}"]
        wrong_add = r["additions"]["n"] - r["additions"]["reader_agrees_with_candidate"]
        wrong_rem = r["removals"]["n"] - r["removals"]["reader_agrees_with_candidate"]
        w(f"- **`{t}` keeps {pt[t]['current_method']}.** {CANDIDATE_LABEL[c].capitalize()} passed the gold gate "
          f"(F1 {x['gold_f1']:.2f} against {pt[t]['current_gold_f1']:.2f}, paired interval {ci_txt(x['paired_delta_ci95'])}) "
          f"but the hand read found only {r['favours_candidate']} of {r['n']} in its favour (needs {r['needed']}): "
          f"{wrong_add} of its {r['additions']['n']} sampled additions do not state the tag"
          + ((", and its only sampled removal did state it" if wrong_rem == r["removals"]["n"] == 1 else
              f", and {wrong_rem} of its {r['removals']['n']} sampled removals did state it") if wrong_rem else "")
          + "." + (" The wrong additions are texts with a 'burn' or 'fire' word that are not burn care: healed-scar "
                   "care, sunburn, fire safety or a fire call, a 'BioFire' test panel, a bare 'burns', and 'burns' as a "
                   "department or programme list item." if t == "burns" else ""))
    others = [t for t in TAGS if t not in changed and t not in {f_["tag"] for f_ in dec["summary"]["failed_hand_read"]}]
    w(f"- **The other {len(others)} tags keep their current method**: no candidate's paired interval lies entirely above 0 on them.")
    if not changed:
        w("- **No tag passes both gates, so the current tagger stays as it is.** That is a valid result under D050; "
          "the candidate file equals the current tags.")
    wh = dec["summary"]["candidates_as_whole_taggers"]
    w("- **As whole taggers, all three candidates score below the shipped mix on gold.** Paired macro F1 "
      "intervals against the current tagger: " + "; ".join(
          f"{c} {ci_txt(v['paired_macro_delta_vs_current_ci95'])}" for c, v in wh.items())
      + ". Opus relabeled a pool built around the weak tags, and the retrained students agree better with those labels "
        "(validation below). On gold they lose more on other tags than they gain on the weak ones, so only a per-tag "
        "choice can use them.")
    mx, cu = g["candidate_mix"], g["current"]
    ms_ = g.get("paired_macro_f1_delta_mix_minus_current_sensitivity")
    w(f"- **Candidate mix on gold:** macro F1 {mx['macro_f1']:.3f} ({ci_txt(mx['macro_f1_ci95'], signed=False)}) against "
      f"{cu['macro_f1']:.3f} for the current tagger; paired difference {ci_txt(g['paired_macro_f1_delta_mix_minus_current_ci95'], 4)}"
      + (f" at the declared seed, {ci_txt(ms_['declared_seed_extended']['ci95'], 4)} at "
         f"{ms_['declared_seed_extended']['resamples']:,} resamples (P(gain <= 0) = "
         f"{ms_['declared_seed_extended']['p_delta_le_0']:.1%}), so the macro gain also barely clears 0" if ms_ else "")
      + f". {len(changed)} tag{'s' if len(changed) != 1 else ''} out of {len(TAGS)} move"
      + ("s, so the macro gain is that tag's gain divided by 24." if len(changed) == 1 else "."))
    gate = imp["grade2_gate"]
    w(f"- **Grade impact: no facility crosses the grade 2 gate** on {', '.join(GATE_LINES)}, under v0.3 "
      f"({gate['facility_lines_crossing_v0.3']} facility-lines) or v0.2 ({gate['facility_lines_crossing_v0.2']})."
      if gate["facility_lines_crossing_v0.3"] == 0 and gate["facility_lines_crossing_v0.2"] == 0 else
      f"- **Grade impact:** {gate['facility_lines_crossing_v0.3']} facility-lines cross the grade 2 gate under v0.3 "
      f"({gate['facility_lines_crossing_v0.2']} under v0.2) on {', '.join(GATE_LINES)}.")
    moved = {l: d for l, d in imp["grades"]["v0.3"].items() if d["facilities_changed"]}
    if moved:
        w("  " + " ".join(
            f"`{l}`: {d['facilities_changed']:,} facilities change grade ("
            + ", ".join(f"{k.replace('->', ' to ')}: {v:,}" for k, v in d["transitions"].items()) + ")."
            for l, d in moved.items())
          + (" `ob_delivery` only feeds the emoc claim (grade 1); grade 2 needs a caesarean fact, so these moves are "
             "between none and claimed." if "ob_delivery" in changed else ""))
    wl = sp["ledgers"]["WP4b (this run)"]
    w(f"- **Spend:** this stage made no LLM calls. WP4b spent ${wl['usd']:.2f} of its ${RUN_CAP_USD:.0f} cap; the "
      f"project total over the three ledgers is ${sp['project_total_usd']:.2f} of ${PROJECT_CAP_USD:.0f} "
      f"(read {sp['read_at_utc'][:16].replace('T', ' ')} UTC).")
    w("- **Nothing is promoted here.** The candidate tags are in `gold/fact_tags_candidate_wp4b.parquet`; "
      "`gold/fact_tags.parquet` and the grade files are untouched (D050 freeze). Promotion is the integration pass's call.")
    pa_ = dec["gold_use_audit"]["teacher_prompt"]
    w("- Caveat: " + ("the gold evidence for the change is marginal (above); " if changed else "")
      + "gold labels and the hand reads are agent-made, gold decides the first gate, the inherited teacher prompt holds "
      f"{len(pa_['hits'])} gold texts (tags {', '.join(pa_['gold_tags_involved'])}), and 25 texts is a small read. "
      "See Disclosures.")
    w("")

    # ---------------------------------------------------------------- protocol
    w("## Protocol (D050)")
    w("")
    w("1. **Teacher.** Haiku 5.5, Sonnet 5.5 and Opus 5.5 label a 600-text pilot pool focused on the weak tags. "
      "Two agent readers, blind to model names and votes, read every (text, tag) pair where the three disagree. "
      "The teacher with the highest accuracy wins; Sonnet if within 5 points of Opus. Gold is not used.")
    w("2. **Relabel and retrain.** The chosen teacher relabels a pool around the weak tags (`neurosurgery`, "
      "`ob_delivery`, `burns`, `trauma`, `icu`, `ventilator`, `cath_lab`). Three students train on the merged labels: "
      "1b (the WP4 student code), 2a (frozen embeddings plus logistic regression), 2b (a fine-tuned small encoder).")
    w(f"3. **Ship rule, per tag.** {rule_sentence(sc['ship_rule_d050'])}")
    w("4. **Budget and freeze.** At most $12 for this run; outputs are candidate files only.")
    w("")

    # ---------------------------------------------------------------- pilot
    w("## Teacher pilot")
    w("")
    d = pilot["design"]; ch = pilot["choice"]
    w(f"{d['items']} disagreement items on {d['what_an_item_is'].split(' on ')[-1]}, read by two independent agent "
      f"readers (one per half). Accuracy is against the reader. Choice: **{ch['model']}** ({ch['why']}).")
    w("")
    w("| Teacher | Right | Accuracy (Wilson 95%) | Pilot cost (600 texts) | Cost per 1,000 texts |")
    w("| --- | ---: | --- | ---: | ---: |")
    cost = pilot.get("pilot_cost_and_latency", {})
    for k, model in (("haiku", "claude-haiku-5-5"), ("sonnet", "claude-sonnet-5-5"), ("opus", "claude-opus-5-5")):
        o = pilot["overall"][k]; cc = cost.get(model, {})
        w(f"| {model} | {o['correct']}/{o['n']} | {o['accuracy']:.2f} ({o['wilson95'][0]:.2f} to {o['wilson95'][1]:.2f}) | "
          f"${cc.get('cost_usd', 0):.2f} | ${cc.get('cost_per_1000_texts_usd', 0):.2f} |")
    w("")
    w("| Weak tag | Items | Reader yes | Haiku | Sonnet | Opus |")
    w("| --- | ---: | ---: | ---: | ---: | ---: |")
    for t, x in pilot["per_tag"].items():
        w(f"| `{t}` | {x['items']} | {x['reader_positive']} | {x['haiku']['accuracy']:.2f} | "
          f"{x['sonnet']['accuracy']:.2f} | {x['opus']['accuracy']:.2f} |")
    pd_ = pilot["paired_differences"]["opus_minus_sonnet"]
    w("")
    vp = pilot.get("vote_patterns", {}).get("h1s0o0", {})
    worse = [t for t, x in pilot["per_tag"].items() if x["opus"]["accuracy"] < x["sonnet"]["accuracy"]]
    hc = pilot.get("hard_call_split", {})
    w(f"Opus minus Sonnet: {pd_['difference']:+.3f} (bootstrap 95% {pd_['boot95'][0]:+.3f} to {pd_['boot95'][1]:+.3f}). "
      "Accuracy is measured only where the teachers disagree, so it ranks them where it matters, not overall. "
      + (f"Haiku was most often the odd one out: on {vp['items']} items Haiku alone said yes, and the reader agreed on "
         f"{vp['reader_yes']}. " if vp else "")
      + (f"Opus scores below Sonnet on {', '.join(f'`{t}`' for t in worse)}. " if worse else "")
      + (f"On the {hc['items_marked_hard_call']} items the readers marked as hard calls, accuracy is "
         + ", ".join(f"{m} {hc[m]['hard_call']['accuracy']:.2f}" for m in ("haiku", "sonnet", "opus"))
         + f"; Opus's lead comes from the other {hc['opus']['other']['n']} items ("
         + ", ".join(f"{m} {hc[m]['other']['accuracy']:.2f}" for m in ("haiku", "sonnet", "opus")) + ")."
         if hc else ""))
    w("")
    w("## Relabeling and merged labels")
    w("")
    cnt = mm["counts"]
    w(f"Opus labeled {strong['rows']:,} texts (600 pilot plus {strong['rows'] - 600:,} scale texts; 0 failed) for "
      f"${strong['spend_usd']['pilot'] + strong['spend_usd']['scale']:.2f} "
      f"(${strong['spend_usd']['scale_cost_per_1000_texts']:.2f} per 1,000 texts at scale). Merged labels "
      f"(`labels/wp4b/merged_labels.parquet`): {sum(sum(v.values()) for v in cnt['by_source_and_split'].values()):,} texts; "
      f"Opus labels replace Haiku's on {cnt['replaced_by_strong']:,} texts and add {cnt['added_by_strong']:,} new ones; "
      f"{mm['gold_near_duplicates_dropped']['n']} near duplicates of gold texts were dropped. Weak-tag changes where Opus "
      "replaced Haiku:")
    w("")
    w("| Weak tag | Opus added | Opus removed |")
    w("| --- | ---: | ---: |")
    for t, v in mm["weak_tag_changes_on_replaced_texts"].items():
        w(f"| `{t}` | {v['added']} | {v['removed']} |")
    w("")

    # ---------------------------------------------------------------- candidates
    w("## Every candidate's scores")
    w("")
    vn = dec["validation_texts"]
    w(f"Validation is agreement with the merged teacher labels on WP4's {vn['wp4_validation_texts']:,} validation texts "
      f"minus {len(vn['missing_near_gold_texts_dropped_at_merge'])} near-gold texts dropped at the merge "
      f"({vn['used']:,} texts; {sc['split']['validation_by_label_source'].get(sc['labels']['chosen_teacher'], 0):,} carry "
      "Opus labels), with thresholds tuned on the same texts, so it is optimistic and ranks candidates only. Gold is the "
      "777-text agent-labeled set, used only for scoring. Paired intervals are stratified bootstrap (2,000 resamples) "
      "of candidate F1 minus current F1 on the same rows.")
    w("")
    w("| Tagger | What | Validation macro F1 | Validation macro F1, Opus-labeled texts | Gold macro F1 (95% CI) | Paired vs current (95% CI) | Tags passing the gold gate |")
    w("| --- | --- | ---: | ---: | --- | --- | --- |")
    vbs = sc["validation_macro_f1_by_label_source"].get("claude-opus-5-5", {})
    cg = sc["current"]["gold"]
    w(f"| current (shipped) | {sc['current_method']['model_version']} | {sc['current']['validation_vs_merged_labels']['macro_f1']:.3f} | "
      f"{vbs.get('current', 0):.3f} | {cg['macro_f1']:.3f} ({ci_txt(cg['macro_f1_ci95'], signed=False)}) | | |")
    for c in cands:
        v = sc["candidates"][c]
        w(f"| {c} | {CANDIDATE_LABEL[c]} | {v['validation_vs_merged_labels']['macro_f1']:.3f} | {vbs.get(c, 0):.3f} | "
          f"{v['gold']['macro_f1']:.3f} ({ci_txt(v['gold']['macro_f1_ci95'], signed=False)}) | "
          f"{ci_txt(v['gold_macro_f1_paired_delta_vs_current_ci95'])} | "
          f"{', '.join(f'`{t}`' for t in v['tags_with_paired_interval_above_0']) or 'none'} |")
    for k, v in sc["references_not_candidates"].items():
        w(f"| {k} (reference) | {v['what']} | {v['validation_vs_merged_labels_macro_f1']:.3f} | {vbs.get(k, 0):.3f} | "
          f"{v['gold_macro_f1']:.3f} ({ci_txt(v['gold_macro_f1_ci95'], signed=False)}) | | not a candidate |")
    w("")
    pc = ctx.get("wp4_population_check", {}).get("burns")
    w("References are reported for context and were never candidates: no read was drawn for them. The round 1 "
      "encoders (trained on Haiku labels) were a setup round"
      + (f"; the WP4 student 1b's `burns` was already rejected by the WP4 hand read "
         f"({sum(bool(x['right']) for x in pc['sample_added'])} of {len(pc['sample_added'])} additions right)."
         if pc and pc.get("sample_added") else "."))
    w("")
    zero = [(c, t) for t in TAGS for c in cands if pt[t]["candidates"][c]["paired_delta_ci95"][0] == 0]
    zz = [(c, t) for c, t in zero if pt[t]["candidates"][c]["paired_delta_ci95"][1] == 0]
    zp = [(c, t) for c, t in zero if (c, t) not in zz]
    rg = dec["gate1_rescore"]
    w("Per tag, gold F1 with the paired interval against the current method (**bold** = interval entirely above 0)."
      + (f" {len(zero)} intervals have a lower bound of exactly 0: {len(zz)} are [0, 0] ("
         + ", ".join(f"{c} `{t}`" for c, t in zz) + f"), and {len(zp)} run from 0 to above 0 ("
         + ", ".join(f"{c} `{t}`" for c, t in zp)
         + "). At the 2.5th percentile the two taggers score the same, so none of them lies entirely above 0, and "
           "all fail the first gate, which is a strict > 0." if zero else "")
      + f" Every paired interval was recomputed from the saved candidate tags with the declared resampling and equals "
        f"scores.json; on full-precision bounds {rg['lower_bound_exactly_0_full_precision']} of these {len(zero)} are "
        f"still exactly 0 and gate 1 passes the same {len(rg['passing'])} (candidate, tag) pairs.")
    w("")
    w("| Tag | Gold positives | Current method | Current F1 | " + " | ".join(cands) + " |")
    w("| --- | ---: | --- | ---: | " + " | ".join("---" for _ in cands) + " |")
    for t in TAGS:
        e = pt[t]
        cells = []
        for c in cands:
            x = e["candidates"][c]
            s_ = f"{x['gold_f1']:.2f} ({ci_txt(x['paired_delta_ci95'], 2)})"
            cells.append(f"**{s_}**" if x["passes_gold_gate"] else s_)
        w(f"| `{t}` | {e['gold_support']} | {'WP4 1b' if e['current_method'].startswith('WP4') else 'regex'} | "
          f"{e['current_gold_f1']:.2f} | " + " | ".join(cells) + " |")
    w("")
    w("Per tag, validation F1 against the merged teacher labels:")
    w("")
    w("| Tag | Validation positives | Current | " + " | ".join(cands) + " |")
    w("| --- | ---: | ---: | " + " | ".join("---:" for _ in cands) + " |")
    for t in TAGS:
        e = sc["per_tag"][t]
        w(f"| `{t}` | {e['current']['validation']['support']} | {e['current']['validation']['f1']:.2f} | "
          + " | ".join(f"{e['candidates'][c]['validation']['f1']:.2f}" for c in cands) + " |")
    w("")

    # ---------------------------------------------------------------- reads
    w("## Population reads (the second gate)")
    w("")
    w(f"For each (candidate, tag) that passed the gold gate, 25 random texts from all 275,095 distinct fact texts "
      "(gold texts and their near duplicates excluded) where the candidate and the current tagger disagree, additions "
      "and removals together. Readers saw only the text and the tag, shuffled, with random ids, and answered whether "
      "the text states the tag under the recipe v0.2 definitions and rules. A text favours the candidate when the "
      "reader agrees with the candidate's call; every drawn text is a disagreement, so it favours one side or the other.")
    w("")
    w("| Candidate, tag | Disagreements outside gold | Drawn: added / removed | In the candidate's favour (Wilson 95%) | Additions right | Removals right | Hard calls for candidate | Range if every hard call flipped | Drawn texts in teacher labels: for candidate | Needs | Result |")
    w("| --- | ---: | --- | --- | ---: | ---: | ---: | --- | --- | ---: | --- |")
    for k, r in reads.items():
        h = r["hard_calls"]; tl = r["texts_in_training_or_validation_labels"]
        w(f"| {r['candidate']}, `{r['tag']}` | {r['pool_non_gold_disagreements']:,} | {r['additions']['n']} / {r['removals']['n']} | "
          f"**{r['favours_candidate']} of {r['n']}** ({r['share_wilson95'][0]:.0%} to {r['share_wilson95'][1]:.0%}) | "
          f"{r['additions']['reader_agrees_with_candidate']}/{r['additions']['n']} | "
          f"{r['removals']['reader_agrees_with_candidate']}/{r['removals']['n']} | {h['favour_candidate']}/{h['n']} | "
          f"{h['favour_if_all_hard_calls_went_to_current']} to {h['favour_if_all_hard_calls_went_to_candidate']} | "
          f"{tl['favour_candidate']}/{tl['n']} (not in labels: {r['texts_not_in_labels']['favour_candidate']}/{r['texts_not_in_labels']['n']}) | "
          f"{r['needed']} | {'pass' if r['passes'] else 'fail'} |")
    w("")
    for k, r in reads.items():
        sh = r["share_wilson95"]; pool = r["pool_non_gold_disagreements"]
        w(f"- {r['candidate']}, `{r['tag']}`: read as a sample of its {pool:,} disagreements outside gold, between "
          f"{sh[0]:.0%} and {sh[1]:.0%} of them ({r['pool_estimate_favouring_candidate'][0]:,} to "
          f"{r['pool_estimate_favouring_candidate'][1]:,} texts) favour the candidate. "
          + (f"The verdict does not depend on the hard calls: it stays at or above {r['needed']} even if every hard call went to the current tagger."
             if r["passes"] and r["hard_calls"]["favour_if_all_hard_calls_went_to_current"] >= r["needed"] else
             f"The verdict does not depend on the hard calls: it stays below {r['needed']} even if every hard call went to the candidate."
             if (not r["passes"]) and r["hard_calls"]["favour_if_all_hard_calls_went_to_candidate"] < r["needed"] else
             "The verdict depends on the hard calls."))
    w("")
    w("Texts the reader decided against the candidate:")
    w("")
    w("| Candidate, tag | Text | Candidate | Reader | Reader's note |")
    w("| --- | --- | ---: | ---: | --- |")
    for k, r in reads.items():
        for it in r["items"]:
            if it["favours"] == "current":
                txt = it["text"].replace("|", "/")
                txt = txt if len(txt) <= 140 else txt[:137] + "..."
                w(f"| {r['candidate']}, `{r['tag']}` | {txt} | {it['candidate_says']} | {it['reader_present']} | "
                  f"{it['note'].replace('|', '/')} |")
    w("")

    # ---------------------------------------------------------------- decision
    w("## Decision per tag")
    w("")
    w("| Tag | Current method | Candidates passing the gold gate | Hand read | Decision | Method in the candidate mix |")
    w("| --- | --- | --- | --- | --- | --- |")
    for t in TAGS:
        e = pt[t]
        gp = [c for c, x in e["candidates"].items() if x["passes_gold_gate"]]
        hr = "; ".join(f"{c}: {x['hand_read']['favours_candidate']}/{x['hand_read']['n']}"
                       for c, x in e["candidates"].items() if x["hand_read"])
        w(f"| `{t}` | {e['current_method']} | {', '.join(gp) or 'none'} | {hr} | {e['decision']} | {e['method_in_mix']} |")
    w("")
    w(f"`model_version` of the candidate file: `{dec['model_version']}`.")
    w("")
    rv = (dec.get("previous_run") or {}).get("reviewed_run")
    if rv:
        w(f"Re-applied in this revision ({VERSION}) from the same scores.json and reads: "
          + ("the same decision per tag" if rv["same_decision_per_tag"] else "a DIFFERENT decision per tag")
          + ", the same `model_version`" * bool(rv["same_model_version"])
          + (", and a candidate file identical row for row" if rv["same_candidate_tags_in_every_row"] else
             ", but candidate rows that differ")
          + f" to the reviewed run ({rv['previous_version']}, {rv['previous_decide_run_utc'][:16].replace('T', ' ')} UTC; "
            f"file sha256 {(rv['previous_candidate_file_sha256'] or '')[:12]}).")
        w("")

    # ---------------------------------------------------------------- mix on gold
    w("## The candidate mix on gold")
    w("")
    w("| Tagger | Macro P | Macro R | Macro F1 (95% CI) | Micro F1 (95% CI) | Exact match |")
    w("| --- | ---: | ---: | --- | --- | ---: |")
    for name, k in (("current (shipped)", "current"), ("candidate mix", "candidate_mix"), ("regex v0.2", "regex_v0.2")):
        x = g[k]
        w(f"| {name} | {x['macro_precision']:.3f} | {x['macro_recall']:.3f} | {x['macro_f1']:.3f} "
          f"({ci_txt(x['macro_f1_ci95'], signed=False)}) | {x['micro_f1']:.3f} ({ci_txt(x['micro_f1_ci95'], signed=False)}) | "
          f"{x['exact_match']:.3f} |")
    w("")
    w(f"Paired macro F1, candidate mix minus current: {ci_txt(g['paired_macro_f1_delta_mix_minus_current_ci95'], 4)}; "
      f"candidate mix minus regex v0.2: {ci_txt(g['paired_macro_f1_delta_mix_minus_regex_ci95'])} (current minus regex: "
      f"{ci_txt(g['paired_macro_f1_delta_current_minus_regex_ci95'])}). {g['gold_rows_whose_tags_change']} of 777 gold rows change tags.")
    if changed:
        w("")
        w("| Tag | Current P / R / F1 (tp, fp, fn) | Candidate mix P / R / F1 (tp, fp, fn) | Paired difference (95% CI) |")
        w("| --- | --- | --- | --- |")
        for t in changed:
            a, b = g["per_tag"][t]["current"], g["per_tag"][t]["candidate_mix"]
            w(f"| `{t}` | {a['precision']:.2f} / {a['recall']:.2f} / {a['f1']:.2f} ({a['tp']}, {a['fp']}, {a['fn']}) | "
              f"{b['precision']:.2f} / {b['recall']:.2f} / {b['f1']:.2f} ({b['tp']}, {b['fp']}, {b['fn']}) | "
              f"{ci_txt(g['per_tag'][t]['mix_minus_current_ci95'])} |")
    w("")

    # ---------------------------------------------------------------- how firm
    gs = dec["gold_gate_sensitivity"]
    if gs["per_candidate_tag"]:
        w("## How firm the gold gate is")
        w("")
        w(f"Disclosure only. The gate is the declared interval (seed {WS.BOOT_SEED + 1}, {WS.N_BOOT:,} stratified "
          "resamples, committed in `scripts/wp4_student.py` before D050, see `seed_provenance` in decision.json); it was not rerun with another seed, and nothing "
          "below feeds the decision. Two checks of how much the pass depends on that one run: the declared rng stream "
          f"run on to {SENS_LONG_N_BOOT:,} resamples (its first {WS.N_BOOT:,} are the declared run), and "
          f"{SENS_ALT_SEEDS} other seeds ({WS.BOOT_SEED + 2} onward), each running the declared {WS.N_BOOT:,}-resample "
          "procedure.")
        w("")
        w(f"| Paired difference | Declared ({WS.N_BOOT:,}) | Same seed, {SENS_LONG_N_BOOT:,} resamples | P(gain <= 0) (MC SE) | "
          f"{SENS_ALT_SEEDS} other seeds: lower bound, min / median / max | Other seeds with lower bound above 0 | "
          f"Pooled other seeds ({SENS_ALT_SEEDS * WS.N_BOOT:,} resamples), P(gain <= 0) |")
        w("| --- | --- | --- | --- | --- | ---: | --- |")
        rows_ = [(f"{k.split('|')[0]}, `{k.split('|')[1]}` F1", v) for k, v in gs["per_candidate_tag"].items()]
        if gs["candidate_mix_macro"]:
            rows_.append(("candidate mix, macro F1", gs["candidate_mix_macro"]))
        for name, v in rows_:
            e_, o_ = v["declared_seed_extended"], v["other_seeds"]
            w(f"| {name} | {ci_txt(v['declared']['ci95'], 4)} | {ci_txt(e_['ci95'], 4)} | {e_['p_delta_le_0']:.1%} "
              f"({e_['monte_carlo_se_of_p_delta_le_0']:.1%}) | {o_['lower_bound_min']:+.4f} / {o_['lower_bound_median']:+.4f} / "
              f"{o_['lower_bound_max']:+.4f} | {o_['runs_with_lower_bound_above_0']} of {o_['runs']} | "
              f"{ci_txt(o_['pooled']['ci95'], 4)}, {o_['pooled']['p_delta_le_0']:.1%} |")
        w("")
        for k, v in gs["per_candidate_tag"].items():
            c_, t_ = k.split("|")
            if t_ in changed:
                o_ = v["other_seeds"]
                w(f"`{t_}` ({c_}): the declared lower bound ({v['declared']['ci95'][0]:+.4f}) is higher than "
                  f"{o_['runs'] - o_['runs_with_lower_bound_at_or_above_declared']} of the {o_['runs']} other runs, so "
                  "the declared run was on the lucky side; with more resamples the interval reaches about 0. The pass "
                  "is valid under D050 but marginal; the full statement is the first item under Disclosures.")
                w("")
        for k in gs["per_candidate_tag"]:
            c_, t_ = k.split("|")
            if t_ not in changed:
                o_ = gs["per_candidate_tag"][k]["other_seeds"]
                w(f"`{t_}` ({c_}) is firm on gold (lower bound above 0 in {o_['runs_with_lower_bound_above_0']} of "
                  f"{o_['runs']} other seeds) but failed the hand read, so it keeps its current method.")
                w("")

    # ---------------------------------------------------------------- impact
    fl = imp["fact_level"]
    w("## Impact on grades")
    w("")
    w(f"{fl['distinct_texts_whose_tags_change']:,} of {fl['distinct_texts']:,} distinct fact texts change tags "
      f"({fl['fact_rows_whose_tags_change']:,} of {fl['fact_rows']:,} fact rows, at {fl['facilities_with_a_changed_fact']:,} "
      "facilities)." + "".join(
          f" `{t}`: {v['texts_with_tag_current']:,} texts tagged now, {v['texts_with_tag_candidate']:,} in the mix "
          f"({v['added_all_texts']:,} added, {v['removed_all_texts']:,} removed)." for t, v in fl["per_changed_tag"].items()))
    w("")
    w("Grade 2 gate (D048 gates the ranker on grade 2+ under recipe v0.3):")
    w("")
    w("| Line | v0.3 grade 2+ now | v0.3 in the mix | Cross up | Cross down | v0.2 grade 2+ now | v0.2 in the mix | Cross up | Cross down |")
    w("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for l in GATE_LINES:
        a, b = imp["grade2_gate"]["per_line"][l]["v0.3"], imp["grade2_gate"]["per_line"][l]["v0.2"]
        w(f"| `{l}` | {a['grade_ge2_current']:,} | {a['grade_ge2_candidate']:,} | {a['up']} | {a['down']} | "
          f"{b['grade_ge2_current']:,} | {b['grade_ge2_candidate']:,} | {b['up']} | {b['down']} |")
    w("")
    for v in ("v0.3", "v0.2"):
        w(f"Facilities per grade, recipe {v} (none / claimed / supported / corroborated), current against the candidate mix:")
        w("")
        w("| Line | Current | Candidate mix | Facilities changed | Moves |")
        w("| --- | --- | --- | ---: | --- |")
        for l, d in imp["grades"][v].items():
            fmt = lambda c: " / ".join(f"{x:,}" for x in c)  # noqa: E731
            w(f"| `{l}` | {fmt(d['current'])} | {fmt(d['candidate'])} | {d['facilities_changed']:,} | "
              + ", ".join(f"{k.replace('->', ' to ')}: {n:,}" for k, n in d["transitions"].items()) + " |")
        w("")
    ex = imp["examples_v0.3"]
    if ex:
        w("Examples of moved records (v0.3), with the changed fact behind each move:")
        w("")
        for l, dd in ex.items():
            for k, lst in dd.items():
                for e_ in lst[:2]:
                    for f_ in e_["facts_changed"][:1]:
                        txt = f_["text"].replace("|", "/")
                        txt = txt if len(txt) <= 160 else txt[:157] + "..."
                        w(f"- `{l}` {k.replace('->', ' to ')}: \"{txt}\" (now {f_['current'] or 'no tag'}, "
                          f"mix {f_['candidate'] or 'no tag'})")
        w("")

    # ---------------------------------------------------------------- spend
    w("## Spend")
    w("")
    w("This stage made no LLM calls. Each ledger line's cost was recomputed from its `usage` block at the claude-api "
      "skill's prices (Opus 5.5 $4 / $20 per million input / output tokens, cache reads $0.20; Sonnet 5.5 $2 / $10, "
      "cache reads $0.10; Haiku 5.5 $0.10 / $0.50, cache reads $0.01; cache writes 1.25x input).")
    w("")
    w("| Ledger | Responses | Recorded USD | Recomputed USD | Largest line difference | Last call (UTC) |")
    w("| --- | ---: | ---: | ---: | ---: | --- |")
    for name, x in sp["ledgers"].items():
        if not x["exists"]:
            w(f"| {name} | 0 | 0.00 | | | |"); continue
        md_ = x["max_abs_line_diff_usd"]
        w(f"| {name} | {x['responses']:,} | {x['usd']:.4f} | {x['recomputed_usd']:.4f} | "
          f"{'' if md_ is None else f'{md_:.6f}'} | {(x['last_at'] or '')[:16].replace('T', ' ')} |")
    w(f"| **project total** | | **{sp['project_total_usd']:.4f}** | | | |")
    w("")
    wl = sp["ledgers"]["WP4b (this run)"]
    w("WP4b by purpose and model:")
    w("")
    w("| Purpose, model | Responses | USD |")
    w("| --- | ---: | ---: |")
    for k, v in wl.get("by_purpose", {}).items():
        w(f"| {k.replace(' | ', ', ')} | {v['responses']} | {v['usd']:.4f} |")
    w("")
    w(f"WP4b cap ${RUN_CAP_USD:.0f}: ${wl['usd']:.2f} spent, ${sp['wp4b_run_remaining_usd']:.2f} left. Project cap "
      f"${PROJECT_CAP_USD:.0f}: ${sp['project_total_usd']:.2f} spent, ${sp['project_remaining_usd']:.2f} left, of which "
      f"about ${SEALED_RESERVE_USD:.0f} is reserved for the sealed evaluation (D050). Other workflows may be spending "
      "while this runs; the ledgers were read at the time above.")
    w("")

    # ---------------------------------------------------------------- disclosures
    w("## Disclosures")
    w("")
    for d_ in dec["disclosures"]:
        w(f"- {d_}")
    w("")
    w("## For future runs")
    w("")
    w("Not changed in this run (they would alter frozen inputs or the protocol), recorded so the next run avoids them:")
    w("")
    for d_ in dec["future_runs"]:
        w(f"- {d_}")
    w("")
    w("## Files")
    w("")
    w("- `gold/fact_tags_candidate_wp4b.parquet` (+ `.meta.json`): one row per fact, the schema of "
      "`gold/fact_tags.parquet`, `model_version` naming the method per tag. Candidate only.")
    w("- `labels/wp4b/decision.json`: the unblinded reads item by item, both gates and the decision per tag, input hashes, "
      "checks; also `gold_gate_sensitivity`, `gate1_rescore`, `gold_use_audit` (teacher prompt, near duplicates), "
      "`read_provenance`, `validation_texts`, `previous_run` and `future_runs`.")
    w("- `labels/wp4b/impact.json`: gold scores of the mix, fact-level changes, grade counts per line under v0.2 and v0.3, "
      "gate crossings, examples, spend.")
    w(f"- Checks: {sum(c['passed'] for c in dec['checks'])} of {len(dec['checks'])} pass (listed in decision.json), "
      "including: the key matches the saved candidate tags and the current tags; the rebuilt current grades equal the "
      "v0.2 and v0.3 grade files; unchanged tags are identical to the current tags.")
    w("")
    w("Reproduce: `.venv/bin/python scripts/wp4b_decide.py` (reads only; no LLM calls).")
    w("")
    return "\n".join(L)


def rule_sentence(s: str) -> str:
    s = s.strip()
    return s[0].upper() + s[1:] + ("" if s.endswith(".") else ".")


if __name__ == "__main__":
    sys.exit(main())
