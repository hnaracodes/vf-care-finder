"""Sealed, fair evaluation of the ranker against two baselines (D044 item 4).

The protocol is in ``reports/eval_sealed_protocol.md`` and is sealed with a
sha256 before any run. In short:

* 120 random geocoded post offices (not suspect, not on a shared placeholder
  point), drawn with a fixed seed and stratified by state (square-root
  allocation, at most 8 per state), split 60 dev / 60 sealed. The list is hashed.
* Services: ``emoc`` and ``dialysis`` at every point, plus one more line at a
  random third of the points (20 per half).
* Three tools, the SAME radius per service (``vf.services``) and the SAME right
  to abstain (nothing qualifying inside the radius):
    (a) ``specialty``: nearest facility listing a related specialty
        (``vf.recipes`` claim specialties), straight-line distance;
    (b) ``any_fact``: nearest facility with a fact naming the service
        (grade 2 or 3 by our tags), no trust checks;
    (c) ``ranker``: ``vf.rank.rank``; its first card inside the radius.
* Judges: two Claude models (A, B) read each record blind to which tool
  produced it. Q1 "does the record clearly show this facility offers the
  service?" (record only, no place, no tool). Q2 "is the pick sensible for the
  place?" (picks only). Abstentions: the facilities within the radius that carry
  any signal for the service are judged on Q1 nearest first until one clearly
  shows it. A and B disagree (or one fails): a third model (C) decides.
* Metrics per tool: coverage, precision of the top pick (also without yes votes
  that rest only on the specialties listing), abstained when a judge-supported
  option existed, skipped a nearer judge-supported option (both split by
  whether that option carries a WP11 major flag). Every "missed" figure is a
  lower bound (see the protocol). The card-quote figure is an integrity check,
  not an evaluation result: it holds by construction.

Commands (all print a short summary)::

    python scripts/eval_sealed.py draw                 # once: points, queries, hashes
    python scripts/eval_sealed.py seal                 # once: hash the protocol
    python scripts/eval_sealed.py dryrun               # dev half, at most 30 judge calls
    python scripts/eval_sealed.py picks  --split dev   # the three tools, no API
    python scripts/eval_sealed.py judge  --split dev --max-calls 30
    python scripts/eval_sealed.py score  --split dev
    python scripts/eval_sealed.py human  --split dev   # 40 blind items for Hruday
    python scripts/eval_sealed.py agreement --split dev  # after the CSV is filled in
    python scripts/eval_sealed.py estimate --split dev
    python scripts/eval_sealed.py rejudge --split dev --prompt-q1 j2 --units-file ids.txt  # dev only

The sealed half runs only with ``--split sealed --unseal``, only while the
protocol and the point list match their hashes, only while the protocol's seal
chain equals ``EXPECTED_SEALS`` pinned below, and (for ``picks`` and ``judge``)
only from a clean commit of the protocol, this script and ``src/vf``. Its picks
are computed once (an existing ``picks_sealed.json`` is never overwritten), and
its judges and walk cap must be the protocol's.

Outputs go to ``vf.paths.data_dir()/labels/eval_sealed/`` (not versioned),
written atomically with ``.meta.json`` sidecars. Judge responses are cached on
disk (one JSON per response) and every API call is appended to ``spend.jsonl``.
Spend is checked before every call against ``--cap-usd`` (this evaluation) and
against ``VF_LLM_BUDGET_USD`` minus every other ledger in the project.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io as _io
import json
import math
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from vf import paths  # noqa: E402
from vf import recipes  # noqa: E402
from vf import services as S  # noqa: E402
from vf.geo import haversine_km  # noqa: E402

SCRIPT = "scripts/eval_sealed.py"
HARNESS_VERSION = "eval_sealed_v1"

# =============================================================================
# Protocol constants (copied into the protocol; changing one after sealing
# breaks the protocol hash check on purpose)
# =============================================================================

SEED = 20261122
N_POINTS = 120
N_DEV = 60
MAX_PER_STATE = 8
CORE_SERVICES = ("emoc", "dialysis")
THIRD_PER_SPLIT = 20  # a random third of each half's 60 points
SPLITS = ("dev", "sealed")
TOOLS = ("specialty", "any_fact", "ranker")
TOOL_PLAIN = {
    "specialty": "specialty filter, nearest first",
    "any_fact": "nearest record with a fact naming the service (no trust checks)",
    "ranker": "our ranker",
}
#: A judge-supported facility counts as "nearer" only if it is at least this much nearer.
SKIP_MARGIN_KM = 1.0
#: Facilities judged per query while looking for the nearest judge-supported option.
WALK_CAP = 10
#: Pool entries stored per query (nearest first).
POOL_STORE = 60

PROMPT_VERSION = "j1"  # the prompt set the protocol's "Judge prompt sha256" names
#: Prompt version per question. Cache keys and cache folders use these, so a new
#: Q1 rubric does not invalidate cached Q2 answers (and the j1 cache keeps its keys).
KIND_PROMPT_VERSION = {"q1": "j1", "q2": "j1"}
DEFAULT_CAP_USD = 12.0  # this evaluation in total (ledger is cumulative)
DRYRUN_MAX_CALLS = 30


@dataclass(frozen=True)
class JudgeSpec:
    name: str  # "A", "B", "C"
    model: str
    effort: str
    thinking: Optional[dict] = None  # None: model default


#: The judges the protocol names ("Judge A: `...`" and so on). The sealed half
#: runs only with exactly these; changing one needs a sealed amendment that
#: names the new model AND an edit here. B's ``between_tools`` is how thinking is
#: turned off on claude-sonnet-5-5 (``disabled`` is rejected by that model).
PROTOCOL_JUDGES = {
    "A": JudgeSpec("A", "claude-haiku-5-5", "low"),
    "B": JudgeSpec("B", "claude-sonnet-5-5", "low", {"type": "between_tools"}),
    "C": JudgeSpec("C", "claude-opus-5-5", "low"),
}

#: The seal chain of reports/eval_sealed_protocol.md, in order: the original
#: protocol, then each sealed amendment. The sealed half refuses to run unless
#: the protocol's seal lines are exactly this list. A protocol edited, stripped
#: of its seal and re-sealed gets a new value that is not here, so the guard is
#: not only self-referential; the git checks below date both files.
EXPECTED_SEALS = (
    "6dae9ca7aff2cd749401d5661237073760d36cda428667ce4ff2b62dc0713dc6",  # protocol, 22 Nov 04:11:42 UTC
    "51e25495d6c0a64935a524994af4a21f1e9cb66ee054fc756e21988d18f07e0f",  # amendment 1, 22 Nov 04:30 UTC
    "01ef778314f40f47c93e7bfba06b98c80849ce62122e1b2f78e2739a8d589ded",  # amendment 2, 22 Nov 04:31 UTC
)

#: Paths whose committed, unmodified state the sealed half requires (git):
#: the protocol, this harness, and every vf module the tools import (the
#: ranker, the recipes the specialty baseline reads, the services and radii).
GUARDED_PATHS = ("reports/eval_sealed_protocol.md", SCRIPT, "src/vf")


def judge_specs(env: dict, preset: str = "mixed") -> dict[str, JudgeSpec]:
    """A is VF_LLM_MODEL. ``mixed`` (default): B is a different model, so the two
    judges do not share one model's blind spots, and C (disagreements only) is
    the strongest. ``single``: all three are VF_LLM_MODEL (cheapest). The sealed
    half accepts only PROTOCOL_JUDGES (see check_judges)."""
    a = env.get("VF_LLM_MODEL") or "claude-haiku-5-5"
    if preset == "single":
        return {"A": JudgeSpec("A", a, "low"), "B": JudgeSpec("B", a, "medium"), "C": JudgeSpec("C", a, "high")}
    return {
        "A": JudgeSpec("A", a, "low"),
        "B": JudgeSpec("B", "claude-sonnet-5-5", "low", {"type": "between_tools"}),
        "C": JudgeSpec("C", "claude-opus-5-5", "low"),
    }


#: USD per million tokens (claude-api skill model table, cached 2026-11-03):
#: input, output. Cache write 1.25x input, cache read 0.1x input.
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}
#: Haiku 5.5's rate above is for prompts up to 100K tokens.
LOW_RATE_MAX_PROMPT = 100_000
MAX_TOKENS = {"q1": 3000, "q2": 3000}

# =============================================================================
# Paths
# =============================================================================


def out_dir() -> Path:
    return paths.data_dir() / "labels" / "eval_sealed"


def protocol_path() -> Path:
    return paths.code_root() / "reports" / "eval_sealed_protocol.md"


def default_bundle() -> Optional[Path]:
    b = paths.code_root() / "dbapp" / "bundle"
    return b if (b / "facilities.parquet").exists() else None


def other_ledgers() -> list[Path]:
    """Every other LLM spend ledger in the project, counted against VF_LLM_BUDGET_USD."""
    lab = paths.data_dir() / "labels"
    found = sorted(p for p in lab.rglob("spend.jsonl") if out_dir() not in p.parents)
    return found


# =============================================================================
# Small helpers
# =============================================================================


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git_info() -> dict:
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=paths.code_root(), capture_output=True,
                                text=True, check=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "--no-optional-locks", "status", "--porcelain", "--", SCRIPT],
                                    cwd=paths.code_root(), capture_output=True, text=True, check=True).stdout.strip())
    except Exception:
        commit, dirty = None, None
    return {"git_commit": commit, "code_uncommitted": dirty}


def write_text_atomic(path: Path, text: str) -> None:
    paths.ensure(path.parent)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def write_json_atomic(path: Path, obj: Any) -> None:
    write_text_atomic(path, json.dumps(obj, indent=2, ensure_ascii=False, default=_json_default) + "\n")


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        f = float(o)
        return None if math.isnan(f) else f
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, Path):
        return str(o)
    raise TypeError(type(o))


def write_csv_atomic(path: Path, df: pd.DataFrame) -> None:
    buf = _io.StringIO()
    df.to_csv(buf, index=False, quoting=csv.QUOTE_MINIMAL)
    write_text_atomic(path, buf.getvalue())


def write_meta(path: Path, meta: dict) -> None:
    base = {"created_at_utc": now_utc(), "script": SCRIPT, "harness_version": HARNESS_VERSION, **git_info()}
    base.update(meta)
    write_json_atomic(path.with_name(path.name + ".meta.json"), base)


def read_env() -> dict:
    """Parse .env without exporting or printing anything."""
    env = {}
    p = paths.code_root() / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    for k in ("ANTHROPIC_API_KEY", "VF_LLM_MODEL", "VF_LLM_BUDGET_USD"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


def wilson(k: int, n: int, z: float = 1.96) -> tuple[Optional[float], Optional[float]]:
    if n == 0:
        return None, None
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, c - h), min(1.0, c + h)


def cohen_kappa(a: list, b: list) -> Optional[float]:
    """Cohen's kappa for two lists of booleans (pairs with a None are dropped)."""
    pairs = [(bool(x), bool(y)) for x, y in zip(a, b) if x is not None and y is not None]
    n = len(pairs)
    if n == 0:
        return None
    po = sum(x == y for x, y in pairs) / n
    pa = sum(x for x, _ in pairs) / n
    pb = sum(y for _, y in pairs) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    if pe >= 1.0:
        return 1.0 if po == 1.0 else 0.0
    return (po - pe) / (1 - pe)


# =============================================================================
# Step 1: draw the points (pure functions, tested)
# =============================================================================


def allocate(counts: dict[str, int], n: int, cap: int) -> dict[str, int]:
    """Square-root allocation of ``n`` draws over states, at most ``cap`` and at
    most the state's eligible count each, largest remainder, ties by name."""
    states = sorted(s for s, c in counts.items() if c > 0)
    limit = {s: min(cap, counts[s]) for s in states}
    if sum(limit.values()) < n:
        raise ValueError(f"cannot place {n} draws: only {sum(limit.values())} fit under the cap")
    alloc = {s: 0 for s in states}
    fixed: set = set()  # states held at their limit
    while True:
        free = [s for s in states if s not in fixed]
        left = n - sum(limit[s] for s in fixed)
        tot = sum(math.sqrt(counts[s]) for s in free)
        want = {s: left * math.sqrt(counts[s]) / tot for s in free}
        over = [s for s in free if want[s] > limit[s]]
        if not over:
            break
        fixed.update(over)
    for s in fixed:
        alloc[s] = limit[s]
    base = {s: int(math.floor(want[s])) for s in free}
    for s in free:
        alloc[s] = base[s]
    rest = n - sum(alloc.values())
    for s in sorted(free, key=lambda s: (-(want[s] - base[s]), s)):
        if rest == 0:
            break
        if alloc[s] < limit[s]:
            alloc[s] += 1
            rest -= 1
    if sum(alloc.values()) != n:
        raise ValueError(f"cannot place {n} draws: only {sum(alloc.values())} fit under the cap")
    return {s: k for s, k in alloc.items() if k > 0}


def eligible_offices(offices: pd.DataFrame) -> pd.DataFrame:
    ok = (offices["coord_valid"].astype(bool) & offices["lat"].notna() & offices["lon"].notna()
          & ~offices["coord_suspect"].astype(bool) & ~offices["coord_placeholder"].astype(bool)
          & offices["state"].notna() & (offices["state"].astype(str).str.upper() != "NA"))
    return offices[ok].sort_values("po_id").reset_index(drop=True)


