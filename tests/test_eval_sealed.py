"""Tests for the sealed evaluation harness (scripts/eval_sealed.py). No API calls:
judges are replaced by a fake call function."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    name = "eval_sealed_under_test"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "eval_sealed.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


E = _load()


# ---------------------------------------------------------------------------- sampling

def _offices(seed=0):
    rng = np.random.default_rng(seed)
    sizes = {"BIG": 5000, "MID": 900, "SMALL": 60, "TINY": 3, "NA": 50}
    rows, pid = [], 0
    for st, n in sizes.items():
        for i in range(n):
            rows.append({"po_id": pid, "office_name": f"O{pid}", "pin": f"{100000 + pid % 900000}", "state": st,
                         "district": f"{st}-D{i % 4}", "lat": 20 + rng.random(), "lon": 78 + rng.random(),
                         "coord_valid": True, "coord_suspect": i % 17 == 0, "coord_placeholder": i % 23 == 0})
            pid += 1
    return pd.DataFrame(rows)


def test_allocate_sqrt_cap_and_total():
    counts = {"A": 10000, "B": 2500, "C": 100, "D": 1}
    a = E.allocate(counts, 20, cap=8)
    assert sum(a.values()) == 20
    assert max(a.values()) <= 8
    assert a["A"] >= a["B"] >= a["C"] >= a.get("D", 0)
    assert a.get("D", 0) <= 1
    with pytest.raises(ValueError):
        E.allocate({"A": 3, "B": 2}, 10, cap=8)


def test_draw_points_deterministic_balanced_and_eligible():
    off = _offices()
    p1 = E.draw_points(off, seed=7, n=40, n_dev=20, cap=15, third_per_split=6)
    p2 = E.draw_points(off, seed=7, n=40, n_dev=20, cap=15, third_per_split=6)
    pd.testing.assert_frame_equal(p1, p2)
    assert E.points_hash(p1) == E.points_hash(p2)
    assert (p1.split == "dev").sum() == 20 and (p1.split == "sealed").sum() == 20
    assert "NA" not in set(p1.state)
    bad = off[off.coord_suspect | off.coord_placeholder].po_id
    assert not set(p1.po_id) & set(bad)
    assert p1.groupby("state").size().max() <= 15
    for sp in E.SPLITS:
        ts = p1.loc[p1.split == sp, "third_service"].dropna()
        assert len(ts) == 6
        assert not set(ts) & set(E.CORE_SERVICES)
    p3 = E.draw_points(off, seed=8, n=40, n_dev=20, cap=15, third_per_split=6)
    assert E.points_hash(p3) != E.points_hash(p1)


def test_points_hash_detects_any_change():
    p = E.draw_points(_offices(), seed=1, n=20, n_dev=10, cap=12, third_per_split=3)
    h = E.points_hash(p)
    q = p.copy()
    q.loc[0, "lat"] = q.loc[0, "lat"] + 1e-5
    assert E.points_hash(q) != h
    assert E.points_hash(p.sample(frac=1, random_state=3)) == h  # row order does not matter


def test_make_queries_two_core_services_plus_third():
    p = E.draw_points(_offices(), seed=1, n=20, n_dev=10, cap=12, third_per_split=3)
    q = E.make_queries(p)
    assert len(q) == 2 * 20 + 6
    assert set(q.service) >= set(E.CORE_SERVICES)
    assert (q.groupby("po_id").size().isin([2, 3])).all()
    assert q.query_id.is_unique
    for r in q.itertuples():
        assert r.radius_km == E.S.RADII_KM[r.service]


# ---------------------------------------------------------------------------- protocol seal

def test_protocol_seal_chain(tmp_path):
    p = tmp_path / "protocol.md"
    p.write_text("# Protocol\n\nPoint list sha256: `" + "a" * 64 + "`\n")
    assert not E.protocol_status(p)["sealed"]
    h = E.seal_protocol(p)
    st = E.protocol_status(p)
    assert st["ok"] and st["sha256"] == h and st["n_seals"] == 1
    assert E.seal_protocol(p) == h  # idempotent
    assert E.points_hash_in_protocol(p) == "a" * 64
    # an amendment below the seal is detected until it is sealed too
    p.write_text(p.read_text() + "\n## Amendment 1\n\nchanged x\n")
    st = E.protocol_status(p)
    assert not st["ok"] and st["unsealed_amendment"]
    h2 = E.seal_protocol(p)
    st = E.protocol_status(p)
    assert st["ok"] and st["n_seals"] == 2 and h2 != h
    # editing above the last seal breaks it and cannot be re-sealed
    p.write_text(p.read_text().replace("# Protocol", "# Protocol!"))
    assert not E.protocol_status(p)["ok"]
    with pytest.raises(SystemExit):
        E.seal_protocol(p)


# ---------------------------------------------------------------------------- judges

def test_consensus_rules():
    assert E.consensus({"A": True, "B": True}) is True
    assert E.consensus({"A": False, "B": False}) is False
    assert E.consensus({"A": True, "B": False, "C": False}) is False
    assert E.consensus({"A": True, "B": False, "C": True}) is True
    assert E.consensus({"A": True, "B": None, "C": True}) is True
    assert E.consensus({"A": True, "B": None, "C": False}) is None
    assert E.consensus({"A": True, "B": False, "C": None}) is None


def _fake_call(answers: dict):
    """answers: (judge model, kind, facility marker in the user text) -> bool."""
    calls = []

    def fn(client, spec, kind, system, user):
        calls.append((spec.name, kind, user[:60]))
        val = None
        for (judge, k, marker), v in answers.items():
            if judge == spec.name and k == kind and marker in user:
                val = v
        if val is None:
            val = True
        key = E.ANSWER_KEY[kind]
        ans = {key: val, "reason": "r"}
        if kind == "q1":
            ans["quote"] = ""
        return {"answer": ans, "usage": {"input_tokens": 1000, "output_tokens": 100}, "stop_reason": "end_turn",
                "request_id": "req_x", "model": spec.model}

    return fn, calls


def _specs():
    return {j: E.JudgeSpec(j, "claude-haiku-5-5", "low") for j in ("A", "B", "C")}


def test_runner_cache_budget_and_third_pass(tmp_path):
    fn, calls = _fake_call({("A", "q1", "REC-1"): True, ("B", "q1", "REC-1"): False, ("C", "q1", "REC-1"): False})
    budget = E.Budget(tmp_path / "spend.jsonl", cap_usd=1.0, project_budget=None, max_calls=None)
    budget.others = 0.0
    runner = E.JudgeRunner(_specs(), budget, client=None, call_fn=fn, cache_dir=tmp_path / "cache")
    units = E.Units(runner)
    u = units.judge("q1", "f1|emoc", E.SYSTEM_Q1, E.user_q1("emoc", "REC-1"), {"facility_id": "f1", "service": "emoc"})
    assert u["third_pass"] and u["final"] is False and u["votes"] == {"A": True, "B": False, "C": False}
    assert len(calls) == 3
    lines = (tmp_path / "spend.jsonl").read_text().splitlines()
    assert len(lines) == 3 and all(json.loads(l)["cost_usd"] > 0 for l in lines)
    # a fresh runner reads the disk cache: no new calls
    runner2 = E.JudgeRunner(_specs(), None, client=None, call_fn=fn, cache_dir=tmp_path / "cache")
    u2 = E.Units(runner2).judge("q1", "f1|emoc", E.SYSTEM_Q1, E.user_q1("emoc", "REC-1"), {})
    assert u2["final"] is False and runner2.n_api == 0 and len(calls) == 3


def test_budget_call_cap_and_dollar_cap(tmp_path):
    fn, calls = _fake_call({})
    b = E.Budget(tmp_path / "s.jsonl", cap_usd=1.0, project_budget=None, max_calls=1)
    b.others = 0.0
    r = E.JudgeRunner(_specs(), b, call_fn=fn, cache_dir=tmp_path / "c")
    r.ask("A", "q1", E.SYSTEM_Q1, "u1", "x")
    with pytest.raises(E.StopJudging):
        r.ask("A", "q1", E.SYSTEM_Q1, "u2", "y")
    b2 = E.Budget(tmp_path / "s2.jsonl", cap_usd=0.0, project_budget=None, max_calls=None)
    b2.others = 0.0
    with pytest.raises(E.StopJudging):
        E.JudgeRunner(_specs(), b2, call_fn=fn, cache_dir=tmp_path / "c2").ask("A", "q1", E.SYSTEM_Q1, "u3", "z")
    b3 = E.Budget(tmp_path / "s3.jsonl", cap_usd=100.0, project_budget=5.0, max_calls=None)
    b3.others = 5.0  # the rest of the project already used the budget
    with pytest.raises(E.StopJudging):
        E.JudgeRunner(_specs(), b3, call_fn=fn, cache_dir=tmp_path / "c3").ask("A", "q1", E.SYSTEM_Q1, "u4", "w")


# ---------------------------------------------------------------------------- one query, judged and scored

def _query():
    pick = lambda fid, km, grp=None: {"facility_id": fid, "pos": 0, "name": fid, "distance_km": km,  # noqa: E731
                                      "dup_group": grp or fid}
    return {
        "query_id": "dev-1-emoc", "po_id": 1, "service": "emoc", "radius_km": 50.0, "state": "S",
        "place_label": "X post office", "pool_size": 4,
        "tools": {"specialty": {"pick": pick("near_claim", 1.0)},
                  "any_fact": {"pick": pick("mid", 5.0)},
                  "ranker": {"pick": None, "status": "no_reliable_option",
                             "quote_check": {"n_quotes": 2, "strict": 2, "loose": 2}}},
        "pool": [["near_claim", 1.0, "near_claim"], ["dup_of_mid", 3.0, "mid"], ["mid", 5.0, "mid"],
                 ["far", 20.0, "far"]],
    }


def test_judge_query_walk_and_outcomes(tmp_path):
    # near_claim: no; dup_of_mid: yes (same record group as mid); mid: yes
    fn, calls = _fake_call({(j, "q1", "REC near_claim"): False for j in "ABC"})
    runner = E.JudgeRunner(_specs(), E.Budget(tmp_path / "s.jsonl", 10.0, None, None), call_fn=fn,
                           cache_dir=tmp_path / "c")
    runner.budget.others = 0.0
    units = E.Units(runner)
    q = _query()
    jq = E.judge_query(units, lambda fid: f"REC {fid}", q)
    assert jq["walk_status"] == "found"
    assert jq["nearest_yes"]["facility_id"] == "dup_of_mid"
    assert jq["q1"]["near_claim"] is False and jq["q1"]["mid"] is True
    rows = {r["tool"]: r for r in E.outcome_rows(q, jq)}
    assert rows["specialty"]["answered"] and rows["specialty"]["correct"] is False
    assert rows["specialty"]["skipped_nearer"] is False  # the supported one is farther
    assert rows["any_fact"]["correct"] is True
    assert rows["any_fact"]["skipped_nearer"] is False  # nearer, but the same record group
    assert rows["ranker"]["answered"] is False and rows["ranker"]["abstained_with_support"] is True
    assert rows["ranker"]["quotes_strict"] == 2
    res = E.summarize(pd.DataFrame(list(rows.values())))
    assert res["tools"]["ranker"]["coverage"]["k"] == 0
    assert res["tools"]["ranker"]["abstained_with_support"]["k"] == 1
    assert res["tools"]["any_fact"]["precision"]["rate"] == 1.0
    md = E.render_results_md("dev", res, {"judges": {"A": "m"}})
    assert md.index("cost of our caution") < md.index("All three tools")
    assert chr(0x2014) not in md


def test_skipped_nearer_and_walk_cap(tmp_path):
    fn, _ = _fake_call({(j, "q1", "REC near_claim"): True for j in "ABC"})
    runner = E.JudgeRunner(_specs(), E.Budget(tmp_path / "s.jsonl", 10.0, None, None), call_fn=fn,
                           cache_dir=tmp_path / "c")
    runner.budget.others = 0.0
    q = _query()
    jq = E.judge_query(E.Units(runner), lambda fid: f"REC {fid}", q)
    rows = {r["tool"]: r for r in E.outcome_rows(q, jq)}
    assert rows["any_fact"]["skipped_nearer"] is True  # 1 km against 5 km, different group
    # walk cap 0 with every pick judged "no": the walk stops without a verdict
    fn2, _ = _fake_call({(j, "q1", "REC"): False for j in "ABC"})
    r2 = E.JudgeRunner(_specs(), E.Budget(tmp_path / "s2.jsonl", 10.0, None, None), call_fn=fn2,
                       cache_dir=tmp_path / "c2")
    r2.budget.others = 0.0
    jq2 = E.judge_query(E.Units(r2), lambda fid: f"REC {fid}", q, walk_cap=0)
    assert jq2["walk_status"] == "capped" and jq2["nearest_yes"] is None


# ---------------------------------------------------------------------------- human labels, agreement, stats

def test_select_human_items_stratified_blind_and_unique():
    units = []
    picks_of = {}
    for i in range(60):
        uid = f"f{i}|emoc"
        units.append({"kind": "q1", "unit_id": uid, "facility_id": f"f{i}", "service": "emoc",
                      "votes": {"A": i % 2 == 0, "B": i % 2 == 0}, "final": i % 2 == 0, "third_pass": i % 7 == 0})
        if i < 45:
            picks_of[uid] = [E.TOOLS[i % 3]]
    items = E.select_human_items(units, picks_of, n=40, seed=3)
    assert len(items) == 40
    assert len({u["unit_id"] for u in items}) == 40
    strata = {u["stratum"].split(":")[0] for u in items}
    assert strata >= {"specialty", "any_fact", "ranker", "walk"}
    assert {u["stratum"].split(":")[1] for u in items} == {"yes", "no"}


def test_agreement_and_kappa():
    human = pd.DataFrame({"item_id": ["H1", "H2", "H3", "H4"], "human_clearly_offers": ["y", "n", "y", ""],
                          "human_sensible": ["y", "", "n", ""]})
    key = pd.DataFrame({"item_id": ["H1", "H2", "H3", "H4"], "q1_final": ["True", "False", "False", "True"],
                        "q1_A": ["True", "False", "True", "True"], "q2_final": ["True", "", "False", ""]})
    res = E.agreement(human, key)
    assert res["q1"]["n_labeled"] == 3
    assert res["q1"]["final"]["n"] == 3 and abs(res["q1"]["final"]["agreement"] - 2 / 3) < 1e-9
    assert res["q1"]["A"]["agreement"] == 1.0 and res["q1"]["A"]["kappa"] == 1.0
    assert res["q2"]["final"]["n"] == 2 and res["q2"]["final"]["agreement"] == 1.0
    assert E.cohen_kappa([True, False, True, False], [True, False, True, False]) == 1.0
    assert abs(E.cohen_kappa([True, True, False, False], [True, False, True, False])) < 1e-9


def test_judged_prefix_ignores_queries_finished_out_of_order():
    # q3 needed no calls and finished after the cap stopped q2: it must not be scored
    jd = {"order": ["q1", "q2", "q3"], "queries": {"q1": {}, "q3": {}}}
    assert E.judged_prefix(jd) == ["q1"]
    assert E.judged_prefix({"order": ["a", "b"], "queries": {"a": {}, "b": {}}}) == ["a", "b"]


def test_wilson():
    lo, hi = E.wilson(0, 10)
    assert lo == 0.0 and 0.2 < hi < 0.35
    lo, hi = E.wilson(5, 10)
    assert lo < 0.5 < hi
    assert E.wilson(0, 0) == (None, None)


# ---------------------------------------------------------------------------- record text, quotes, pool

def test_record_text_has_record_only():
    fac = {"name": "Alpha Hospital", "facility_type": "hospital", "operator_type": "private", "city_raw": "Pune",
           "state_raw": "Maharashtra", "pin": "411001", "specialties": np.array(["gynecologyAndObstetrics"]),
           "n_beds": pd.NA, "n_doctors": 12, "description": "A hospital.", "grade": 3, "flags": "x"}
    facts = [{"field": "procedure", "text": "Performs LSCS"}, {"field": "capability", "text": "24x7 blood bank"}]
    t = E.record_text(fac, facts)
    assert "gynecology and obstetrics" in t and "Performs LSCS" in t and "24x7 blood bank" in t
    assert "doctors: 12" in t and "beds" not in t
    assert "grade" not in t.lower() and "flag" not in t.lower()


def _ctx():
    idx = SimpleNamespace(
        id_to_pos={"a": 0, "b": 1, "c": 2},
        lines={"emoc": SimpleNamespace(grade=np.array([2, 1, 0]))},
        dup_canon={},
        fac=pd.DataFrame({"facility_id": ["a", "b", "c"], "name": ["A", "B", "C"]}),
    )
    return E.Ctx(idx=idx, rank_mod=None, silver=pd.DataFrame({"description": ["desc text here", None, None]}),
                 facts_by_pos={0: [{"fact_id": "x1", "field": "procedure", "text": "Performs LSCS daily"}]},
                 fact_text={"x1": ("a", "Performs LSCS daily")},
                 spec_mask={"emoc": np.array([False, True, False])},
                 kw_mask={"emoc": np.array([False, False, True])})


def test_quote_check_strict_and_loose():
    ctx = _ctx()
    cards = [{"facility_id": "a", "evidence": [{"quote": "Performs LSCS daily", "fact_id": "x1"},
                                               {"quote": "LSCS", "fact_id": "zz"},
                                               {"quote": "text here", "fact_id": None},
                                               {"quote": "not in record", "fact_id": "x1"}]}]
    qc = E.quote_check(ctx, cards)
    assert qc["n_quotes"] == 4 and qc["strict"] == 1 and qc["loose"] == 3
    assert qc["failures"][0]["quote"] == "not in record"


def test_tools_and_pool_share_radius_and_signal():
    ctx = _ctx()
    d = np.array([10.0, 2.0, np.nan])
    assert E.tool_any_fact(ctx, "emoc", d, 50.0) == 0
    assert E.tool_any_fact(ctx, "emoc", d, 5.0) is None  # the same right to abstain
    assert E.tool_specialty(ctx, "emoc", d, 50.0) == 1
    assert E.judge_pool(ctx, "emoc", d, 50.0) == [1, 0]  # c has no usable location
    d2 = np.array([10.0, 2.0, 1.0])
    assert E.judge_pool(ctx, "emoc", d2, 50.0) == [2, 1, 0]  # keyword signal counts


def test_estimate_cost_bounds():
    doc = {"queries": [_query()]}
    est = E.estimate_cost(doc, [], _specs())
    lo, mid, hi = (est[k]["usd"] for k in ("low (picks only)", "expected", "high (every walk to the cap)"))
    assert 0 < lo <= mid <= hi


def test_prompts_are_blind_and_plain():
    for s in (E.SYSTEM_Q1, E.SYSTEM_Q2):
        low = s.lower()
        assert "ranker" not in low and "tool a" not in low and "grade" not in low
        assert chr(0x2014) not in s
    assert set(E.SERVICE_NEED) == set(E.S.SERVICE_LINES)


# ---------------------------------------------------------------------------- the real sample, if drawn

def test_real_points_match_protocol_and_sealed_guard():
    if not E.points_path().exists() or not E.protocol_path().exists():
        pytest.skip("points not drawn yet")
    pts = E.read_points()
    assert len(pts) == E.N_POINTS and (pts.split == "dev").sum() == E.N_DEV
    want = E.points_hash_in_protocol()
    if want:
        assert E.points_hash(pts) == want
    with pytest.raises(SystemExit):
        E.check_sealed_inputs("sealed", unseal=False)


# ---------------------------------------------------------------------------- fix round: tamper evidence and rules in code

def _small_points():
    return pd.DataFrame({"po_id": [1, 2], "office_name": ["A", "B"], "pin": ["100001", "100002"],
                         "state": ["S", "S"], "district": ["D", "D"], "lat": [20.0, 21.0], "lon": [78.0, 79.0],
                         "split": ["dev", "sealed"], "third_service": [None, None]})


def _protocol(tmp_path, body_extra=""):
    pts = _small_points()
    p = tmp_path / "protocol.md"
    p.write_text("# Protocol\n\nPoint list sha256: `" + E.points_hash(pts) + "`\n\n"
                 "- Judge A: `claude-haiku-5-5`, effort low. Judge B: `claude-sonnet-5-5`, thinking off. "
                 "Judge C, used only when A and B disagree: `claude-opus-5-5`.\n\n"
                 "Judge prompt sha256: `" + E.PROMPT_SHA + "`\n\nat most 10 per query\n" + body_extra)
    return p, pts


def _clean_git(monkeypatch, clean=True):
    monkeypatch.setattr(E, "git_guard", lambda root, rel_paths=E.GUARDED_PATHS: {
        "git_commit": "abc", "clean": clean, "tracked": {}, "porcelain": [] if clean else ["?? x"]})


def test_reseal_after_edit_is_caught_by_the_pinned_chain(tmp_path, monkeypatch):
    p, pts = _protocol(tmp_path)
    h = E.seal_protocol(p)
    monkeypatch.setattr(E, "read_points", lambda: pts)
    _clean_git(monkeypatch)
    st = E.protocol_status(p, expected=(h,))
    assert st["ok"] and st["pinned"] and st["chain"] == [h]
    E.check_sealed_inputs("sealed", True, root=tmp_path, protocol=p, expected=(h,))  # passes
    # the review's attack: edit the body, delete the seal line, seal again
    body = E.protocol_body(p.read_text()).replace("at most 10 per query", "at most 3 per query")
    p.write_text(body)
    h2 = E.seal_protocol(p)
    st = E.protocol_status(p, expected=(h,))
    assert st["ok"] and not st["pinned"] and h2 != h
    with pytest.raises(SystemExit, match="seal chain"):
        E.check_sealed_inputs("sealed", True, root=tmp_path, protocol=p, expected=(h,))
    # dev runs only warn
    E.check_sealed_inputs("dev", False, root=tmp_path, protocol=p, expected=(h,))
    # deleting a sealed amendment is caught too (the chain is shorter than pinned)
    (tmp_path / "x").mkdir()
    p2, _ = _protocol(tmp_path / "x")
    a = E.seal_protocol(p2)
    p2.write_text(p2.read_text() + "\n## Amendment 1\n\nx\n")
    b = E.seal_protocol(p2)
    assert E.protocol_status(p2, expected=(a, b))["pinned"]
    p2.write_text(E.protocol_body(E.protocol_body(p2.read_text())) + E.SEAL_PREFIX + f"`{a}`\n")
    assert E.protocol_status(p2, expected=(a, b))["ok"] and not E.protocol_status(p2, expected=(a, b))["pinned"]


def test_sealed_picks_and_judge_refuse_uncommitted_code(tmp_path, monkeypatch):
    p, pts = _protocol(tmp_path)
    h = E.seal_protocol(p)
    monkeypatch.setattr(E, "read_points", lambda: pts)
    _clean_git(monkeypatch, clean=False)
    for stage in ("picks", "judge"):
        with pytest.raises(SystemExit, match="commit"):
            E.check_sealed_inputs("sealed", True, stage=stage, root=tmp_path, protocol=p, expected=(h,))
    E.check_sealed_inputs("sealed", True, stage="score", root=tmp_path, protocol=p, expected=(h,))  # warns
    E.check_sealed_inputs("dev", False, stage="picks", root=tmp_path, protocol=p, expected=(h,))


def test_git_guard_on_a_real_repo(tmp_path):
    import subprocess
    run = lambda *a: subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True)  # noqa: E731
    run("init", "-q")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "t")
    (tmp_path / "reports").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "src" / "vf").mkdir(parents=True)
    for f in ("reports/eval_sealed_protocol.md", "scripts/eval_sealed.py", "src/vf/rank.py"):
        (tmp_path / f).write_text("x\n")
    assert not E.git_guard(tmp_path)["clean"]  # nothing committed yet
    run("add", "-A")
    run("commit", "-q", "-m", "c")
    assert E.git_guard(tmp_path)["clean"]
    (tmp_path / "src/vf/recipes.py").write_text("new\n")  # untracked file under src/vf
    assert not E.git_guard(tmp_path)["clean"]
    (tmp_path / "src/vf/recipes.py").unlink()
    (tmp_path / "src/vf/rank.py").write_text("changed\n")
    g = E.git_guard(tmp_path)
    assert not g["clean"] and any("rank.py" in l for l in g["porcelain"])
    hs = E.code_hashes(tmp_path)
    assert set(hs) == {"scripts/eval_sealed.py", "reports/eval_sealed_protocol.md", "src/vf/rank.py"}


def test_sealed_judge_settings_are_the_protocols(tmp_path, monkeypatch):
    p, _ = _protocol(tmp_path)
    E.check_judges(E.PROTOCOL_JUDGES, p)
    with pytest.raises(SystemExit):
        E.check_judges(E.judge_specs({"VF_LLM_MODEL": "claude-sonnet-5-5"}), p)
    with pytest.raises(SystemExit):
        E.check_judges(E.judge_specs({}, "single"), p)
    assert E.judge_specs({}) == E.PROTOCOL_JUDGES
    monkeypatch.setattr(E, "protocol_path", lambda: p)
    info = {"protocol": {"sha256": "s"}, "code_sha256": {"scripts/eval_sealed.py": "1"}}
    doc = {"protocol": {"sha256": "s"}, "code_sha256": {"scripts/eval_sealed.py": "1"}}
    ok = SimpleNamespace(walk_cap=E.WALK_CAP, judges="mixed")
    E.check_sealed_judge_settings(ok, doc, info, E.PROTOCOL_JUDGES)
    with pytest.raises(SystemExit, match="walk-cap"):
        E.check_sealed_judge_settings(SimpleNamespace(walk_cap=3, judges="mixed"), doc, info, E.PROTOCOL_JUDGES)
    with pytest.raises(SystemExit, match="mixed"):
        E.check_sealed_judge_settings(SimpleNamespace(walk_cap=10, judges="single"), doc, info, E.PROTOCOL_JUDGES)
    with pytest.raises(SystemExit, match="code changed"):
        E.check_sealed_judge_settings(ok, {**doc, "code_sha256": {"scripts/eval_sealed.py": "2"}}, info,
                                      E.PROTOCOL_JUDGES)
    with pytest.raises(SystemExit, match="seal changed"):
        E.check_sealed_judge_settings(ok, {**doc, "protocol": {"sha256": "t"}}, info, E.PROTOCOL_JUDGES)


def test_sealed_picks_are_never_overwritten(tmp_path, monkeypatch):
    monkeypatch.setattr(E, "out_dir", lambda: tmp_path)
    monkeypatch.setattr(E, "check_sealed_inputs", lambda *a, **k: {})
    (tmp_path / "picks_sealed.json").write_text("{}")
    args = SimpleNamespace(split="sealed", unseal=True, bundle=None)
    with pytest.raises(SystemExit, match="computed once"):
        E.cmd_picks(args)
    assert (tmp_path / "picks_sealed.json").read_text() == "{}"


def test_listing_only_yes():
    rec = ("Name: X Hospital\nSpecialties listed: general surgery; neurosurgery\n"
           "Capability facts:\n- 24x7 emergency\n- Neurosurgery department with OT")
    u = lambda qa, qb, a=True, b=True: {"final": a and b, "votes": {"A": a, "B": b},  # noqa: E731
                                        "answers": {"A": {"quote": qa}, "B": {"quote": qb}}}
    assert E.listing_only_yes(u("Specialties listed: general surgery; neurosurgery", "general surgery; neurosurgery"),
                              rec)
    assert not E.listing_only_yes(u("Specialties listed: neurosurgery", "- Neurosurgery department with OT"), rec)
    assert not E.listing_only_yes(u("neurosurgery", "neurosurgery"), rec)  # also in a fact line
    assert not E.listing_only_yes(u("", "general surgery; neurosurgery"), rec)
    assert not E.listing_only_yes(u("general surgery; neurosurgery", "x", b=False), rec)  # verdict not yes


def test_flag_split_and_listing_only_in_scoring(tmp_path):
    fn, _ = _fake_call({(j, "q1", "REC near_claim"): False for j in "ABC"})
    runner = E.JudgeRunner(_specs(), E.Budget(tmp_path / "s.jsonl", 10.0, None, None), call_fn=fn,
                           cache_dir=tmp_path / "c")
    runner.budget.others = 0.0
    q = _query()
    q["major_flags"] = {"dup_of_mid": ["merged_record"]}
    jq = E.judge_query(E.Units(runner), lambda fid: f"REC {fid}", q)
    rows = E.outcome_rows(q, jq, listing_only={"mid|emoc": True})
    rk = {r["tool"]: r for r in rows}["ranker"]
    assert rk["abstained_with_support"] and rk["nearest_yes_flagged"] is True
    assert rk["nearest_yes_major_flags"] == "merged_record"
    res = E.summarize(pd.DataFrame(rows))
    s = res["tools"]["ranker"]["abstained_with_support_split"]
    assert s["nearest_flagged"]["k"] == 1 and s["nearest_unflagged"]["k"] == 0
    af = res["tools"]["any_fact"]
    assert af["precision"]["k"] == 1 and af["precision_excluding_listing_only"]["k"] == 0
    assert af["pick_yes_listing_only"] == 1
    # picks made before flags were stored: no split rather than a wrong "unflagged"
    q2 = _query()
    res2 = E.summarize(pd.DataFrame(E.outcome_rows(q2, jq)))
    assert "abstained_with_support_split" not in res2["tools"]["ranker"]
    md = E.render_results_md("dev", res, {"judges": {"A": "m"}, "judge_runs": [{"at": "t", "api_calls": 0}],
                                          "ledger": {"api_calls": 28, "by_judge": {"A": 14}, "usd": 0.1}})
    assert "## Integrity check (not part of the evaluation)" in md and "It is not a judged result" in md
    table = md[md.index("| --- |"):md.index("\n\n", md.index("| --- |"))]
    assert "card" not in table.lower() and "verbatim" not in table.lower()  # no card-quote figure in the table
    assert "WP11 major flag" in md and chr(0x2014) not in md
    assert res["integrity_checks"]["card_quotes_match_cited_fact"]["k"] == 2


def test_mask_contacts():
    t = "Incharge: Dr X. Contact number: 8709512345. Mail x.y@abc.co.in. PIN 411001, beds: 200, est 1995"
    m = E.mask_contacts(t)
    assert "8709512345" not in m and "x.y@abc.co.in" not in m
    assert "411001" in m and "200" in m and "1995" in m


def test_real_protocol_is_pinned_and_names_the_pinned_judges():
    if not E.protocol_path().exists():
        pytest.skip("no protocol")
    st = E.protocol_status()
    assert st["ok"] and st["chain_ok"] and st["pinned"], st
    assert E.judges_in_protocol() == {k: v.model for k, v in E.PROTOCOL_JUDGES.items()}
    assert E.prompt_sha_in_protocol() == E.PROMPT_SHA


def test_listing_any_catches_one_judge_quoting_the_listing():
    rec = ("Name: X\nSpecialties listed: neurology; neurosurgery\n"
           "Description: Departments including Neurology, Neurosurgery and Dentistry")
    u = {"final": True, "votes": {"A": True, "B": True},
         "answers": {"A": {"quote": "Specialties listed: neurology; neurosurgery"},
                     "B": {"quote": "Departments including Neurology, Neurosurgery and Dentistry"}}}
    assert not E.listing_only_yes(u, rec) and E.listing_only_yes(u, rec, "any")
    assert E._lo_level(True) == "all" and E._lo_level("any") == "any" and E._lo_level(None) is None


# ---------------------------------------------------------------------------- rubric versions (D051 item 2)

def test_j1_prompt_sha_and_cache_keys_are_unchanged_by_versioning(tmp_path):
    # the sealed protocol names this sha for j1; per-question versions must not move it
    assert E.prompt_sha("j1", E.SYSTEM_Q1_J1) == "7fba0ba0095d9c0696050f1aa45c80b002da7c8717116f9a5a7d6ea9c03de19a"
    r = E.JudgeRunner(_specs(), None, cache_dir=tmp_path, versions={"q1": "j1", "q2": "j1"})
    spec = _specs()["A"]
    old = E.sha256_bytes(json.dumps(["j1", "q1", spec.model, spec.effort, spec.thinking,
                                     E.sha256_bytes(E.SYSTEM_Q1_J1.encode()), "u"], ensure_ascii=False).encode())
    assert r._key(spec, "q1", E.SYSTEM_Q1_J1, "u") == old


def test_j2_adds_only_the_listing_rule_and_stays_blind():
    j1, j2 = E.SYSTEM_Q1_J1, E.SYSTEM_Q1_J2
    assert [l for l in j1.splitlines() if l not in j2.splitlines()] == []  # nothing of j1 removed
    added = [l for l in j2.splitlines() if l not in j1.splitlines()]
    assert len(added) == 1 and "specialty, department or doctor's qualification alone" in added[0]
    low = j2.lower()
    assert "ranker" not in low and "grade" not in low and chr(0x2014) not in j2
    assert E.prompt_sha("j2", j2) != E.prompt_sha("j1", j1)


def test_new_q1_version_keeps_the_q2_cache(tmp_path):
    fn, calls = _fake_call({})
    b = E.Budget(tmp_path / "spend.jsonl", cap_usd=1.0, project_budget=None, max_calls=None)
    b.others = 0.0
    r1 = E.JudgeRunner(_specs(), b, call_fn=fn, cache_dir=tmp_path / "c", versions={"q1": "j1", "q2": "j1"})
    E.Units(r1).judge("q1", "f|emoc", E.SYSTEM_Q1_J1, "REC", {})
    E.Units(r1).judge("q2", "q|f", E.SYSTEM_Q2, "REC2", {})
    n = len(calls)
    r2 = E.JudgeRunner(_specs(), b, call_fn=fn, cache_dir=tmp_path / "c", versions={"q1": "j2", "q2": "j1"})
    E.Units(r2).judge("q2", "q|f", E.SYSTEM_Q2, "REC2", {})
    assert len(calls) == n and r2.n_cached == 2  # Q2 answers reused
    E.Units(r2).judge("q1", "f|emoc", E.SYSTEM_Q1_J2, "REC", {})
    assert len(calls) == n + 2  # Q1 asked again under j2
    assert (tmp_path / "c" / "j2" / "A").exists() and (tmp_path / "c" / "j1" / "A").exists()
    rows = [json.loads(l) for l in (tmp_path / "spend.jsonl").read_text().splitlines()]
    assert rows[-1]["prompt_version"] == "j2"


def test_rejudge_refuses_the_sealed_half():
    args = SimpleNamespace(split="sealed", unseal=True, prompt_q1="j2", units_file="x")
    with pytest.raises(SystemExit, match="dev half only"):
        E.cmd_rejudge(args)
