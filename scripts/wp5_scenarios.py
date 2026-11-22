"""WP5 step 7: run the scenario suite and write the raw answers for judging.

Reads ``data/labels/scenarios.csv`` (written before any tuning), resolves each
place with ``vf.locate`` (WP2 B), runs ``vf.rank.rank`` or ``vf.rank.search``
on the real v0.2 grades and WP11 trust checks, and writes
``reports/ranking_scenarios_raw.json``: one entry per scenario with the query,
the resolution, the status, the cards with their quoted evidence, flags and
score parts, ``beyond_radius`` and ``unverified_nearby``.

This script does not judge the answers. Independent judges read the JSON and
mark each scenario right, defensible or wrong.

It also runs the latency check: every service line at every resolved scenario
point, timed after loading.

    python scripts/wp5_scenarios.py                       # writes reports/ranking_scenarios_raw.json
    python scripts/wp5_scenarios.py --out /tmp/x.json --md /tmp/x.md
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vf import paths, rank  # noqa: E402

SCRIPT = "scripts/wp5_scenarios.py"
OUT_DEFAULT = ROOT / "reports" / "ranking_scenarios_raw.json"
#: Acceptance (tasks/WP5_ranking.md): rank answers in under this many ms after loading.
RANK_MS_LIMIT = 100.0


def fingerprint(ans: rank.Answer) -> str:
    """Short hash of the status and the facilities shown, so a judge's mark can
    be tied to the exact answer it was made on."""
    ids = [c.facility_id for c in ans.results] or ([ans.beyond_radius.facility_id] if ans.beyond_radius else [])
    key = ans.status + "|" + ",".join(ids)
    return hashlib.sha1(key.encode()).hexdigest()[:10]


def get_resolver():
    """(resolve(query, state) -> vf.locate.Resolution, name). Also used by
    ``scripts/wp11_trust_checks.py``. No fallback: WP2 B is required."""
    from vf import locate
    r = locate.default_resolver()
    return (lambda q, st=None: r.resolve(q, st)), "vf.locate"


def _git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    except Exception:
        return ""


def _code_dirty() -> bool:
    try:
        out = subprocess.run(["git", "status", "--porcelain", "--", "src/vf/rank.py", "src/vf/services.py", SCRIPT],
                             cwd=ROOT, capture_output=True, text=True).stdout
        return bool(out.strip())
    except Exception:
        return True


def _sidecar_meta(name: str) -> dict:
    p = paths.gold_dir() / f"{name}.meta.json"
    if not p.exists():
        return {}
    m = json.loads(p.read_text())
    return {k: m.get(k) for k in ("created_at_utc", "script", "check_version", "recipe_version", "model_version",
                                  "method", "rows") if k in m}


def _pct(xs, q):
    return round(float(np.percentile(xs, q)), 2) if len(xs) else None


def latency_check(idx: rank.Index, points: list[tuple[float, float]]) -> dict:
    """Every graded line at every point, after one warm-up query."""
    if not points:
        return {"n_queries": 0}
    w = {"status": "ok", "lat": points[0][0], "lon": points[0][1], "precision_km": 5.0, "label": "warm-up", "how": ""}
    rank.rank(idx, idx.graded_lines[0], w)
    times = []
    for lat, lon in points:
        res = {"status": "ok", "lat": lat, "lon": lon, "precision_km": 5.0, "label": "latency", "how": ""}
        for line in idx.graded_lines:
            t = time.perf_counter()
            rank.rank(idx, line, res)
            times.append((time.perf_counter() - t) * 1000)
    return {"n_queries": len(times), "n_points": len(points), "lines": list(idx.graded_lines),
            "ms_median": _pct(times, 50), "ms_p95": _pct(times, 95), "ms_max": round(float(max(times)), 2),
            "limit_ms": RANK_MS_LIMIT, "passed": bool(max(times) < RANK_MS_LIMIT)}


def run(out: Path, md_out: Path | None = None) -> dict:
    t0 = time.time()
    idx = rank.load_index()
    t_load = time.time() - t0
    t1 = time.time()
    idx.ensure_keyword()  # built from facts in data_dir mode (prebuilt in the bundle); timed apart from queries
    t_kw = time.time() - t1
    resolve, resolver_name = get_resolver()
    sc_path = paths.labels_dir() / "scenarios.csv"
    sc = pd.read_csv(sc_path, dtype=str, keep_default_na=False)

    entries, times, points = [], [], []
    for r in sc.itertuples(index=False):
        t = time.perf_counter()
        res = resolve(r.place_text, r.state_hint or None)
        resolve_ms = (time.perf_counter() - t) * 1000
        t = time.perf_counter()
        if r.service.startswith("search:"):
            ans = rank.search(idx, r.service.split(":", 1)[1], res)
        else:
            ans = rank.rank(idx, r.service, res)
        ms = (time.perf_counter() - t) * 1000
        d = ans.to_dict()
        if ans.status in ("ok", "no_reliable_option"):
            points.append((ans.location["lat"], ans.location["lon"]))
        times.append((ans.method, ans.status, ms))
        entries.append({
            "id": r.id,
            "query": {"service": r.service, "place_text": r.place_text, "state_hint": r.state_hint or None,
                      "expected_kind": r.expected_kind, "why": r.why},
            "resolution": res.to_dict() if hasattr(res, "to_dict") else dict(res),
            "status": ans.status,
            "status_as_expected": ans.status == r.expected_kind,
            "method": ans.method,
            "service": ans.service,
            "service_label": ans.service_label,
            "radius_km": ans.radius_km,
            "headline": ans.how_we_decided.get("headline"),
            "cards": d["results"],
            "beyond_radius": d["beyond_radius"],
            "unverified_nearby": d["unverified_nearby"],
            "how_we_decided": d["how_we_decided"],
            "fingerprint": fingerprint(ans),
            "ms": {"resolve": round(resolve_ms, 2), "answer": round(ms, 2)},
        })

    lat = latency_check(idx, points)
    n = len(entries)
    resolved = [e for e in entries if e["status"] in ("ok", "no_reliable_option")]
    abst = [e for e in resolved if e["status"] == "no_reliable_option"]
    rec = [e for e in resolved if e["method"] == "recipe"]
    rec_abst = [e for e in rec if e["status"] == "no_reliable_option"]
    t_rec = [ms for m, st, ms in times if m == "recipe" and st in ("ok", "no_reliable_option")]
    t_kw_q = [ms for m, st, ms in times if m == "keyword" and st in ("ok", "no_reliable_option")]
    expected_abst = [e for e in entries if e["query"]["expected_kind"] == "no_reliable_option"]
    summary = {
        "scenarios": n,
        "status_counts": pd.Series([e["status"] for e in entries]).value_counts().to_dict(),
        "status_as_expected": sum(e["status_as_expected"] for e in entries),
        "status_not_as_expected": [{"id": e["id"], "expected": e["query"]["expected_kind"], "got": e["status"]}
                                   for e in entries if not e["status_as_expected"]],
        "resolved": len(resolved),
        "abstentions": len(abst),
        "abstention_rate": round(len(abst) / len(resolved), 3) if resolved else None,
        "abstention_ids": [e["id"] for e in abst],
        "recipe_resolved": len(rec), "recipe_abstentions": len(rec_abst),
        "recipe_abstention_rate": round(len(rec_abst) / len(rec), 3) if rec else None,
        "expected_abstentions_that_abstained": sum(e["status"] == "no_reliable_option" for e in expected_abst),
        "expected_abstentions": len(expected_abst),
        "cards_shown": sum(len(e["cards"]) for e in entries),
        "cards_by_confidence": pd.Series([c["confidence"] for e in entries for c in e["cards"]]).value_counts().to_dict(),
        "cards_with_a_flag": sum(bool(c["flags"]) for e in entries for c in e["cards"]),
        "load_s": round(t_load, 1), "keyword_index_build_s": round(t_kw, 1),
        "scenario_recipe_ms_median": _pct(t_rec, 50), "scenario_recipe_ms_max": round(max(t_rec), 2) if t_rec else None,
        "scenario_keyword_ms_median": _pct(t_kw_q, 50), "scenario_keyword_ms_max": round(max(t_kw_q), 2) if t_kw_q else None,
        "latency_check": lat,
    }
    meta = {
        "script": SCRIPT, "created_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git_commit": _git_commit(), "code_uncommitted": _code_dirty(),
        "rows": n, "scenarios_csv": "labels/scenarios.csv",
        "recipe_version": idx.recipe_version, "grades_method": idx.grades_method,
        "flags_source": idx.flags_source, "resolver": resolver_name,
        "score_mode": "probability" if any(idx.is_probability(l) for l in idx.graded_lines) else "v0 (provisional, D039)",
        "graded_lines": list(idx.graded_lines),
        "duplicate_groups": len(set(idx.dup_canon.values())),
        "inputs": {"capability_grades": _sidecar_meta("capability_grades.parquet"),
                   "facility_flags": _sidecar_meta("facility_flags.parquet"),
                   "facility_duplicates": _sidecar_meta("facility_duplicates.parquet")},
        "judging": "not judged here; independent judges mark each scenario right, defensible or wrong",
    }
    doc = {"meta": meta, "summary": summary, "scenarios": entries}
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name("." + out.name + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1))
    os.replace(tmp, out)
    if md_out:
        write_md(md_out, doc)
    return summary


def _card_md(c: dict, i) -> list[str]:
    km = c["distance_km"]
    out = [f"{i}. **{c['name']}** ({c['type'] or 'type unknown'}, {c['city'] or '?'}), {km} km straight line. "
           f"{c['grade_label'].split(' (')[0]}, evidence {c['evidence_strength']}; confidence {c['confidence']}"
           + (f"; score {c['score']:.3f}" if c["score"] is not None else "") + "."]
    for e in c["evidence"][:4]:
        out.append(f"   - {e['requirement']}: \"{e['quote']}\" ({e['field']})")
    for f in c["flags"]:
        out.append(f"   - flag ({f['severity']}, {f['flag']}): {f['reason']}")
    if c["missing"]:
        out.append(f"   - missing: {'; '.join(c['missing'])}")
    if c["why_here"]:
        out.append(f"   - why here: {c['why_here']}")
    out.append(f"   - could be wrong: {c['could_be_wrong']}")
    return out


def write_md(out: Path, doc: dict) -> None:
    """Readable rendering of the raw JSON, unjudged."""
    s, m = doc["summary"], doc["meta"]
    L = ["# Ranking scenarios, raw answers (WP5)", "",
         f"Generated {m['created_at_utc']} by `{SCRIPT}`. Grades {m['recipe_version']} ({m['grades_method']}), "
         f"flags {m['flags_source']}, resolver {m['resolver']}, score {m['score_mode']}. Not judged.", "",
         f"- Abstention rate: {s['abstentions']} of {s['resolved']} resolved scenarios ({s['abstention_rate']:.0%}).",
         f"- Status as expected: {s['status_as_expected']} of {s['scenarios']}.", ""]
    for e in doc["scenarios"]:
        q, r = e["query"], e["resolution"]
        L += [f"## {e['id']}", "",
              f"{e['service_label']}, place \"{q['place_text']}\"" + (f" (state {q['state_hint']})" if q["state_hint"] else "") + ".",
              f"Resolved: {r.get('status')}, {r.get('label') or ''}; precision {r.get('precision_km')} km. {r.get('how') or ''}",
              f"Expected {q['expected_kind']}, got **{e['status']}** ({e['method']}). {e['headline'] or ''}", ""]
        for i, c in enumerate(e["cards"], 1):
            L += _card_md(c, i)
        if e["beyond_radius"]:
            L += ["Nearest beyond the radius:"] + _card_md(e["beyond_radius"], "-")
        if e["unverified_nearby"]:
            L.append("Also nearby, not verified: " + "; ".join(
                f"{c['name']} ({c['distance_km']} km): {c['confidence_reason']}" for c in e["unverified_nearby"]))
        L.append("")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name("." + out.name + ".tmp")
    tmp.write_text("\n".join(L) + "\n")
    os.replace(tmp, out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path, default=OUT_DEFAULT)
    ap.add_argument("--md", type=Path, default=None, help="also write an unjudged markdown rendering here")
    args = ap.parse_args()
    s = run(args.out, args.md)
    lat = s["latency_check"]
    print(f"wrote {args.out}")
    print(f"scenarios {s['scenarios']}: {s['status_counts']}; status as expected {s['status_as_expected']}/{s['scenarios']}")
    if s["status_not_as_expected"]:
        print("  not as expected: " + ", ".join(f"{x['id']} ({x['expected']} -> {x['got']})" for x in s["status_not_as_expected"]))
    print(f"abstention {s['abstentions']}/{s['resolved']} ({s['abstention_rate']:.0%}); recipe only "
          f"{s['recipe_abstentions']}/{s['recipe_resolved']} ({s['recipe_abstention_rate']:.0%}); "
          f"expected abstentions that abstained {s['expected_abstentions_that_abstained']}/{s['expected_abstentions']}")
    print(f"cards {s['cards_shown']} by confidence {s['cards_by_confidence']}; with a flag {s['cards_with_a_flag']}")
    print(f"load {s['load_s']} s (+ keyword index {s['keyword_index_build_s']} s); scenario recipe median "
          f"{s['scenario_recipe_ms_median']} ms, max {s['scenario_recipe_ms_max']} ms; keyword median "
          f"{s['scenario_keyword_ms_median']} ms, max {s['scenario_keyword_ms_max']} ms")
    print(f"latency check: {lat['n_queries']} rank queries ({lat.get('n_points')} points x {len(lat.get('lines', []))} lines): "
          f"median {lat.get('ms_median')} ms, p95 {lat.get('ms_p95')} ms, max {lat.get('ms_max')} ms, "
          f"under {RANK_MS_LIMIT:.0f} ms: {lat.get('passed')}")


if __name__ == "__main__":
    main()