def draw_points(offices: pd.DataFrame, seed: int = SEED, n: int = N_POINTS, n_dev: int = N_DEV,
                cap: int = MAX_PER_STATE, third_per_split: int = THIRD_PER_SPLIT) -> pd.DataFrame:
    """The sample. ``offices``: po_id, office_name, pin, state, district, lat,
    lon, coord_valid, coord_suspect, coord_placeholder."""
    if n != 2 * n_dev:
        raise ValueError("the split must be half and half")
    el = eligible_offices(offices)
    counts = el.groupby("state").size().to_dict()
    alloc = allocate(counts, n, cap)
    rng = np.random.default_rng(seed)
    parts = []
    for st in sorted(alloc):
        pool = el[el["state"] == st]
        take = rng.choice(len(pool), size=alloc[st], replace=False)
        part = pool.iloc[np.sort(take)].copy()
        part["_order"] = rng.permutation(len(part))
        parts.append(part)
    pts = pd.concat(parts, ignore_index=True).sort_values(["state", "_order"], kind="stable").reset_index(drop=True)
    pts["split"] = np.where(np.arange(len(pts)) % 2 == 0, "dev", "sealed")
    others = [l for l in S.SERVICE_LINES if l not in CORE_SERVICES]
    pts["third_service"] = None
    for sp in SPLITS:
        ix = np.flatnonzero(pts["split"].to_numpy() == sp)
        chosen = np.sort(rng.choice(ix, size=third_per_split, replace=False))
        pts.loc[chosen, "third_service"] = rng.choice(others, size=third_per_split, replace=True)
    pts = pts.drop(columns="_order")
    pts["lat"] = pts["lat"].astype(float).round(6)
    pts["lon"] = pts["lon"].astype(float).round(6)
    cols = ["po_id", "office_name", "pin", "state", "district", "lat", "lon", "split", "third_service"]
    return pts[cols]


def points_hash(pts: pd.DataFrame) -> str:
    """sha256 of the canonical point list (sorted by po_id, fixed columns and number format)."""
    p = pts.sort_values("po_id")
    lines = ["po_id,pin,state,district,lat,lon,split,third_service"]
    for r in p.itertuples(index=False):
        lines.append(",".join([str(int(r.po_id)), str(r.pin), str(r.state), str(r.district),
                               f"{float(r.lat):.6f}", f"{float(r.lon):.6f}", str(r.split),
                               "" if r.third_service is None or (isinstance(r.third_service, float)
                                                               and math.isnan(r.third_service))
                               else str(r.third_service)]))
    return sha256_bytes(("\n".join(lines) + "\n").encode("utf-8"))


