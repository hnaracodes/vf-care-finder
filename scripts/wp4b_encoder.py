#!/usr/bin/env python
"""WP4b student 2: small sentence encoders as fact taggers (D050, candidate files only).

Variants, each trained on a labels file (text_norm, tags) and scored on the 24
evidence tags of recipe v0.2 (vf.recipes.EVIDENCE_TAGS):

  2a  frozen sentence embeddings (BAAI/bge-small-en-v1.5, optional one
      alternative encoder) + one logistic regression per tag on standardized
      embeddings. Per tag, C and the decision threshold are tuned on validation
      F1. If two encoders are given, the one with the higher validation macro F1
      is kept (validation labels only).
  2b  BAAI/bge-small-en-v1.5 fine-tuned for multi-label classification: CLS
      vector, dropout, one linear layer, BCE with logits (pos_weight
      sqrt(neg/pos) clipped to [1, 10]), max length 64, AdamW with linear
      warmup and decay, early stopping on validation macro F1 at tuned
      thresholds, on the Apple MPS GPU. Thresholds tuned on validation.

Split: the WP4 split (scripts/wp4_student.py, seed 20261121, by text_norm). The
WP4 validation texts are frozen once in data/models/wp4b_encoder/wp4_split.json;
validation is every labeled text in that list, and every other labeled text
trains, so validation texts are identical across label files. A labels file
holding a gold text is refused.

Gold (labels/gold_facts.csv) is never used for training, C, thresholds, epochs
or encoder choice. --report scores the saved outputs on gold for REPORTING
only, after every choice has been written to the variant's summary.json.

Writes, per variant, data/models/wp4b_encoder/<run>_<variant>/:
  scores.parquet (+ .meta.json)  every distinct silver text_norm, p_<tag> score
                                 (float32) per evidence tag, and tags (list)
  summary.json                   settings, per-tag C/threshold, validation, timings
  model files                    2a: lr.pkl (scaler + per-tag sklearn models)
                                 2b: encoder/ (HF save_pretrained), head.pt
Shared: data/models/wp4b_encoder/emb_cache/ (frozen-embedding cache, float16).
Report (--report PATH): validation against the labels, gold per-tag F1
(reporting only), reference numbers from gold/model_a_report.json, timings,
and the LLM spend ledgers (this script makes no LLM calls).

Run with the separate ML venv (torch, transformers, sentence-transformers):
  .venv-ml/bin/python scripts/wp4b_encoder.py --run r1 --variants 2a,2b \\
      --report data/labels/wp4b/encoder_round1.json
  .venv-ml/bin/python scripts/wp4b_encoder.py --run r1 --report-only \\
      --report data/labels/wp4b/encoder_round1.json        # rebuild the report
  add --limit 3000 --out-dir <scratch> for a smoke test
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import pickle
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pyarrow as pa  # noqa: E402
import pyarrow.parquet as pq  # noqa: E402

from vf import paths, recipes  # noqa: E402

SCRIPT = "scripts/wp4b_encoder.py"
VERSION = "wp4b-enc-1"
SEED = 20261121
TAGS = list(recipes.EVIDENCE_TAGS)
MODEL_ROOT = paths.data_dir() / "models" / "wp4b_encoder"
SPLIT_FILE = MODEL_ROOT / "wp4_split.json"
TEACHER_LABELS = paths.labels_dir() / "teacher_facts.parquet"
WP4_REPORT = paths.gold_dir() / "model_a_report.json"
LEDGERS = {
    "wp3_teacher (haiku labels)": paths.labels_dir() / "teacher_cache" / "spend.jsonl",
    "eval_sealed (judges)": paths.labels_dir() / "eval_sealed" / "spend.jsonl",
    "wp4b (this run)": paths.labels_dir() / "wp4b" / "spend.jsonl",
}

#: Declared before any gold score was looked at; round 2 reuses them unchanged.
SETTINGS = {
    "2a": {
        "encoders": ["BAAI/bge-small-en-v1.5", "BAAI/bge-base-en-v1.5"],
        "embedding": "sentence-transformers encode, CLS pooling, L2-normalized, max_seq_length 128",
        "features": "StandardScaler fit on train embeddings",
        "C_grid": [0.01, 0.1, 1.0],
        "solver": "lbfgs, max_iter 3000",
        "threshold_grid": "0.05 to 0.95 step 0.01 (wp4_student.best_threshold, ties nearest 0.5)",
        "choice": "per tag C and threshold by validation F1; encoder by validation macro F1",
    },
    "2b": {
        "encoder": "BAAI/bge-small-en-v1.5",
        "head": "CLS hidden state -> dropout 0.1 -> linear(384, 24)",
        "loss": "BCEWithLogits, pos_weight = clip(sqrt(neg/pos), 1, 10) per tag from train labels",
        "max_length": 64,
        "batch_size": 32,
        "lr_encoder": 5e-5,
        "lr_head": 1e-3,
        "weight_decay": 0.01,
        "warmup_frac": 0.06,
        "schedule": "linear warmup then linear decay over max_epochs",
        "max_epochs": 6,
        "patience": 2,
        "grad_clip": 1.0,
        "early_stopping": "validation macro F1 at per-tag tuned thresholds; best epoch kept",
        "device": "mps",
    },
}


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


W4 = _load_script("wp4_student")   # load_teacher, split, best_threshold, val_scores, Y_of, sets_of
WS = W4.WS                          # wp3_score: load_gold, score_tags, _prf


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha_texts(texts) -> str:
    h = hashlib.sha256()
    for t in texts:
        h.update(t.encode("utf-8")); h.update(b"\x00")
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


def rel(p: Path) -> str:
    p = Path(p).resolve()
    return str(p.relative_to(paths.data_dir())) if p.is_relative_to(paths.data_dir()) else str(p)


def write_json(path: Path, obj) -> None:
    body = json.dumps(obj, indent=2, ensure_ascii=False, default=str) + "\n"
    write_atomic(path, lambda p: p.write_text(body, encoding="utf-8"))


def git_info() -> dict:
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                                check=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "--no-optional-locks", "status", "--porcelain", "--", SCRIPT,
                                     "scripts/wp4_student.py", "src/vf/recipes.py"], cwd=ROOT,
                                    capture_output=True, text=True, check=True).stdout.strip())
    except Exception:
        commit, dirty = None, None
    return {"git_commit": commit, "code_uncommitted": dirty}


def env_info() -> dict:
    import sklearn
    import torch
    import transformers
    import sentence_transformers
    return {"python": platform.python_version(), "torch": torch.__version__,
            "transformers": transformers.__version__, "sentence_transformers": sentence_transformers.__version__,
            "sklearn": sklearn.__version__, "mps_available": bool(torch.backends.mps.is_available()),
            "machine": f"{platform.machine()} {platform.mac_ver()[0]}", "hardware": "Apple M5 Pro, 24 GB",
            "venv": str(Path(sys.executable).parent.parent.name)}


def device():
    import torch
    return torch.device("mps" if torch.backends.mps.is_available() else "cpu")


# --------------------------------------------------------------------------
# data and split
# --------------------------------------------------------------------------

def parse_tags(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, (list, tuple, np.ndarray)):
        return [str(x) for x in v]
    s = str(v).strip()
    if not s or s in ("null", "[]"):
        return []
    if s.startswith("["):
        return [str(x) for x in json.loads(s)]
    sep = "|" if "|" in s else ";"
    return [x.strip() for x in s.split(sep) if x.strip()]


def load_labels(path: Path) -> pd.DataFrame:
    if path.suffix == ".parquet":
        df = pd.read_parquet(path)
    elif path.suffix == ".jsonl":
        df = pd.read_json(path, lines=True)
    else:
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
    if not {"text_norm", "tags"} <= set(df.columns):
        raise SystemExit(f"{path}: need columns text_norm and tags, got {list(df.columns)}")
    df = df[["text_norm", "tags"]].copy()
    df["tags"] = df["tags"].map(lambda v: [t for t in parse_tags(v) if t in TAGS])
    if not df["text_norm"].is_unique:
        raise SystemExit(f"{path}: text_norm not unique")
    return df.reset_index(drop=True)


def wp4_split() -> dict:
    """The WP4 validation texts, frozen to SPLIT_FILE on first use."""
    if SPLIT_FILE.exists():
        sp = json.loads(SPLIT_FILE.read_text())
        if sha_texts(sp["val_texts"]) != sp["val_sha256"]:
            raise SystemExit(f"{SPLIT_FILE} is corrupt")
        return sp
    gold = WS.load_gold()
    teacher = W4.load_teacher()
    tr, va = W4.split(teacher, set(gold["text_norm"]))
    val_texts = teacher["text_norm"].iloc[va].tolist()
    train_texts = teacher["text_norm"].iloc[tr].tolist()
    rep = json.loads(WP4_REPORT.read_text()) if WP4_REPORT.exists() else {}
    want = rep.get("split", {})
    if want and (want.get("train"), want.get("val")) != (len(tr), len(va)):
        raise SystemExit(f"WP4 split mismatch: got {len(tr)}/{len(va)}, model_a_report says {want}")
    sp = {"created_at_utc": now(), "script": SCRIPT,
          "how": "scripts/wp4_student.py load_teacher() + split(): rng(20261121).random(n) < 0.2 over "
                 "labels/teacher_facts.parquet rows, by text_norm",
          "teacher_labels_sha256": sha256_file(TEACHER_LABELS), "train": len(tr), "val": len(va),
          "train_sha256": sha_texts(train_texts), "val_sha256": sha_texts(val_texts), "val_texts": val_texts}
    write_json(SPLIT_FILE, sp)
    return sp


def make_split(labels: pd.DataFrame, limit: int | None) -> tuple[np.ndarray, np.ndarray, dict]:
    gold_texts = set(WS.load_gold()["text_norm"])
    if labels["text_norm"].isin(gold_texts).any():
        raise SystemExit("labels contain gold texts; refusing to train")
    sp = wp4_split()
    val_set = set(sp["val_texts"])
    is_val = labels["text_norm"].isin(val_set).to_numpy()
    tr, va = np.flatnonzero(~is_val), np.flatnonzero(is_val)
    info = {"wp4_val_texts": len(val_set), "val": int(len(va)), "train": int(len(tr)),
            "wp4_val_texts_missing_from_labels": int(len(val_set) - len(va)),
            "val_sha256_wp4": sp["val_sha256"], "gold_texts_in_labels": 0,
            "rule": "validation = labeled texts in the frozen WP4 validation list; all other labeled texts train"}
    if limit:
        rng = np.random.default_rng(SEED)
        tr = np.sort(rng.choice(tr, size=min(limit, len(tr)), replace=False))
        va = np.sort(rng.choice(va, size=min(max(limit // 4, 200), len(va)), replace=False))
        info.update({"limit": limit, "train": int(len(tr)), "val": int(len(va))})
    return tr, va, info


def silver_texts(limit: int | None) -> list[str]:
    t = pq.read_table(paths.silver_dir() / "facts.parquet", columns=["text_norm"]).column(0).to_pylist()
    distinct = sorted(set(t))
    if limit:
        rng = np.random.default_rng(SEED)
        distinct = sorted(rng.choice(distinct, size=min(limit * 2, len(distinct)), replace=False).tolist())
    return distinct


# --------------------------------------------------------------------------
# thresholds and validation metrics
# --------------------------------------------------------------------------

def tune(P: np.ndarray, Y: np.ndarray) -> tuple[np.ndarray, dict]:
    th = np.ones(len(TAGS)); per = {}
    for j, t in enumerate(TAGS):
        thr, f1, pr, rc = W4.best_threshold(P[:, j], Y[:, j])
        th[j] = thr
        per[t] = {"threshold": round(thr, 4), "f1": round(f1, 4), "precision": round(pr, 4),
                  "recall": round(rc, 4), "support": int(Y[:, j].sum())}
    return th, per


def macro_at(P: np.ndarray, Y: np.ndarray, th) -> float:
    return W4.val_scores(Y, P >= th)["macro_f1"]


# --------------------------------------------------------------------------
# 2a: frozen embeddings + logistic regression
# --------------------------------------------------------------------------

class EmbCache:
    """Frozen-embedding cache per encoder: texts list + float16 matrix, extended on demand."""

    def __init__(self, model_name: str, max_len: int = 128):
        self.name, self.max_len = model_name, max_len
        slug = model_name.replace("/", "__") + f"_len{max_len}"
        self.dir = MODEL_ROOT / "emb_cache"
        self.f_emb, self.f_txt = self.dir / f"{slug}.npy", self.dir / f"{slug}.texts.json"
        self.index, self.emb = {}, None
        if self.f_emb.exists() and self.f_txt.exists():
            texts = json.loads(self.f_txt.read_text())
            self.emb = np.load(self.f_emb)
            if len(texts) != len(self.emb):
                raise SystemExit(f"embedding cache {self.f_emb} is inconsistent")
            self.index = {t: i for i, t in enumerate(texts)}
        self._model = None
        self.encode_seconds, self.encoded = 0.0, 0

    def model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self.name, device=str(device()))
            self._model.max_seq_length = self.max_len
        return self._model

    def get(self, texts: list[str]) -> np.ndarray:
        missing = list(dict.fromkeys(t for t in texts if t not in self.index))
        if missing:
            t0 = time.time()
            E = self.model().encode(missing, batch_size=256, normalize_embeddings=True,
                                    convert_to_numpy=True, show_progress_bar=False).astype(np.float16)
            self.encode_seconds += time.time() - t0; self.encoded += len(missing)
            base = 0 if self.emb is None else len(self.emb)
            self.emb = E if self.emb is None else np.vstack([self.emb, E])
            for i, t in enumerate(missing):
                self.index[t] = base + i
            self.save()
        return self.emb[[self.index[t] for t in texts]].astype(np.float32)

    def save(self):
        texts = [None] * len(self.index)
        for t, i in self.index.items():
            texts[i] = t
        def _save(p):
            with open(p, "wb") as fh:
                np.save(fh, self.emb)
        write_atomic(self.f_emb, _save)
        write_atomic(self.f_txt, lambda p: p.write_text(json.dumps(texts, ensure_ascii=False)))


def _fit_tag(X, y, Xv, yv, C_grid):
    from sklearn.linear_model import LogisticRegression
    if y.sum() == 0:
        return None, float("nan"), np.zeros(len(Xv)), 1.0, 0.0
    best = None
    for C in C_grid:
        m = LogisticRegression(C=C, solver="lbfgs", max_iter=3000)
        m.fit(X, y)
        p = m.predict_proba(Xv)[:, 1]
        th, f1, _, _ = W4.best_threshold(p, yv)
        if best is None or f1 > best[4] + 1e-9:
            best = (m, C, p, th, f1)
    return best


def train_2a(labels, tr, va, run_dir: Path, encoders: list[str], all_texts: list[str]) -> dict:
    from joblib import Parallel, delayed
    from sklearn.preprocessing import StandardScaler
    texts = labels["text_norm"].tolist()
    Y = W4.Y_of(labels["tags"])
    tr_t = [texts[i] for i in tr]; va_t = [texts[i] for i in va]
    cands = {}
    for enc in encoders:
        t0 = time.time()
        cache = EmbCache(enc)
        Xtr, Xva = cache.get(tr_t), cache.get(va_t)
        emb_secs = time.time() - t0
        t1 = time.time()
        sc = StandardScaler().fit(Xtr)
        Xtr_s, Xva_s = sc.transform(Xtr), sc.transform(Xva)
        C_grid = SETTINGS["2a"]["C_grid"]
        res = Parallel(n_jobs=8, prefer="threads")(
            delayed(_fit_tag)(Xtr_s, Y[tr][:, j], Xva_s, Y[va][:, j], C_grid) for j in range(len(TAGS)))
        fit_secs = time.time() - t1
        P = np.column_stack([r[2] for r in res])
        th = np.array([r[3] for r in res])
        vs = W4.val_scores(Y[va], P >= th)
        per_tag = {t: {"C": res[j][1], "threshold": round(float(th[j]), 4), "f1": vs["per_tag_f1"][t],
                       "support": int(Y[va][:, j].sum())} for j, t in enumerate(TAGS)}
        cands[enc] = {"scaler": sc, "models": [r[0] for r in res], "th": th, "val": vs, "per_tag": per_tag,
                      "embed_seconds_train_val": round(emb_secs, 1), "fit_seconds": round(fit_secs, 1),
                      "texts_embedded_now": cache.encoded, "cache": cache}
        print(f"  2a {enc}: validation macro F1 {vs['macro_f1']:.4f} (micro {vs['micro_f1']:.4f}); "
              f"embed {emb_secs:.0f}s, fit {fit_secs:.0f}s")
    choice = max(cands, key=lambda k: cands[k]["val"]["macro_f1"])
    c = cands[choice]
    print(f"  2a encoder kept (validation only): {choice}")
    # inference over every distinct silver text
    t0 = time.time()
    X = c["cache"].get(all_texts)
    embed_all = time.time() - t0
    t1 = time.time()
    Xs = c["scaler"].transform(X)
    P = np.zeros((len(all_texts), len(TAGS)), dtype=np.float32)
    for j, m in enumerate(c["models"]):
        if m is not None:
            P[:, j] = m.predict_proba(Xs)[:, 1]
    lr_secs = time.time() - t1
    print(f"  2a inference: {len(all_texts):,} texts, embed {embed_all:.0f}s (cache), LR {lr_secs:.1f}s")
    blob = {"what": "2a: frozen sentence embeddings + logistic regression per tag", "encoder": choice,
            "max_seq_length": 128, "normalize_embeddings": True, "tags": TAGS, "scaler": c["scaler"],
            "models": dict(zip(TAGS, c["models"])), "thresholds": dict(zip(TAGS, c["th"].tolist())),
            "usage": "E = SentenceTransformer(encoder).encode(texts, normalize_embeddings=True); "
                     "X = scaler.transform(E); tag fires when models[tag].predict_proba(X)[:,1] >= thresholds[tag]"}
    write_atomic(run_dir / "lr.pkl", lambda p: p.write_bytes(pickle.dumps(blob)))
    summary = {
        "variant": "2a", "what": blob["what"], "encoder_kept": choice, "encoder_choice_rule":
        "higher validation macro F1 against the training labels (gold not consulted)",
        "settings": SETTINGS["2a"],
        "candidates": {k: {"validation_macro_f1": v["val"]["macro_f1"], "validation_micro_f1": v["val"]["micro_f1"],
                           "validation_macro_precision": v["val"]["macro_precision"],
                           "validation_macro_recall": v["val"]["macro_recall"],
                           "per_tag": v["per_tag"], "embed_seconds_train_val": v["embed_seconds_train_val"],
                           "fit_seconds": v["fit_seconds"]} for k, v in cands.items()},
        "validation": {k: c["val"][k] for k in ("macro_f1", "micro_f1", "macro_precision", "macro_recall")},
        "per_tag": c["per_tag"],
        "timing": {"embed_all_seconds": round(embed_all, 1), "lr_inference_seconds": round(lr_secs, 1),
                   "inference_texts": len(all_texts),
                   "train_seconds": round(c["embed_seconds_train_val"] + c["fit_seconds"], 1)},
    }
    return {"summary": summary, "P": P, "th": c["th"]}


# --------------------------------------------------------------------------
# 2b: fine-tuned encoder
# --------------------------------------------------------------------------

def _tagger(name: str):
    import torch.nn as nn
    from transformers import AutoModel

    class Tagger(nn.Module):
        def __init__(self):
            super().__init__()
            self.encoder = AutoModel.from_pretrained(name, add_pooling_layer=False)
            self.dropout = nn.Dropout(0.1)
            self.head = nn.Linear(self.encoder.config.hidden_size, len(TAGS))

        def forward(self, input_ids, attention_mask):
            h = self.encoder(input_ids=input_ids, attention_mask=attention_mask).last_hidden_state[:, 0]
            return self.head(self.dropout(h))

    return Tagger()


def _batches(ids: list[list[int]], order, bs: int, pad: int):
    import torch
    for s in range(0, len(order), bs):
        idx = order[s:s + bs]
        L = max(len(ids[i]) for i in idx)
        a = np.full((len(idx), L), pad, dtype=np.int64); m = np.zeros((len(idx), L), dtype=np.int64)
        for k, i in enumerate(idx):
            a[k, :len(ids[i])] = ids[i]; m[k, :len(ids[i])] = 1
        yield idx, torch.from_numpy(a), torch.from_numpy(m)


def predict_2b(model, tok, texts: list[str], max_len: int, bs: int = 512) -> np.ndarray:
    import torch
    dev = device()
    model.eval()
    ids = tok(texts, truncation=True, max_length=max_len, add_special_tokens=True)["input_ids"]
    order = np.argsort([len(x) for x in ids], kind="stable")
    out = np.zeros((len(texts), len(TAGS)), dtype=np.float32)
    with torch.inference_mode():
        for idx, a, m in _batches(ids, order, bs, tok.pad_token_id):
            out[idx] = torch.sigmoid(model(a.to(dev), m.to(dev))).float().cpu().numpy()
    return out


def train_2b(labels, tr, va, run_dir: Path, all_texts: list[str], max_minutes: float) -> dict:
    import torch
    from transformers import AutoTokenizer, get_linear_schedule_with_warmup
    S = SETTINGS["2b"]
    torch.manual_seed(SEED); np.random.seed(SEED)
    dev = device()
    texts = labels["text_norm"].tolist()
    Y = W4.Y_of(labels["tags"])
    tr_t = [texts[i] for i in tr]; va_t = [texts[i] for i in va]
    Ytr, Yva = Y[tr], Y[va]
    tok = AutoTokenizer.from_pretrained(S["encoder"])
    model = _tagger(S["encoder"]).to(dev)
    ids = tok(tr_t, truncation=True, max_length=S["max_length"])["input_ids"]
    pos = Ytr.sum(0).astype(float); neg = len(Ytr) - pos
    pw = np.clip(np.sqrt(np.where(pos > 0, neg / np.maximum(pos, 1), 1.0)), 1.0, 10.0)
    loss_fn = torch.nn.BCEWithLogitsLoss(pos_weight=torch.tensor(pw, dtype=torch.float32, device=dev))
    enc_p = [p for n, p in model.named_parameters() if n.startswith("encoder.")]
    head_p = [p for n, p in model.named_parameters() if not n.startswith("encoder.")]
    opt = torch.optim.AdamW([{"params": enc_p, "lr": S["lr_encoder"]}, {"params": head_p, "lr": S["lr_head"]}],
                            weight_decay=S["weight_decay"])
    steps_per_epoch = math.ceil(len(tr_t) / S["batch_size"])
    total = steps_per_epoch * S["max_epochs"]
    sched = get_linear_schedule_with_warmup(opt, int(S["warmup_frac"] * total), total)
    Yt = torch.from_numpy(Ytr.astype(np.float32))
    rng = np.random.default_rng(SEED)
    log, best, bad, stopped = [], None, 0, None
    t_start = time.time()
    for ep in range(1, S["max_epochs"] + 1):
        model.train()
        t0 = time.time(); run_loss, n_b = 0.0, 0
        order = rng.permutation(len(tr_t))
        for idx, a, m in _batches(ids, order, S["batch_size"], tok.pad_token_id):
            logits = model(a.to(dev), m.to(dev))
            loss = loss_fn(logits, Yt[idx].to(dev))
            opt.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), S["grad_clip"])
            opt.step(); sched.step()
            run_loss += float(loss.detach()); n_b += 1
            if n_b % 200 == 0:
                print(f"    epoch {ep} step {n_b}/{steps_per_epoch} loss {run_loss / n_b:.4f} "
                      f"({time.time() - t0:.0f}s)", flush=True)
        ep_secs = time.time() - t0
        Pva = predict_2b(model, tok, va_t, S["max_length"])
        th, _ = tune(Pva, Yva)
        f_tuned = macro_at(Pva, Yva, th); f_half = macro_at(Pva, Yva, 0.5)
        rec = {"epoch": ep, "train_loss": round(run_loss / n_b, 5), "val_macro_f1_tuned": f_tuned,
               "val_macro_f1_at_0.5": f_half, "epoch_seconds": round(ep_secs, 1)}
        log.append(rec)
        print(f"  2b epoch {ep}: loss {rec['train_loss']:.4f}, validation macro F1 {f_tuned:.4f} tuned "
              f"({f_half:.4f} at 0.5), {ep_secs:.0f}s", flush=True)
        if best is None or f_tuned > best["f1"] + 1e-4:
            best = {"f1": f_tuned, "epoch": ep,
                    "state": {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}}
            bad = 0
        else:
            bad += 1
            if bad >= S["patience"]:
                stopped = f"early stop: no gain for {S['patience']} epochs"
                break
        if (time.time() - t_start) / 60 > max_minutes:
            stopped = f"time box {max_minutes:.0f} min reached"
            break
    train_secs = time.time() - t_start
    stopped = stopped or "max_epochs reached"
    print(f"  2b best epoch {best['epoch']} (validation macro F1 {best['f1']:.4f}); {stopped}; "
          f"train {train_secs / 60:.1f} min")
    model.load_state_dict(best["state"])
    Pva = predict_2b(model, tok, va_t, S["max_length"])
    th, per = tune(Pva, Yva)
    vs = W4.val_scores(Yva, Pva >= th)
    t0 = time.time()
    P = predict_2b(model, tok, all_texts, S["max_length"])
    inf_secs = time.time() - t0
    print(f"  2b inference: {len(all_texts):,} texts in {inf_secs:.0f}s ({len(all_texts) / inf_secs:,.0f}/s)")
    enc_dir = run_dir / "encoder"
    model.encoder.save_pretrained(enc_dir); tok.save_pretrained(enc_dir)
    write_atomic(run_dir / "head.pt", lambda p: torch.save(
        {"head": model.head.state_dict(), "tags": TAGS, "thresholds": dict(zip(TAGS, th.tolist())),
         "max_length": S["max_length"], "pooling": "CLS"}, p))
    first = log[0]["val_macro_f1_tuned"]
    summary = {
        "variant": "2b", "what": "2b: BAAI/bge-small-en-v1.5 fine-tuned, multi-label head", "settings": SETTINGS["2b"],
        "pos_weight": dict(zip(TAGS, [round(float(x), 3) for x in pw])),
        "epochs": log, "best_epoch": best["epoch"], "stopped": stopped,
        "converged": bool(len(log) >= 2 and log[-1]["train_loss"] < log[0]["train_loss"] and first > 0.5),
        "validation": {k: vs[k] for k in ("macro_f1", "micro_f1", "macro_precision", "macro_recall")},
        "validation_macro_f1_at_0.5": macro_at(Pva, Yva, 0.5),
        "per_tag": {t: {**per[t], "f1": vs["per_tag_f1"][t]} for t in TAGS},
        "timing": {"train_seconds": round(train_secs, 1), "inference_seconds": round(inf_secs, 1),
                   "inference_texts": len(all_texts),
                   "inference_texts_per_second": round(len(all_texts) / inf_secs, 0),
                   "seconds_per_epoch": [r["epoch_seconds"] for r in log], "device": str(dev)},
    }
    return {"summary": summary, "P": P, "th": th}


# --------------------------------------------------------------------------
# outputs
# --------------------------------------------------------------------------

def write_scores(run_dir: Path, all_texts, P, th, meta: dict) -> None:
    fired = P >= th
    cols = {"text_norm": pa.array(all_texts, pa.string())}
    for j, t in enumerate(TAGS):
        cols[f"p_{t}"] = pa.array(P[:, j].astype(np.float32), pa.float32())
    cols["tags"] = pa.array([[TAGS[j] for j in np.flatnonzero(r)] for r in fired], pa.list_(pa.string()))
    table = pa.table(cols)
    write_atomic(run_dir / "scores.parquet", lambda p: pq.write_table(table, p))
    side = dict(meta, rows=table.num_rows, columns=table.column_names,
                texts_with_tag={t: int(fired[:, j].sum()) for j, t in enumerate(TAGS)},
                thresholds=dict(zip(TAGS, [round(float(x), 4) for x in th])))
    write_json(run_dir / "scores.parquet.meta.json", side)


def ledger_totals() -> dict:
    out, total = {}, 0.0
    for name, p in LEDGERS.items():
        if not p.exists():
            out[name] = {"file": rel(p), "responses": 0, "usd": 0.0, "exists": False}
            continue
        rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
        usd = sum(float(r.get("cost_usd", 0) or 0) for r in rows)
        out[name] = {"file": rel(p), "responses": len(rows), "usd": round(usd, 4),
                     "exists": True}
        total += usd
    return {"ledgers": out, "project_total_usd": round(total, 4), "this_stage_usd": 0.0,
            "read_at_utc": now(), "note": "this script makes no LLM calls"}


def gold_report(variant_dirs: dict[str, Path]) -> dict:
    """Gold per-tag F1 of each saved variant. REPORTING ONLY: runs after every choice is saved."""
    gold = WS.load_gold()
    gt = gold["text_norm"].tolist()
    out = {}
    for name, d in variant_dirs.items():
        sc = pd.read_parquet(d / "scores.parquet", columns=["text_norm", "tags"]).set_index("text_norm")["tags"]
        miss = [t for t in gt if t not in sc.index]
        if miss:
            out[name] = {"error": f"{len(miss)} gold texts not in scores.parquet (smoke run?)"}
            continue
        pred = [list(sc[t]) for t in gt]
        res = WS.score_tags(gold, pred, tags=TAGS)
        res.pop("_matrices", None)
        sm = res["summary"]
        out[name] = {"macro_f1": sm["macro"]["f1"], "macro_f1_ci95": sm["macro"]["f1_ci95"],
                     "micro_f1": sm["micro"]["f1"], "exact_match": sm.get("exact_match_evidence_tags"),
                     "per_tag": {t: {"f1": res["per_tag"][t]["f1"], "f1_ci95": res["per_tag"][t]["f1_ci95"],
                                     "precision": res["per_tag"][t]["precision"],
                                     "recall": res["per_tag"][t]["recall"],
                                     "support": res["per_tag"][t]["support"]} for t in TAGS}}
    ref = {}
    if WP4_REPORT.exists():
        r = json.loads(WP4_REPORT.read_text())
        for k in ("regex", "teacher", "student_1b", "shipped"):
            g = r["gold_scores"].get(k)
            if g:
                ref[k] = {"macro_f1": g["summary"]["macro"]["f1"], "macro_f1_ci95": g["summary"]["macro"]["f1_ci95"],
                          "per_tag_f1": {t: g["per_tag"][t]["f1"] for t in TAGS}}
        ref["_source"] = f"gold/model_a_report.json ({r.get('model_version')}, {r.get('created_at_utc')})"
    return {"use": "REPORTING ONLY. Computed after every training choice was saved; not used for any choice.",
            "rows": len(gold), "variants": out, "reference_from_wp4": ref}


def reference_validation() -> dict:
    if not WP4_REPORT.exists():
        return {}
    r = json.loads(WP4_REPORT.read_text())
    v = r.get("validation", {})
    return {k: {"macro_f1": v[k]["macro_f1"], "micro_f1": v[k].get("micro_f1"),
                "per_tag_f1": v[k].get("per_tag_f1")} for k in v} | {
        "_note": "WP4 numbers on the same validation texts and the same Haiku labels "
                 "(thresholds tuned on validation, as here)"}


def build_report(args, variant_dirs: dict[str, Path], split_info: dict, labels_meta: dict,
                 wall: dict) -> dict:
    summaries = {k: json.loads((d / "summary.json").read_text()) for k, d in variant_dirs.items()}
    wall = dict(wall)
    for k, s in summaries.items():
        wall.setdefault(k, s.get("wall_seconds"))
        wall.setdefault(f"{k}_train_seconds", s.get("timing", {}).get("train_seconds"))
    val_tbl = {k: s["validation"] for k, s in summaries.items()}
    rep = {
        "created_at_utc": now(), "script": SCRIPT, "version": VERSION, **git_info(),
        "stage": "WP4b encoder-setup, round 1" if args.run == "r1" else f"WP4b encoder, run {args.run}",
        "decision_ref": "D050 (protocol and ship rule); this file makes no ship decision",
        "environment": env_info(),
        "labels": labels_meta, "split": split_info, "tags": TAGS, "recipe_version": recipes.RECIPE_VERSION,
        "gold_use": "The gold set (labels/gold_facts.csv) shares no text with the labels and was used for none of: "
                    "training, C, thresholds, epochs, early stopping, encoder choice. Gold F1 below is for "
                    "reporting only and was computed after every variant's summary.json was written.",
        "settings_declared_before_gold": SETTINGS,
        "validation_vs_labels": val_tbl,
        "validation_reference_wp4": reference_validation(),
        "variants": summaries,
        "variant_dirs": {k: rel(d) for k, d in variant_dirs.items()},
        "wall_clock": wall,
        "gold_reporting_only": gold_report(variant_dirs) if not args.limit else {"skipped": "smoke run"},
        "spend": ledger_totals(),
    }
    rep["headline"] = headline(rep)
    rep["notes"] = [
        "Validation F1 uses thresholds (and, for 2a, C and the encoder) tuned on the same validation texts, as in "
        "WP4, so it is optimistic in absolute terms; it is comparable to the WP4 validation numbers.",
        "Validation is scored against the training labels' source (the teacher), so it measures how well a "
        "student copies the teacher, not how right it is. The student inherits the teacher's mistakes.",
        "Gold is agent-labeled, 777 texts, 16 to 74 positives per tag: per-tag differences of a few points are noise.",
        "2b runs on the MPS GPU; MPS kernels are not bit-for-bit deterministic, so a rerun can move validation F1 "
        "by a small amount. Saved models reload and reproduce the saved scores.",
        "2a scores come from a float16 embedding cache (data/models/wp4b_encoder/emb_cache/); a fresh float32 "
        "encode differs by under 0.001 in score.",
        "Next round: rerun with --labels <merged labels> --run r2 and the same declared settings; validation "
        "texts stay the frozen WP4 list (data/models/wp4b_encoder/wp4_split.json).",
    ]
    return rep


def headline(rep: dict) -> str:
    v = rep["validation_vs_labels"]
    ref = rep["validation_reference_wp4"]
    parts = [f"{k}: validation macro F1 {s['macro_f1']:.3f} against the labels" for k, s in v.items()]
    if "student_1b" in ref:
        parts.append(f"WP4 student 1a {ref['student_1a']['macro_f1']:.3f}, student 1b "
                     f"{ref['student_1b']['macro_f1']:.3f} on the same texts")
    g = rep["gold_reporting_only"]
    if "variants" in g:
        gv = [f"{k} {x['macro_f1']:.3f} ({x['macro_f1_ci95'][0]:.3f} to {x['macro_f1_ci95'][1]:.3f})"
              for k, x in g["variants"].items() if "macro_f1" in x]
        rf = g.get("reference_from_wp4", {})
        gr = [f"{k} {rf[k]['macro_f1']:.3f}" for k in ("regex", "teacher", "student_1b", "shipped") if k in rf]
        parts.append("gold macro F1 (reporting only): " + ", ".join(gv) + "; reference " + ", ".join(gr))
    return ". ".join(parts) + "."


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--labels", type=Path, default=TEACHER_LABELS, help="labels file with text_norm, tags")
    ap.add_argument("--run", default="r1", help="run name; variant dirs are <run>_<variant>")
    ap.add_argument("--variants", default="2a,2b")
    ap.add_argument("--encoders-2a", default=",".join(SETTINGS["2a"]["encoders"]),
                    help="comma list: the first is the default encoder, at most one alternative")
    ap.add_argument("--max-minutes-2b", type=float, default=35.0, help="time box for 2b training")
    ap.add_argument("--report", type=Path, default=None, help="write the run report JSON here")
    ap.add_argument("--report-only", action="store_true", help="rebuild the report from saved outputs")
    ap.add_argument("--limit", type=int, default=None, help="smoke test: subsample train/val/inference")
    ap.add_argument("--out-dir", type=Path, default=None, help="override data/models/wp4b_encoder (smoke tests)")
    args = ap.parse_args(argv)
    t_all = time.time()
    root = args.out_dir or MODEL_ROOT
    variants = [v.strip() for v in args.variants.split(",") if v.strip()]
    encoders = [e.strip() for e in args.encoders_2a.split(",") if e.strip()][:2]
    labels_path = args.labels.resolve()
    labels = load_labels(labels_path)
    tr, va, split_info = make_split(labels, args.limit)
    labels_meta = {"file": rel(labels_path), "sha256": sha256_file(labels_path),
                   "rows": len(labels), "texts_with_any_evidence_tag": int(labels["tags"].map(len).gt(0).sum())}
    print(f"WP4b encoder ({args.run}): {len(labels):,} labeled texts -> train {len(tr):,}, validation {len(va):,} "
          f"(WP4 validation texts present: {split_info['val']:,} of {split_info['wp4_val_texts']:,})")
    variant_dirs = {v: root / f"{args.run}_{v}" for v in variants}
    wall = {}
    if not args.report_only:
        all_texts = silver_texts(args.limit)
        meta_base = {"created_at_utc": now(), "script": SCRIPT, "version": VERSION, **git_info(),
                     "run": args.run, "labels": labels_meta, "split": split_info, "recipe_version":
                     recipes.RECIPE_VERSION, "tags": TAGS, "candidate_only": "D050: not a shipped tagger",
                     "inputs": {"silver/facts.parquet": {"sha256": sha256_file(paths.silver_dir() / "facts.parquet")}}}
        for v in variants:
            t0 = time.time()
            d = paths.ensure(variant_dirs[v])
            if v == "2a":
                out = train_2a(labels, tr, va, d, encoders, all_texts)
            elif v == "2b":
                out = train_2b(labels, tr, va, d, all_texts, args.max_minutes_2b)
            else:
                raise SystemExit(f"unknown variant {v}")
            out["summary"].update({"run": args.run, "created_at_utc": now(), "labels": labels_meta,
                                   "split": split_info, "wall_seconds": round(time.time() - t0, 1)})
            write_json(d / "summary.json", out["summary"])
            write_scores(d, all_texts, out["P"], out["th"],
                         dict(meta_base, output=rel(d / "scores.parquet"), variant=v,
                              what=out["summary"]["what"]))
            wall[v] = round(time.time() - t0, 1)
            print(f"  wrote {d}  [{wall[v] / 60:.1f} min]")
    if args.report:
        rep = build_report(args, variant_dirs, split_info, labels_meta, wall)
        rep["wall_clock"]["total_seconds_this_invocation"] = round(time.time() - t_all, 1)
        write_json(args.report, rep)
        write_json(args.report.with_name(args.report.name + ".meta.json"), {
            "output": rel(args.report), "created_at_utc": rep["created_at_utc"], "script": SCRIPT,
            **git_info(), "labels_sha256": labels_meta["sha256"],
            "variant_dirs": rep["variant_dirs"], "gold_use": "reporting only"})
        g = rep["gold_reporting_only"].get("variants", {})
        for k, s in rep["validation_vs_labels"].items():
            gm = g.get(k, {}).get("macro_f1")
            print(f"  {k}: validation macro F1 {s['macro_f1']:.4f} vs labels"
                  + (f"; gold macro F1 {gm:.4f} (reporting only)" if gm is not None else ""))
        print(f"  project LLM spend so far ${rep['spend']['project_total_usd']:.4f}; wrote {args.report}")
    print(f"done in {(time.time() - t_all) / 60:.1f} min")
    return 0


if __name__ == "__main__":
    sys.exit(main())
