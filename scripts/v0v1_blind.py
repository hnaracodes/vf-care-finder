"""Build a blinded side-by-side of v0 and v1 ranking answers on the same 26 scenarios.

Inputs (read only):
  reports/ranking_scenarios_raw.json     (v0 answers)
  reports/ranking_scenarios_v1_raw.json  (v1 answers)

Outputs (owned, under data/labels/v0v1_blind/):
  blind.json            per scenario: query, status, top 3 cards for answer X and answer Y
  blind_0/1/2.json      blind.json split into thirds, in scenario order
  key.json              per scenario: which version is X and which is Y

Each scenario gets its own coin flip from a fixed seed, so the mapping is reproducible.
blind*.json carries no version information: no meta block, no headline, no score parts,
no requirement text (v1 requirement text cites the v0.3 recipe sources). Only the fields a
judge needs to compare the two answers: name, straight-line km, estimated drive minutes when
the answer gives them, quoted evidence (quote and field), and flags. Nothing is judged here.

Run: python scripts/v0v1_blind.py
"""

from __future__ import annotations

import json
import os
import random
import tempfile
from pathlib import Path

from vf import paths

SEED = 20261122
TOP_N = 3
ROOT = paths.code_root()
V0_PATH = ROOT / "reports" / "ranking_scenarios_raw.json"
V1_PATH = ROOT / "reports" / "ranking_scenarios_v1_raw.json"
OUT_DIR = paths.data_dir() / "labels" / "v0v1_blind"


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
        f.write("\n")
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def _card(c: dict) -> dict:
    minutes = c.get("minutes")
    return {
        "name": c.get("name"),
        "city": c.get("city"),
        "state": c.get("state"),
        "distance_km": c.get("distance_km"),
        "drive_minutes": minutes,
        "drive_minutes_range": (
            [c.get("minutes_low"), c.get("minutes_high")] if minutes is not None else None
        ),
        "evidence": [
            {"quote": e.get("quote"), "field": e.get("field")} for e in (c.get("evidence") or [])
        ],
        "flags": [
            {"flag": f.get("flag"), "severity": f.get("severity"), "reason": f.get("reason")}
            for f in (c.get("flags") or [])
        ],
    }


def _answer(s: dict) -> dict:
    cards = s.get("cards") or []
    return {
        "status": s.get("status"),
        "n_cards": len(cards),
        "top_cards": [_card(c) for c in cards[:TOP_N]],
    }


def _query(q: dict) -> dict:
    # The 'why' and 'expected_kind' fields are the scenario author's notes, not part of either
    # answer; they are identical across versions, so keeping them does not leak a version.
    return {k: q.get(k) for k in ("service", "place_text", "state_hint", "lat", "lon", "pin",
                                  "expected_kind", "why") if q.get(k) not in (None, "")}


def main() -> None:
    v0 = json.loads(V0_PATH.read_text())["scenarios"]
    v1 = json.loads(V1_PATH.read_text())["scenarios"]
    ids0 = [s["id"] for s in v0]
    ids1 = [s["id"] for s in v1]
    assert ids0 == ids1, "scenario ids differ between v0 and v1"
    for a, b in zip(v0, v1):
        assert a["query"] == b["query"], f"query differs for {a['id']}"

    rng = random.Random(SEED)
    blind, key = [], []
    for a, b in zip(v0, v1):
        v0_is_x = rng.random() < 0.5
        x, y = (a, b) if v0_is_x else (b, a)
        blind.append({"id": a["id"], "query": _query(a["query"]), "X": _answer(x), "Y": _answer(y)})
        key.append({"id": a["id"], "X": "v0" if v0_is_x else "v1", "Y": "v1" if v0_is_x else "v0"})

    _write_json(OUT_DIR / "blind.json", blind)
    n = len(blind)
    bounds = [0, (n + 2) // 3, 2 * ((n + 2) // 3), n]
    for i in range(3):
        _write_json(OUT_DIR / f"blind_{i}.json", blind[bounds[i]:bounds[i + 1]])
    _write_json(OUT_DIR / "key.json", {
        "seed": SEED,
        "v0_source": "reports/ranking_scenarios_raw.json",
        "v1_source": "reports/ranking_scenarios_v1_raw.json",
        "mapping": key,
    })

    n_v0_x = sum(k["X"] == "v0" for k in key)
    sizes = [bounds[i + 1] - bounds[i] for i in range(3)]
    print(f"{n} scenarios blinded (seed {SEED}); v0 is X in {n_v0_x}, Y in {n - n_v0_x}")
    print(f"thirds: {sizes}; out: {OUT_DIR}")


if __name__ == "__main__":
    main()
