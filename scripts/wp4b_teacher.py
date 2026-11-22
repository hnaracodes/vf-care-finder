#!/usr/bin/env python
"""WP4b part A, stage pilot: pick a stronger teacher on a blinded pilot (D050).

D050 fixes the protocol before any result: a stronger teacher (claude-sonnet-5-5
or claude-opus-5-5) is chosen on blinded, independently read pilot
disagreements, never on gold; Sonnet wins if it is within 5 points of Opus.
This script builds the pilot and writes the blinded worksheets. It does not
read them and does not choose the teacher.

Stages (default: all, in order; each is idempotent, and a rerun makes no API
calls because every response is cached):

  pool   about 600 distinct non-gold texts from silver/facts.parquet, aimed at
         the seven weak tags (neurosurgery, ob_delivery, burns, trauma, icu,
         ventilator, cath_lab). Per tag, about 71 texts:
           disagreement  regex v0.2, the Haiku teacher (labels/teacher_facts.parquet)
                         and the shipped tagger (gold/fact_tags.parquet) do not
                         all agree on the tag; drawn round-robin over the vote
                         patterns so every kind of disagreement shows up
           near_miss     up to 25 texts a loose keyword pattern for the tag
                         matches (LOOSE), the regex does not tag, and none of
                         the three disagree on
         plus 100 uniform random texts from the rest. Gold texts (exact, and
         after lowercasing and dropping punctuation) and the 70 example texts
         of the teacher prompt are excluded.
  label  label the pool with claude-sonnet-5-5 and claude-opus-5-5 using the
         Haiku teacher's prompt p2 unchanged (scripts/wp3_teacher.py: system
         prompt, JSON schema, effort low, adaptive thinking, 40 facts per
         request, max_tokens 8000; the system prompt carries the cache
         breakpoint), and with claude-haiku-5-5 where the WP3 cache lacks a text.
         Sonnet and Opus see identical batches.
  items  every (text, weak tag) where the three teachers do not all agree, at
         most 300 spread evenly over the weak tags, written blinded as two
         halves (item_id, text, tag) plus a key with the three votes.

Temperature: the installed anthropic SDK (2.x) no longer takes temperature in
messages.create, so the run first sends one tiny request per model with
temperature=0 in extra_body. If the API rejects it (HTTP 400, unbilled), the model runs at
its default sampling, as the WP3 Haiku teacher did; repeatability comes from
the disk cache. The probe result is saved in teacher_cache/temperature_probe.json.

No server-side refusal fallback: the model that labels a text is the thing
being compared, so a silent switch to another model would corrupt the pilot.
A refused batch is split and retried; a single text that still fails is
cached as failed and left out of the comparison.

Budget: before every request the three ledgers are re-read
(labels/wp4b/spend.jsonl, labels/teacher_cache/spend.jsonl,
labels/eval_sealed/spend.jsonl). A request is sent only if its worst case
(input estimate at the cache-write price plus max_tokens of output) keeps the
WP4b ledger under --cap-usd (12, D050) and the project total under
VF_LLM_BUDGET_USD (30) minus the 5 USD reserved for the sealed evaluation.

After the blind reads (stage scale of the run):

  unblind  join pilot_read_1.csv and pilot_read_2.csv (one independent agent
           reader per half) with pilot_key.csv; per teacher and weak tag,
           accuracy against the reader with Wilson 95% intervals; apply the
           D050 rule and write pilot_result.json. No API calls.
  scale    if the chosen teacher beats Haiku: draw a pool of 12,000 distinct
           non-gold texts (teacher_cache/scale_pool.csv) in labeling order:
           the 600 pilot texts (cached), weak-tag disagreements among regex
           v0.2, the shipped mix, student 1b (all tags) and Haiku (in and outside
           the WP4 teacher set), loose near misses, agreement hits water-filled
           over the weak tags, 2,000 random. The chosen teacher labels them with
           the same p2 prompt, settings and cache; the budget check runs before
           every request and a stop leaves the tail unlabeled. Writes
           teacher_strong_facts.parquet.
  merge    merged_labels.parquet: the strong teacher's tags where it labeled a
           text, else Haiku's; WP4 validation texts stay in validation.

Run:  .venv/bin/python scripts/wp4b_teacher.py                 # pilot: pool, label, items
      .venv/bin/python scripts/wp4b_teacher.py --stage items   # no API calls
      .venv/bin/python scripts/wp4b_teacher.py --stage unblind # no API calls
      .venv/bin/python scripts/wp4b_teacher.py --stage scale --workers 6
      .venv/bin/python scripts/wp4b_teacher.py --stage merge   # no API calls
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

from vf import paths, recipes

SCRIPT = "scripts/wp4b_teacher.py"
ROOT = paths.code_root()


def _load_wp3():
    spec = importlib.util.spec_from_file_location("wp3_teacher", ROOT / "scripts" / "wp3_teacher.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


#: The WP3 Haiku teacher: prompt, JSON schema, request settings, call_batch,
#: write helpers. Reused as is, so the three teachers get the same prompt.
W3 = _load_wp3()

WEAK = ("neurosurgery", "ob_delivery", "burns", "trauma", "icu", "ventilator", "cath_lab")
MODELS = {"haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5"}
PROMPT_VERSION = W3.PROMPT_VERSION  # "p2"
#: sha256 of the p2 system prompt the Haiku teacher used (every WP3 cache file
#: carries it). The run refuses to label with any other prompt.
FROZEN_PROMPT_SHA = "44665b502a01396d0e351914773bb9a34b64c90486dab078e3c8ea60226965a5"
RECIPE_VERSION = W3.RECIPE_VERSION
SEED = 20261124

N_TARGETED = 500        # spread over the 7 weak tags (71 or 72 each)
N_NEAR_PER_TAG = 25
N_RANDOM = 100
ITEM_CAP = 300

# USD per million tokens, from the claude-api skill (model table cached
# 2026-11-03; prompt-caching economics): cache writes are 1.25x input (5-minute
# TTL). Cache reads: Sonnet 5.5 $0.10 and Opus 5.5 $0.20 (0.05x), Haiku 5.5
# $0.01 (0.1x). Haiku's rate holds for prompts up to 100K tokens; ours are
# under 10K, and cost() refuses to price a longer prompt.
PRICES = {
    "claude-haiku-5-5": {"input": 0.10, "output": 0.50, "cache_write": 0.125, "cache_read": 0.01},
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00, "cache_write": 2.50, "cache_read": 0.10},
    "claude-opus-5-5": {"input": 4.00, "output": 20.00, "cache_write": 5.00, "cache_read": 0.20},
}
MAX_PRICED_PROMPT = 100_000
RUN_CAP_USD = 12.0
SEALED_RESERVE_USD = 5.0

#: Loose keyword patterns per weak tag for the near-miss stratum: the cue words
#: D050's pilot names, plus spelling variants. burns uses the bare word because
#: "burn ward" style cues are all already regex hits or disagreements.
LOOSE: dict[str, str] = {
    "neurosurgery": r"spin(?:e|al)[- ]?surg|craniotom|head injur",
    "ob_delivery": r"\bdeliver(?:y|ies)\b|labou?r (?:room|ward|suite)|normal deliver",
    "burns": r"\bburns?\b",
    "trauma": r"polytrauma|casualty",
    "icu": r"critical care|intensive care",
    "ventilator": r"ventilat",
    "cath_lab": r"\bcath|angiograph",
}

LABELS = paths.labels_dir()
OUT = LABELS / "wp4b"
CACHE_ROOT = OUT / "teacher_cache"
LEDGER = OUT / "spend.jsonl"
OTHER_LEDGERS = {"wp3_teacher": LABELS / "teacher_cache" / "spend.jsonl",
                 "eval_sealed": LABELS / "eval_sealed" / "spend.jsonl"}
PROMPT_SNAPSHOT = CACHE_ROOT / f"prompt_{PROMPT_VERSION}.json"
PROBE_FILE = CACHE_ROOT / "temperature_probe.json"
RUNS_FILE = CACHE_ROOT / "label_runs.jsonl"
POOL = OUT / "pilot_pool.csv"
PILOT_LABELS = OUT / "pilot_labels.parquet"
BLIND = (OUT / "pilot_blind_1.csv", OUT / "pilot_blind_2.csv")
KEY = OUT / "pilot_key.csv"

write_text_atomic = W3.write_text_atomic
write_parquet_atomic = W3.write_parquet_atomic
write_meta = W3.write_meta
now_utc = W3.now_utc
order_tags = W3.order_tags


# =============================================================================
# helpers
# =============================================================================

def git_info() -> dict:
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                                check=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "--no-optional-locks", "status", "--porcelain", "--", SCRIPT,
                                     "scripts/wp3_teacher.py", "src/vf/recipes.py"], cwd=ROOT,
                                    capture_output=True, text=True, check=True).stdout.strip())
    except Exception:
        commit, dirty = None, None
    return {"git_commit": commit, "code_uncommitted": dirty}


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def write_csv_atomic(df: pd.DataFrame, path: Path) -> None:
    paths.ensure(path.parent)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        df.to_csv(tmp, index=False)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def loose_norm(s: str) -> str:
    return re.sub(r"[\W_]+", " ", s.lower()).strip()


def cost_usd(model: str, usage: dict) -> float:
    p = PRICES[model]
    prompt = usage.get("input_tokens", 0) + usage.get("cache_creation_input_tokens", 0) + \
        usage.get("cache_read_input_tokens", 0)
    if prompt > MAX_PRICED_PROMPT:
        raise RuntimeError(f"prompt of {prompt} tokens is above the {MAX_PRICED_PROMPT} priced limit")
    return (usage.get("input_tokens", 0) * p["input"]
            + usage.get("cache_creation_input_tokens", 0) * p["cache_write"]
            + usage.get("cache_read_input_tokens", 0) * p["cache_read"]
            + usage.get("output_tokens", 0) * p["output"]) / 1e6


def read_ledger(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def ledger_totals() -> dict:
    out = {"wp4b": round(sum(r.get("cost_usd", 0.0) for r in read_ledger(LEDGER)), 6)}
    for k, p in OTHER_LEDGERS.items():
        out[k] = round(sum(r.get("cost_usd", 0.0) for r in read_ledger(p)), 6)
    out["project"] = round(sum(out.values()), 6)
    return out


def freeze_prompt() -> dict:
    """Make sure the system prompt is the exact p2 prompt the Haiku teacher used.

    The live prompt is rebuilt from src/vf/recipes.py, which other work may
    edit. The first run snapshots it (only if its sha256 is the p2 one); later
    runs use the snapshot if the live prompt has drifted.
    """
    live_sha = sha256_text(W3.SYSTEM_PROMPT)
    if PROMPT_SNAPSHOT.exists():
        snap = json.loads(PROMPT_SNAPSHOT.read_text())
        if sha256_text(snap["system_prompt"]) != FROZEN_PROMPT_SHA:
            raise SystemExit(f"{PROMPT_SNAPSHOT} does not hold the p2 prompt")
        if live_sha != FROZEN_PROMPT_SHA or W3.OUTPUT_SCHEMA != snap["output_schema"]:
            print("WARNING: the live wp3 prompt differs from p2; using the frozen snapshot")
            W3.SYSTEM_PROMPT = snap["system_prompt"]
            W3.OUTPUT_SCHEMA = snap["output_schema"]
        return snap
    if live_sha != FROZEN_PROMPT_SHA:
        raise SystemExit(f"live wp3 prompt sha {live_sha[:16]} is not p2 ({FROZEN_PROMPT_SHA[:16]}); refusing")
    snap = {"prompt_version": PROMPT_VERSION, "sha256": FROZEN_PROMPT_SHA, "recipe_version": RECIPE_VERSION,
            "source": "scripts/wp3_teacher.py SYSTEM_PROMPT and OUTPUT_SCHEMA", "effort": W3.EFFORT,
            "max_tokens": W3.MAX_TOKENS, "batch_size": W3.BATCH_SIZE, "all_tags": list(recipes.ALL_TAGS),
            "system_prompt": W3.SYSTEM_PROMPT, "output_schema": W3.OUTPUT_SCHEMA, "snapshot_at": now_utc()}
    write_text_atomic(PROMPT_SNAPSHOT, json.dumps(snap, indent=2, ensure_ascii=False) + "\n")
    return snap


# =============================================================================
# data
# =============================================================================

def load_texts() -> pd.DataFrame:
    """Distinct silver text_norm with fact count and the shipped tagger's tags."""
    f = pd.read_parquet(paths.silver_dir() / "facts.parquet", columns=["fact_id", "text_norm"])
    ft = pd.read_parquet(paths.gold_dir() / "fact_tags.parquet", columns=["fact_id", "tags"])
    m = f.merge(ft, on="fact_id", how="left", validate="one_to_one")
    if m["tags"].isna().any():
        raise SystemExit("gold/fact_tags.parquet does not cover every silver fact")
    key = m["tags"].map(lambda a: "|".join(sorted(a)))
    if (key.groupby(m["text_norm"]).nunique() > 1).any():
        raise SystemExit("the shipped tagger gives one text different tags")
    first = m.drop_duplicates("text_norm").set_index("text_norm")["tags"]
    u = m.groupby("text_norm", sort=True).agg(n_facts=("fact_id", "size")).reset_index()
    u["shipped"] = u["text_norm"].map(lambda t: order_tags(first[t]))
    return u


