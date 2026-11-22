"""WP5 step 7: run the scenario suite and write the raw answers for judging.

Reads ``data/labels/scenarios.csv`` (written before any tuning), resolves each
place with ``vf.locate`` (WP2 B), runs ``vf.rank.rank`` or ``vf.rank.search``
on the real grades (recipe v0.3 when written, D048) and WP11 trust checks, and
writes one entry per scenario with the query, the resolution, the status, the
cards with their quoted evidence, flags and parts, ``beyond_radius``,
``further_away`` and ``unverified_nearby``.

``--version v1`` (default) runs score spec v1-final and writes
``reports/ranking_scenarios_v1_raw.json``; ``--version v0`` runs the legacy
ranker and writes ``reports/ranking_scenarios_raw.json`` (same shape).

This script does not judge the answers. Independent judges read the JSON and
mark each scenario right, defensible or wrong.

It also runs the latency check: every service line at every resolved scenario
point, timed after loading.

    python scripts/wp5_scenarios.py                       # v1: writes reports/ranking_scenarios_v1_raw.json
    python scripts/wp5_scenarios.py --version v0          # legacy: writes reports/ranking_scenarios_raw.json
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
OUT_DEFAULT_V1 = ROOT / "reports" / "ranking_scenarios_v1_raw.json"
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


def latency_check(idx: rank.Index, points: list[tuple[float, float]], version: str = "v1") -> dict:
    """Every graded line at every point, after one warm-up query."""
    if not points:
        return {"n_queries": 0}
    w = {"status": "ok", "lat": points[0][0], "lon": points[0][1], "precision_km": 5.0, "label": "warm-up", "how": ""}
    rank.rank(idx, idx.graded_lines[0], w, version=version)
    times = []
    for lat, lon in points:
        res = {"status": "ok", "lat": lat, "lon": lon, "precision_km": 5.0, "label": "latency", "how": ""}
        for line in idx.graded_lines:
            t = time.perf_counter()
            rank.rank(idx, line, res, version=version)
            times.append((time.perf_counter() - t) * 1000)
    return {"n_queries": len(times), "n_points": len(points), "lines": list(idx.graded_lines),
            "ms_median": _pct(times, 50), "ms_p95": _pct(times, 95), "ms_max": round(float(max(times)), 2),
            "limit_ms": RANK_MS_LIMIT, "passed": bool(max(times) < RANK_MS_LIMIT)}


def run(out: Path, md_out: Path | None = None, version: str = "v1") -> dict:
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
            ans = rank.rank(idx, r.service, res, version=version)
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
            "further_away": d.get("further_away", []),
            "window": d.get("window", {}),
            "rank_version": d.get("rank_version", "v0"),
            "unverified_nearby": d["unverified_nearby"],
            "how_we_decided": d["how_we_decided"],
            "fingerprint": fingerprint(ans),
            "ms": {"resolve": round(resolve_ms, 2), "answer": round(ms, 2)},
        })

    lat = latency_check(idx, points, version)
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
        "cards_by_confidence": pd.Series([c["confidence"] for e in entries for c in e["cards"]],
                                         dtype=object).value_counts(dropna=False).to_dict(),
        "cards_by_grade": pd.Series([c.get("grade") for e in entries for c in e["cards"]],
                                    dtype=object).value_counts(dropna=False).to_dict(),
        "cards_nearest_reserved": sum(1 for e in entries for c in e["cards"]
                                      if c.get("nearest_in_window") and "always shown" in (c.get("why_here") or "")),
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
        "rank_version": version,
        "score_mode": ("v1-final (D048): gates, drive-time windows, nearest first with the evidence allowance"
                       if version == "v1" else
                       "probability" if any(idx.is_probability(l) for l in idx.graded_lines) else "v0 (provisional, D039)"),
        "grades_source": idx.grades_source, "grades_status": idx.grades_status,
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
    where = (f"{c['distance_label']}; {c['minutes_label']}" if c.get("minutes_label")
             else f"{km} km straight line")
    out = [f"{i}. **{c['name']}** ({c['type'] or 'type unknown'}, {c['city'] or '?'}), {where}. "
           f"{c['grade_label'].split(' (')[0]}, evidence {c['evidence_strength']}; "
           + (f"confidence {c['confidence']}" if c.get("confidence") else f"note: {c.get('confidence_reason') or ''}")
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
        if e.get("further_away"):
            L += ["Further away (beyond the window):"] + [x for c in e["further_away"] for x in _card_md(c, "-")]
        elif e["beyond_radius"]:
            L += ["Nearest beyond the radius:"] + _card_md(e["beyond_radius"], "-")
        if e["unverified_nearby"]:
            L.append("Also nearby, not verified: " + "; ".join(
                f"{c['name']} ({c['distance_km']} km): {c['confidence_reason']}" for c in e["unverified_nearby"]))
        L.append("")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name("." + out.name + ".tmp")
    tmp.write_text("\n".join(L) + "\n")
    os.replace(tmp, out)


# ---------------------------------------------------------------------------
# Sensitivity of the v1 order and abstentions (D048 rule: every declared design
# choice gets a sensitivity check): reports/ordering_behaviour.md
# ---------------------------------------------------------------------------

SENS_OUT = ROOT / "reports" / "ordering_behaviour.md"
SENS_N = 1500
SENS_SEED = 7  # the score research's seed (scripts/research/score_v1/f3_order.py)
#: (name, window_scale, allowance override or None for the line's own, terrain factor on,
#:  warnings deny the allowance)
SENS_SETTINGS = (
    ("base", 1.0, None, True, True),
    ("allowance 0", 1.0, 0.0, True, True),
    ("allowance 15", 1.0, 15.0, True, True),
    ("allowance 30", 1.0, 30.0, True, True),
    ("windows x0.5", 0.5, None, True, True),
    ("windows x2", 2.0, None, True, True),
    ("terrain off", 1.0, None, False, True),
    ("flag denial off", 1.0, None, True, False),
)


def sensitivity_origins(n: int = SENS_N, seed: int = SENS_SEED) -> pd.DataFrame:
    """``n`` random geocoded post offices inside India's box, without the 1,032 sea-pixel offices (D048 item 7)."""
    po = pd.read_parquet(paths.silver_dir() / "post_offices.parquet",
                         columns=["po_id", "lat", "lon", "coord_valid", "state_pin", "pin"])
    po = po[po["coord_valid"] & po["lat"].between(6, 37.5) & po["lon"].between(68, 97.5)]
    sea_path = ROOT / "reference" / "sea_pixel_post_offices.csv"
    sea = set(pd.read_csv(sea_path)["po_id"]) if sea_path.exists() else set()
    po = po[~po["po_id"].isin(sea)]
    return po.sample(n, random_state=seed).reset_index(drop=True), len(sea)