def make_queries(pts: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for r in pts.itertuples(index=False):
        svcs = list(CORE_SERVICES)
        ts = r.third_service
        if isinstance(ts, str) and ts:
            svcs.append(ts)
        for s in svcs:
            rows.append({"query_id": f"{r.split}-{int(r.po_id)}-{s}", "po_id": int(r.po_id), "split": r.split,
                         "service": s, "radius_km": float(S.RADII_KM[s]), "lat": float(r.lat), "lon": float(r.lon),
                         "office_name": r.office_name, "pin": str(r.pin), "state": r.state, "district": r.district})
    return pd.DataFrame(rows)


def load_offices_for_draw() -> pd.DataFrame:
    """WP2 B's post offices with state, district and the suspect and placeholder
    flags (scripts/wp2_places.py load_offices, the same set the bundle uses)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("_wp2_places_eval", paths.code_root() / "scripts" / "wp2_places.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    offices, _ = mod.load_offices()
    return offices[["po_id", "office_name", "pin", "state", "district", "lat", "lon", "coord_valid",
                    "coord_suspect", "coord_placeholder"]]


# =============================================================================
# Protocol seal
# =============================================================================

SEAL_PREFIX = "Protocol sha256 (of every byte above this line): "


def _last_seal(text: str) -> tuple[int, Optional[str], str]:
    """(index of the last seal line or -1, its value, the text after that line)."""
    i = text.rfind(SEAL_PREFIX)
    if i < 0:
        return -1, None, text
    rest = text[i + len(SEAL_PREFIX):]
    nl = rest.find("\n")
    value = (rest if nl < 0 else rest[:nl]).strip().strip("`")
    return i, value, ("" if nl < 0 else rest[nl + 1:])


def protocol_body(text: str) -> str:
    i, _, _ = _last_seal(text)
    return text if i < 0 else text[:i]


def protocol_seal_value(text: str) -> Optional[str]:
    return _last_seal(text)[1]


def seal_chain(text: str) -> list[dict]:
    """Every seal line in order: its value and whether it equals the sha256 of
    every byte above it."""
    out, start = [], 0
    while True:
        i = text.find(SEAL_PREFIX, start)
        if i < 0:
            return out
        rest = text[i + len(SEAL_PREFIX):]
        nl = rest.find("\n")
        value = (rest if nl < 0 else rest[:nl]).strip().strip("`")
        out.append({"value": value, "ok": sha256_bytes(text[:i].encode("utf-8")) == value})
        start = i + len(SEAL_PREFIX)


def protocol_status(path: Optional[Path] = None, expected: tuple = EXPECTED_SEALS) -> dict:
    """The last seal line covers every byte above it (earlier seals and
    amendments included). Text after the last seal is an unsealed amendment.
    ``pinned``: the seal lines are exactly ``expected`` (the chain pinned in
    this script), so a protocol that was edited and re-sealed is caught."""
    p = path or protocol_path()
    if not p.exists():
        return {"exists": False, "sealed": False, "ok": False, "pinned": False}
    text = p.read_text(encoding="utf-8")
    i, sealed, after = _last_seal(text)
    actual = sha256_bytes(text[: max(i, 0) if i >= 0 else len(text)].encode("utf-8"))
    chain = seal_chain(text)
    values = [c["value"] for c in chain]
    return {"exists": True, "sealed": sealed is not None, "ok": sealed == actual and not after.strip(),
            "sha256": sealed if sealed == actual else actual, "sealed_value": sealed,
            "unsealed_amendment": bool(after.strip()), "n_seals": text.count(SEAL_PREFIX),
            "chain": values, "chain_ok": bool(chain) and all(c["ok"] for c in chain),
            "pinned": bool(values) and values == list(expected)}


def seal_protocol(path: Optional[Path] = None) -> str:
    """Seal the protocol, or seal a dated amendment appended below the last seal."""
    p = path or protocol_path()
    text = p.read_text(encoding="utf-8")
    i, value, after = _last_seal(text)
    if i >= 0:
        if sha256_bytes(text[:i].encode("utf-8")) != value:
            raise SystemExit("the protocol was edited above its last seal; restore it (git) rather than re-sealing")
        if not after.strip():
            return value
    body = text if text.endswith("\n") else text + "\n"
    h = sha256_bytes(body.encode("utf-8"))
    write_text_atomic(p, body + SEAL_PREFIX + f"`{h}`\n")
    return h


def prompt_sha_in_protocol(path: Optional[Path] = None) -> Optional[str]:
    p = path or protocol_path()
    if not p.exists():
        return None
    found = re.findall(r"Judge prompt sha256: `([0-9a-f]{64})`", p.read_text(encoding="utf-8"))
    return found[-1] if found else None


def points_hash_in_protocol(path: Optional[Path] = None) -> Optional[str]:
    p = path or protocol_path()
    if not p.exists():
        return None
    m = re.search(r"Point list sha256: `([0-9a-f]{64})`", p.read_text(encoding="utf-8"))
    return m.group(1) if m else None


_JUDGE_RX = {"A": r"Judge A: `([^`]+)`", "B": r"Judge B: `([^`]+)`", "C": r"Judge C[^`]*?: `([^`]+)`"}


def judges_in_protocol(path: Optional[Path] = None) -> dict:
    """The judge models the protocol names (the last mention of each wins, so a
    sealed amendment can change one)."""
    p = path or protocol_path()
    if not p.exists():
        return {}
    text = p.read_text(encoding="utf-8")
    out = {}
    for j, rx in _JUDGE_RX.items():
        found = re.findall(rx, text)
        if found:
            out[j] = found[-1]
    return out


def check_judges(specs: dict, path: Optional[Path] = None) -> None:
    """Sealed half: the judges must be PROTOCOL_JUDGES exactly (model, effort,
    thinking), and those models must be the ones the protocol names."""
    if {k: v for k, v in specs.items()} != PROTOCOL_JUDGES:
        raise SystemExit(f"sealed half: judges {specs} differ from the protocol's {PROTOCOL_JUDGES} "
                         "(use --judges mixed and leave VF_LLM_MODEL at the protocol's judge A)")
    named = judges_in_protocol(path)
    want = {k: v.model for k, v in PROTOCOL_JUDGES.items()}
    if named != want:
        raise SystemExit(f"sealed half: the protocol names judges {named}, the harness pins {want}")


def git_guard(root: Path, rel_paths: tuple = GUARDED_PATHS) -> dict:
    """Is every guarded path committed and unmodified? ``tracked``: each path is
    in the index; ``clean``: ``git status --porcelain`` lists nothing under them
    (untracked and modified files both show)."""
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True,
                                check=True).stdout.strip()
        por = subprocess.run(["git", "--no-optional-locks", "status", "--porcelain", "--untracked-files=all",
                              "--", *rel_paths], cwd=root, capture_output=True, text=True, check=True).stdout
        tracked = {}
        for rp in rel_paths:
            r = subprocess.run(["git", "ls-files", "--", rp], cwd=root, capture_output=True, text=True)
            tracked[rp] = bool(r.stdout.strip())
    except Exception as e:  # not a git checkout, or git missing
        return {"git_commit": None, "clean": False, "tracked": {}, "porcelain": [], "error": repr(e)[:200]}
    lines = [l for l in por.splitlines() if l.strip()]
    return {"git_commit": commit, "clean": not lines and all(tracked.values()), "tracked": tracked,
            "porcelain": lines[:30]}


def code_hashes(root: Optional[Path] = None) -> dict:
    """sha256 of this harness, the protocol and every vf module: the code the
    sealed numbers depend on beyond the frozen data bundle (the ranker, and
    vf.recipes' claim specialties, which the specialty baseline reads)."""
    root = root or paths.code_root()
    files = [root / SCRIPT, root / "reports" / "eval_sealed_protocol.md"] + sorted((root / "src" / "vf").glob("*.py"))
    return {str(f.relative_to(root)): sha256_file(f) for f in files if f.exists()}


def check_sealed_inputs(split: str, unseal: bool, stage: str = "picks", root: Optional[Path] = None,
                        protocol: Optional[Path] = None, expected: tuple = EXPECTED_SEALS) -> dict:
    """Refuse the sealed half unless asked, and refuse any run whose protocol or
    point list no longer matches its hash. For the sealed half also: the seal
    chain must be the one pinned in this script, and (for ``picks`` and
    ``judge``, the measuring stages) the protocol, this harness and src/vf must
    be committed and unmodified, so a commit dates the seal and the code."""
    root = root or paths.code_root()
    st = protocol_status(protocol, expected)
    pts = read_points()
    ph = points_hash(pts)
    want = points_hash_in_protocol(protocol)
    gg = git_guard(root)
    info = {"protocol": st, "points_sha256": ph, "points_sha256_protocol": want, "git": gg,
            "code_sha256": code_hashes(root)}
    if split == "sealed" and not unseal:
        raise SystemExit("the sealed half runs only with --unseal, once, on the frozen bundle (protocol rule 2)")
    if split == "sealed" or st.get("sealed"):
        if not st.get("ok"):
            raise SystemExit(f"protocol hash check failed: {st}")
        if want is not None and want != ph:
            raise SystemExit(f"point list hash {ph} does not match the protocol's {want}")
    if st.get("sealed") and not st.get("pinned"):
        msg = (f"the protocol's seal chain {st.get('chain')} is not the chain pinned in {SCRIPT} "
               f"(EXPECTED_SEALS {list(expected)})")
        if split == "sealed":
            raise SystemExit(msg + "; the sealed half refuses an unpinned or re-sealed protocol")
        print(f"warning: {msg}; dev runs go on, the sealed half would refuse", file=sys.stderr)
    if split == "sealed":
        ps = prompt_sha_in_protocol(protocol)
        if ps != PROMPT_SHA:
            raise SystemExit(f"judge prompt sha256 {PROMPT_SHA} does not match the protocol's {ps}")
        if stage in ("picks", "judge") and not gg.get("clean"):
            raise SystemExit("sealed half: commit the protocol, the harness and src/vf first; git reports "
                             f"{gg.get('porcelain') or gg.get('tracked') or gg.get('error')}")
        if not gg.get("clean"):
            print(f"warning: guarded code is not clean at stage {stage}: {gg.get('porcelain')}", file=sys.stderr)
    return info


# =============================================================================
# Step 2: the tools (no API)
# =============================================================================

#: Service regexes over fact and description text, a safety net for the judge
#: pool (facilities the tags missed). Independent of the tags and of TF-IDF.
KEYWORD_RX = {
    "emoc": r"\b(caesarean|cesarean|caeserean|c[- ]?sections?|lscs)\b",
    "nicu": r"\b(nicu|sncu|neonatal intensive|newborn intensive|special newborn care)\b",
    "dialysis": r"\b(dialysis|haemodialysis|hemodialysis)\b",
    "icu": r"\b(icu|intensive care|critical care unit|ventilators?)\b",
    "cardiac_surgery": r"\b(cabg|bypass surgery|open[- ]heart|valve (replacement|repair|surgery)|cardiac surgery|cardiothoracic)\b",
    "cath_lab": r"\b(cath[- ]?lab|catheteri[sz]ation lab|angioplasty|coronary angiography|ptca|stents?)\b",
    "mri": r"\b(mri|magnetic resonance)\b",
    "ct": r"\b(ct[- ]?scans?|cect|computed tomography|slice ct|ct imaging)\b",
    "trauma": r"\b(trauma|polytrauma|accident (and|&) emergency)\b",
    "burns": r"\b(burns? (unit|ward|care|centre|center|icu|management|treatment)|burn injur\w*)\b",
    "neurosurgery": r"\b(neurosurg\w*|craniotomy|brain surgery|spine surgery|head injur\w*)\b",
    "oncology": r"\b(chemotherapy|radiotherapy|radiation therapy|linear accelerator|linac|oncology)\b",
}
assert set(KEYWORD_RX) == set(S.SERVICE_LINES)

#: The need each judge is asked about, in plain words (not our recipe).
SERVICE_NEED = {
    "emoc": "an emergency caesarean section (C-section) for a woman in labour",
    "nicu": "intensive care for a sick newborn baby (a neonatal intensive care unit or special newborn care unit)",
    "dialysis": "haemodialysis for a patient with kidney failure",
    "icu": "intensive care with a ventilator for a critically ill adult",
    "cardiac_surgery": "heart surgery such as coronary bypass (CABG) or heart valve surgery",
    "cath_lab": "emergency heart attack treatment with coronary angiography and angioplasty in a cath lab",
    "mri": "an MRI scan",
    "ct": "a CT scan",
    "trauma": "emergency treatment of a seriously injured road accident victim (trauma care)",
    "burns": "treatment of serious burns (a burns unit or specialised burn care)",
    "neurosurgery": "brain or spine surgery, including surgery for a serious head injury",
    "oncology": "cancer treatment with chemotherapy or radiotherapy",
}
assert set(SERVICE_NEED) == set(S.SERVICE_LINES)


def _as_list(v) -> list:
    if v is None:
        return []
    if isinstance(v, float) and math.isnan(v):
        return []
    if isinstance(v, (list, tuple, np.ndarray)):
        return list(v)
    return [v]


def _s(v) -> Optional[str]:
    if v is None or v is pd.NA:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    t = str(v).strip()
    return t if t and t.lower() != "null" else None


_CAMEL = re.compile(r"(?<=[a-z])(?=[A-Z])")


def specialty_words(s: str) -> str:
    return _CAMEL.sub(" ", str(s)).lower()


def record_text(fac: dict, facts: list[dict]) -> str:
    """The record as the judges read it: what the dataset says about the
    facility, with nothing from our pipeline (no tags, grades, flags or scores)."""
    out = [f"Name: {_s(fac.get('name')) or '(no name)'}"]
    kind = [x for x in (_s(fac.get("facility_type")), _s(fac.get("operator_type"))) if x]
    if kind:
        out.append("Type: " + ", ".join(kind))
    addr = [x for x in (_s(fac.get("city_raw")), _s(fac.get("state_raw"))) if x]
    pin = _s(fac.get("pin"))
    if addr or pin:
        out.append("Listed address: " + ", ".join(addr) + (f" (PIN {pin})" if pin else ""))
    specs = [specialty_words(x) for x in _as_list(fac.get("specialties")) if _s(x)]
    if specs:
        out.append("Specialties listed: " + "; ".join(specs))
    nums = []
    for key, lab in (("n_doctors", "doctors"), ("n_beds", "beds"), ("year_established", "established")):
        v = fac.get(key)
        if v is not None and not (isinstance(v, float) and math.isnan(v)) and v is not pd.NA:
            nums.append(f"{lab}: {int(v)}")
    if nums:
        out.append("Numbers stated: " + ", ".join(nums))
    desc = _s(fac.get("description"))
    if desc:
        out.append("Description: " + desc)
    for fld in ("capability", "procedure", "equipment"):
        items = [f["text"] for f in facts if f["field"] == fld]
        if items:
            out.append(f"{fld.capitalize()} facts:")
            out.extend(f"- {t}" for t in items)
    return "\n".join(out)


@dataclass
class Ctx:
    """Everything the tools need, loaded once."""
    idx: Any  # vf.rank.Index
    rank_mod: Any
    silver: pd.DataFrame  # silver facilities in index position order
    facts_by_pos: dict  # pos -> list of {fact_id, field, text}
    fact_text: dict  # fact_id -> (facility_id, text)
    spec_mask: dict = field(default_factory=dict)  # line -> bool array
    kw_mask: dict = field(default_factory=dict)
    bundle_info: dict = field(default_factory=dict)

    def dup_group(self, pos: int) -> int:
        return int(self.idx.dup_canon.get(pos, pos))

    def fid(self, pos: int) -> str:
        return str(self.idx.fac["facility_id"].iat[pos])

    def record(self, pos: int) -> str:
        return record_text(self.silver.iloc[pos].to_dict(), self.facts_by_pos.get(pos, []))


def build_ctx(bundle: Optional[Path]) -> Ctx:
    from vf import rank as R
    idx = R.load_index(bundle, load_post_offices=False)
    silver = pd.read_parquet(paths.silver_dir() / "facilities.parquet")
    silver = silver.set_index("facility_id").reindex(idx.fac["facility_id"]).reset_index()
    facts = pd.read_parquet(paths.silver_dir() / "facts.parquet", columns=["fact_id", "facility_id", "field", "text"])
    facts["pos"] = facts["facility_id"].map(idx.id_to_pos)
    facts = facts[facts["pos"].notna()]
    facts["pos"] = facts["pos"].astype(int)
    by_pos: dict = {}
    for r in facts.itertuples(index=False):
        by_pos.setdefault(r.pos, []).append({"fact_id": r.fact_id, "field": r.field, "text": r.text})
    fact_text = dict(zip(facts["fact_id"], zip(facts["facility_id"], facts["text"])))
    ctx = Ctx(idx=idx, rank_mod=R, silver=silver, facts_by_pos=by_pos, fact_text=fact_text)
    n = len(idx.fac)
    specs = [set(_as_list(x)) for x in silver["specialties"]]
    desc = silver["description"].fillna("").astype(str)
    for line in S.SERVICE_LINES:
        want = recipes.GRADE_RULES[line].claim_specialties
        ctx.spec_mask[line] = np.array([bool(want & s) for s in specs], dtype=bool)
        rx = re.compile(re.sub(r"\((?!\?)", "(?:", KEYWORD_RX[line]), re.I)  # no capture groups
        hit = facts.loc[facts["text"].str.contains(rx, na=False), "pos"].unique()
        m = np.zeros(n, dtype=bool)
        m[hit] = True
        m |= desc.str.contains(rx, na=False).to_numpy()
        ctx.kw_mask[line] = m
    if bundle is not None and (Path(bundle) / "manifest.json").exists():
        man = Path(bundle) / "manifest.json"
        mj = json.loads(man.read_text())
        ctx.bundle_info = {"bundle_dir": str(bundle), "manifest_sha256": sha256_file(man),
                           "bundle_created_at_utc": mj.get("created_at_utc"), "bundle_git_commit": mj.get("git_commit"),
                           "code_uncommitted": mj.get("code_uncommitted")}
    else:
        ctx.bundle_info = {"bundle_dir": None, "source": "vf.paths.data_dir() silver and gold"}
    ctx.bundle_info["silver_facilities_sha256"] = sha256_file(paths.silver_dir() / "facilities.parquet")
    ctx.bundle_info["silver_facts_sha256"] = sha256_file(paths.silver_dir() / "facts.parquet")
    return ctx


def _pick(ctx: Ctx, pos: int, d: float) -> dict:
    return {"facility_id": ctx.fid(pos), "pos": int(pos), "name": str(ctx.idx.fac["name"].iat[pos]),
            "distance_km": round(float(d), 3), "dup_group": ctx.fid(ctx.dup_group(pos))}


def nearest_in(mask: np.ndarray, d: np.ndarray, R: float) -> Optional[int]:
    """Nearest position with ``mask`` inside ``R`` km (ties: lower position)."""
    ok = mask & ~np.isnan(d) & (d <= R)
    ix = np.flatnonzero(ok)
    if len(ix) == 0:
        return None
    return int(ix[np.lexsort((ix, d[ix]))][0])


def tool_specialty(ctx: Ctx, line: str, d: np.ndarray, R: float) -> Optional[int]:
    return nearest_in(ctx.spec_mask[line], d, R)


def tool_any_fact(ctx: Ctx, line: str, d: np.ndarray, R: float) -> Optional[int]:
    return nearest_in(ctx.idx.lines[line].grade >= 2, d, R)


def quote_check(ctx: Ctx, cards: list) -> dict:
    """Every quote on every card of an answer, against the record: ``strict``
    means the quote equals the text of the fact it cites (same facility);
    ``loose`` means it appears inside one of the facility's facts or its description."""
    n = strict = loose = 0
    bad = []
    for c in cards:
        if not c:
            continue
        fid = c.get("facility_id")
        pos = ctx.idx.id_to_pos.get(fid)
        texts = [f["text"] for f in ctx.facts_by_pos.get(pos, [])] if pos is not None else []
        desc = _s(ctx.silver["description"].iat[pos]) if pos is not None else None
        for ev in c.get("evidence") or []:
            q = ev.get("quote")
            if not q:
                continue
            n += 1
            ft = ctx.fact_text.get(ev.get("fact_id"))
            ok_s = ft is not None and ft[0] == fid and ft[1] == q
            ok_l = ok_s or any(q in t for t in texts) or (desc is not None and q in desc)
            strict += ok_s
            loose += ok_l
            if not ok_l:
                bad.append({"facility_id": fid, "fact_id": ev.get("fact_id"), "quote": q[:200]})
    return {"n_quotes": n, "strict": strict, "loose": loose, "failures": bad[:20]}


def tool_ranker(ctx: Ctx, line: str, lat: float, lon: float, R: float, d: np.ndarray, label: str) -> dict:
    res = {"status": "ok", "lat": lat, "lon": lon, "precision_km": 2.0, "kind": "post_office",
           "how": "Evaluation point: the post office's own coordinates.", "label": label}
    ans = ctx.rank_mod.rank(ctx.idx, line, res, k=3)
    a = ans.to_dict()
    pick = None
    for c in a.get("results") or []:
        pos = ctx.idx.id_to_pos.get(c["facility_id"])
        if pos is not None and not math.isnan(d[pos]) and d[pos] <= R:
            pick = pos
            break
    cards = list(a.get("results") or []) + ([a["beyond_radius"]] if a.get("beyond_radius") else []) + \
        list(a.get("unverified_nearby") or [])
    return {"pos": pick, "status": a.get("status"), "n_results": len(a.get("results") or []),
            "score_status": (a.get("how_we_decided") or {}).get("score_status"),
            "result_ids": [c["facility_id"] for c in a.get("results") or []],
            "quote_check": quote_check(ctx, cards)}


def judge_pool(ctx: Ctx, line: str, d: np.ndarray, d_stop: float) -> list[int]:
    """Facilities the judges may read while looking for the nearest
    judge-supported option: usable location, within ``d_stop`` km, and any
    signal for the service (a grade of 1 or more by our tags, a related
    specialty, or a keyword in its text). Nearest first."""
    sig = (ctx.idx.lines[line].grade >= 1) | ctx.spec_mask[line] | ctx.kw_mask[line]
    ok = sig & ~np.isnan(d) & (d <= d_stop)
    ix = np.flatnonzero(ok)
    return [int(p) for p in ix[np.lexsort((ix, d[ix]))]]


def run_tools(ctx: Ctx, q: dict) -> dict:
    line, R = q["service"], float(q["radius_km"])
    d = haversine_km(q["lat"], q["lon"], ctx.idx.lat, ctx.idx.lon)
    label = f"{q['office_name']} post office, {q['district']} district, {q['state']} (PIN {q['pin']})"
    out = {k: q[k] for k in ("query_id", "po_id", "split", "service", "radius_km", "lat", "lon", "office_name",
                             "pin", "state", "district")}
    out["place_label"] = label
    tools = {}
    p = tool_specialty(ctx, line, d, R)
    tools["specialty"] = {"pick": _pick(ctx, p, d[p]) if p is not None else None}
    p = tool_any_fact(ctx, line, d, R)
    tools["any_fact"] = {"pick": _pick(ctx, p, d[p]) if p is not None else None}
    rk = tool_ranker(ctx, line, q["lat"], q["lon"], R, d, label)
    p = rk.pop("pos")
    tools["ranker"] = {"pick": _pick(ctx, p, d[p]) if p is not None else None, **rk}
    out["tools"] = tools
    picks = [t["pick"]["distance_km"] for t in tools.values() if t["pick"]]
    any_abstain = any(t["pick"] is None for t in tools.values())
    d_stop = R if any_abstain or not picks else max(picks)
    pool = judge_pool(ctx, line, d, d_stop)
    out["d_stop_km"] = round(float(d_stop), 3)
    out["pool_size"] = len(pool)
    out["pool"] = [[ctx.fid(p), round(float(d[p]), 3), ctx.fid(ctx.dup_group(p))] for p in pool[:POOL_STORE]]
    # WP11 major flags for this line (record-level ones included) on every pick
    # and stored pool entry: the judges never see them, the report splits the
    # "skipped" and "abstained" rates by them.
    fl = {}
    for p in {pk["pos"] for pk in (t["pick"] for t in tools.values()) if pk} | set(pool[:POOL_STORE]):
        names = major_flags_for(ctx, line, p)
        if names:
            fl[ctx.fid(p)] = names
    out["major_flags"] = fl
    return out


def major_flags_for(ctx: Ctx, line: str, pos: int) -> list[str]:
    """Names of the WP11 major flags that block ``pos`` for ``line`` in the ranker."""
    ld = ctx.idx.lines.get(line) if hasattr(ctx.idx.lines, "get") else None
    fl = getattr(ld, "major_flags", None) or {}
    return sorted({str(f.get("flag")) for f in fl.get(int(pos), [])})


# =============================================================================
# Step 3: judges
# =============================================================================

SYSTEM_Q1_J1 = """You are reading one health facility's record for a research study in India. The record was compiled automatically from web pages, so it can be incomplete, repetitive, out of date, or mix in text about other places.

You will be told one service a patient needs. Decide whether the record CLEARLY SHOWS that THIS facility offers that service.

- clearly_offers = true only if text in the record states or directly shows that this facility provides the service.
- clearly_offers = false if the record only makes the service seem likely, if the text that mentions the service is about a different facility, branch or organisation, if the mention is negated, or if you cannot tell.
- Judge the text only. Do not use anything you may know about the facility from elsewhere.
- quote: copy, character for character, the one line from the record that best shows the service. Use an empty string if nothing in the record comes close.
- reason: one short plain sentence."""

#: Candidate rubric j2 (D051 item 2): j1 plus one rule against yes votes that
#: rest on a specialty or department name alone. NOT adopted: on the full dev
#: half 8 of 107 final yes verdicts (7.5%) were listing-only, under the 10% line
#: fixed beforehand (reports/eval_dev_rubric_check.md). Active only through a
#: sealed amendment (KIND_PROMPT_VERSION); usable on the dev half by `rejudge`.
SYSTEM_Q1_J2 = SYSTEM_Q1_J1.replace(
    "- Judge the text only.",
    "- A specialty, department or doctor's qualification alone (for example a listed specialty, \"Department of "
    "Nephrology\" or \"MD (Obstetrics and Gynaecology)\") does not show that the service is offered: "
    "clearly_offers = true needs text that names the service itself, or a unit or equipment that provides it, at "
    "this facility.\n- Judge the text only.", 1)
assert SYSTEM_Q1_J2 != SYSTEM_Q1_J1

Q1_PROMPTS = {"j1": SYSTEM_Q1_J1, "j2": SYSTEM_Q1_J2}
SYSTEM_Q1 = Q1_PROMPTS[KIND_PROMPT_VERSION["q1"]]

SYSTEM_Q2 = """You are checking, for a research study in India, whether a facility that a search tool returned is a sensible place to send a patient. You get the patient's place (a post office area), the service needed, the straight-line distance from that place to where the facility is mapped, and the facility's record. The record was compiled automatically from web pages, so it can be incomplete or mix in text about other places.

For this question, assume the facility does offer the service. Decide whether sending this patient there is sensible, judging from the record:
- sensible = false if the record places the facility somewhere clearly inconsistent with the stated distance (for example, its listed city or state is far from the patient's place while the distance is short, or the record says it is located elsewhere);
- sensible = false if the record describes several branches or appears to merge several facilities, so that it is unclear which building the patient would reach;
- sensible = false if this kind of facility does not fit the service (for example, a clinic, laboratory, pharmacy or a specialty centre of an unrelated kind);
- otherwise sensible = true. Distance alone is not a reason for false: every facility shown is inside the same search radius for this service.
- reason: one short plain sentence. Judge the text only."""

SCHEMA_Q1 = {"type": "object", "additionalProperties": False, "required": ["clearly_offers", "quote", "reason"],
             "properties": {"clearly_offers": {"type": "boolean"}, "quote": {"type": "string"},
                            "reason": {"type": "string"}}}
SCHEMA_Q2 = {"type": "object", "additionalProperties": False, "required": ["sensible", "reason"],
             "properties": {"sensible": {"type": "boolean"}, "reason": {"type": "string"}}}
ANSWER_KEY = {"q1": "clearly_offers", "q2": "sensible"}


def user_q1(service: str, record: str) -> str:
    return f"Service the patient needs: {SERVICE_NEED[service]}.\n\nThe facility's record:\n{record}"


def user_q2(service: str, place_label: str, distance_km: float, record: str) -> str:
    return (f"Patient's place: {place_label}.\nService the patient needs: {SERVICE_NEED[service]}.\n"
            f"Straight-line distance from the place to the facility's mapped location: {distance_km:.1f} km.\n\n"
            f"The facility's record:\n{record}")


def prompt_sha(version: str, system_q1: str) -> str:
    """sha256 of a prompt set: its version label, both system prompts, both
    schemas and the service needs (the protocol's "Judge prompt sha256")."""
    return sha256_bytes((version + system_q1 + SYSTEM_Q2 + json.dumps(SCHEMA_Q1, sort_keys=True)
                         + json.dumps(SCHEMA_Q2, sort_keys=True) + json.dumps(SERVICE_NEED, sort_keys=True)
                         ).encode("utf-8"))


PROMPT_SHA = prompt_sha(PROMPT_VERSION, SYSTEM_Q1)


class StopJudging(Exception):
    """The call cap or the budget would be passed."""


def cost_usd(model: str, usage: dict) -> float:
    pin, pout = PRICES[model]
    prompt = usage.get("input_tokens", 0) + usage.get("cache_creation_input_tokens", 0) + \
        usage.get("cache_read_input_tokens", 0)
    if model == "claude-haiku-5-5" and prompt > LOW_RATE_MAX_PROMPT:
        raise RuntimeError(f"prompt of {prompt} tokens is above the {LOW_RATE_MAX_PROMPT} rate-card limit")
    return (usage.get("input_tokens", 0) * pin + usage.get("cache_creation_input_tokens", 0) * pin * 1.25
            + usage.get("cache_read_input_tokens", 0) * pin * 0.1 + usage.get("output_tokens", 0) * pout) / 1e6


def ledger_total(path: Path) -> tuple[float, int]:
    total, n = 0.0, 0
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip():
                total += json.loads(line).get("cost_usd", 0.0)
                n += 1
    return total, n


class Budget:
    """Checks before every call: this evaluation's cap, the project's budget
    minus every other ledger, and an optional cap on the number of calls."""

    def __init__(self, ledger: Path, cap_usd: float, project_budget: Optional[float], max_calls: Optional[int]):
        self.ledger = ledger
        self.spent, self.n_ledger = ledger_total(ledger)
        self.others = sum(ledger_total(p)[0] for p in other_ledgers())
        self.cap = cap_usd
        self.project_budget = project_budget
        self.max_calls = max_calls
        self.calls = 0
        self.reserved = 0.0
        self.lock = threading.Lock()
        self.stopped: Optional[str] = None

    def reserve(self, amount: float) -> None:
        with self.lock:
            if self.max_calls is not None and self.calls >= self.max_calls:
                self.stopped = self.stopped or f"call cap {self.max_calls} reached"
                raise StopJudging(self.stopped)
            if self.spent + self.reserved + amount > self.cap:
                self.stopped = self.stopped or f"eval cap ${self.cap:.2f} would be passed"
                raise StopJudging(self.stopped)
            if self.project_budget is not None and \
                    self.others + self.spent + self.reserved + amount > self.project_budget:
                self.stopped = self.stopped or f"project budget ${self.project_budget:.2f} would be passed"
                raise StopJudging(self.stopped)
            self.calls += 1
            self.reserved += amount

    def settle(self, reserved: float, actual: float, row: Optional[dict]) -> None:
        with self.lock:
            self.reserved -= reserved
            self.spent += actual
            if row is not None:
                paths.ensure(self.ledger.parent)
                with open(self.ledger, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def worst_case_usd(model: str, n_chars: int, max_tokens: int) -> float:
    pin, pout = PRICES[model]
    est_in = 1200 + n_chars // 3
    return (est_in * pin * 1.25 + max_tokens * pout) / 1e6


def anthropic_call(client, spec: JudgeSpec, kind: str, system: str, user: str) -> dict:
    """One Messages API request with a JSON-schema answer. Returns
    {answer (dict or None), usage, stop_reason, request_id, model}."""
    schema = SCHEMA_Q1 if kind == "q1" else SCHEMA_Q2
    kw = dict(model=spec.model, max_tokens=MAX_TOKENS[kind], system=system,
              output_config={"effort": spec.effort, "format": {"type": "json_schema", "schema": schema}},
              messages=[{"role": "user", "content": user}])
    if spec.thinking is not None:
        kw["thinking"] = spec.thinking
    resp = client.messages.create(**kw)
    u = resp.usage
    usage = {k: int(getattr(u, k, 0) or 0) for k in
             ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")}
    out = {"usage": usage, "stop_reason": resp.stop_reason, "request_id": getattr(resp, "_request_id", None),
           "model": getattr(resp, "model", spec.model), "answer": None}
    if resp.stop_reason in ("end_turn", "stop_sequence"):
        text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), None)
        if text:
            try:
                ans = json.loads(text)
                if isinstance(ans.get(ANSWER_KEY[kind]), bool):
                    out["answer"] = ans
            except json.JSONDecodeError:
                pass
    return out


class JudgeRunner:
    """Calls judges with a disk cache, the budget check and a ledger."""

    def __init__(self, specs: dict[str, JudgeSpec], budget: Optional[Budget], client=None,
                 call_fn: Callable = anthropic_call, cache_dir: Optional[Path] = None, purpose: str = "eval",
                 versions: Optional[dict] = None):
        self.specs = specs
        self.versions = dict(versions or KIND_PROMPT_VERSION)
        self.budget = budget
        self.client = client
        self.call_fn = call_fn
        self.cache_root = cache_dir or (out_dir() / "judge_cache")  # then <version>/<judge>/<key>.json
        self.purpose = purpose
        self.n_cached = 0
        self.n_api = 0
        self.lock = threading.Lock()

    def _key(self, spec: JudgeSpec, kind: str, system: str, user: str) -> str:
        return sha256_bytes(json.dumps([self.versions[kind], kind, spec.model, spec.effort, spec.thinking,
                                        sha256_bytes(system.encode()), user], ensure_ascii=False).encode("utf-8"))

    def ask(self, judge: str, kind: str, system: str, user: str, unit_id: str) -> dict:
        spec = self.specs[judge]
        key = self._key(spec, kind, system, user)
        cp = self.cache_root / self.versions[kind] / spec.name / f"{key}.json"
        if cp.exists():
            with self.lock:
                self.n_cached += 1
            return json.loads(cp.read_text())
        if self.budget is None:
            raise StopJudging("no API budget in this run")
        need = worst_case_usd(spec.model, len(system) + len(user), MAX_TOKENS[kind])
        self.budget.reserve(need)
        t0 = time.time()
        try:
            res = self.call_fn(self.client, spec, kind, system, user)
        except Exception as e:
            self.budget.settle(need, 0.0, None)
            return {"answer": None, "error": repr(e)[:300], "judge": judge, "model": spec.model}
        c = cost_usd(spec.model, res["usage"])
        row = {"at": now_utc(), "purpose": self.purpose, "judge": judge, "model": spec.model, "kind": kind,
               "unit_id": unit_id, "prompt_version": self.versions[kind], "stop_reason": res.get("stop_reason"),
               "request_id": res.get("request_id"), "usage": res["usage"], "cost_usd": round(c, 8),
               "seconds": round(time.time() - t0, 2)}
        self.budget.settle(need, c, row)
        with self.lock:
            self.n_api += 1
        rec = {"judge": judge, "model": spec.model, "kind": kind, "unit_id": unit_id, "key": key,
               "answer": res.get("answer"), "stop_reason": res.get("stop_reason"), "usage": res["usage"],
               "cost_usd": c, "at": row["at"], "request_id": res.get("request_id")}
        if res.get("answer") is not None:  # failures are not cached, so a rerun retries them
            write_json_atomic(cp, rec)
        return rec


def consensus(votes: dict) -> Optional[bool]:
    """Two agreeing valid votes decide; otherwise the majority of the valid votes
    among A, B and C, if two agree; else None (unresolved)."""
    vals = [v for k, v in votes.items() if k in ("A", "B", "C") and v is not None]
    if votes.get("A") is not None and votes.get("A") == votes.get("B"):
        return votes["A"]
    if sum(vals) >= 2:
        return True
    if sum(1 for v in vals if not v) >= 2:
        return False
    return None


class Units:
    """Judged units, computed once each (thread-safe): Q1 per (facility,
    service), Q2 per (query, facility)."""

    def __init__(self, runner: JudgeRunner):
        self.runner = runner
        self.done: dict = {}
        self.locks: dict = {}
        self.lock = threading.Lock()

    def _lock_for(self, key) -> threading.Lock:
        with self.lock:
            return self.locks.setdefault(key, threading.Lock())

    def judge(self, kind: str, unit_id: str, system: str, user: str, meta: dict) -> dict:
        key = (kind, unit_id)
        with self._lock_for(key):
            if key in self.done:
                return self.done[key]
            ak = ANSWER_KEY[kind]
            recs = {}
            with ThreadPoolExecutor(2) as ex:
                fa = ex.submit(self.runner.ask, "A", kind, system, user, unit_id)
                fb = ex.submit(self.runner.ask, "B", kind, system, user, unit_id)
                errs = []
                for j, f in (("A", fa), ("B", fb)):
                    try:
                        recs[j] = f.result()
                    except StopJudging as e:
                        errs.append(e)
                if errs:
                    raise errs[0]
            votes = {j: (r.get("answer") or {}).get(ak) for j, r in recs.items()}
            if not (votes["A"] is not None and votes["A"] == votes["B"]):
                recs["C"] = self.runner.ask("C", kind, system, user, unit_id)
                votes["C"] = (recs["C"].get("answer") or {}).get(ak)
            final = consensus(votes)
            out = {"kind": kind, "unit_id": unit_id, **meta, "votes": votes, "final": final, "complete": True,
                   "third_pass": "C" in recs,
                   "answers": {j: r.get("answer") for j, r in recs.items()},
                   "cost_usd": sum(r.get("cost_usd", 0.0) or 0.0 for r in recs.values())}
            self.done[key] = out
            return out


def judge_query(units: Units, ctx_records: Callable[[str], str], q: dict, walk_cap: int = WALK_CAP) -> dict:
    """Judge one query: each distinct pick (Q1 and Q2), then the pool nearest
    first until a facility clearly shows the service (Q1). Returns the query's
    judged state; raises StopJudging when the cap or budget stops it."""
    line = q["service"]
    picks = {}
    for t in TOOLS:
        pk = q["tools"][t]["pick"]
        if pk:
            picks[pk["facility_id"]] = pk
    q1 = {}
    q2 = {}
    for fid, pk in picks.items():
        rec = ctx_records(fid)
        q1[fid] = units.judge("q1", f"{fid}|{line}", SYSTEM_Q1, user_q1(line, rec),
                              {"facility_id": fid, "service": line})["final"]
        q2[fid] = units.judge("q2", f"{q['query_id']}|{fid}", SYSTEM_Q2,
                              user_q2(line, q["place_label"], pk["distance_km"], rec),
                              {"facility_id": fid, "service": line, "query_id": q["query_id"]})["final"]
    walked = 0
    nearest_yes = None
    status = "exhausted"
    for fid, dist, grp in q["pool"]:
        if fid in q1:
            v = q1[fid]
        else:
            if walked >= walk_cap:
                status = "capped"
                break
            v = units.judge("q1", f"{fid}|{line}", SYSTEM_Q1, user_q1(line, ctx_records(fid)),
                            {"facility_id": fid, "service": line})["final"]
            q1[fid] = v
            walked += 1
        if v:
            nearest_yes = {"facility_id": fid, "distance_km": dist, "dup_group": grp}
            status = "found"
            break
    if status == "exhausted" and q["pool_size"] > len(q["pool"]):
        status = "capped"  # the stored pool was cut; treat as not resolved
    return {"query_id": q["query_id"], "q1": q1, "q2": q2, "walked": walked, "walk_status": status,
            "nearest_yes": nearest_yes, "complete": True}


# =============================================================================
# Step 4: scoring (pure, tested)
# =============================================================================


SPEC_LINE = "Specialties listed: "


def listing_only_yes(unit: dict, record: str, mode: str = "all") -> bool:
    """A "yes" that rests only on the specialties listing: the verdict is yes and
    every judge who said yes quoted text that appears in the record's
    "Specialties listed" line and in no other line. The Q1 rubric says a record
    that only makes the service seem likely is a "no", and the specialty
    baseline selects on that same field, so these are reported apart.
    ``mode="any"``: at least one judge who said yes quoted only that line (a
    looser reading: the other yes may rest on a department list elsewhere)."""
    if not unit or unit.get("final") is not True:
        return False
    lines = record.splitlines()
    spec = [l.lower() for l in lines if l.startswith(SPEC_LINE)]
    other = [l.lower() for l in lines if not l.startswith(SPEC_LINE)]
    if not spec:
        return False
    yes = [j for j, v in (unit.get("votes") or {}).items() if v is True]
    if not yes:
        return False
    hits = []
    for j in yes:
        qt = str(((unit.get("answers") or {}).get(j) or {}).get("quote") or "").strip().strip('"').strip()
        qt = re.sub(r"^-\s*", "", qt).lower()
        if qt.startswith(SPEC_LINE.lower()):
            qt = qt[len(SPEC_LINE):].strip()
        hits.append(bool(qt) and any(qt in s for s in spec) and not any(qt in o for o in other))
    return any(hits) if mode == "any" else all(hits)


def _lo_level(v) -> Optional[str]:
    """Listing-only level of a Q1 unit: "all" (every yes voter quoted only the
    specialties line), "any" (at least one did), or None."""
    if v is True or v == "all":
        return "all"
    return "any" if v == "any" else None


def outcome_rows(q: dict, jq: dict, listing_only: Optional[dict] = None,
                 flags: Optional[dict] = None) -> list[dict]:
    """Per tool outcome for one judged query. ``listing_only``: Q1 unit id ->
    whether its yes rests only on the specialties listing. ``flags``: facility id
    -> WP11 major flags for this line (default: the ones stored with the picks)."""
    rows = []
    R = float(q["radius_km"])
    ny = jq.get("nearest_yes")
    lo = listing_only or {}
    fl_known = flags is not None or "major_flags" in q  # picks made before flags were stored: unknown
    fl = (q.get("major_flags") if flags is None else flags) or {}
    line = q["service"]
    ny_flags = fl.get(ny["facility_id"], []) if ny else []
    ny_lv = _lo_level(lo.get(f"{ny['facility_id']}|{line}")) if ny else None
    ny_lo = (ny_lv == "all") if ny else None
    for t in TOOLS:
        pk = q["tools"][t]["pick"]
        r = {"query_id": q["query_id"], "po_id": q["po_id"], "service": q["service"], "state": q["state"],
             "tool": t, "radius_km": R, "walk_status": jq["walk_status"],
             "nearest_yes_km": ny["distance_km"] if ny else None,
             "nearest_yes_id": ny["facility_id"] if ny else None,
             "nearest_yes_major_flags": "|".join(ny_flags) if ny else None,
             "nearest_yes_flagged": (bool(ny_flags) if fl_known else None) if ny else None,
             "nearest_yes_listing_only": ny_lo, "nearest_yes_listing_any": (ny_lv is not None) if ny else None,
             "flags_known": fl_known}
        if pk is None:
            r.update(answered=False, pick_id=None, pick_km=None, q1=None, q2=None, correct=None, good=None,
                     pick_listing_only=None, pick_listing_any=None, pick_major_flags=None, skipped_nearer=None,
                     abstained_with_support=bool(ny is not None and ny["distance_km"] <= R))
        else:
            v1 = jq["q1"].get(pk["facility_id"])
            v2 = jq["q2"].get(pk["facility_id"])
            skipped = bool(ny is not None and ny["dup_group"] != pk["dup_group"]
                           and ny["distance_km"] < pk["distance_km"] - SKIP_MARGIN_KM)
            r.update(answered=True, pick_id=pk["facility_id"], pick_km=pk["distance_km"], q1=v1, q2=v2,
                     correct=bool(v1) if v1 is not None else False, good=bool(v1 and v2),
                     pick_listing_only=_lo_level(lo.get(f"{pk['facility_id']}|{line}")) == "all",
                     pick_listing_any=_lo_level(lo.get(f"{pk['facility_id']}|{line}")) is not None,
                     pick_major_flags="|".join(fl.get(pk["facility_id"], [])),
                     skipped_nearer=skipped, abstained_with_support=None)
        if t == "ranker":
            qc = q["tools"]["ranker"].get("quote_check") or {}
            r.update(quotes=qc.get("n_quotes"), quotes_strict=qc.get("strict"), quotes_loose=qc.get("loose"),
                     ranker_status=q["tools"]["ranker"].get("status"))
        rows.append(r)
    return rows


def _split_by_nearest(sub: pd.DataFrame, hit_col: str, n: int) -> dict:
    """``hit_col`` rows split by the nearest judge-supported facility: carries a
    WP11 major flag for this line (skipped or passed over on purpose, the
    judges did not see flags) or not; and whether its yes rests only on the
    specialties listing."""
    hit = sub[sub[hit_col].astype(bool)] if len(sub) else sub
    fl = hit["nearest_yes_flagged"].astype(bool) if len(hit) and "nearest_yes_flagged" in hit else pd.Series(dtype=bool)
    lo = hit["nearest_yes_listing_only"].astype(bool) if len(hit) and "nearest_yes_listing_only" in hit \
        else pd.Series(dtype=bool)
    la = hit["nearest_yes_listing_any"].astype(bool) if len(hit) and "nearest_yes_listing_any" in hit \
        else pd.Series(dtype=bool)
    return {"nearest_flagged": _rate(int(fl.sum()), n), "nearest_unflagged": _rate(int(len(hit) - fl.sum()), n),
            "nearest_listing_only": _rate(int(lo.sum()), n), "nearest_listing_any": _rate(int(la.sum()), n),
            "nearest_unflagged_not_listing_only": _rate(int((~fl & ~lo).sum()) if len(hit) else 0, n)}


def _rate(k: int, n: int) -> dict:
    lo, hi = wilson(k, n)
    return {"k": int(k), "n": int(n), "rate": (k / n) if n else None, "ci95": [lo, hi]}


def summarize(rows: pd.DataFrame) -> dict:
    out = {}
    nq = rows["query_id"].nunique() if len(rows) else 0
    for t in TOOLS:
        r = rows[rows["tool"] == t]
        ans = r[r["answered"]]
        abst = r[~r["answered"]]
        m = {
            "queries": int(len(r)),
            "coverage": _rate(int(ans.shape[0]), int(len(r))),
            "precision": _rate(int(ans["correct"].astype(bool).sum()), int(len(ans))),
            "good_pick": _rate(int(ans["good"].astype(bool).sum()), int(len(ans))),
            "pick_unresolved": int(ans["q1"].isna().sum()),
            "abstained_with_support": _rate(int(abst["abstained_with_support"].astype(bool).sum()), int(len(abst))),
            "abstained_with_support_of_all": _rate(int(abst["abstained_with_support"].astype(bool).sum()), int(len(r))),
            "skipped_nearer": _rate(int(ans["skipped_nearer"].astype(bool).sum()), int(len(ans))),
            "wrong_picks_of_all": _rate(int((~ans["correct"].astype(bool)).sum()), int(len(r))),
        }
        plo = ans["pick_listing_only"].astype(bool) if "pick_listing_only" in ans and len(ans) else \
            pd.Series(False, index=ans.index)
        pla = ans["pick_listing_any"].astype(bool) if "pick_listing_any" in ans and len(ans) else \
            pd.Series(False, index=ans.index)
        m["pick_yes_listing_only"] = int((ans["correct"].astype(bool) & plo).sum()) if len(ans) else 0
        m["pick_yes_listing_any"] = int((ans["correct"].astype(bool) & pla).sum()) if len(ans) else 0
        m["precision_excluding_listing_only"] = _rate(
            int((ans["correct"].astype(bool) & ~plo).sum()) if len(ans) else 0, int(len(ans)))
        m["precision_excluding_listing_any"] = _rate(
            int((ans["correct"].astype(bool) & ~pla).sum()) if len(ans) else 0, int(len(ans)))
        if "flags_known" in r and len(r) and r["flags_known"].astype(bool).all():
            m["abstained_with_support_split"] = _split_by_nearest(abst, "abstained_with_support", int(len(abst)))
            m["skipped_nearer_split"] = _split_by_nearest(ans, "skipped_nearer", int(len(ans)))
        out[t] = m
    integ = {}
    rk = rows[rows["tool"] == "ranker"] if len(rows) else rows
    if "quotes" in rk and len(rk):
        nq_ = int(pd.to_numeric(rk["quotes"], errors="coerce").fillna(0).sum())
        integ = {"card_quotes_match_cited_fact": {
            **_rate(int(pd.to_numeric(rk["quotes_strict"], errors="coerce").fillna(0).sum()), nq_),
            "label": INTEGRITY_LABEL},
            "card_quotes_found_in_record": _rate(int(pd.to_numeric(rk["quotes_loose"], errors="coerce").fillna(0).sum()),
                                                 nq_)}
    by_service = {}
    for s, g in rows.groupby("service"):
        by_service[s] = {t: {"answered": int(g[(g.tool == t) & g.answered].shape[0]),
                             "correct": int(g[(g.tool == t) & g.answered]["correct"].astype(bool).sum()),
                             "queries": int(g[g.tool == t].shape[0])} for t in TOOLS}
    walk = rows.drop_duplicates("query_id")["walk_status"].value_counts().to_dict() if len(rows) else {}
    return {"n_queries": int(nq), "tools": out, "by_service": by_service, "walk_status": walk,
            "integrity_checks": integ}


#: The card-quote figure holds by construction (the ranker's quote IS the cited
#: fact's text), so it is an integrity check of the gold against the silver
#: files, never a judged or evaluation result.
INTEGRITY_LABEL = ("Integrity check, not a judged result: every quote on a card is the exact text of the fact it "
                   "cites. It holds by construction, so it confirms that the bundle and the silver facts agree; "
                   "it does not measure reading accuracy.")


def fmt_rate(m: dict, pct: bool = True) -> str:
    if not m or m.get("n", 0) == 0:
        return "n/a (0)"
    lo, hi = m["ci95"]
    return f"{m['k']} of {m['n']} ({m['rate']:.0%}; 95% CI {lo:.0%} to {hi:.0%})"


def _split_line(m: dict, what: str) -> str:
    s = m.get(what)
    if not s:
        return ""
    n_hit = s["nearest_flagged"]["k"] + s["nearest_unflagged"]["k"]
    if n_hit == 0:
        return ""
    return (f" Of those {n_hit}, the nearest supported record carries a WP11 major flag for this service in "
            f"{s['nearest_flagged']['k']} (the ranker passes such records over on purpose; the judges did not see "
            f"flags) and none in {s['nearest_unflagged']['k']}. Its yes rests only on the specialties listing in "
            f"{s['nearest_listing_only']['k']} (strict: every judge who said yes quoted only that line) or "
            f"{s['nearest_listing_any']['k']} (loose: at least one did).")


def render_results_md(split: str, res: dict, meta: dict) -> str:
    t = res["tools"]
    rk, an, sp = t["ranker"], t["any_fact"], t["specialty"]
    L = [f"# Sealed evaluation results: {split} half", ""]
    if split == "dev":
        L += ["**Dev half. A dry run of the harness, not the result.** Numbers below cover only the queries "
              "that were fully judged in this run; they are too few to read as rates.", ""]
    n_abst = rk["coverage"]["n"] - rk["coverage"]["k"]
    L += ["## The cost of our caution (lead)", "",
          f"- Our ranker abstained on {n_abst} of {rk['coverage']['n']} judged "
          f"queries. In {fmt_rate(rk['abstained_with_support'])} of those abstentions, the AI judges found a "
          "facility inside the same radius whose record clearly shows the service." +
          _split_line(rk, "abstained_with_support_split"),
          f"- As a share of all judged queries, that is {fmt_rate(rk['abstained_with_support_of_all'])}. "
          "This is a lower bound: the judges read only facilities with some signal for the service, at most "
          f"{WALK_CAP} per query.",
          f"- When the ranker did answer, it skipped a nearer judge-supported option in "
          f"{fmt_rate(rk['skipped_nearer'])} of answers (at least {SKIP_MARGIN_KM:.0f} km nearer, a different record "
          "group)." + _split_line(rk, "skipped_nearer_split"),
          "", "## All three tools, same radius, same right to abstain", "",
          "| | Specialty filter | Any fact names it | Our ranker |", "| --- | --- | --- | --- |"]

    def row(name, key):
        return f"| {name} | {fmt_rate(sp.get(key))} | {fmt_rate(an.get(key))} | {fmt_rate(rk.get(key))} |"

    L += [row("Places answered (coverage)", "coverage"),
          row("Top pick: record clearly shows the service (precision)", "precision"),
          row("Precision, not counting yes votes that rest only on the specialties listing",
              "precision_excluding_listing_only"),
          row("Precision, not counting yes votes where any judge quoted only the specialties listing",
              "precision_excluding_listing_any"),
          row("Top pick also sensible for the place", "good_pick"),
          row("Abstained when a judge-supported option existed (of abstentions)", "abstained_with_support"),
          row("Skipped a nearer judge-supported option (of answers)", "skipped_nearer"),
          row("Wrong top pick, as a share of all queries", "wrong_picks_of_all"), ""]
    L += ["Yes votes resting only on the specialties listing (the judges quoted only the \"Specialties listed\" "
          "line): specialty " + str(sp.get("pick_yes_listing_only", 0)) + ", any fact " +
          str(an.get("pick_yes_listing_only", 0)) + ", ranker " + str(rk.get("pick_yes_listing_only", 0)) +
          " top picks (at least one judge quoted only that line: specialty " + str(sp.get("pick_yes_listing_any", 0)) +
          ", any fact " + str(an.get("pick_yes_listing_any", 0)) + ", ranker " + str(rk.get("pick_yes_listing_any", 0)) +
          "). The Q1 rubric says a record that only makes the service seem likely is a no, and the "
          "specialty filter selects on that same field, so its precision is shown both ways.", ""]
    ic = (res.get("integrity_checks") or {}).get("card_quotes_match_cited_fact")
    if ic:
        L += ["## Integrity check (not part of the evaluation)", "",
              f"- Every quote on a ranker card is the exact text of the fact it cites: {ic['k']} of {ic['n']}. "
              "This holds by construction (the card's quote is the cited fact's text), so it confirms that the "
              "bundle and the silver facts agree. It is not a judged result and does not measure reading accuracy.",
              ""]
    L += ["## How to read this", "",
          "- Judges are AI models (" + ", ".join(f"{k}: {v}" for k, v in meta.get("judges", {}).items()) +
          "), blind to which tool produced each pick. Disagreements between A and B went to C. "
          "They are checked against hand labels by Hruday on 40 items (agreement reported separately).",
          "- 'Clearly shows' is a statement about the record, not about the facility: a 'no' means the record does "
          "not show the service, not that the facility does not offer it.",
          "- Straight-line distances. Same radius per service for every tool (vf.services).",
          f"- Scored queries: {res['n_queries']} of {meta.get('queries_total', res['n_queries'])} (the fully judged "
          f"prefix of the fixed seeded order). Walk status per query: {res['walk_status']}.",
          f"- Unresolved picks (judges split and the third pass did not settle it): "
          f"specialty {sp.get('pick_unresolved', 0)}, any fact {an.get('pick_unresolved', 0)}, ranker "
          f"{rk.get('pick_unresolved', 0)}; counted as not shown."]
    led = meta.get("ledger") or {}
    if led:
        L += [f"- Judge API calls on this half, from the spend ledger: {led.get('api_calls')} "
              f"({led.get('by_judge')}), ${led.get('usd', 0):.4f}, from {led.get('first_at')} to {led.get('last_at')}."]
    runs = meta.get("judge_runs") or []
    if runs:
        L += ["- Judge runs on this half, in order (a run with 0 API calls is a cache-only rerun): " +
              "; ".join(f"{r.get('at')}: {r.get('api_calls')} calls, {r.get('queries_complete')} queries complete, "
                        f"stopped: {r.get('stopped') or 'no'}" for r in runs) + "."]
    L += ["", "## Run", "", "```", json.dumps(meta, indent=2, default=_json_default), "```", ""]
    return "\n".join(L)


# =============================================================================
# Step 5: human labels and agreement
# =============================================================================

HUMAN_Q1 = "Does the record clearly show this facility offers the service? (y / n)"
HUMAN_Q2 = "Is it a sensible place to send this patient, judging from the record? (y / n)"


def select_human_items(units: list[dict], picks_of: dict, n: int = 40, seed: int = SEED) -> list[dict]:
    """Up to ``n`` Q1 units, stratified across tools and outcomes: cells are
    (tool that picked it, judges' answer) for the three tools, (walk only,
    answer) for facilities read while checking abstentions and skipped options,
    and a cell for judge disagreements. Round-robin over cells, seeded, then shuffled."""
    rng = np.random.default_rng(seed)
    cells: dict = {}
    for u in units:
        if u["kind"] != "q1":
            continue
        tools = picks_of.get(u["unit_id"], [])
        outcome = "unresolved" if u.get("final") is None else ("yes" if u["final"] else "no")
        keys = [(t, outcome) for t in tools] or [("walk", outcome)]
        if u.get("third_pass"):
            keys.append(("disagreement", outcome))
        for k in keys:
            cells.setdefault(k, []).append(u)
    order = sorted(cells)
    for k in order:
        cells[k] = [cells[k][i] for i in rng.permutation(len(cells[k]))]
    chosen, seen = [], set()
    while len(chosen) < n and any(cells[k] for k in order):
        for k in order:
            while cells[k] and cells[k][0]["unit_id"] in seen:
                cells[k].pop(0)
            if cells[k] and len(chosen) < n:
                u = cells[k].pop(0)
                seen.add(u["unit_id"])
                chosen.append({**u, "stratum": f"{k[0]}:{k[1]}"})
    return [chosen[i] for i in rng.permutation(len(chosen))]


def parse_yn(v) -> Optional[bool]:
    s = str(v).strip().lower() if v is not None and not (isinstance(v, float) and math.isnan(v)) else ""
    if s in ("y", "yes", "true", "1"):
        return True
    if s in ("n", "no", "false", "0"):
        return False
    return None


def agreement(human: pd.DataFrame, key: pd.DataFrame) -> dict:
    """Judge-vs-human agreement on the filled-in label CSV."""
    m = human.merge(key, on="item_id", how="inner")
    out = {"n_items": int(len(m))}
    for q, hcol, prefix in (("q1", "human_clearly_offers", "q1"), ("q2", "human_sensible", "q2")):
        h = [parse_yn(x) for x in m[hcol]] if hcol in m else []
        res = {"n_labeled": int(sum(x is not None for x in h))}
        for who in ("final", "A", "B", "C"):
            col = f"{prefix}_{who}"
            if col not in m:
                continue
            j = [None if (isinstance(x, float) and math.isnan(x)) or x is None or x == "" else parse_yn(x)
                 for x in m[col]]
            pairs = [(a, b) for a, b in zip(h, j) if a is not None and b is not None]
            res[who] = {"n": len(pairs), "agreement": (sum(a == b for a, b in pairs) / len(pairs)) if pairs else None,
                        "kappa": cohen_kappa([a for a, _ in pairs], [b for _, b in pairs])}
        out[q] = res
    return out


# =============================================================================
# I/O of the evaluation files
# =============================================================================


def points_path() -> Path:
    return out_dir() / "points.csv"


def read_points() -> pd.DataFrame:
    p = points_path()
    if not p.exists():
        raise SystemExit("no points yet: run `python scripts/eval_sealed.py draw` first")
    df = pd.read_csv(p, dtype={"pin": str})
    df["third_service"] = df["third_service"].where(df["third_service"].notna(), None)
    return df


def picks_path(split: str) -> Path:
    return out_dir() / f"picks_{split}.json"


def records_path(split: str) -> Path:
    return out_dir() / f"records_{split}.json"


# =============================================================================
# Commands
# =============================================================================


def cmd_draw(args) -> None:
    if points_path().exists() and not args.force:
        pts = read_points()
        print(f"points already drawn: {len(pts)} points, sha256 {points_hash(pts)} (use --force to redraw "
              "before the protocol is sealed)")
        return
    if protocol_status().get("sealed") and args.force:
        raise SystemExit("the protocol is sealed; the points cannot be redrawn")
    offices = load_offices_for_draw()
    el = eligible_offices(offices)
    pts = draw_points(offices)
    q = make_queries(pts)
    h = points_hash(pts)
    write_csv_atomic(points_path(), pts)
    alloc = pts.groupby("state").size().to_dict()
    write_meta(points_path(), {"rows": int(len(pts)), "seed": SEED, "points_sha256": h, "n_dev": int((pts.split == "dev").sum()),
                               "n_sealed": int((pts.split == "sealed").sum()), "max_per_state": MAX_PER_STATE,
                               "eligible_offices": int(len(el)), "eligible_states": int(el.state.nunique()),
                               "per_state": alloc, "rule": "sqrt allocation, largest remainder, cap per state"})
    qp = out_dir() / "queries.csv"
    write_csv_atomic(qp, q)
    write_meta(qp, {"rows": int(len(q)), "by_split": q.groupby("split").size().to_dict(),
                    "by_service": q.groupby(["split", "service"]).size().unstack(0).fillna(0).astype(int).to_dict(),
                    "points_sha256": h})
    print(f"drew {len(pts)} points from {len(el):,} eligible offices in {el.state.nunique()} states; "
          f"dev {int((pts.split == 'dev').sum())}, sealed {int((pts.split == 'sealed').sum())}; "
          f"{len(q)} queries; largest state share {max(alloc.values())}/{len(pts)}; sha256 {h}")


def cmd_seal(args) -> None:
    h = seal_protocol()
    print(f"protocol sealed: sha256 {h}")


def cmd_picks(args) -> None:
    info = check_sealed_inputs(args.split, args.unseal, stage="picks")
    if args.split == "sealed" and picks_path("sealed").exists():
        raise SystemExit(f"{picks_path('sealed')} exists: the sealed picks are computed once (protocol rule 2). "
                         "Resume an interrupted sealed run with `judge --split sealed --unseal`, which reads them "
                         "and the judge cache.")
    pts = read_points()
    q = make_queries(pts[pts["split"] == args.split])
    t0 = time.time()
    ctx = build_ctx(Path(args.bundle) if args.bundle else default_bundle())
    load_s = time.time() - t0
    out, records = [], {}
    for r in q.to_dict("records"):
        o = run_tools(ctx, r)
        out.append(o)
        for t in TOOLS:
            pk = o["tools"][t]["pick"]
            if pk:
                records.setdefault(pk["facility_id"], ctx.record(pk["pos"]))
    for o in out:  # every stored pool entry, so the judges never need the index
        for fid, _, _ in o["pool"]:
            if fid not in records:
                records[fid] = ctx.record(ctx.idx.id_to_pos[fid])
    doc = {"split": args.split, "created_at_utc": now_utc(), "harness_version": HARNESS_VERSION,
           "protocol": info["protocol"], "points_sha256": info["points_sha256"], "bundle": ctx.bundle_info,
           "git": info["git"], "code_sha256": info["code_sha256"],
           "radius_km": {s: float(S.RADII_KM[s]) for s in S.SERVICE_LINES}, "queries": out}
    # records first: the picks file is the "computed once" marker for the sealed half
    write_json_atomic(records_path(args.split), records)
    write_meta(records_path(args.split), {"rows": len(records), "note": "record text as the judges read it"})
    write_json_atomic(picks_path(args.split), doc)
    write_meta(picks_path(args.split), {"rows": len(out), "split": args.split, "bundle": ctx.bundle_info,
                                        "points_sha256": info["points_sha256"], "git": info["git"],
                                        "code_sha256": info["code_sha256"],
                                        "protocol_seal": info["protocol"].get("sha256"),
                                        "seal_pinned": info["protocol"].get("pinned")})
    n_ans = {t: sum(1 for o in out if o["tools"][t]["pick"]) for t in TOOLS}
    qc = [o["tools"]["ranker"]["quote_check"] for o in out]
    nq, ns = sum(x["n_quotes"] for x in qc), sum(x["strict"] for x in qc)
    same = sum(1 for o in out if len({(o["tools"][t]["pick"] or {}).get("facility_id") for t in TOOLS}) == 1)
    print(f"[picks {args.split}] {len(out)} queries; answered: " +
          ", ".join(f"{t} {n_ans[t]}" for t in TOOLS) +
          f"; all three tools agree (same pick or all abstain) on {same}; ranker card quotes verbatim "
          f"{ns}/{nq}; index load {load_s:.1f}s; {len(records)} records stored")


def _load_picks(split: str) -> tuple[dict, dict]:
    p = picks_path(split)
    if not p.exists():
        raise SystemExit(f"no picks for {split}: run `picks --split {split}` first")
    return json.loads(p.read_text()), json.loads(records_path(split).read_text())


def judged_path(split: str) -> Path:
    return out_dir() / f"judged_{split}.json"


def make_runner(args, purpose: str) -> tuple[JudgeRunner, Budget, dict]:
    env = read_env()
    specs = judge_specs(env, args.judges)
    budget_env = env.get("VF_LLM_BUDGET_USD")
    budget = Budget(out_dir() / "spend.jsonl", args.cap_usd, float(budget_env) if budget_env else None,
                    args.max_calls)
    client = None
    if args.max_calls is None or args.max_calls > 0:
        key = env.get("ANTHROPIC_API_KEY")
        if not key:
            raise SystemExit("ANTHROPIC_API_KEY is not set in .env")
        import anthropic
        client = anthropic.Anthropic(api_key=key, max_retries=4, timeout=120.0)
    return JudgeRunner(specs, budget, client, purpose=purpose), budget, {k: v.model for k, v in specs.items()}


def check_sealed_judge_settings(args, doc: dict, info: dict, specs: dict) -> None:
    """Sealed half: the walk cap and judges are the protocol's, and the code is
    the code that computed the sealed picks."""
    if args.walk_cap != WALK_CAP:
        raise SystemExit(f"sealed half: --walk-cap {args.walk_cap} differs from the protocol's {WALK_CAP}")
    if args.judges != "mixed":
        raise SystemExit("sealed half: --judges must be mixed (the protocol's judges)")
    check_judges(specs)
    then, now = doc.get("code_sha256") or {}, info.get("code_sha256") or {}
    changed = sorted(k for k in set(then) | set(now) if then.get(k) != now.get(k))
    if changed:
        raise SystemExit(f"sealed half: code changed since the sealed picks were computed: {changed}")
    if doc.get("protocol", {}).get("sha256") != info["protocol"].get("sha256"):
        raise SystemExit("sealed half: the protocol seal changed since the sealed picks were computed")


def ledger_summary(path: Path, purpose: str) -> dict:
    """API calls and spend in the ledger for one purpose (for example judge_dev):
    the record of every run, cache-only reruns included."""
    rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()] if path.exists() else []
    rows = [r for r in rows if r.get("purpose") == purpose]
    by = {}
    for r in rows:
        by[r.get("judge")] = by.get(r.get("judge"), 0) + 1
    return {"purpose": purpose, "api_calls": len(rows), "by_judge": by,
            "usd": round(sum(r.get("cost_usd", 0.0) for r in rows), 6),
            "first_at": min((r["at"] for r in rows), default=None), "last_at": max((r["at"] for r in rows), default=None)}


def cmd_judge(args) -> None:
    info = check_sealed_inputs(args.split, args.unseal, stage="judge")
    doc, records = _load_picks(args.split)
    if args.split == "sealed":
        check_sealed_judge_settings(args, doc, info, judge_specs(read_env(), args.judges))
    prev = json.loads(judged_path(args.split).read_text()) if judged_path(args.split).exists() else {}
    runner, budget, models = make_runner(args, f"judge_{args.split}")
    units = Units(runner)
    qs = doc["queries"]
    rng = np.random.default_rng(SEED + (0 if args.split == "dev" else 1))
    order = [qs[i] for i in rng.permutation(len(qs))]
    if args.limit_queries:
        order = order[: args.limit_queries]
    results: dict = {}
    stopped = {"why": None}

    def one(q):
        try:
            results[q["query_id"]] = judge_query(units, lambda fid: records[fid], q, args.walk_cap)
        except StopJudging as e:
            stopped["why"] = str(e)

    t0 = time.time()
    with ThreadPoolExecutor(max(1, args.workers)) as ex:
        list(ex.map(one, order))
    unit_list = list(units.done.values())
    this_run = {"at": now_utc(), "api_calls": runner.n_api, "cache_hits": runner.n_cached,
                "queries_complete": len(results), "stopped": stopped["why"] or budget.stopped,
                "max_calls": args.max_calls, "cap_usd": args.cap_usd, "walk_cap": args.walk_cap,
                "limit_queries": args.limit_queries}
    same = prev.get("prompt_sha256") == PROMPT_SHA and prev.get("walk_cap") == args.walk_cap
    runs = (prev.get("runs") or ([{"at": prev.get("created_at_utc"), "api_calls": prev.get("api_calls_this_run"),
                                   "cache_hits": prev.get("cache_hits_this_run"),
                                   "queries_complete": prev.get("queries_complete"), "stopped": prev.get("stopped"),
                                   "note": "reconstructed from the previous judged file"}] if prev else [])) \
        if same else []
    runs = runs + [this_run]
    doc_out = {"split": args.split, "created_at_utc": now_utc(), "judges": models, "prompt_version": PROMPT_VERSION,
               "order": [q["query_id"] for q in order],
               "prompt_sha256": PROMPT_SHA, "walk_cap": args.walk_cap, "queries_attempted": len(order),
               "queries_complete": len(results), "stopped": stopped["why"] or budget.stopped,
               "api_calls_this_run": runner.n_api, "cache_hits_this_run": runner.n_cached,
               "spend_eval_ledger_usd": round(budget.spent, 6), "spend_other_ledgers_usd": round(budget.others, 6),
               "ledger": ledger_summary(out_dir() / "spend.jsonl", f"judge_{args.split}"), "runs": runs,
               "picks_created_at_utc": doc.get("created_at_utc"), "code_sha256": info["code_sha256"],
               "git": info["git"], "protocol_seal": info["protocol"].get("sha256"),
               "queries": results, "units": unit_list}
    write_json_atomic(judged_path(args.split), doc_out)
    write_meta(judged_path(args.split), {"rows": len(results), "split": args.split, "judges": models,
                                         "prompt_sha256": PROMPT_SHA, "units": len(unit_list),
                                         "code_sha256": info["code_sha256"], "git": info["git"],
                                         "protocol_seal": info["protocol"].get("sha256"),
                                         "seal_pinned": info["protocol"].get("pinned")})
    jrows = []
    for u in unit_list:
        a = u.get("answers") or {}
        jrows.append({"kind": u["kind"], "unit_id": u["unit_id"], "facility_id": u.get("facility_id"),
                      "service": u.get("service"), "query_id": u.get("query_id"),
                      **{f"vote_{j}": u["votes"].get(j) for j in ("A", "B", "C")}, "final": u["final"],
                      **{f"reason_{j}": (a.get(j) or {}).get("reason") for j in ("A", "B", "C")},
                      **{f"quote_{j}": (a.get(j) or {}).get("quote") for j in ("A", "B", "C")}})
    if jrows:
        write_csv_atomic(out_dir() / f"judgments_{args.split}.csv", pd.DataFrame(jrows))
    tp = sum(1 for u in unit_list if u["third_pass"])
    print(f"[judge {args.split}] {len(results)} of {len(order)} queries fully judged; {len(unit_list)} units "
          f"({tp} needed the third pass); {runner.n_api} API calls, {runner.n_cached} cached; eval spend so far "
          f"${budget.spent:.4f} (other ledgers ${budget.others:.4f}); "
          f"{time.time() - t0:.0f}s" + (f"; stopped: {doc_out['stopped']}" if doc_out["stopped"] else ""))


def rejudge_units(units: Units, ids: list[str], records: dict, system_q1: str, workers: int = 6) -> dict:
    """Q1 on the listed unit ids (``facility_id|service``) with ``system_q1``.
    Returns unit id -> judged unit; stops quietly when the budget does."""
    out: dict = {}
    stopped = {"why": None}

    def one(uid):
        fid, svc = uid.rsplit("|", 1)
        try:
            out[uid] = units.judge("q1", uid, system_q1, user_q1(svc, records[fid]),
                                   {"facility_id": fid, "service": svc})
        except StopJudging as e:
            stopped["why"] = str(e)

    with ThreadPoolExecutor(max(1, workers)) as ex:
        list(ex.map(one, ids))
    return {"units": out, "stopped": stopped["why"]}


def cmd_rejudge(args) -> None:
    """Dev half only: judge listed Q1 units again with another Q1 rubric (for
    example a candidate j2) and compare with the judged file's verdicts. Writes
    ``rejudge_dev_<version>.json`` and ``.csv``; the judged file is not changed."""
    if args.split != "dev":
        raise SystemExit("rejudge is for the dev half only (protocol rule 1)")
    check_sealed_inputs("dev", False, stage="rejudge")
    if args.prompt_q1 not in Q1_PROMPTS:
        raise SystemExit(f"unknown Q1 prompt {args.prompt_q1}; known: {sorted(Q1_PROMPTS)}")
    if not args.units_file:
        raise SystemExit("--units-file is required (one Q1 unit id, facility_id|service, per line)")
    ids = [l.strip() for l in Path(args.units_file).read_text().splitlines() if l.strip()]
    _, records = _load_picks("dev")
    jd = json.loads(judged_path("dev").read_text())
    before = {u["unit_id"]: u for u in jd["units"] if u["kind"] == "q1"}
    missing = [i for i in ids if i.rsplit("|", 1)[0] not in records]
    if missing:
        raise SystemExit(f"{len(missing)} unit ids have no stored record, e.g. {missing[:3]}")
    runner, budget, models = make_runner(args, f"rejudge_dev_{args.prompt_q1}")
    runner.versions = {**runner.versions, "q1": args.prompt_q1}
    res = rejudge_units(Units(runner), ids, records, Q1_PROMPTS[args.prompt_q1], args.workers)
    rows = []
    for uid in ids:
        u = res["units"].get(uid)
        b = before.get(uid) or {}
        a = (u or {}).get("answers") or {}
        rows.append({"unit_id": uid, "final_before": b.get("final"), "final_after": (u or {}).get("final"),
                     **{f"vote_{j}": (u or {}).get("votes", {}).get(j) for j in ("A", "B", "C")},
                     **{f"quote_{j}": (a.get(j) or {}).get("quote") for j in ("A", "B", "C")},
                     **{f"reason_{j}": (a.get(j) or {}).get("reason") for j in ("A", "B", "C")}})
    stem = out_dir() / f"rejudge_dev_{args.prompt_q1}"
    doc = {"split": "dev", "created_at_utc": now_utc(), "prompt_q1": args.prompt_q1,
           "prompt_sha256": prompt_sha(args.prompt_q1, Q1_PROMPTS[args.prompt_q1]), "judges": models,
           "units_file": str(args.units_file), "n_units": len(ids), "n_judged": len(res["units"]),
           "stopped": res["stopped"] or budget.stopped, "api_calls_this_run": runner.n_api,
           "cache_hits_this_run": runner.n_cached, "spend_eval_ledger_usd": round(budget.spent, 6),
           "ledger": ledger_summary(out_dir() / "spend.jsonl", f"rejudge_dev_{args.prompt_q1}"),
           "units": list(res["units"].values())}
    write_json_atomic(stem.with_suffix(".json"), doc)
    write_meta(stem.with_suffix(".json"), {"rows": len(res["units"]), "split": "dev", "prompt_q1": args.prompt_q1})
    write_csv_atomic(stem.with_suffix(".csv"), pd.DataFrame(rows))
    flips = sum(1 for r in rows if r["final_before"] is True and r["final_after"] is False)
    print(f"[rejudge dev {args.prompt_q1}] {len(res['units'])} of {len(ids)} units judged; yes to no {flips}; "
          f"{runner.n_api} API calls, {runner.n_cached} cached; eval spend so far ${budget.spent:.4f}"
          + (f"; stopped: {doc['stopped']}" if doc["stopped"] else ""))


def judged_prefix(jd: dict) -> list[str]:
    """Query ids scored: the longest run of the fixed seeded order in which every
    query was fully judged (protocol rule 5). A capped run would otherwise favour
    queries that need no calls (every tool abstains and nothing is in the radius)."""
    out = []
    for qid in jd.get("order") or list(jd["queries"]):
        if qid not in jd["queries"]:
            break
        out.append(qid)
    return out


def listing_only_map(jd: dict, records: dict) -> dict:
    """Q1 unit id -> whether its yes rests only on the specialties listing."""
    out = {}
    for u in jd.get("units") or []:
        if u.get("kind") != "q1":
            continue
        rec = records.get(u.get("facility_id"), "")
        out[u["unit_id"]] = "all" if listing_only_yes(u, rec) else ("any" if listing_only_yes(u, rec, "any") else None)
    return out


def cmd_score(args) -> None:
    info = check_sealed_inputs(args.split, args.unseal, stage="score")
    doc, records = _load_picks(args.split)
    jp = judged_path(args.split)
    if not jp.exists():
        raise SystemExit(f"no judgments for {args.split}: run `judge --split {args.split}` first")
    jd = json.loads(jp.read_text())
    qmap = {q["query_id"]: q for q in doc["queries"]}
    lo = listing_only_map(jd, records)
    rows = []
    prefix = judged_prefix(jd)
    for qid in prefix:
        rows.extend(outcome_rows(qmap[qid], jd["queries"][qid], listing_only=lo))
    df = pd.DataFrame(rows)
    if len(df):
        write_csv_atomic(out_dir() / f"outcomes_{args.split}.csv", df)
    res = summarize(df) if len(df) else {"n_queries": 0, "tools": {t: {} for t in TOOLS}, "by_service": {},
                                          "walk_status": {}, "integrity_checks": {}}
    # coverage over every query (no judging needed): the tools ran on all of them
    cov_all = {t: _rate(sum(1 for q in doc["queries"] if q["tools"][t]["pick"]), len(doc["queries"])) for t in TOOLS}
    qc = [q["tools"]["ranker"]["quote_check"] for q in doc["queries"]]
    nq = sum(x["n_quotes"] for x in qc)
    res["all_queries"] = {"n": len(doc["queries"]), "coverage": cov_all}
    res["integrity_checks_all_queries"] = {
        "card_quotes_match_cited_fact": {**_rate(sum(x["strict"] for x in qc), nq), "label": INTEGRITY_LABEL},
        "card_quotes_found_in_record": _rate(sum(x["loose"] for x in qc), nq)}
    meta = {"split": args.split, "judges": jd["judges"], "prompt_sha256": jd["prompt_sha256"],
            "protocol_sha256": doc["protocol"].get("sha256"), "seal_pinned_at_picks": doc["protocol"].get("pinned"),
            "points_sha256": doc["points_sha256"],
            "bundle": doc["bundle"], "queries_scored": len(prefix), "queries_fully_judged": jd["queries_complete"],
            "queries_total": len(doc["queries"]),
            "stopped_last_run": jd.get("stopped"), "judge_runs": jd.get("runs") or [],
            "ledger": ledger_summary(out_dir() / "spend.jsonl", f"judge_{args.split}"),
            "picks_created_at_utc": doc.get("created_at_utc"), "judged_created_at_utc": jd.get("created_at_utc"),
            "flags_stored_with_picks": all("major_flags" in q for q in doc["queries"]),
            "code_sha256_at_picks": doc.get("code_sha256"), "git_at_picks": (doc.get("git") or {}).get("git_commit"),
            "git_clean_at_picks": (doc.get("git") or {}).get("clean"),
            "code_sha256_now": info["code_sha256"]}
    if doc.get("created_at_utc") and jd.get("created_at_utc") and jd["created_at_utc"] < doc["created_at_utc"]:
        meta["order_note"] = ("the picks file is newer than the judgments; the judgments were made on an earlier "
                              "picks file and are scored against this one (rerun `judge` to line them up)")
    res["meta"] = meta
    write_json_atomic(out_dir() / f"results_{args.split}.json", res)
    write_meta(out_dir() / f"results_{args.split}.json", {"rows": int(res["n_queries"]), "split": args.split,
                                                          "code_sha256": info["code_sha256"]})
    md = render_results_md(args.split, res, meta)
    ica = res["integrity_checks_all_queries"]["card_quotes_match_cited_fact"]
    md += ("\n## Coverage over all queries (no judging needed)\n\n" +
           "\n".join(f"- {TOOL_PLAIN[t]}: {fmt_rate(cov_all[t])}" for t in TOOLS) +
           f"\n- Integrity check over all queries (not a judged result): every quote on a ranker card is the exact "
           f"text of the fact it cites, {ica['k']} of {ica['n']}.\n")
    write_text_atomic(out_dir() / f"results_{args.split}.md", md)
    t = res["tools"]
    print(f"[score {args.split}] scored {res['n_queries']} of {len(doc['queries'])} queries (the fully judged prefix "
          f"of the seeded order; {jd['queries_complete']} fully judged in all); coverage over all: " +
          ", ".join(f"{k} {fmt_rate(cov_all[k])}" for k in TOOLS))
    if res["n_queries"]:
        print("  precision: " + ", ".join(f"{k} {fmt_rate(t[k]['precision'])}" for k in TOOLS))
        print("  precision without listing-only yes votes: " +
              ", ".join(f"{k} {fmt_rate(t[k]['precision_excluding_listing_only'])}" for k in TOOLS))
        print(f"  ranker abstained with support: {fmt_rate(t['ranker']['abstained_with_support'])}; skipped nearer: "
              f"{fmt_rate(t['ranker']['skipped_nearer'])}")
        for k in ("abstained_with_support_split", "skipped_nearer_split"):
            sp_ = t["ranker"].get(k)
            if sp_:
                print(f"    {k}: nearest supported record flagged {sp_['nearest_flagged']['k']}, unflagged "
                      f"{sp_['nearest_unflagged']['k']}, listing-only {sp_['nearest_listing_only']['k']}")
    print(f"  integrity (not judged): card quotes = cited fact text {ica['k']}/{ica['n']}")


_PHONE_RX = re.compile(r"(?<![\w])(?:\+?91[\s-]*|0)?(?:\d[\s-]?){9,11}\d(?![\w])")
_EMAIL_RX = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def mask_contacts(text: str) -> str:
    """Phone numbers and email addresses out of record text shown to a person
    (the human label CSV only; the judges read the record as the dataset has it)."""
    return _PHONE_RX.sub("[phone removed]", _EMAIL_RX.sub("[email removed]", text))


def cmd_human(args) -> None:
    check_sealed_inputs(args.split, args.unseal, stage="human")
    doc, records = _load_picks(args.split)
    jd = json.loads(judged_path(args.split).read_text())
    picks_of: dict = {}
    pick_query: dict = {}
    prefix_order = judged_prefix(jd)
    prefix = set(prefix_order)
    qmap = {q["query_id"]: q for q in doc["queries"]}
    # walk items: the first query in the seeded order whose walk read the facility, and its km from that place
    walk_query: dict = {}
    for qid in prefix_order:
        q = qmap[qid]
        km = {fid: dist for fid, dist, _ in q["pool"]}
        for fid in jd["queries"][qid]["q1"]:
            if fid in km:
                walk_query.setdefault(f"{fid}|{q['service']}", (q, km[fid]))
    in_scope = set()
    for q in doc["queries"]:
        if q["query_id"] in prefix:
            in_scope.update(f"{fid}|{q['service']}" for fid in jd["queries"][q["query_id"]]["q1"])
    for q in doc["queries"]:
        if q["query_id"] not in prefix:
            continue
        for t in TOOLS:
            pk = q["tools"][t]["pick"]
            if pk:
                uid = f"{pk['facility_id']}|{q['service']}"
                picks_of.setdefault(uid, [])
                if t not in picks_of[uid]:
                    picks_of[uid].append(t)
                pick_query.setdefault(uid, (q, pk))
    units = {(u["kind"], u["unit_id"]): u for u in jd["units"]}
    chosen = select_human_items([u for u in jd["units"] if u["kind"] == "q1" and u["unit_id"] in in_scope],
                                picks_of, n=args.n)
    hrows, krows = [], []
    for i, u in enumerate(chosen, 1):
        item = f"H{i:02d}"
        fid, svc = u["facility_id"], u["service"]
        qp = pick_query.get(u["unit_id"])
        place, dist, q2u, src = "", "", None, ""
        if qp:
            q, pk = qp
            place, dist, src = q["place_label"], f"{pk['distance_km']:.1f}", q["query_id"]
            q2u = units.get(("q2", f"{q['query_id']}|{fid}"))
        elif u["unit_id"] in walk_query:  # same columns for walk items, so a blank place does not reveal the stratum
            q, km = walk_query[u["unit_id"]]
            place, dist, src = q["place_label"], f"{km:.1f}", q["query_id"]
        hrows.append({"item_id": item, "service_needed": SERVICE_NEED[svc], "place": place,
                      "straight_line_km": dist, "record": mask_contacts(records[fid]), "question_1": HUMAN_Q1,
                      "human_clearly_offers": "", "question_2": HUMAN_Q2,
                      "human_sensible": "", "human_note": ""})
        krows.append({"item_id": item, "facility_id": fid, "service": svc, "stratum": u["stratum"],
                      "tools_that_picked": "|".join(picks_of.get(u["unit_id"], [])),
                      "query_id": src, "place_from": "pick" if qp else ("walk" if src else ""),
                      "q1_A": u["votes"].get("A"), "q1_B": u["votes"].get("B"), "q1_C": u["votes"].get("C"),
                      "q1_final": u["final"],
                      "q2_A": (q2u or {}).get("votes", {}).get("A"), "q2_B": (q2u or {}).get("votes", {}).get("B"),
                      "q2_C": (q2u or {}).get("votes", {}).get("C"), "q2_final": (q2u or {}).get("final")})
    hp = out_dir() / f"human_labels_{args.split}.csv"
    kp = out_dir() / f"human_key_{args.split}.csv"
    if hp.exists() and not args.force:
        filled = pd.read_csv(hp, dtype=str)
        if filled.get("human_clearly_offers", pd.Series(dtype=str)).fillna("").str.strip().ne("").any():
            raise SystemExit(f"{hp} already has hand labels; not overwriting (use --force)")
    write_csv_atomic(hp, pd.DataFrame(hrows, columns=["item_id", "service_needed", "place", "straight_line_km",
                                                     "record", "question_1", "human_clearly_offers", "question_2",
                                                     "human_sensible", "human_note"]))
    write_meta(hp, {"rows": len(hrows), "split": args.split, "blind": "no tool, judge answer or stratum in this file",
                    "key": kp.name, "place_and_km": "every item: the pick's query, or for a facility read during a "
                    "walk the first query in the seeded order that read it",
                    "question_2": "asked on every item; only items whose judges answered Q2 (picks) enter the Q2 "
                    "agreement", "contacts": "phone numbers and email addresses masked in this file only"})
    write_csv_atomic(kp, pd.DataFrame(krows))
    write_meta(kp, {"rows": len(krows), "split": args.split, "note": "do not open before labeling"})
    strata = pd.Series([k["stratum"] for k in krows]).value_counts().to_dict() if krows else {}
    print(f"[human {args.split}] {len(hrows)} items written to {hp} (target {args.n}); strata {strata}")


def cmd_agreement(args) -> None:
    hp = out_dir() / f"human_labels_{args.split}.csv"
    kp = out_dir() / f"human_key_{args.split}.csv"
    h = pd.read_csv(hp, dtype=str)
    k = pd.read_csv(kp, dtype=str)
    res = agreement(h, k)
    write_json_atomic(out_dir() / f"agreement_{args.split}.json", res)
    write_meta(out_dir() / f"agreement_{args.split}.json", {"rows": res["n_items"], "split": args.split})
    print(f"[agreement {args.split}] " + json.dumps(res))


def estimate_cost(doc: dict, ledger_rows: list[dict], specs: dict[str, JudgeSpec], walk_cap: int = WALK_CAP,
                  disagreement_rate: Optional[float] = None, walk_share: Optional[float] = None) -> dict:
    """Expected API cost of judging every query in ``doc``, from the measured
    tokens per call (by kind and judge model) in ``ledger_rows``."""
    tok: dict = {}
    for r in ledger_rows:
        k = (r["kind"], r["model"])
        tok.setdefault(k, []).append(r["usage"])

    def mean_cost(kind, model):
        us = tok.get((kind, model))
        if not us:
            us = tok.get((kind, specs["A"].model)) or [{"input_tokens": 1800, "output_tokens": 400}]
        return float(np.mean([cost_usd(model, u) for u in us]))

    q1_units, q2_units, walk_upper = set(), 0, 0
    for q in doc["queries"]:
        picks = {q["tools"][t]["pick"]["facility_id"] for t in TOOLS if q["tools"][t]["pick"]}
        for f in picks:
            q1_units.add(f"{f}|{q['service']}")
        q2_units += len(picks)
        extra = [p for p in q["pool"] if p[0] not in picks]
        walk_upper += min(walk_cap, len(extra))
    dis = 0.15 if disagreement_rate is None else disagreement_rate
    # walk_share: measured walked / possible walk on judged queries that had something to walk
    wm = walk_upper * (0.5 if walk_share is None else walk_share)
    out = {}
    for label, walk in (("low (picks only)", 0.0), ("expected", wm), ("high (every walk to the cap)", walk_upper)):
        n1 = len(q1_units) + walk
        c = 0.0
        for kind, n in (("q1", n1), ("q2", q2_units)):
            c += n * (mean_cost(kind, specs["A"].model) + mean_cost(kind, specs["B"].model)
                      + dis * mean_cost(kind, specs["C"].model))
        out[label] = {"q1_units": round(n1), "q2_units": q2_units,
                      "api_calls": round((n1 + q2_units) * (2 + dis)), "usd": round(c, 3)}
    out["assumptions"] = {"disagreement_rate": dis, "walk_upper": walk_upper,
                          "walk_share": 0.5 if walk_share is None else walk_share,
                          "measured_calls": {f"{k[0]}|{k[1]}": len(v) for k, v in tok.items()}}
    return out


def cmd_estimate(args) -> None:
    doc, _ = _load_picks(args.split)
    env = read_env()
    specs = judge_specs(env, args.judges)
    led = out_dir() / "spend.jsonl"
    rows = [json.loads(l) for l in led.read_text().splitlines() if l.strip()] if led.exists() else []
    jd = json.loads(judged_path(args.split).read_text()) if judged_path(args.split).exists() else None
    dis = share = None
    if jd and jd["units"]:
        dis = sum(1 for u in jd["units"] if u["third_pass"]) / len(jd["units"])
        qmap = {q["query_id"]: q for q in doc["queries"]}
        used = possible = 0
        for qid, jq in jd["queries"].items():
            q = qmap[qid]
            picks = {q["tools"][t]["pick"]["facility_id"] for t in TOOLS if q["tools"][t]["pick"]}
            room = min(args.walk_cap, len([p for p in q["pool"] if p[0] not in picks]))
            if room:
                used += jq["walked"]
                possible += room
        if possible >= 30:  # too few walks measured: keep the 0.5 default
            share = used / possible
    est = estimate_cost(doc, rows, specs, args.walk_cap, dis, share)
    est["per_half"] = args.split
    est["full_run_note"] = ("a full run is this half plus the sealed half, which is assumed to look like this half "
                            "(its picks are not computed before the seal is broken): about twice these figures")
    write_json_atomic(out_dir() / f"cost_estimate_{args.split}.json", est)
    print(f"[estimate {args.split}] judges {', '.join(f'{k}={v.model}' for k, v in specs.items())}: " +
          "; ".join(f"{k}: ${v['usd']:.2f} ({v['api_calls']} calls)" for k, v in est.items()
                    if isinstance(v, dict) and "usd" in v) + " per half")


def cmd_dryrun(args) -> None:
    if args.split != "dev":
        raise SystemExit("the dry run is for the dev half only")
    if not protocol_status().get("ok"):
        raise SystemExit("seal the protocol first (`seal`)")
    cmd_picks(args)
    args.max_calls = DRYRUN_MAX_CALLS if args.max_calls is None else min(args.max_calls, DRYRUN_MAX_CALLS)
    cmd_judge(args)
    cmd_score(args)
    args.force = True
    cmd_human(args)
    cmd_estimate(args)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, split=True):
        if split:
            p.add_argument("--split", choices=SPLITS, default="dev")
            p.add_argument("--unseal", action="store_true", help="required for the sealed half")
        p.add_argument("--bundle", default=None, help="WP12 bundle dir (default dbapp/bundle)")
        p.add_argument("--judges", choices=("mixed", "single"), default="mixed")
        p.add_argument("--cap-usd", type=float, default=DEFAULT_CAP_USD)
        p.add_argument("--max-calls", type=int, default=None)
        p.add_argument("--workers", type=int, default=6)
        p.add_argument("--walk-cap", type=int, default=WALK_CAP)
        p.add_argument("--limit-queries", type=int, default=None)
        p.add_argument("--n", type=int, default=40, help="human label items")
        p.add_argument("--force", action="store_true")
        p.add_argument("--prompt-q1", default=KIND_PROMPT_VERSION["q1"], help="rejudge: Q1 rubric version")
        p.add_argument("--units-file", default=None, help="rejudge: file of Q1 unit ids, one per line")

    for name in ("draw", "seal"):
        common(sub.add_parser(name), split=False)
    for name in ("picks", "judge", "score", "human", "agreement", "estimate", "dryrun", "rejudge"):
        common(sub.add_parser(name))
    args = ap.parse_args(argv)
    {"draw": cmd_draw, "seal": cmd_seal, "picks": cmd_picks, "judge": cmd_judge, "score": cmd_score,
     "human": cmd_human, "agreement": cmd_agreement, "estimate": cmd_estimate, "dryrun": cmd_dryrun,
     "rejudge": cmd_rejudge}[args.cmd](args)


if __name__ == "__main__":
    main()