def haiku_wp3_labels() -> dict[str, list]:
    """labels/teacher_facts.parquet: Haiku p2 labels for the 29,300 WP3 texts."""
    tf = pd.read_parquet(LABELS / "teacher_facts.parquet", columns=["text_norm", "tags", "prompt_version"])
    assert (tf["prompt_version"] == PROMPT_VERSION).all()
    return {t: order_tags(x) for t, x in zip(tf["text_norm"], tf["tags"])}


def exclusions(texts: pd.Series) -> tuple[pd.Series, dict]:
    gold = W3.load_gold()
    g_exact = set(gold["text_norm"])
    g_loose = {loose_norm(t) for t in g_exact}
    examples = {t for t, _ in W3.EXAMPLES}
    is_exact = texts.isin(g_exact)
    is_loose = ~is_exact & texts.map(lambda t: loose_norm(t) in g_loose)
    is_example = texts.isin(examples)
    excl = is_exact | is_loose | is_example
    return excl, {"gold_exact": int(is_exact.sum()), "gold_after_normalising": int(is_loose.sum()),
                  "prompt_examples": int(is_example.sum()), "gold_rows": int(len(gold))}


def tag_flags(df: pd.DataFrame, t: str) -> dict:
    rx = df["regex"].map(lambda s: t in s).to_numpy()
    sh = df["shipped"].map(lambda s: t in s).to_numpy()
    has_h = df["haiku_wp3"].map(lambda s: isinstance(s, list)).to_numpy()
    hk = df["haiku_wp3"].map(lambda s: isinstance(s, list) and t in s).to_numpy()
    dis = (rx != sh) | (has_h & ((hk != rx) | (hk != sh)))
    pattern = np.array([f"regex={int(a)} shipped={int(b)} haiku={int(c) if h else 'NA'}"
                        for a, b, c, h in zip(rx, sh, hk, has_h)], dtype=object)
    return {"regex": rx, "shipped": sh, "has_haiku": has_h, "haiku": hk, "disagree": dis, "pattern": pattern}


def round_robin(idx: np.ndarray, groups: np.ndarray, n: int, rng) -> list[int]:
    by = {}
    for g in sorted(set(groups[idx])):
        members = idx[groups[idx] == g].copy()
        rng.shuffle(members)
        by[g] = list(members)
    out = []
    while len(out) < n and any(by.values()):
        for g in sorted(by):
            if by[g] and len(out) < n:
                out.append(int(by[g].pop(0)))
    return out


def stage_pool(force: bool = False) -> pd.DataFrame:
    if POOL.exists() and not force:
        pool = read_csv(POOL)
        print(f"[pool] {len(pool)} texts already in {POOL.name}; reusing (pass --repool to redraw)")
        return pool
    u = load_texts()
    excl, ex_counts = exclusions(u["text_norm"])
    pool_all = u[~excl].reset_index(drop=True)
    t0 = time.time()
    pool_all["regex"] = [order_tags(x) for x in recipes.tag_facts(pool_all["text_norm"].tolist())]
    print(f"[pool] regex v0.2 over {len(pool_all):,} distinct candidate texts in {time.time() - t0:.0f}s")
    hk = haiku_wp3_labels()
    pool_all["haiku_wp3"] = pool_all["text_norm"].map(hk)
    rng = np.random.default_rng(SEED)
    flags = {t: tag_flags(pool_all, t) for t in WEAK}
    loose = {t: pool_all["text_norm"].str.contains(LOOSE[t], regex=True).to_numpy() for t in WEAK}
    n_dis = {t: int(flags[t]["disagree"].sum()) for t in WEAK}
    order = sorted(WEAK, key=lambda t: (n_dis[t], t))   # rarest first, so it is not starved by overlaps
    base, extra = divmod(N_TARGETED, len(WEAK))
    quota = {t: base + (1 if i < extra else 0) for i, t in enumerate(order)}
    taken = np.zeros(len(pool_all), dtype=bool)
    stratum = np.full(len(pool_all), "", dtype=object)
    target = np.full(len(pool_all), "", dtype=object)
    avail = {}
    for t in order:
        f = flags[t]
        near = loose[t] & ~f["regex"] & ~f["disagree"]
        d_idx = np.flatnonzero(f["disagree"] & ~taken)
        n_idx = np.flatnonzero(near & ~taken)
        avail[t] = {"disagreement": int(len(d_idx)), "near_miss": int(len(n_idx)),
                    "disagreement_all": n_dis[t], "near_miss_all": int(near.sum())}
        nn = min(N_NEAR_PER_TAG, len(n_idx))
        nd = min(quota[t] - nn, len(d_idx))
        nn = min(quota[t] - nd, len(n_idx))   # top up from near misses if disagreements run short
        pick_d = round_robin(d_idx, f["pattern"], nd, rng)
        pick_n = [int(i) for i in rng.choice(n_idx, size=nn, replace=False)] if nn else []
        for i in pick_d:
            taken[i], stratum[i], target[i] = True, "disagreement", t
        for i in pick_n:
            taken[i], stratum[i], target[i] = True, "near_miss", t
    r_idx = np.flatnonzero(~taken)
    for i in rng.choice(r_idx, size=min(N_RANDOM, len(r_idx)), replace=False):
        taken[i], stratum[i], target[i] = True, "random", ""
    sel = pool_all[taken].copy()
    sel["stratum"] = stratum[taken]
    sel["target_tag"] = target[taken]
    sel = sel.sample(frac=1.0, random_state=SEED).reset_index(drop=True)   # labeling order mixes strata
    sel.insert(0, "pool_id", [f"P{i + 1:04d}" for i in range(len(sel))])
    out = sel[["pool_id", "text_norm", "stratum", "target_tag", "n_facts"]].copy()
    out["n_facts"] = out["n_facts"].astype(int)
    # gold check, again, on the final pool
    assert not exclusions(out["text_norm"])[0].any(), "an excluded text reached the pilot pool"
    write_csv_atomic(out, POOL)
    counts = out.groupby(["target_tag", "stratum"]).size().unstack(fill_value=0)
    write_meta(POOL, {
        "output": "labels/wp4b/pilot_pool.csv", "rows": int(len(out)), "created_at_utc": now_utc(), **git_info(),
        "script": SCRIPT, "seed": SEED, "recipe_version": RECIPE_VERSION, "weak_tags": list(WEAK),
        "design": {"targeted_texts": N_TARGETED, "near_miss_per_tag": N_NEAR_PER_TAG, "random": N_RANDOM,
                   "quota_per_tag": quota, "tag_order": order,
                   "disagreement": "regex v0.2, Haiku teacher p2 (labels/teacher_facts.parquet; only its 29,300 "
                                   "texts have a Haiku vote) and gold/fact_tags.parquet do not all agree on the tag; "
                                   "drawn round-robin over the vote patterns",
                   "near_miss": "LOOSE pattern matches, regex v0.2 does not tag it, no disagreement",
                   "loose_patterns": LOOSE,
                   "random": "uniform over the remaining candidate texts"},
        "candidates": {"distinct_texts": int(len(u)), "after_exclusions": int(len(pool_all)),
                       "excluded": ex_counts, "available_when_drawn": avail},
        "drawn": {"by_stratum": out["stratum"].value_counts().to_dict(),
                  "by_tag_and_stratum": {t: {k: int(v) for k, v in counts.loc[t].items()}
                                         for t in counts.index if t}},
        "inputs": {"silver/facts.parquet": W3.sha256_file(paths.silver_dir() / "facts.parquet"),
                   "gold/fact_tags.parquet": W3.sha256_file(paths.gold_dir() / "fact_tags.parquet"),
                   "labels/teacher_facts.parquet": W3.sha256_file(LABELS / "teacher_facts.parquet"),
                   "labels/gold_facts.csv (exclusion only)": W3.sha256_file(LABELS / "gold_facts.csv")},
        "gold_texts_excluded": True,
        "columns": list(out.columns),
        "note": "The pool file carries no model labels. Selection signals are in pilot_labels.parquet.",
    })
    print(f"[pool] {len(out)} texts -> {POOL}")
    print(counts.to_string())
    return out