def run_sensitivity(out: Path = SENS_OUT, n: int = SENS_N) -> dict:
    from vf import services as S
    from vf.geo import haversine_km

    t0 = time.time()
    idx = rank.load_index(load_post_offices=False)
    orig, n_sea = sensitivity_origins(n)
    lines = list(idx.graded_lines)
    res = {line: {name: [] for name, *_ in SENS_SETTINGS} for line in lines}
    cap_binds = {line: 0 for line in lines}
    for o in orig.itertuples(index=False):
        d = haversine_km(o.lat, o.lon, idx.lat, idx.lon)
        loc = {"lat": o.lat, "lon": o.lon, "state": o.state_pin, "precision_km": None, "label": ""}
        for line in lines:
            for name, ws, al, terr, deny in SENS_SETTINGS:
                res[line][name].append(rank.pick_v1(idx, line, o.lat, o.lon, state=o.state_pin, window_scale=ws,
                                                    allowance=al, terrain=terr, d=d, deny_flags=deny))
            # does the candidate cap ever hide a corroborated record the allowance would put first?
            ctx = rank._v1_context(idx, line, loc, d=d)
            sets = rank._v1_sets(idx, ctx)
            inw = sets["in_R"]
            if len(inw) > S.MAX_CANDIDATES and ctx.t is not None:
                g = idx.lines[line].grade
                beyond = inw[S.MAX_CANDIDATES:]
                if ((g[beyond] >= 3) & (ctx.t[beyond] <= ctx.t[inw[0]] + ctx.A)).any():
                    cap_binds[line] += 1

    def pct(x):
        return f"{100 * x:.1f}%"

    rows, summary = [], {}
    for line in lines:
        base = res[line]["base"]
        ans_b = np.array([r["top"] is not None for r in base])
        extra = np.array([r["top_minutes"] - r["nearest_minutes"] for r in base if r["top"] is not None
                          and r["top_minutes"] is not None])
        row = {"line": line, "window": S.WINDOWS[line].minutes, "allowance": S.WINDOWS[line].allowance,
               "kind": S.WINDOWS[line].kind, "abstain_base": float(1 - ans_b.mean()),
               "top_not_nearest": float((extra > 0.01).mean()) if len(extra) else 0.0,
               "extra_p90": float(np.quantile(extra, 0.9)) if len(extra) else 0.0,
               "extra_max": float(extra.max()) if len(extra) else 0.0, "cap_binds": cap_binds[line],
               "nearest_reserved": float(np.mean([r["nearest_reserved"] for r in base if r["top"] is not None]))
               if ans_b.any() else 0.0}
        for name, *_ in SENS_SETTINGS[1:]:
            alt = res[line][name]
            ans_a = np.array([r["top"] is not None for r in alt])
            both = ans_b & ans_a
            changed = np.array([b["top"] != a["top"] for b, a in zip(base, alt)])[both]
            row[f"top1:{name}"] = float(changed.mean()) if both.any() else 0.0
            changed3 = np.array([set(b["shown3"]) != set(a["shown3"]) for b, a in zip(base, alt)])[both]
            row[f"top3:{name}"] = float(changed3.mean()) if both.any() else 0.0
            row[f"abst:{name}"] = float(1 - ans_a.mean())
        rows.append(row)
    T = pd.DataFrame(rows)
    alts = [name for name, *_ in SENS_SETTINGS[1:]]
    mean_top1 = {a: float(T[f"top1:{a}"].mean()) for a in alts}
    mean_abst = {a: float(T[f"abst:{a}"].mean()) for a in alts}
    base_abst = float(T["abstain_base"].mean())
    most_top = max(mean_top1, key=mean_top1.get)
    most_abst = max(alts, key=lambda a: abs(mean_abst[a] - base_abst))
    secs = time.time() - t0

    L = ["# How the v1-final order and abstentions respond to their design choices", "",
         f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} by `{SCRIPT} --sensitivity` "
         f"(git {(_git_commit() or '?')[:7]}, code uncommitted: {_code_dirty()}). Grades: {idx.grades_status}. "
         f"Ranker: {rank.RANK_V1_VERSION}.", "",
         "## Findings", "",
         f"- **The window size drives abstention; the allowance drives which facility comes first.** "
         f"Averaged over the 12 lines, {pct(base_abst)} of origins get no recommendable facility inside the window. "
         f"Halving every window raises that to {pct(mean_abst['windows x0.5'])}; doubling it lowers it to "
         f"{pct(mean_abst['windows x2'])}.",
         f"- **Turning the allowance off changes the top pick in {pct(mean_top1['allowance 0'])} of answered origins** "
         f"(mean over lines; range {pct(T['top1:allowance 0'].min())} to {pct(T['top1:allowance 0'].max())}). "
         f"A 30-minute allowance on every line changes it in {pct(mean_top1['allowance 30'])}.",
         f"- **The terrain factor changes the top pick in {pct(mean_top1['terrain off'])} of answered origins** and moves "
         f"abstention from {pct(base_abst)} to {pct(mean_abst['terrain off'])} when it is turned off. Inside one origin "
         "the factor scales every facility's minutes alike, so it changes the order only through the allowance.",
         f"- **The allowance has a cost in minutes, capped by design.** Under the shipped settings the top pick is not "
         f"the nearest recommendable facility in {pct(float(T['top_not_nearest'].mean()))} of answered origins (mean "
         f"over lines); the extra estimated drive is at most the allowance ({T['extra_max'].max():.1f} minutes at "
         "worst across all lines).",
         f"- **The nearest recommendable facility is always on the cards.** When the allowance pushes it past the "
         f"third card it takes the last place; that happens in {pct(float(T['nearest_reserved'].mean()))} of answered "
         f"origins (mean over lines; range {pct(T['nearest_reserved'].min())} to {pct(T['nearest_reserved'].max())}).",
         f"- **Denying the allowance to records with a possible-merge or location-conflict warning** changes the top "
         f"pick in {pct(mean_top1['flag denial off'])} of answered origins and the set of three cards in "
         f"{pct(float(T['top3:flag denial off'].mean()))} (mean over lines), measured by turning the denial off.",
         f"- Largest mean top-pick change: {most_top} ({pct(mean_top1[most_top])}). Largest mean abstention change: "
         f"{most_abst} ({pct(base_abst)} to {pct(mean_abst[most_abst])}).",
         f"- The candidate cap ({S.MAX_CANDIDATES} nearest recommendable records) hid a corroborated record that the "
         f"allowance would have put first in {int(T['cap_binds'].sum())} of {len(lines) * len(orig):,} line-origin "
         "queries.", "",
         "Caveats: origins are unweighted post offices, not people; minutes are free-flow estimates from straight-line "
         "distance (log error SD 0.26), so these are statements about the rule, not about real trips. Windows and the "
         "allowance are planning thresholds and declared design choices, not clinical deadlines. Nothing here is a "
         "probability.", "",
         "## Method", "",
         f"- Origins: {len(orig):,} random geocoded post offices (seed {SENS_SEED}, the score research's seed), inside "
         f"India's box, without the {n_sea:,} sea-pixel offices in `reference/sea_pixel_post_offices.csv` (D048 item 7). "
         "Each origin uses its PIN-directory state's terrain factor.",
         "- For every origin, line and setting, the v1 ranker's own code (`vf.rank.pick_v1`, the same gates and order "
         "as `vf.rank.rank`) gives the top pick or an abstention.",
         "- Top-1 change: among origins answered under both the shipped settings and the alternative, the share whose "
         "first card is a different facility. Abstention: the share of all origins with no recommendable facility "
         "inside the window.",
         "- Settings: allowance 0, 15 or 30 minutes on every line (shipped: 15, and 30 for MRI, cardiac surgery and "
         "oncology); every window halved or doubled; terrain factor off (1.00 everywhere; islands still get no minutes); "
         "flag denial off (records with a possible-merge or location-conflict warning get the allowance too).",
         "- Top-3 change: the same, for the set of up to three cards shown (with the nearest always among them).",
         f"- Run time {secs:.0f} s.", "",
         "## Top pick changes against the shipped settings (share of origins answered under both)", "",
         "| Line | Window (min) | Basis | Allowance | " + " | ".join(alts) + " |",
         "| --- | --- | --- | --- | " + " | ".join("---" for _ in alts) + " |"]
    for r in rows:
        L.append(f"| {r['line']} | {r['window']:.0f} | {r['kind']} | {r['allowance']:.0f} | "
                 + " | ".join(pct(r[f'top1:{a}']) for a in alts) + " |")
    L += ["", "## Abstention (share of all origins with nothing recommendable inside the window)", "",
          "| Line | Shipped | " + " | ".join(alts) + " |", "| --- | --- | " + " | ".join("---" for _ in alts) + " |"]
    for r in rows:
        L.append(f"| {r['line']} | {pct(r['abstain_base'])} | " + " | ".join(pct(r[f'abst:{a}']) for a in alts) + " |")
    L += ["", "## Top-3 card set changes against the shipped settings (share of origins answered under both)", "",
          "| Line | " + " | ".join(alts) + " |", "| --- | " + " | ".join("---" for _ in alts) + " |"]
    for r in rows:
        L.append(f"| {r['line']} | " + " | ".join(pct(r[f'top3:{a}']) for a in alts) + " |")
    L += ["", "## What the allowance costs under the shipped settings", "",
          "| Line | Top pick not the nearest | Nearest moved to the last card | Extra minutes, p90 | Extra minutes, max "
          "| Cap hid a promotable record |",
          "| --- | --- | --- | --- | --- | --- |"]
    for r in rows:
        L.append(f"| {r['line']} | {pct(r['top_not_nearest'])} | {pct(r['nearest_reserved'])} | {r['extra_p90']:.1f} | "
                 f"{r['extra_max']:.1f} | {r['cap_binds']} |")
    L.append("")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name("." + out.name + ".tmp")
    tmp.write_text("\n".join(L))
    os.replace(tmp, out)
    summary = {"origins": len(orig), "sea_excluded": n_sea, "mean_top1_change": mean_top1,
               "mean_abstention": {"base": base_abst, **mean_abst}, "cap_binds": int(T["cap_binds"].sum()),
               "seconds": round(secs, 1)}
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--version", choices=rank.RANK_VERSIONS, default="v1",
                    help="v1: score spec v1-final (D048, shipped); v0: the legacy ranker")
    ap.add_argument("--out", type=Path, default=None,
                    help="default reports/ranking_scenarios_v1_raw.json (v1) or reports/ranking_scenarios_raw.json (v0)")
    ap.add_argument("--md", type=Path, default=None, help="also write an unjudged markdown rendering here")
    ap.add_argument("--sensitivity", action="store_true",
                    help="instead: the v1 order and abstention sensitivity table, reports/ordering_behaviour.md")
    args = ap.parse_args()
    if args.sensitivity:
        s = run_sensitivity()
        print(f"wrote {SENS_OUT}")
        print(f"origins {s['origins']} (sea-pixel offices excluded: {s['sea_excluded']}); "
              f"cap hid a promotable record {s['cap_binds']} times; {s['seconds']} s")
        print("mean top-1 change: " + ", ".join(f"{k} {v:.1%}" for k, v in s["mean_top1_change"].items()))
        print("mean abstention: " + ", ".join(f"{k} {v:.1%}" for k, v in s["mean_abstention"].items()))
        return
    args.out = args.out or (OUT_DEFAULT_V1 if args.version == "v1" else OUT_DEFAULT)
    s = run(args.out, args.md, args.version)
    lat = s["latency_check"]
    print(f"wrote {args.out}")
    print(f"scenarios {s['scenarios']}: {s['status_counts']}; status as expected {s['status_as_expected']}/{s['scenarios']}")
    if s["status_not_as_expected"]:
        print("  not as expected: " + ", ".join(f"{x['id']} ({x['expected']} -> {x['got']})" for x in s["status_not_as_expected"]))
    print(f"abstention {s['abstentions']}/{s['resolved']} ({s['abstention_rate']:.0%}); recipe only "
          f"{s['recipe_abstentions']}/{s['recipe_resolved']} ({s['recipe_abstention_rate']:.0%}); "
          f"expected abstentions that abstained {s['expected_abstentions_that_abstained']}/{s['expected_abstentions']}")
    print(f"cards {s['cards_shown']} by grade {s['cards_by_grade']}; nearest moved to the last card "
          f"{s['cards_nearest_reserved']}; with a flag {s['cards_with_a_flag']}")
    print(f"load {s['load_s']} s (+ keyword index {s['keyword_index_build_s']} s); scenario recipe median "
          f"{s['scenario_recipe_ms_median']} ms, max {s['scenario_recipe_ms_max']} ms; keyword median "
          f"{s['scenario_keyword_ms_median']} ms, max {s['scenario_keyword_ms_max']} ms")
    print(f"latency check: {lat['n_queries']} rank queries ({lat.get('n_points')} points x {len(lat.get('lines', []))} lines): "
          f"median {lat.get('ms_median')} ms, p95 {lat.get('ms_p95')} ms, max {lat.get('ms_max')} ms, "
          f"under {RANK_MS_LIMIT:.0f} ms: {lat.get('passed')}")


if __name__ == "__main__":
    main()