# =============================================================================
# cache and API
# =============================================================================

def cache_dir(model: str) -> Path:
    return CACHE_ROOT / model / PROMPT_VERSION


def cache_index(model: str) -> dict[str, dict]:
    idx = {}
    d = cache_dir(model)
    if not d.exists():
        return idx
    for p in sorted(d.glob("*.json")):
        rec = json.loads(p.read_text())
        if rec["prompt_sha256"] != FROZEN_PROMPT_SHA or rec["recipe_version"] != RECIPE_VERSION:
            continue
        for it in rec["items"]:
            idx[it["text_norm"]] = {"tags": it["tags"], "status": it.get("status", "ok"), "file": p.name,
                                    "model": rec["model"], "request_id": rec["request_id"]}
    return idx


def batch_key(model: str, texts: list[str]) -> str:
    h = hashlib.sha1()
    h.update(f"{model}|{RECIPE_VERSION}|{PROMPT_VERSION}|{FROZEN_PROMPT_SHA}\n".encode())
    for t in texts:
        h.update(t.encode())
        h.update(b"\n")
    return h.hexdigest()


class Budget:
    """Re-reads every ledger before each request; reserves the worst case."""

    def __init__(self, run_cap: float, project_cap: float):
        self.run_cap, self.project_cap = run_cap, project_cap
        self.reserved = 0.0
        self.lock = threading.Lock()

    @staticmethod
    def worst_case(model: str, n_chars: int, max_tokens: int) -> float:
        p = PRICES[model]
        est_in = 9000 + n_chars // 2        # system prompt ~5-7K tokens, facts ~1 token per 3-4 chars
        return (est_in * max(p["input"], p["cache_write"]) + max_tokens * p["output"]) / 1e6

    def reserve(self, amount: float) -> tuple[bool, dict]:
        with self.lock:
            tot = ledger_totals()
            ok = (tot["wp4b"] + self.reserved + amount <= self.run_cap
                  and tot["project"] + self.reserved + amount <= self.project_cap)
            if ok:
                self.reserved += amount
            return ok, tot

    def release(self, amount: float) -> None:
        with self.lock:
            self.reserved -= amount


LEDGER_LOCK = threading.Lock()


def log_response(*, purpose: str, model: str, served_model: str, n_texts: int, stop_reason, request_id,
                 usage: dict, seconds: float, temperature) -> float:
    c = cost_usd(model, usage)
    rec = {"at": now_utc(), "script": SCRIPT, "purpose": purpose, "model": model, "served_model": served_model,
           "prompt_version": PROMPT_VERSION, "temperature": temperature, "effort": W3.EFFORT, "n_texts": n_texts,
           "stop_reason": stop_reason, "request_id": request_id, "usage": usage, "cost_usd": round(c, 8),
           "seconds": round(seconds, 2)}
    with LEDGER_LOCK:
        paths.ensure(LEDGER.parent)
        with open(LEDGER, "a") as fh:
            fh.write(json.dumps(rec) + "\n")
    return c


def make_client():
    import anthropic
    key = W3.read_env().get("ANTHROPIC_API_KEY")
    if not key:
        raise SystemExit("ANTHROPIC_API_KEY is not set in .env")
    return anthropic.Anthropic(api_key=key, max_retries=4, timeout=300.0)


def temperature_client(client, temperature):
    """A stand-in client whose messages.create adds temperature, so W3.call_batch is reused unchanged."""
    if temperature is None:
        return client
    return SimpleNamespace(messages=SimpleNamespace(
        create=lambda **kw: client.messages.create(extra_body={"temperature": temperature}, **kw)))


def probe_temperature(client, budget: Budget) -> dict:
    """One tiny request per model with temperature=0. A 400 is not billed."""
    import anthropic
    probe = json.loads(PROBE_FILE.read_text()) if PROBE_FILE.exists() else {}
    for model in MODELS.values():
        if model in probe:
            continue
        need = Budget.worst_case(model, 0, 16)
        ok, _ = budget.reserve(need)
        if not ok:
            raise SystemExit("budget cap reached before the temperature probe")
        t0 = time.monotonic()
        try:
            # anthropic 1.x+ dropped temperature from messages.create (a TypeError), so it goes in
            # extra_body: the API, not the SDK, decides whether the model accepts it
            resp = client.messages.create(model=model, max_tokens=16, extra_body={"temperature": 0.0},
                                          messages=[{"role": "user", "content": "Reply with the word ok."}])
        except anthropic.BadRequestError as e:
            probe[model] = {"temperature_0_accepted": False, "status": e.status_code,
                            "error": str(getattr(e, "message", e))[:400], "at": now_utc()}
        else:
            u = resp.usage
            usage = {k: int(getattr(u, k, 0) or 0) for k in
                     ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")}
            log_response(purpose="temperature_probe", model=model, served_model=resp.model, n_texts=0,
                         stop_reason=resp.stop_reason, request_id=getattr(resp, "_request_id", None), usage=usage,
                         seconds=time.monotonic() - t0, temperature=0.0)
            probe[model] = {"temperature_0_accepted": True, "at": now_utc()}
        finally:
            budget.release(need)
        write_text_atomic(PROBE_FILE, json.dumps(probe, indent=2) + "\n")
    return probe


def label_texts(client, model: str, texts: list[str], *, budget: Budget, workers: int, purpose: str,
                temperature) -> dict:
    """Label every text not yet cached for this model, in fixed batches of 40."""
    import anthropic
    idx = cache_index(model)
    todo = [t for t in dict.fromkeys(texts) if t not in idx]
    st = {"model": model, "requested": len(texts), "cached_before": len(texts) - len(todo), "calls": 0,
          "cost_usd": 0.0, "failed_single": 0, "splits": 0, "stopped_for_budget": False, "newly_labeled": 0,
          "workers": workers, "temperature": temperature}
    if not todo:
        print(f"[label {model}] all {len(texts)} texts cached; no API calls")
        return st
    tclient = temperature_client(client, temperature)
    paths.ensure(cache_dir(model))
    bs = W3.BATCH_SIZE
    queue = [todo[i:i + bs] for i in range(0, len(todo), bs)]
    attempts: dict[tuple, int] = {}
    print(f"[label {model}] {len(todo)} texts in {len(queue)} requests of up to {bs}; temperature "
          f"{'default' if temperature is None else temperature}; ledger {ledger_totals()}")
    t_start = time.monotonic()

    def run(b):
        t0 = time.monotonic()
        res = W3.call_batch(tclient, model, b)
        res["seconds"] = time.monotonic() - t0
        return res

    def record(b, res) -> list:
        c = log_response(purpose=purpose, model=model, served_model=res["model"], n_texts=len(b),
                         stop_reason=res["stop_reason"], request_id=res["request_id"], usage=res["usage"],
                         seconds=res["seconds"], temperature=temperature)
        st["calls"] += 1
        st["cost_usd"] += c
        base = {"recipe_version": RECIPE_VERSION, "prompt_version": PROMPT_VERSION,
                "prompt_sha256": FROZEN_PROMPT_SHA, "model_requested": model, "model": res["model"],
                "effort": W3.EFFORT, "max_tokens": W3.MAX_TOKENS, "temperature": temperature,
                "labeled_at": now_utc(), "request_id": res["request_id"], "usage": res["usage"],
                "seconds": round(res["seconds"], 2), "stop_reason": res["stop_reason"]}
        labels = res["labels"]
        if labels is not None and len(labels) == len(b):
            rec = {**base, "items": [{"text_norm": t, "tags": labels[i]} for i, t in enumerate(b)]}
            write_text_atomic(cache_dir(model) / f"{batch_key(model, b)}.json", json.dumps(rec, ensure_ascii=False))
            st["newly_labeled"] += len(b)
            return []
        if len(b) == 1:
            rec = {**base, "items": [{"text_norm": b[0], "tags": [], "status": f"failed:{res['stop_reason']}"}]}
            write_text_atomic(cache_dir(model) / f"{batch_key(model, b)}.json", json.dumps(rec, ensure_ascii=False))
            st["failed_single"] += 1
            return []
        st["splits"] += 1
        h = len(b) // 2
        return [b[:h], b[h:]]

    with ThreadPoolExecutor(max_workers=workers) as ex:
        running = {}
        first = True
        while queue or running:
            # the first request runs alone so it writes the prompt cache the others then read
            limit = 1 if first else workers
            while queue and len(running) < limit and not st["stopped_for_budget"]:
                b = queue.pop(0)
                need = Budget.worst_case(model, sum(len(t) for t in b), W3.MAX_TOKENS)
                ok, tot = budget.reserve(need)
                if not ok:
                    st["stopped_for_budget"] = True
                    queue.insert(0, b)
                    print(f"[label {model}] STOP: the next request could pass a cap (ledgers {tot}, run cap "
                          f"${budget.run_cap:.2f}, project cap ${budget.project_cap:.2f})")
                    break
                running[ex.submit(run, b)] = (b, need)
            if not running:
                break
            done, _ = wait(list(running), return_when=FIRST_COMPLETED)
            for fut in done:
                b, need = running.pop(fut)
                try:
                    res = fut.result()
                except Exception as e:  # noqa: BLE001
                    budget.release(need)
                    if isinstance(e, anthropic.APIStatusError) and e.status_code < 500 and e.status_code != 429:
                        print(f"[label {model}] fatal API error {e.status_code}: {getattr(e, 'message', e)}")
                        raise
                    k = tuple(b)
                    attempts[k] = attempts.get(k, 0) + 1
                    if attempts[k] >= 3:
                        print(f"[label {model}] giving up on a batch of {len(b)} after 3 transient errors")
                    else:
                        print(f"[label {model}] transient error ({type(e).__name__}); requeued")
                        queue.append(b)
                    continue
                try:
                    queue.extend(record(b, res))
                finally:
                    budget.release(need)
                first = False
    st["remaining"] = sum(len(b) for b in queue)
    st["wall_seconds"] = round(time.monotonic() - t_start, 1)
    st["cost_usd"] = round(st["cost_usd"], 6)
    with LEDGER_LOCK:
        with open(RUNS_FILE, "a") as fh:
            fh.write(json.dumps({"at": now_utc(), "purpose": purpose, **st}) + "\n")
    print(f"[label {model}] {st['newly_labeled']} newly labeled, {st['calls']} calls, ${st['cost_usd']:.4f}, "
          f"{st['failed_single']} single-text failures, {st['splits']} splits, {st['remaining']} left, "
          f"{st['wall_seconds']:.0f}s")
    return st


def stage_label(pool: pd.DataFrame, workers: int, run_cap: float, project_cap: float) -> dict:
    freeze_prompt()
    budget = Budget(run_cap, project_cap)
    client = make_client()
    probe = probe_temperature(client, budget)
    texts = pool["text_norm"].tolist()
    temps = {}
    for k, model in MODELS.items():
        # Haiku keeps the WP3 setting (no temperature) so new labels match its cached ones
        acc = probe[model]["temperature_0_accepted"]
        temps[model] = 0.0 if (acc and k != "haiku") else None
    stats = {}
    w3 = {t: v for t, v in W3.cache_index().items() if v["status"] == "ok"}
    missing = [t for t in texts if t not in w3]
    stats["haiku"] = label_texts(client, MODELS["haiku"], missing, budget=budget, workers=workers,
                                 purpose="pilot", temperature=temps[MODELS["haiku"]])
    stats["haiku"]["from_wp3_cache"] = len(texts) - len(missing)
    for k in ("sonnet", "opus"):
        stats[k] = label_texts(client, MODELS[k], texts, budget=budget, workers=workers, purpose="pilot",
                               temperature=temps[MODELS[k]])
    return {"stats": stats, "probe": probe, "temperatures": temps}


# =============================================================================
# disagreements, blinding
# =============================================================================

def pilot_table(pool: pd.DataFrame) -> pd.DataFrame:
    """One row per pool text: selection signals and each teacher's tags and status."""
    u = load_texts().set_index("text_norm")
    df = pool.copy()
    df["n_facts"] = df["n_facts"].astype(int)
    df["regex_tags"] = [order_tags(x) for x in recipes.tag_facts(df["text_norm"].tolist())]
    df["shipped_tags"] = df["text_norm"].map(lambda t: list(u.loc[t, "shipped"]))
    hk_tf = haiku_wp3_labels()
    df["haiku_wp3_selection"] = df["text_norm"].map(hk_tf)
    w3 = W3.cache_index()
    new_h = cache_index(MODELS["haiku"])
    h_tags, h_status, h_src = [], [], []
    for t in df["text_norm"]:
        if t in w3 and w3[t]["status"] == "ok":
            h_tags.append(order_tags(w3[t]["tags"])); h_status.append("ok"); h_src.append("wp3_cache")
        elif t in new_h:
            h_tags.append(order_tags(new_h[t]["tags"])); h_status.append(new_h[t]["status"]); h_src.append("wp4b")
        else:
            h_tags.append([]); h_status.append("missing"); h_src.append("")
    df["haiku_tags"], df["haiku_status"], df["haiku_source"] = h_tags, h_status, h_src
    for k in ("sonnet", "opus"):
        idx = cache_index(MODELS[k])
        df[f"{k}_tags"] = df["text_norm"].map(lambda t: order_tags(idx[t]["tags"]) if t in idx else [])
        df[f"{k}_status"] = df["text_norm"].map(lambda t: idx[t]["status"] if t in idx else "missing")
    # the WP3 cache and teacher_facts.parquet must agree where both exist
    both = df["haiku_wp3_selection"].map(lambda s: isinstance(s, list)) & (df["haiku_source"] == "wp3_cache")
    bad = int((df.loc[both, "haiku_wp3_selection"].map(tuple) != df.loc[both, "haiku_tags"].map(tuple)).sum())
    if bad:
        raise SystemExit(f"{bad} texts: teacher_facts.parquet and the WP3 cache disagree")
    df["complete"] = (df["haiku_status"] == "ok") & (df["sonnet_status"] == "ok") & (df["opus_status"] == "ok")
    return df


def water_fill(counts: dict[str, int], cap: int) -> dict[str, int]:
    """Even per-tag quotas summing to min(cap, total); a tag with fewer items gives its share to the rest."""
    quota = {t: 0 for t in counts}
    left = dict(counts)
    remaining = min(cap, sum(counts.values()))
    while remaining > 0 and any(left.values()):
        open_tags = [t for t in WEAK if left.get(t, 0) > 0]
        share, extra = divmod(remaining, len(open_tags))
        if share == 0:
            # fewer slots than open tags: give one each to the tags with the most items left
            for t in sorted(open_tags, key=lambda x: (-left[x], WEAK.index(x)))[:extra]:
                quota[t] += 1
                left[t] -= 1
            break
        for t in open_tags:
            g = min(share, left[t])
            quota[t] += g
            left[t] -= g
            remaining -= g
    return quota


def pair_stats(df: pd.DataFrame, a: str, b: str, t: str) -> dict:
    A = df[f"{a}_tags"].map(lambda s: t in s).to_numpy()
    B = df[f"{b}_tags"].map(lambda s: t in s).to_numpy()
    return {"agree": int((A == B).sum()), f"{a}_only": int((A & ~B).sum()), f"{b}_only": int((~A & B).sum()),
            "agreement": round(float((A == B).mean()), 4) if len(A) else None}


def cost_latency() -> dict:
    """Per model, from the WP4b ledger (pilot requests) and the label run log."""
    rows = [r for r in read_ledger(LEDGER) if r.get("purpose") == "pilot"]
    runs = [json.loads(line) for line in RUNS_FILE.read_text().splitlines()] if RUNS_FILE.exists() else []
    out = {}
    for model in MODELS.values():
        rs = [r for r in rows if r["model"] == model]
        if not rs:
            out[model] = {"requests": 0}
            continue
        cost = sum(r["cost_usd"] for r in rs)
        secs = np.array([r["seconds"] for r in rs if r["n_texts"] == W3.BATCH_SIZE] or [r["seconds"] for r in rs])
        tok = {k: int(sum(r["usage"].get(k, 0) for r in rs)) for k in
               ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")}
        texts_sent = int(sum(r["n_texts"] for r in rs))
        mruns = [r for r in runs if r["model"] == model and r.get("calls", 0) > 0 and r.get("purpose") == "pilot"]
        wall = sum(r["wall_seconds"] for r in mruns)
        newly = sum(r["newly_labeled"] for r in mruns)
        n_ok = newly
        out[model] = {
            "requests": len(rs), "texts_labeled_ok": n_ok, "texts_sent_incl_retries": texts_sent,
            "cost_usd": round(cost, 4),
            "cost_per_1000_texts_usd": round(1000 * cost / n_ok, 4) if n_ok else None,
            "tokens": tok,
            "output_tokens_per_text": round(tok["output_tokens"] / texts_sent, 1) if texts_sent else None,
            "cache_read_share_of_prompt": round(tok["cache_read_input_tokens"] / max(1, tok["input_tokens"]
                                                + tok["cache_creation_input_tokens"]
                                                + tok["cache_read_input_tokens"]), 3),
            "seconds_per_request_40_texts": {"median": round(float(np.median(secs)), 1),
                                             "p90": round(float(np.percentile(secs, 90)), 1),
                                             "max": round(float(secs.max()), 1), "n": int(len(secs))},
            "wall_seconds": round(wall, 1), "workers": sorted({r["workers"] for r in mruns}),
            "texts_per_minute_at_workers": round(60 * newly / wall, 1) if wall else None,
            "served_models": sorted({r.get("served_model") for r in rs}),
            "stop_reasons": {k: sum(1 for r in rs if r["stop_reason"] == k) for k in {r["stop_reason"] for r in rs}},
        }
    # the full WP3 Haiku run, for reference (ledger has no latency)
    w3rows = read_ledger(OTHER_LEDGERS["wp3_teacher"])
    w3_cost = sum(r["cost_usd"] for r in w3rows if r.get("purpose") in ("train", "gold"))
    w3_texts = sum(r["n_texts"] for r in w3rows if r.get("purpose") in ("train", "gold"))
    out["claude-haiku-5-5 (WP3 full run, train and gold requests)"] = {
        "cost_usd": round(w3_cost, 4), "texts_sent_incl_retries": w3_texts,
        "cost_per_1000_texts_usd": round(1000 * w3_cost / w3_texts, 4) if w3_texts else None}
    return out


def stage_items(pool: pd.DataFrame) -> dict:
    df = pilot_table(pool)
    comp = df[df["complete"]]
    rows = []
    for _, r in comp.iterrows():
        for t in WEAK:
            v = {k: int(t in r[f"{k}_tags"]) for k in ("haiku", "sonnet", "opus")}
            if len(set(v.values())) > 1:
                rows.append({"text": r["text_norm"], "tag": t, **v})
    items = pd.DataFrame(rows, columns=["text", "tag", "haiku", "sonnet", "opus"])
    counts = {t: int((items["tag"] == t).sum()) for t in WEAK}
    quota = water_fill(counts, ITEM_CAP)
    rng = np.random.default_rng(SEED + 1)
    keep = []
    for t in WEAK:
        sub = np.flatnonzero((items["tag"] == t).to_numpy())
        keep += [int(i) for i in rng.choice(sub, size=quota[t], replace=False)] if quota[t] else []
    kept = items.iloc[sorted(keep)].reset_index(drop=True)
    # blind halves: shuffle, alternate within each tag so both halves get the same tag mix, shuffle again
    kept = kept.sample(frac=1.0, random_state=SEED + 2).reset_index(drop=True)
    half = np.zeros(len(kept), dtype=int)
    for t in WEAK:
        ii = np.flatnonzero((kept["tag"] == t).to_numpy())
        flip = int(rng.integers(0, 2))
        half[ii] = (np.arange(len(ii)) + flip) % 2
    parts = []
    for h, prefix in ((0, "A"), (1, "B")):
        p = kept[half == h].sample(frac=1.0, random_state=SEED + 3 + h).reset_index(drop=True)
        p.insert(0, "item_id", [f"{prefix}{i + 1:03d}" for i in range(len(p))])
        parts.append(p)
    key = pd.concat(parts, ignore_index=True)
    for (path, p) in zip(BLIND, parts):
        write_csv_atomic(p[["item_id", "text", "tag"]], path)
        write_meta(path, {"output": f"labels/wp4b/{path.name}", "rows": int(len(p)), "created_at_utc": now_utc(),
                          "script": SCRIPT, "columns": ["item_id", "text", "tag"],
                          "blinded": "no model names and no votes; the key is pilot_key.csv",
                          "task_for_the_reader": "for each row, does the text state the tag under the recipe "
                                                 "v0.2 TAG_DEFINITIONS and TAGGING_RULES (src/vf/recipes.py)?"})
    write_csv_atomic(key[["item_id", "text", "tag", "haiku", "sonnet", "opus"]], KEY)
    # ---- pilot_labels.parquet ----------------------------------------------------
    lab = df[["pool_id", "text_norm", "stratum", "target_tag", "n_facts", "regex_tags", "shipped_tags",
              "haiku_tags", "haiku_status", "haiku_source", "sonnet_tags", "sonnet_status", "opus_tags",
              "opus_status", "complete"]].copy()
    write_parquet_atomic(lab, PILOT_LABELS)
    pos = {t: {k: int(comp[f"{k}_tags"].map(lambda s: t in s).sum()) for k in ("haiku", "sonnet", "opus")}
           for t in WEAK}
    pairs = {t: {f"{a}_vs_{b}": pair_stats(comp, a, b, t) for a, b in
                 (("haiku", "sonnet"), ("haiku", "opus"), ("sonnet", "opus"))} for t in WEAK}
    patterns_all = {t: items[items["tag"] == t].apply(lambda r: f"h{r.haiku}s{r.sonnet}o{r.opus}", axis=1)
                    .value_counts().to_dict() if counts[t] else {} for t in WEAK}
    patterns_kept = {t: key[key["tag"] == t].apply(lambda r: f"h{r.haiku}s{r.sonnet}o{r.opus}", axis=1)
                     .value_counts().to_dict() if quota[t] else {} for t in WEAK}
    cl = cost_latency()
    tot = ledger_totals()
    probe = json.loads(PROBE_FILE.read_text()) if PROBE_FILE.exists() else {}
    snap = json.loads(PROMPT_SNAPSHOT.read_text()) if PROMPT_SNAPSHOT.exists() else {}
    summary = {
        "pool_texts": int(len(df)), "complete_texts": int(len(comp)),
        "incomplete_texts": {k: int((df[f"{k}_status"] != "ok").sum()) for k in ("haiku", "sonnet", "opus")},
        "haiku_from_wp3_cache": int((df["haiku_source"] == "wp3_cache").sum()),
        "haiku_new_wp4b": int((df["haiku_source"] == "wp4b").sum()),
        "disagreements_before_cap": counts, "disagreements_total": int(len(items)),
        "kept_per_tag": quota, "kept_total": int(sum(quota.values())),
        "dropped_per_tag": {t: counts[t] - quota[t] for t in WEAK},
        "dropped_total": int(len(items) - sum(quota.values())),
        "texts_with_any_disagreement": int(items["text"].nunique()) if len(items) else 0,
        "blind_rows": {BLIND[0].name: int(len(parts[0])), BLIND[1].name: int(len(parts[1]))},
        "vote_patterns_all": patterns_all, "vote_patterns_kept": patterns_kept,
        "positives_on_complete_texts": pos, "pairwise_on_complete_texts": pairs,
    }
    meta_common = {"created_at_utc": now_utc(), **git_info(), "script": SCRIPT, "seed": SEED,
                   "recipe_version": RECIPE_VERSION, "prompt_version": PROMPT_VERSION,
                   "prompt_sha256": FROZEN_PROMPT_SHA, "weak_tags": list(WEAK), "models": MODELS}
    write_meta(KEY, {"output": "labels/wp4b/pilot_key.csv", "rows": int(len(key)), **meta_common,
                     "columns": ["item_id", "text", "tag", "haiku", "sonnet", "opus"],
                     "votes": "1 = the teacher gave the tag, 0 = it did not",
                     "item_id_prefix": {"A": BLIND[0].name, "B": BLIND[1].name},
                     "cap": ITEM_CAP, "sampling": "every (text, weak tag) where haiku, sonnet and opus do not all "
                     "agree, on texts all three labeled; per-tag quotas filled evenly (water-filling), uniform "
                     "random within tag; halves alternate within tag", **summary})
    write_meta(PILOT_LABELS, {
        "output": "labels/wp4b/pilot_labels.parquet", "rows": int(len(lab)), **meta_common,
        "columns": list(lab.columns),
        "request_settings": {"effort": snap.get("effort"), "max_tokens": snap.get("max_tokens"),
                             "batch_size": snap.get("batch_size"), "thinking": "adaptive (model default)",
                             "structured_output": "json_schema, tag enum of the 31 recipe v0.2 tags",
                             "system_prompt_cache_control": "ephemeral (5 minutes)",
                             "server_side_fallback": "off: model identity is the variable being compared"},
        "temperature_probe": probe,
        "haiku_source": "wp3_cache = labels/teacher_cache/p2 (identical to labels/teacher_facts.parquet); "
                        "wp4b = labeled in this run with the same prompt and settings",
        "cost_and_latency": cl, "spend_ledgers_usd": tot, **summary,
        "gold_texts_excluded": True,
        "note": "tags hold evidence and noise tags in recipes.ALL_TAGS order. Only the 7 weak tags are compared.",
    })
    return {"summary": summary, "cost_latency": cl, "ledgers": tot, "probe": probe}


# =============================================================================
# unblind: score the teachers against the independent readers, pick one (D050)
# =============================================================================

READS = (OUT / "pilot_read_1.csv", OUT / "pilot_read_2.csv")
PILOT_RESULT = OUT / "pilot_result.json"
TEACHERS = ("haiku", "sonnet", "opus")
#: D050, declared before any result: highest accuracy on the blinded, independently
#: read pilot disagreements; Sonnet if within 5 points of Opus. This run adds: if
#: the chosen teacher's pooled accuracy is not above Haiku's, nothing is scaled.
WITHIN_POINTS = 0.05
N_BOOT_PILOT = 10_000


def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float | None, float | None]:
    if n == 0:
        return None, None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return round(float(c - h), 4), round(float(c + h), 4)


def acc_cell(correct: np.ndarray) -> dict:
    k, n = int(correct.sum()), int(len(correct))
    lo, hi = wilson(k, n)
    return {"correct": k, "n": n, "accuracy": round(k / n, 4) if n else None, "wilson95": [lo, hi]}


def paired_boot(a: np.ndarray, b: np.ndarray, groups: np.ndarray, seed: int) -> dict:
    """Accuracy of a minus b on the same items; bootstrap resamples items within tag."""
    rng = np.random.default_rng(seed)
    d = a.astype(float) - b.astype(float)
    idx_by = [np.flatnonzero(groups == g) for g in sorted(set(groups))]
    boots = np.empty(N_BOOT_PILOT)
    for i in range(N_BOOT_PILOT):
        s = np.concatenate([rng.choice(ix, size=len(ix), replace=True) for ix in idx_by])
        boots[i] = d[s].mean()
    return {"difference": round(float(d.mean()), 4),
            "boot95": [round(float(np.percentile(boots, 2.5)), 4), round(float(np.percentile(boots, 97.5)), 4)],
            "a_right_b_wrong": int(((a == 1) & (b == 0)).sum()), "a_wrong_b_right": int(((a == 0) & (b == 1)).sum())}


def stage_unblind() -> dict:
    key = read_csv(KEY)
    reads = []
    for i, p in enumerate(READS, start=1):
        r = read_csv(p)
        if list(r.columns)[:2] != ["item_id", "present"]:
            raise SystemExit(f"{p.name}: unexpected columns {list(r.columns)}")
        r["reader"] = f"reader_{i}"
        reads.append(r)
    rd = pd.concat(reads, ignore_index=True)
    if rd["item_id"].duplicated().any():
        raise SystemExit("an item was read twice; this design has one reader per item")
    if not rd["present"].isin(["0", "1"]).all():
        raise SystemExit("present must be 0 or 1")
    df = key.merge(rd, on="item_id", how="left", validate="one_to_one")
    if df["present"].isna().any():
        raise SystemExit(f"{int(df['present'].isna().sum())} key items have no reading")
    extra = set(rd["item_id"]) - set(key["item_id"])
    if extra:
        raise SystemExit(f"{len(extra)} readings have no key item")
    # half A went to reader 1 and half B to reader 2, as written by stage_items
    wrong_half = ((df["item_id"].str[0] == "A") != (df["reader"] == "reader_1")).sum()
    if wrong_half:
        raise SystemExit(f"{wrong_half} items were read by the reader of the other half")
    y = df["present"].astype(int).to_numpy()
    votes = {k: df[k].astype(int).to_numpy() for k in TEACHERS}
    right = {k: (votes[k] == y).astype(int) for k in TEACHERS}
    tags = df["tag"].to_numpy()
    per_tag = {}
    for t in WEAK:
        m = tags == t
        per_tag[t] = {"items": int(m.sum()), "reader_positive": int(y[m].sum()),
                      **{k: acc_cell(right[k][m]) for k in TEACHERS}}
    overall = {k: acc_cell(right[k]) for k in TEACHERS}
    by_reader = {}
    for rname in ("reader_1", "reader_2"):
        m = (df["reader"] == rname).to_numpy()
        by_reader[rname] = {"items": int(m.sum()), "reader_positive": int(y[m].sum()),
                            **{k: acc_cell(right[k][m]) for k in TEACHERS}}
    # how the readers resolved each vote pattern
    pat = np.array([f"h{a}s{b}o{c}" for a, b, c in zip(votes["haiku"], votes["sonnet"], votes["opus"])])
    patterns = {p: {"items": int((pat == p).sum()), "reader_yes": int(y[pat == p].sum())}
                for p in sorted(set(pat), key=lambda s: -int((pat == s).sum()))}
    # positive-call precision and recall of each teacher on these items (reader as reference)
    pr = {}
    for k in TEACHERS:
        v = votes[k]
        tp, fp, fn = int(((v == 1) & (y == 1)).sum()), int(((v == 1) & (y == 0)).sum()), int(((v == 0) & (y == 1)).sum())
        pr[k] = {"said_yes": int(v.sum()), "yes_right": tp, "yes_wrong": fp, "missed": fn,
                 "precision": round(tp / (tp + fp), 4) if tp + fp else None,
                 "recall": round(tp / (tp + fn), 4) if tp + fn else None}
    diffs = {"opus_minus_sonnet": paired_boot(right["opus"], right["sonnet"], tags, SEED + 10),
             "sonnet_minus_haiku": paired_boot(right["sonnet"], right["haiku"], tags, SEED + 11),
             "opus_minus_haiku": paired_boot(right["opus"], right["haiku"], tags, SEED + 12)}
    a = {k: overall[k]["accuracy"] for k in TEACHERS}
    # ---- D050 rule ------------------------------------------------------------
    if a["sonnet"] >= a["opus"]:
        chosen, why = "sonnet", f"Sonnet's accuracy {a['sonnet']:.3f} is at least Opus's {a['opus']:.3f}"
    elif a["opus"] - a["sonnet"] <= WITHIN_POINTS + 1e-12:
        chosen, why = "sonnet", (f"Opus {a['opus']:.3f} is higher but within {WITHIN_POINTS * 100:.0f} points of "
                                 f"Sonnet {a['sonnet']:.3f} (gap {100 * (a['opus'] - a['sonnet']):.1f} points)")
    else:
        chosen, why = "opus", (f"Opus {a['opus']:.3f} beats Sonnet {a['sonnet']:.3f} by "
                               f"{100 * (a['opus'] - a['sonnet']):.1f} points, more than {WITHIN_POINTS * 100:.0f}")
    beats_haiku = a[chosen] > a["haiku"]
    scale = bool(beats_haiku)
    cl = json.loads(PILOT_LABELS.with_name(PILOT_LABELS.name + ".meta.json").read_text()).get("cost_and_latency", {})
    notes = rd.set_index("item_id")["note"] if "note" in rd.columns else pd.Series(dtype=str)
    hard = df["item_id"].map(lambda i: "hard call" in str(notes.get(i, "")).lower()).to_numpy()
    hard_cells = {k: {"hard_call": acc_cell(right[k][hard]), "other": acc_cell(right[k][~hard])} for k in TEACHERS}
    res = {
        "created_at_utc": now_utc(), **git_info(), "script": SCRIPT, "stage": "unblind",
        "inputs": {p.name: W3.sha256_file(p) for p in (KEY, *READS)},
        "design": {
            "items": int(len(df)),
            "what_an_item_is": "a (text, weak tag) pair where claude-haiku-5-5, claude-sonnet-5-5 and "
                               "claude-opus-5-5 (prompt p2) do not all agree; 269 of 269 such pairs on 600 pool texts",
            "readers": "two independent agent readers, one per half (A items reader 1, B items reader 2), "
                       "each working from the tag definitions and rules only, blind to model names and votes. "
                       "No item was read by both, so inter-reader agreement cannot be measured here.",
            "reference": "the reader's present (0 or 1) is taken as correct",
            "gold_used": False,
            "intervals": "Wilson 95% for each accuracy; paired differences by bootstrap over items within tag "
                         f"({N_BOOT_PILOT:,} resamples)",
        },
        "rule": {"source": "D050 (declared before any result)",
                 "text": "highest accuracy on the blinded, independently read pilot disagreements; Sonnet if "
                         "within 5 points of Opus",
                 "this_run": "if the chosen teacher's pooled accuracy is not above Haiku's, skip scaling and set "
                             "merged_labels.parquet to the Haiku labels",
                 "within_points": WITHIN_POINTS},
        "overall": overall,
        "per_tag": per_tag,
        "by_reader": by_reader,
        "paired_differences": diffs,
        "positive_calls_vs_reader": pr,
        "vote_patterns": patterns,
        "hard_call_split": {"items_marked_hard_call": int(hard.sum()), **hard_cells},
        "choice": {"teacher": chosen, "model": MODELS[chosen], "why": why,
                   "beats_haiku": bool(beats_haiku), "haiku_accuracy": a["haiku"], "scale": scale},
        "pilot_cost_and_latency": {m: {k: v.get(k) for k in ("requests", "cost_usd", "cost_per_1000_texts_usd",
                                                             "seconds_per_request_40_texts",
                                                             "texts_per_minute_at_workers")}
                                   for m, v in cl.items() if isinstance(v, dict) and "requests" in v},
        "caveats": [
            "Accuracy is measured only on items where the three teachers disagree, so it ranks the teachers "
            "where it matters for the weak tags; it is not an overall accuracy.",
            "Each item has one agent reader. The readers are another model's reading of the same definitions, "
            "not clinicians.",
            "Per-tag cells have 13 to 29 items; most per-tag intervals overlap.",
        ],
    }
    write_text_atomic(PILOT_RESULT, json.dumps(res, indent=2, ensure_ascii=False) + "\n")
    return res


def print_unblind(res: dict) -> None:
    print(f"\n[unblind] {res['design']['items']} items; accuracy against the readers (Wilson 95%)")
    hdr = f"  {'tag':<13}{'n':>4}{'yes':>5}  " + "  ".join(f"{k:<22}" for k in TEACHERS)
    print(hdr)
    rows = [(t, res["per_tag"][t]) for t in WEAK] + [("ALL", {**res["overall"], "items": res["design"]["items"],
                                                               "reader_positive": sum(res["per_tag"][t]["reader_positive"] for t in WEAK)})]
    for t, c in rows:
        cells = []
        for k in TEACHERS:
            x = c[k]
            cells.append(f"{x['correct']:>3}/{x['n']:<3} {x['accuracy']:.2f} ({x['wilson95'][0]:.2f}-{x['wilson95'][1]:.2f})")
        print(f"  {t:<13}{c['items']:>4}{c['reader_positive']:>5}  " + "  ".join(f"{s:<22}" for s in cells))
    for k, d in res["paired_differences"].items():
        print(f"  {k}: {d['difference']:+.3f} (95% {d['boot95'][0]:+.3f} to {d['boot95'][1]:+.3f})")
    ch = res["choice"]
    print(f"[unblind] choice: {ch['model']} ({ch['why']}); beats Haiku: {ch['beats_haiku']}; scale: {ch['scale']}")


# =============================================================================
# scale: the chosen teacher labels the weak-tag pool
# =============================================================================

SCALE_CAP = 12_000           # texts in the pool, the 600 pilot texts included
SCALE_RANDOM = 2_000
SCALE_SEED = SEED + 100
SCALE_POOL = CACHE_ROOT / "scale_pool.csv"
STRONG = OUT / "teacher_strong_facts.parquet"
MERGED = OUT / "merged_labels.parquet"
STUDENT_1B = paths.data_dir() / "models" / "wp4_student" / "student_1b.pkl"
WP4_SPLIT_SEED, WP4_VAL_FRAC, WP4_SPLIT_COUNTS = 20261121, 0.2, {"train": 23441, "val": 5859}
NEW_VAL_SALT = "wp4b-split-v1"
#: labeling order: when the budget stops the run, the later strata are the ones cut
STRATA = ("pilot", "disagree_in_wp4_set", "disagree_new", "near_miss", "agree", "random")


def student_1b_weak(texts: list[str]) -> dict[str, np.ndarray]:
    """The saved WP4 student 1b (all 24 tags, not only the 4 it ships on), weak tags only."""
    import pickle
    import scipy.sparse as sp
    b = pickle.loads(STUDENT_1B.read_bytes())
    parts = [b["word_vectorizer"].transform(texts), b["char_vectorizer"].transform(texts)]
    if b["use_regex_features"]:
        R = np.array([[t in set(s) for t in b["tags"]] for s in recipes.tag_facts(texts)], dtype=np.float64)
        parts.append(sp.csr_matrix(R * b["regex_feature_weight"]))
    X = sp.hstack(parts, format="csr")
    return {t: b["models"][t].predict_proba(X)[:, 1] >= b["thresholds"][t] for t in WEAK}


def wp4_split() -> dict[str, str]:
    """text_norm -> train/validation exactly as scripts/wp4_student.py split() draws it."""
    t = pd.read_parquet(LABELS / "teacher_facts.parquet", columns=["text_norm"]).reset_index(drop=True)
    is_val = np.random.default_rng(WP4_SPLIT_SEED).random(len(t)) < WP4_VAL_FRAC
    got = {"train": int((~is_val).sum()), "val": int(is_val.sum())}
    if got != WP4_SPLIT_COUNTS:
        raise SystemExit(f"WP4 split does not reproduce: {got} vs {WP4_SPLIT_COUNTS}")
    return {x: ("validation" if v else "train") for x, v in zip(t["text_norm"], is_val)}


def new_text_split(text: str) -> str:
    """Texts outside the WP4 teacher set: a fixed hash draw at the same 20% validation rate."""
    h = int(hashlib.sha256(f"{NEW_VAL_SALT}|{text}".encode()).hexdigest()[:12], 16) / 16 ** 12
    return "validation" if h < WP4_VAL_FRAC else "train"


def stage_scale_pool(force: bool = False) -> pd.DataFrame:
    if SCALE_POOL.exists() and not force:
        pool = read_csv(SCALE_POOL)
        print(f"[scale] {len(pool):,} texts already in {SCALE_POOL.name}; reusing")
        return pool
    u = load_texts()
    excl, ex_counts = exclusions(u["text_norm"])
    c = u[~excl].reset_index(drop=True)
    texts = c["text_norm"].tolist()
    t0 = time.time()
    rx_all = [set(x) for x in recipes.tag_facts(texts)]
    st = student_1b_weak(texts)
    print(f"[scale] regex v0.2 and student 1b over {len(c):,} candidate texts in {time.time() - t0:.0f}s")
    hk = haiku_wp3_labels()
    has_h = c["text_norm"].isin(hk).to_numpy()
    pilot = set(read_csv(POOL)["text_norm"])
    in_pilot = c["text_norm"].isin(pilot).to_numpy()
    n = len(c)
    hit = np.zeros(n, dtype=bool)
    dis = np.zeros(n, dtype=bool)
    loose = np.zeros(n, dtype=bool)
    hit_by_tag, per_tag_counts = {}, {}
    for t in WEAK:
        rx = np.array([t in s for s in rx_all])
        sh = c["shipped"].map(lambda s, t=t: t in s).to_numpy()
        hv = np.array([x in hk and t in hk[x] for x in texts])
        s1 = st[t]
        h_t = rx | sh | s1 | hv
        d_t = h_t & ((rx != s1) | (rx != sh) | (has_h & ((hv != rx) | (hv != s1))))
        l_t = c["text_norm"].str.contains(LOOSE[t], regex=True).to_numpy()
        hit |= h_t
        dis |= d_t
        loose |= l_t
        hit_by_tag[t] = h_t
        per_tag_counts[t] = {"regex": int(rx.sum()), "shipped_mix": int(sh.sum()), "student_1b": int(s1.sum()),
                             "haiku_wp3": int(hv.sum()), "any": int(h_t.sum()), "disagree": int(d_t.sum()),
                             "loose": int(l_t.sum())}
    near = loose & ~hit
    agree = hit & ~dis
    stratum = np.full(n, "", dtype=object)
    stratum[in_pilot] = "pilot"
    for name, m in (("disagree_in_wp4_set", dis & has_h), ("disagree_new", dis & ~has_h), ("near_miss", near)):
        stratum[(stratum == "") & m] = name
    rng = np.random.default_rng(SCALE_SEED)
    rest = np.flatnonzero((stratum == "") & ~hit & ~near)
    n_fixed = int((stratum != "").sum())
    n_random = min(SCALE_RANDOM, len(rest), max(0, SCALE_CAP - n_fixed))
    pick_r = rng.choice(rest, size=n_random, replace=False)
    stratum[pick_r] = "random"
    # agreement hits fill what is left, evenly over the weak tags (each text counted under its rarest tag)
    room = max(0, SCALE_CAP - int((stratum != "").sum()))
    freq = {t: int(hit_by_tag[t].sum()) for t in WEAK}
    order = sorted(WEAK, key=lambda t: (freq[t], t))
    a_idx = np.flatnonzero(agree & (stratum == ""))
    rare = np.array([next(t for t in order if hit_by_tag[t][i]) for i in a_idx], dtype=object)
    counts = {t: int((rare == t).sum()) for t in WEAK}
    quota = water_fill(counts, room)
    for t in WEAK:
        sub = a_idx[rare == t]
        if quota[t]:
            stratum[rng.choice(sub, size=quota[t], replace=False)] = "agree"
    # if the fixed strata alone pass the cap, cut from the end of the labeling order
    sel = np.flatnonzero(stratum != "")
    if len(sel) > SCALE_CAP:
        keep = []
        for s in STRATA:
            ii = sel[stratum[sel] == s]
            rng.shuffle(ii)
            keep += list(ii[:max(0, SCALE_CAP - len(keep))])
        drop = np.setdiff1d(sel, np.array(keep, dtype=int))
        stratum[drop] = ""
    df = c.loc[stratum != "", ["text_norm", "n_facts"]].copy()
    df["stratum"] = stratum[stratum != ""]
    df["in_wp4_teacher_set"] = has_h[stratum != ""]
    for t in WEAK:
        df[f"any_{t}"] = hit_by_tag[t][stratum != ""]
    # labeling order: strata in STRATA order, shuffled within
    df["_o"] = df["stratum"].map({s: i for i, s in enumerate(STRATA)})
    df = df.sample(frac=1.0, random_state=SCALE_SEED).sort_values("_o", kind="stable").drop(columns="_o")
    df.insert(0, "scale_id", [f"S{i + 1:05d}" for i in range(len(df))])
    assert not exclusions(df["text_norm"])[0].any(), "an excluded text reached the scale pool"
    assert df["text_norm"].is_unique
    out = df.copy()
    for col in out.columns:
        if out[col].dtype == bool:
            out[col] = out[col].astype(int)
    write_csv_atomic(out, SCALE_POOL)
    avail = {"weak_hit_any_method": int(hit.sum()), "disagree": int(dis.sum()),
             "disagree_in_wp4_set": int((dis & has_h).sum()), "disagree_new": int((dis & ~has_h).sum()),
             "agree": int(agree.sum()), "near_miss": int(near.sum()), "rest_for_random": int(len(rest)),
             "pilot_texts": int(in_pilot.sum())}
    write_meta(SCALE_POOL, {
        "output": "labels/wp4b/teacher_cache/scale_pool.csv", "rows": int(len(out)), "created_at_utc": now_utc(),
        **git_info(), "script": SCRIPT, "seed": SCALE_SEED, "cap": SCALE_CAP,
        "design": {
            "pilot": "the 600 pilot texts (already labeled by every teacher; no new cost)",
            "disagree_in_wp4_set": "a weak tag is given by at least one of regex v0.2, the shipped mix "
                                   "(gold/fact_tags.parquet), student 1b (all tags, data/models/wp4_student/"
                                   "student_1b.pkl) or Haiku p2, and they do not all agree; the text is in the WP4 "
                                   "teacher set, so its new label replaces a Haiku training label",
            "disagree_new": "the same, for texts outside the WP4 teacher set (no Haiku vote)",
            "near_miss": "a LOOSE pattern matches and no method gives any weak tag",
            "agree": "every method that voted gives the same weak tags; fills the room left under the cap, evenly "
                     "over the weak tags, each text under its rarest weak tag",
            "random": "uniform over texts with no weak-tag signal and no loose match",
            "labeling_order": list(STRATA),
            "loose_patterns": LOOSE,
        },
        "why_capped": "every weak-tag hit (16,322 texts) plus near misses and 2,000 random is about 19,600 texts, "
                      "past the ~12,000 the run asked for and close to the whole remaining run budget at Opus "
                      "prices; disagreements and near misses are kept whole and agreement hits are sampled",
        "available": avail, "per_tag_signals": per_tag_counts,
        "agree_quota_per_tag": quota, "agree_available_per_tag": counts,
        "drawn": out["stratum"].value_counts().to_dict(),
        "candidates": {"distinct_texts": int(len(u)), "after_exclusions": int(len(c)), "excluded": ex_counts},
        "gold_texts_excluded": True,
        "inputs": {"silver/facts.parquet": W3.sha256_file(paths.silver_dir() / "facts.parquet"),
                   "gold/fact_tags.parquet": W3.sha256_file(paths.gold_dir() / "fact_tags.parquet"),
                   "labels/teacher_facts.parquet": W3.sha256_file(LABELS / "teacher_facts.parquet"),
                   "models/wp4_student/student_1b.pkl": W3.sha256_file(STUDENT_1B),
                   "labels/gold_facts.csv (exclusion only)": W3.sha256_file(LABELS / "gold_facts.csv")},
    })
    print(f"[scale] pool {len(out):,} -> {SCALE_POOL}: {out['stratum'].value_counts().to_dict()}")
    return df


def chosen_teacher() -> dict:
    if not PILOT_RESULT.exists():
        raise SystemExit("run --stage unblind first")
    return json.loads(PILOT_RESULT.read_text())["choice"]


def stage_scale(workers: int, run_cap: float, project_cap: float, repool: bool) -> dict:
    ch = chosen_teacher()
    if not ch["scale"]:
        print(f"[scale] {ch['model']} does not beat Haiku on the pilot; nothing is scaled")
        return {"skipped": True}
    model = ch["model"]
    pool = stage_scale_pool(force=repool)
    freeze_prompt()
    probe = json.loads(PROBE_FILE.read_text())
    temperature = 0.0 if probe.get(model, {}).get("temperature_0_accepted") else None
    budget = Budget(run_cap, project_cap)
    client = make_client()
    st = label_texts(client, model, pool["text_norm"].tolist(), budget=budget, workers=workers, purpose="scale",
                     temperature=temperature)
    write_strong(pool, model)
    return st


def write_strong(pool: pd.DataFrame, model: str) -> pd.DataFrame:
    idx = cache_index(model)
    df = pool[["scale_id", "text_norm", "stratum", "n_facts", "in_wp4_teacher_set"]].copy()
    df["n_facts"] = df["n_facts"].astype(int)
    df["in_wp4_teacher_set"] = df["in_wp4_teacher_set"].astype(str).isin(["1", "True", "true"])
    df["status"] = df["text_norm"].map(lambda t: idx[t]["status"] if t in idx else "not_labeled")
    df["tags"] = df["text_norm"].map(lambda t: order_tags(idx[t]["tags"]) if t in idx and idx[t]["status"] == "ok"
                                     else [])
    df["model"] = df["text_norm"].map(lambda t: idx[t]["model"] if t in idx else None)
    df["request_id"] = df["text_norm"].map(lambda t: idx[t]["request_id"] if t in idx else None)
    df["prompt_version"] = PROMPT_VERSION
    lab = df[df["status"] == "ok"]
    # agreement with the Haiku teacher on texts both labeled, per evidence tag
    hk = haiku_wp3_labels()
    both = lab[lab["text_norm"].isin(hk)]
    agree = {}
    for t in recipes.EVIDENCE_TAGS if hasattr(recipes, "EVIDENCE_TAGS") else recipes.ALL_TAGS:
        a = both["tags"].map(lambda s: t in s).to_numpy()
        h = both["text_norm"].map(lambda x: t in hk[x]).to_numpy()
        agree[t] = {"both": int((a & h).sum()), "strong_only": int((a & ~h).sum()), "haiku_only": int((~a & h).sum())}
    cov = {}
    for s in STRATA:
        stt = df.loc[df["stratum"] == s, "status"]
        if len(stt):
            cov[s] = {"pool": int(len(stt)), "labeled": int((stt == "ok").sum()),
                      "failed": int(stt.str.startswith("failed").sum()), "not_labeled": int((stt == "not_labeled").sum())}
    rows = [r for r in read_ledger(LEDGER) if r.get("model") == model]
    spend = {p: round(sum(r["cost_usd"] for r in rows if r.get("purpose") == p), 4) for p in ("pilot", "scale")}
    tok = {k: int(sum(r["usage"].get(k, 0) for r in rows if r.get("purpose") == "scale")) for k in
           ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")}
    n_scale = int(sum(r["n_texts"] for r in rows if r.get("purpose") == "scale"))
    write_parquet_atomic(df, STRONG)
    write_meta(STRONG, {
        "output": "labels/wp4b/teacher_strong_facts.parquet", "rows": int(len(df)), "labeled_ok": int(len(lab)),
        "created_at_utc": now_utc(), **git_info(), "script": SCRIPT, "model": model,
        "chosen_by": "pilot_result.json (D050 rule on blinded, independently read pilot disagreements)",
        "prompt_version": PROMPT_VERSION, "prompt_sha256": FROZEN_PROMPT_SHA, "recipe_version": RECIPE_VERSION,
        "request_settings": {"effort": W3.EFFORT, "max_tokens": W3.MAX_TOKENS, "batch_size": W3.BATCH_SIZE,
                             "thinking": "adaptive (model default)", "temperature": "model default (0 rejected)",
                             "structured_output": "json_schema, tag enum of the 31 recipe v0.2 tags",
                             "system_prompt_cache_control": "ephemeral (5 minutes)",
                             "server_side_fallback": "off: a refused text is split out and cached as failed, "
                                                     "and merged_labels falls back to Haiku for it"},
        "columns": list(df.columns),
        "status": "ok = labeled; failed:<stop_reason> = refused or unusable as a single text; not_labeled = "
                  "the run stopped (budget) before reaching it",
        "coverage_by_stratum": cov,
        "spend_usd": {**spend, "scale_tokens": tok, "scale_texts_sent_incl_retries": n_scale,
                      "scale_cost_per_1000_texts": round(1000 * spend["scale"] / n_scale, 4) if n_scale else None},
        "agreement_with_haiku_on_shared_texts": {"texts": int(len(both)), "per_tag": agree},
        "positives_per_weak_tag": {t: int(lab["tags"].map(lambda s, t=t: t in s).sum()) for t in WEAK},
        "spend_ledgers_usd": ledger_totals(),
        "gold_texts_excluded": True,
    })
    print(f"[scale] {STRONG.name}: {len(lab):,} of {len(df):,} labeled; coverage {cov}")
    return df


# =============================================================================
# merge
# =============================================================================

def stage_merge() -> pd.DataFrame:
    ch = chosen_teacher()
    hk = pd.read_parquet(LABELS / "teacher_facts.parquet", columns=["text_norm", "tags", "model", "prompt_version"])
    assert (hk["prompt_version"] == PROMPT_VERSION).all()
    split = wp4_split()
    rows = {t: {"text_norm": t, "tags": order_tags(x), "source": m, "split": split[t],
                "in_wp4_teacher_set": True, "wp4b_stratum": ""}
            for t, x, m in zip(hk["text_norm"], hk["tags"], hk["model"])}
    n_haiku = len(rows)
    replaced = added = haiku_wp4b = 0
    strong_model = None
    if ch["scale"] and STRONG.exists():
        sf = pd.read_parquet(STRONG)
        strong_model = ch["model"]
        if set(sf["model"].dropna()) - {strong_model}:
            raise SystemExit("teacher_strong_facts.parquet holds a model other than the chosen one")
        for t, x, s, z in zip(sf["text_norm"], sf["tags"], sf["status"], sf["stratum"]):
            if s != "ok":
                if t in rows:
                    rows[t]["wp4b_stratum"] = z
                continue
            if t in rows:
                replaced += 1
                rows[t].update(tags=order_tags(x), source=strong_model, wp4b_stratum=z)
            else:
                added += 1
                rows[t] = {"text_norm": t, "tags": order_tags(x), "source": strong_model, "split": new_text_split(t),
                           "in_wp4_teacher_set": False, "wp4b_stratum": z}
        # pilot texts Haiku labeled in this run (none should be left once the strong teacher covers them)
        hidx = cache_index(MODELS["haiku"])
        for t, v in hidx.items():
            if t not in rows and v["status"] == "ok":
                haiku_wp4b += 1
                rows[t] = {"text_norm": t, "tags": order_tags(v["tags"]), "source": MODELS["haiku"],
                           "split": new_text_split(t), "in_wp4_teacher_set": False, "wp4b_stratum": "pilot"}
    df = pd.DataFrame(list(rows.values()),
                      columns=["text_norm", "tags", "source", "split", "in_wp4_teacher_set", "wp4b_stratum"])
    df = df.sort_values("text_norm", kind="stable").reset_index(drop=True)
    # checks: no gold text, unique texts, WP4 validation texts still in validation.
    # labels/teacher_facts.parquet (WP3) excluded exact gold texts only; a few of its texts equal a gold text
    # after lowercasing and dropping punctuation. Gold is evaluation-only, so those are dropped here.
    # Prompt-example texts are ordinary non-gold silver facts and stay.
    assert df["text_norm"].is_unique
    gold = W3.load_gold()
    g_exact = set(gold["text_norm"])
    g_loose = {loose_norm(t) for t in g_exact}
    is_exact = df["text_norm"].isin(g_exact)
    if is_exact.any():
        raise SystemExit(f"{int(is_exact.sum())} exact gold texts are in the merged labels")
    near_gold = df["text_norm"].map(lambda t: loose_norm(t) in g_loose)
    dropped_near_gold = df.loc[near_gold, ["text_norm", "source", "split"]].to_dict("records")
    df = df[~near_gold].reset_index(drop=True)
    n_examples = int(df["text_norm"].isin({t for t, _ in W3.EXAMPLES}).sum())
    wp4v = {t for t, s in split.items() if s == "validation"}
    moved = int((df["text_norm"].isin(wp4v) & (df["split"] != "validation")).sum())
    if moved:
        raise SystemExit(f"{moved} WP4 validation texts left validation")
    write_parquet_atomic(df, MERGED)
    by = df.groupby(["source", "split"]).size().unstack(fill_value=0)
    pos = {}
    for t in WEAK:
        has = df["tags"].map(lambda s, t=t: t in s)
        pos[t] = {src: int(has[df["source"] == src].sum()) for src in sorted(df["source"].unique())}
    hk_map = {t: order_tags(x) for t, x in zip(hk["text_norm"], hk["tags"])}
    changed = {}
    for t in WEAK:
        r = df[(df["source"] == strong_model) & df["in_wp4_teacher_set"]] if strong_model else df.iloc[:0]
        a = r["tags"].map(lambda s, t=t: t in s).to_numpy()
        h = r["text_norm"].map(lambda x, t=t: t in hk_map[x]).to_numpy()
        changed[t] = {"added": int((a & ~h).sum()), "removed": int((~a & h).sum())}
    write_meta(MERGED, {
        "output": "labels/wp4b/merged_labels.parquet", "rows": int(len(df)), "created_at_utc": now_utc(),
        **git_info(), "script": SCRIPT,
        "rule": "for each text, the chosen stronger teacher's tags where it labeled the text (status ok), else the "
                "Haiku p2 teacher's (labels/teacher_facts.parquet)" + ("" if ch["scale"] else
                "; the stronger teacher did not beat Haiku on the pilot, so this file equals the Haiku labels"),
        "chosen_teacher": ch,
        "columns": {"text_norm": "distinct fact text", "tags": "teacher tags (evidence and noise) in recipes.ALL_TAGS "
                    "order", "source": "model that gave the tags", "split": "train or validation",
                    "in_wp4_teacher_set": "text is one of the 29,300 WP4 teacher texts",
                    "wp4b_stratum": "scale-pool stratum (teacher_cache/scale_pool.csv) or empty; 'pilot' marks the "
                                    "600 pilot texts, 220 of which hold the 269 reader-labeled items: drop them from "
                                    "training if the reader labels are to serve as an untouched check"},
        "split_rule": "WP4 teacher texts keep their WP4 split exactly (scripts/wp4_student.py split(): "
                      "default_rng(20261121).random(29300) < 0.2 in teacher_facts.parquet row order; 5,859 "
                      "validation). New texts: validation when sha256('" + NEW_VAL_SALT + "|' + text_norm) as a "
                      "fraction is below 0.2.",
        "counts": {"haiku_texts": n_haiku, "replaced_by_strong": replaced, "added_by_strong": added,
                   "haiku_wp4b_pilot_only": haiku_wp4b,
                   "by_source_and_split": {s: {k: int(v) for k, v in by.loc[s].items()} for s in by.index}},
        "weak_tag_positives_by_source": pos,
        "weak_tag_changes_on_replaced_texts": changed,
        "gold_texts_excluded": True,
        "gold_near_duplicates_dropped": {
            "n": len(dropped_near_gold),
            "why": "equal to a gold text after lowercasing and dropping punctuation; they came in through "
                   "labels/teacher_facts.parquet, which WP3 filtered on exact gold matches only (so the shipped WP4 "
                   "student was trained or validated on them)",
            "texts": dropped_near_gold},
        "prompt_example_texts_kept": n_examples,
        "inputs": {"labels/teacher_facts.parquet": W3.sha256_file(LABELS / "teacher_facts.parquet"),
                   **({"labels/wp4b/teacher_strong_facts.parquet": W3.sha256_file(STRONG)} if STRONG.exists() else {})},
        "note": "Teacher labels are noisy targets. The stronger teacher was chosen on 269 pilot disagreements read "
                "by two agent readers, not on gold. Any student trained on this file still faces the D050 per-tag "
                "ship rule on gold.",
    })
    print(f"[merge] {MERGED.name}: {len(df):,} texts; Haiku {n_haiku:,}, replaced {replaced:,}, added {added:,}, "
          f"Haiku pilot-only {haiku_wp4b}; by source and split:\n{by.to_string()}")
    return df


# =============================================================================

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stage", choices=["pool", "label", "items", "all", "unblind", "scale", "merge"],
                    default="all", help="all = pool, label, items (the pilot); unblind, scale and merge run alone")
    ap.add_argument("--cap-usd", type=float, default=RUN_CAP_USD, help="cap on the WP4b ledger (D050: 12)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--repool", action="store_true", help="redraw the pilot pool (or, with --stage scale, the scale pool)")
    ap.add_argument("--pool-only", action="store_true", help="with --stage scale: draw the scale pool, no API calls")
    args = ap.parse_args()
    env = W3.read_env()
    project_cap = float(env.get("VF_LLM_BUDGET_USD", "30")) - SEALED_RESERVE_USD
    print(f"WP4b pilot: prompt {PROMPT_VERSION} ({FROZEN_PROMPT_SHA[:12]}), run cap ${args.cap_usd:.2f}, project cap "
          f"${project_cap:.2f} after the ${SEALED_RESERVE_USD:.0f} sealed-eval reserve; ledgers {ledger_totals()}")
    if args.stage == "unblind":
        print_unblind(stage_unblind())
        return 0
    if args.stage == "scale":
        if args.pool_only:
            stage_scale_pool(force=args.repool)
            return 0
        st = stage_scale(args.workers, args.cap_usd, project_cap, args.repool)
        print(f"[scale] {json.dumps(st)}")
        print(f"[spend] ledgers {ledger_totals()}")
        return 0
    if args.stage == "merge":
        stage_merge()
        return 0
    pool = stage_pool(force=args.repool)
    if args.stage == "pool":
        return 0
    if args.stage in ("label", "all"):
        stage_label(pool, args.workers, args.cap_usd, project_cap)
    if args.stage in ("items", "all"):
        r = stage_items(pool)
        s = r["summary"]
        print(f"\n[items] pool {s['pool_texts']} texts, {s['complete_texts']} labeled by all three")
        print(f"[items] disagreements per tag: {s['disagreements_before_cap']} (total {s['disagreements_total']})")
        print(f"[items] kept {s['kept_total']} ({s['kept_per_tag']}), dropped {s['dropped_total']}")
        print(f"[items] blind files {s['blind_rows']} -> {BLIND[0].parent}")
        for m, v in r["cost_latency"].items():
            print(f"[cost] {m}: {json.dumps({k: v.get(k) for k in ('requests', 'cost_usd', 'cost_per_1000_texts_usd', 'seconds_per_request_40_texts', 'texts_per_minute_at_workers') if k in v})}")
        print(f"[spend] ledgers {r['ledgers']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
