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


def _draft_amendment(number: int = 3) -> str:
    """The reach amendment exactly as drafted (the text between the draft's two
    rules), with its placeholder number filled in."""
    t = (ROOT / "reports" / "eval_sealed_amendment_reach_draft.md").read_text(encoding="utf-8")
    parts = t.split("\n---\n")
    assert len(parts) >= 3, "the draft keeps the amendment between two '---' rules"
    return parts[1].replace("## Amendment N:", f"## Amendment {number}:", 1)


def _add_reach(p, number: int = 3, edit=None) -> str:
    """Append the drafted reach amendment below the last seal and seal it."""
    txt = _draft_amendment(number)
    if edit:
        txt = edit(txt)
    p.write_text(p.read_text() + "\n" + txt.strip("\n") + "\n")
    return E.seal_protocol(p)


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
    hr = _add_reach(p)
    E.check_sealed_inputs("sealed", True, root=tmp_path, protocol=p, expected=(h, hr))  # passes
    # the review's attack: edit the text under the last seal, delete that seal line, seal again
    body = E.protocol_body(p.read_text()).replace("the walk cap of 10", "the walk cap of 3")
    assert body != E.protocol_body(p.read_text())
    p.write_text(body)
    h2 = E.seal_protocol(p)
    st = E.protocol_status(p, expected=(h, hr))
    assert st["ok"] and not st["pinned"] and h2 != hr
    with pytest.raises(SystemExit, match="seal chain"):
        E.check_sealed_inputs("sealed", True, root=tmp_path, protocol=p, expected=(h, hr))
    # dev runs only warn
    E.check_sealed_inputs("dev", False, root=tmp_path, protocol=p, expected=(h, hr))
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
    hr = _add_reach(p)
    monkeypatch.setattr(E, "read_points", lambda: pts)
    _clean_git(monkeypatch, clean=False)
    for stage in ("picks", "judge"):
        with pytest.raises(SystemExit, match="commit"):
            E.check_sealed_inputs("sealed", True, stage=stage, root=tmp_path, protocol=p, expected=(h, hr))
    E.check_sealed_inputs("sealed", True, stage="score", root=tmp_path, protocol=p, expected=(h, hr))  # warns
    E.check_sealed_inputs("dev", False, stage="picks", root=tmp_path, protocol=p, expected=(h, hr))


def test_git_guard_on_a_real_repo(tmp_path):
    import subprocess
    run = lambda *a: subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True)  # noqa: E731
    run("init", "-q")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "t")
    (tmp_path / "reports").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "src" / "vf").mkdir(parents=True)
    (tmp_path / "reference").mkdir()
    for f in ("reports/eval_sealed_protocol.md", "scripts/eval_sealed.py", "src/vf/rank.py", E.TERRAIN_PATH,
              E.SEA_PIXEL_PATH):
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
    assert set(hs) == {"scripts/eval_sealed.py", "reports/eval_sealed_protocol.md", "src/vf/rank.py", E.TERRAIN_PATH,
                       E.SEA_PIXEL_PATH}
    (tmp_path / E.TERRAIN_PATH).write_text("changed\n")  # the terrain factors are guarded too
    assert any("terrain" in l for l in E.git_guard(tmp_path)["porcelain"])


def test_sealed_judge_settings_are_the_protocols(tmp_path, monkeypatch):
    p, _ = _protocol(tmp_path)
    E.check_judges(E.PROTOCOL_JUDGES, p)
    with pytest.raises(SystemExit):
        E.check_judges(E.judge_specs({"VF_LLM_MODEL": "claude-sonnet-5-5"}), p)
    with pytest.raises(SystemExit):
        E.check_judges(E.judge_specs({}, "single"), p)
    assert E.judge_specs({}) == E.PROTOCOL_JUDGES
    monkeypatch.setattr(E, "protocol_path", lambda: p)
    info = {"protocol": {"sha256": "s"}, "code_sha256": {"scripts/eval_sealed.py": "1"},
            "reach_amendment": {"seal": "r", "active": True}}
    doc = {"protocol": {"sha256": "s"}, "code_sha256": {"scripts/eval_sealed.py": "1"},
           "reach": {"rule": "window", "amendment": {"seal": "r"}}}
    ok = SimpleNamespace(walk_cap=E.WALK_CAP, judges="mixed")
    E.check_sealed_judge_settings(ok, doc, info, E.PROTOCOL_JUDGES)
    for bad in ({**doc, "reach": {"rule": "radius"}}, {k: v for k, v in doc.items() if k != "reach"},
                {**doc, "reach": {"rule": "window", "amendment": {"seal": "other"}}}):
        with pytest.raises(SystemExit, match="reach amendment"):
            E.check_sealed_judge_settings(ok, bad, info, E.PROTOCOL_JUDGES)
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


# ---------------------------------------------------------------------------- reach amendment (D048 windows)

KM_PER_DEG = 111.195  # haversine degree of latitude on vf.geo's sphere, close enough for these tests


def test_drive_minutes_formula_monotone_and_window_km_inverse():
    assert abs(float(E.drive_minutes(10.0, 1.0)) - 2.30 * 10 ** 0.842) < 1e-9
    assert abs(float(E.drive_minutes(10.0, 2.0)) - 2 * 2.30 * 10 ** 0.842) < 1e-9
    km = np.array([0.0, 1.0, 5.0, 50.0, 200.0, np.nan])
    m = E.drive_minutes(km, 1.11)
    assert np.all(np.diff(m[:5]) > 0) and np.isnan(m[5])  # minutes rise strictly with km for one origin
    for w in E.WINDOW_MIN.values():
        for f in (0.8, 1.0, 3.08):
            assert abs(float(E.drive_minutes(E.window_km(w, f), f)) - w) < 1e-6
    # the draft's reading table (km at factor 0.80, 1.00, 3.08)
    assert [round(E.window_km(120, f)) for f in (0.8, 1.0, 3.08)] == [143, 110, 29]
    assert [round(E.window_km(60, f)) for f in (0.8, 1.0, 3.08)] == [63, 48, 13]
    assert E.ALLOWANCE_MIN["mri"] == 30 and E.ALLOWANCE_MIN["emoc"] == 15 and E.ALLOWANCE_MIN["oncology"] == 30


def test_terrain_factor_by_origin_state_and_blank_refuses():
    terrain = E.load_terrain()
    assert E.state_factor(terrain, "Jammu And Kashmir") == E.state_factor(terrain, "JAMMU AND KASHMIR") == 0.85
    assert E.state_factor(terrain, "ARUNACHAL PRADESH") == 3.08
    for st in ("ANDAMAN AND NICOBAR ISLANDS", "LAKSHADWEEP", "NOWHERE"):
        with pytest.raises(SystemExit, match="terrain factor"):
            E.state_factor(terrain, st)
    assert E.sha256_file(ROOT / E.TERRAIN_PATH) == E.TERRAIN_SHA256
    assert E.sha256_file(ROOT / E.SEA_PIXEL_PATH) == E.SEA_PIXEL_SHA256


def test_every_sampled_state_has_a_factor():
    if not E.points_path().exists():
        pytest.skip("points not drawn yet")
    terrain = E.load_terrain()
    for st in E.read_points()["state"].unique():
        assert E.state_factor(terrain, st) > 0


def _win_ctx():
    """Facilities due north of (20, 78) at known km. a: grade 2 at 60 km; b:
    specialty only at 30 km; c: grade 2 at 100 km; d: grade 2 at 130 km; e:
    grade 1 keyword at 105 km; f: no location."""
    km = np.array([60.0, 30.0, 100.0, 130.0, 105.0, np.nan])
    ids = ["a", "b", "c", "d", "e", "f"]
    idx = SimpleNamespace(
        id_to_pos={k: i for i, k in enumerate(ids)},
        lines={"emoc": SimpleNamespace(grade=np.array([2, 0, 2, 2, 1, 3]))},
        dup_canon={}, lat=20.0 + km / KM_PER_DEG, lon=np.full(len(km), 78.0),
        fac=pd.DataFrame({"facility_id": ids, "name": [i.upper() for i in ids]}))
    n = len(ids)
    return E.Ctx(idx=idx, rank_mod=None, silver=pd.DataFrame({"description": [None] * n}), facts_by_pos={},
                 fact_text={}, spec_mask={"emoc": np.array([False, True, False, False, False, False])},
                 kw_mask={"emoc": np.array([False, False, False, False, True, False])})


TERRAIN = {"FLAT": 1.0, "HILL": 3.08, "ISLE": None}


def _q(state="FLAT"):
    return {"query_id": f"dev-1-emoc-{state}", "po_id": 1, "split": "dev", "service": "emoc", "radius_km": 50.0,
            "lat": 20.0, "lon": 78.0, "office_name": "X", "pin": "100001", "state": state, "district": "D"}


def test_baselines_take_fewest_minutes_inside_the_window():
    ctx = _win_ctx()
    from vf.geo import haversine_km
    d = haversine_km(20.0, 78.0, ctx.idx.lat, ctx.idx.lon)
    flat = E.reach_for(_q("FLAT"), "window", TERRAIN)
    hill = E.reach_for(_q("HILL"), "window", TERRAIN)
    assert flat.window_min == 120 and flat.factor == 1.0 and abs(flat.limit_km - 109.6) < 0.2
    # radius 50 km: any_fact abstains (a is at 60 km); the window (about 110 km at factor 1) takes a
    assert E.tool_any_fact(ctx, "emoc", d, 50.0) is None
    assert E.tool_any_fact(ctx, "emoc", d, flat) == 0
    assert E.tool_specialty(ctx, "emoc", d, flat) == 1
    # a hill origin (factor 3.08, about 29 km): both abstain, the same rule for every tool
    assert E.tool_any_fact(ctx, "emoc", d, hill) is None and E.tool_specialty(ctx, "emoc", d, hill) is None
    # the judge pool under the window: signal and inside the window, nearest first; no-location f never
    assert E.judge_pool(ctx, "emoc", d, flat) == [1, 0, 2, 4]
    assert E.judge_pool(ctx, "emoc", d, 50.0) == [1]
    with pytest.raises(SystemExit):
        E.reach_for(_q("ISLE"), "window", TERRAIN)


class _FakeRank:
    """rank() returns cards for d (130 km, outside the window) then a then c,
    each reporting its own minutes and window like the frozen ranker should."""

    def __init__(self, minutes_off=0.0, window=120, report=True, status="ok"):
        self.minutes_off, self.window, self.report, self.status = minutes_off, window, report, status

    def rank(self, idx, line, res, k=3):
        cards = []
        for fid, km in (("d", 130.0), ("a", 60.0), ("c", 100.0)):
            c = {"facility_id": fid, "evidence": []}
            if self.report:
                c.update(minutes=float(E.drive_minutes(km, 1.0)) + self.minutes_off, window_min=self.window)
            cards.append(c)
        out = {"status": self.status, "results": cards if self.status == "ok" else [], "unverified_nearby": [],
               "how_we_decided": {"score_status": "v1"}}
        return SimpleNamespace(to_dict=lambda: out)


def test_ranker_pick_is_first_card_inside_the_window_and_cards_are_checked():
    ctx = _win_ctx()
    ctx.rank_mod = _FakeRank()
    o = E.run_tools(ctx, _q("FLAT"), "window", TERRAIN)
    rk = o["tools"]["ranker"]
    assert rk["pick"]["facility_id"] == "a"  # d is the first card but outside the window
    assert abs(rk["pick"]["minutes"] - float(E.drive_minutes(60.0, 1.0))) < 0.01
    assert [c["inside"] for c in rk["cards_reach"]] == [False, True, True]
    assert o["tools"]["any_fact"]["pick"]["facility_id"] == "a" and "minutes" in o["tools"]["any_fact"]["pick"]
    assert o["reach"]["rule"] == "window" and o["reach"]["window_min"] == 120.0
    # every tool answered: the walk stops at the farthest pick (a, 60 km); pool minutes run parallel
    assert o["d_stop_km"] == pytest.approx(60.0, abs=0.05)
    assert len(o["pool_minutes"]) == len(o["pool"]) and all(len(e) == 3 for e in o["pool"])
    cc = E.ranker_card_consistency([o])
    assert cc["ok"] and cc["n_cards"] == 3 and cc["max_abs_diff_min"] < 0.01
    # the card check fails on minutes off by more than 0.5, a wrong window, or no minutes at all
    for fake in (_FakeRank(minutes_off=0.6), _FakeRank(window=90), _FakeRank(report=False)):
        ctx.rank_mod = fake
        assert not E.ranker_card_consistency([E.run_tools(ctx, _q("FLAT"), "window", TERRAIN)])["ok"]
    # no reliable option: the ranker abstains and the walk stops at the window km
    ctx.rank_mod = _FakeRank(status="no_reliable_option")
    o2 = E.run_tools(ctx, _q("FLAT"), "window", TERRAIN)
    assert o2["tools"]["ranker"]["pick"] is None
    assert o2["d_stop_km"] == pytest.approx(E.window_km(120, 1.0), abs=1e-3)
    assert [e[0] for e in o2["pool"]] == ["b", "a", "c", "e"]
    # the radius rule is unchanged: no reach block, no minutes
    ctx.rank_mod = _FakeRank()
    o3 = E.run_tools(ctx, _q("FLAT"), "radius")
    assert "reach" not in o3 and "pool_minutes" not in o3 and o3["tools"]["any_fact"]["pick"] is None
    assert o3["tools"]["ranker"]["pick"] is None and "cards_reach" not in o3["tools"]["ranker"]


def _win_query(pick_km, ny_km=None):
    reach = {"rule": "window", "radius_km": 50.0, "window_min": 120.0, "factor": 1.0,
             "window_km": E.window_km(120, 1.0), "allowance_min": 15.0}
    pk = lambda fid, km: {"facility_id": fid, "pos": 0, "name": fid, "distance_km": km, "dup_group": fid,  # noqa
                          "minutes": round(float(E.drive_minutes(km, 1.0)), 4)}
    return {"query_id": "q", "po_id": 7, "service": "emoc", "radius_km": 50.0, "state": "FLAT", "reach": reach,
            "tools": {"specialty": {"pick": None}, "any_fact": {"pick": None},
                      "ranker": {"pick": pk("p", pick_km) if pick_km else None, "quote_check": {}}},
            "pool": [], "pool_size": 0, "major_flags": {}}


def test_outcomes_under_the_window_inside_and_allowance_split():
    # skipped a facility at 50 km for a pick at 60 km: about 10 minutes further, inside the 15-minute allowance
    q = _win_query(60.0)
    jq = {"walk_status": "found", "q1": {"p": True}, "q2": {"p": True},
          "nearest_yes": {"facility_id": "n", "distance_km": 50.0, "dup_group": "n"}}
    rows = {r["tool"]: r for r in E.outcome_rows(q, jq)}
    rk = rows["ranker"]
    assert rk["skipped_nearer"] and rk["skip_within_allowance"] and not rk["skip_beyond_allowance"]
    assert 9 < rk["skip_extra_min"] < 12 and rk["pick_minutes"] > rk["nearest_yes_minutes"]
    # a facility at 20 km: about 44 minutes nearer, beyond the allowance
    jq2 = {**jq, "nearest_yes": {"facility_id": "n", "distance_km": 20.0, "dup_group": "n"}}
    rk2 = {r["tool"]: r for r in E.outcome_rows(q, jq2)}["ranker"]
    assert rk2["skipped_nearer"] and rk2["skip_beyond_allowance"] and not rk2["skip_within_allowance"]
    # abstentions: supported option inside the window (100 km at factor 1) counts; at 115 km it does not
    assert rows["any_fact"]["abstained_with_support"] is True
    q3 = _win_query(None)
    out = {r["tool"]: r for r in E.outcome_rows(q3, {**jq, "nearest_yes": {"facility_id": "n", "distance_km": 100.0,
                                                                           "dup_group": "n"}})}
    assert out["ranker"]["abstained_with_support"] is True
    out = {r["tool"]: r for r in E.outcome_rows(q3, {**jq, "nearest_yes": {"facility_id": "n", "distance_km": 115.0,
                                                                           "dup_group": "n"}})}
    assert out["ranker"]["abstained_with_support"] is False  # 115 km > 50 km radius too, but here it is the window
    # summary carries the split and the markdown names both counts
    res = E.summarize(pd.DataFrame(E.outcome_rows(q, jq) + E.outcome_rows(
        {**q, "query_id": "q2"}, jq2)))
    a = res["tools"]["ranker"]["skipped_nearer_allowance"]
    assert a["within_allowance"]["k"] == 1 and a["beyond_allowance"]["k"] == 1 and res["reach_rule"] == "window"
    md = E.render_results_md("dev", res, {"judges": {"A": "m"}, "reach_amendment_sealed": False})
    assert "drive-time window" in md and "allowance" in md and "Preview" in md and chr(0x2014) not in md
    # radius picks keep the old outcome and report no split
    res_r = E.summarize(pd.DataFrame(E.outcome_rows(_query(), {"walk_status": "found", "q1": {}, "q2": {},
                                                               "nearest_yes": None})))
    assert "skipped_nearer_allowance" not in res_r["tools"]["ranker"] and res_r["reach_rule"] == "radius"


def test_sea_pixel_sensitivity_keeps_and_reports_without():
    q = _win_query(60.0)
    jq = {"walk_status": "found", "q1": {"p": True}, "q2": {"p": True}, "nearest_yes": None}
    qa, qb = {**q, "query_id": "a", "po_id": 1}, {**q, "query_id": "b", "po_id": 2}
    df = pd.DataFrame(E.outcome_rows(qa, jq) + E.outcome_rows(qb, jq))
    sp = E.sea_pixel_sensitivity(df, [qa, qb], {2, 999})
    assert sp["po_ids"] == [2] and sp["n_queries_dropped"] == 1 and sp["n_queries_scored"] == 1
    assert sp["tools"]["ranker"]["coverage"]["n"] == 1 and sp["coverage_all_queries"]["ranker"]["n"] == 1
    md = E.render_sea_pixel_md(sp)
    assert "stay in the sample" in md and "po_id 2" in md and chr(0x2014) not in md
    # the real list holds the two dev points the draft names
    sea = E.load_sea_pixel_ids()
    assert len(sea) == 1032 and {142781, 13304} <= sea


def test_draft_amendment_activates_only_once_sealed(tmp_path):
    p, _ = _protocol(tmp_path)
    E.seal_protocol(p)
    st = E.reach_amendment_status(p)
    assert not st["present"] and not st["active"]
    assert E.resolve_reach_rule("dev", status=st)[0] == "radius"
    with pytest.raises(SystemExit, match="not in the protocol"):
        E.resolve_reach_rule("sealed", status=st)
    p.write_text(p.read_text() + "\n" + _draft_amendment(3))
    st = E.reach_amendment_status(p)
    assert st["present"] and not st["sealed"] and not st["active"] and st["number"] == 3 and not st["problems"]
    assert E.resolve_reach_rule("dev", status=st)[0] == "radius"
    assert E.resolve_reach_rule("dev", "window", status=st)[0] == "window"  # a dev preview
    with pytest.raises(SystemExit, match="not sealed"):
        E.resolve_reach_rule("sealed", status=st)
    h = E.seal_protocol(p)
    st = E.reach_amendment_status(p)
    assert st["active"] and st["seal"] == h and st["windows_in_text"] == {k: float(v) for k, v in E.WINDOW_MIN.items()}
    assert E.resolve_reach_rule("dev", status=st)[0] == "window"
    assert E.resolve_reach_rule("sealed", status=st)[0] == "window"
    with pytest.raises(SystemExit, match="cannot change"):
        E.resolve_reach_rule("sealed", "radius", status=st)
    assert E.resolve_reach_rule("dev", "radius", status=st)[0] == "radius"  # dev comparison only


def test_amendment_that_differs_from_the_harness_does_not_activate(tmp_path):
    for edit, why in ((lambda t: t.replace("| `emoc` | 120 |", "| `emoc` | 100 |"), "windows"),
                      (lambda t: t.replace("2.30 x km^0.842", "2.40 x km^0.842"), "formula"),
                      (lambda t: t.replace("at most 15 minutes further", "at most 20 minutes further"), "allowance")):
        d = tmp_path / why
        d.mkdir()
        p, _ = _protocol(d)
        E.seal_protocol(p)
        _add_reach(p, edit=edit)
        st = E.reach_amendment_status(p)
        assert st["sealed"] and not st["active"] and any(why in x for x in st["problems"]), (why, st)
        with pytest.raises(SystemExit, match="does not match"):
            E.resolve_reach_rule("sealed", status=st)


def test_sealed_half_refuses_until_the_reach_amendment_is_sealed(tmp_path, monkeypatch):
    p, pts = _protocol(tmp_path)
    h = E.seal_protocol(p)
    monkeypatch.setattr(E, "read_points", lambda: pts)
    _clean_git(monkeypatch)
    for stage in ("picks", "judge", "score", "human"):
        with pytest.raises(SystemExit, match="reach amendment"):
            E.check_sealed_inputs("sealed", True, stage=stage, root=tmp_path, protocol=p, expected=(h,))
    E.check_sealed_inputs("dev", False, root=tmp_path, protocol=p, expected=(h,))  # dev is not blocked
    # appended but not sealed: the protocol hash check refuses first
    p.write_text(p.read_text() + "\n" + _draft_amendment(3))
    with pytest.raises(SystemExit, match="protocol hash check failed"):
        E.check_sealed_inputs("sealed", True, root=tmp_path, protocol=p, expected=(h,))
    hr = E.seal_protocol(p)
    # sealed but its seal not pinned in the harness: the chain check refuses
    with pytest.raises(SystemExit, match="seal chain"):
        E.check_sealed_inputs("sealed", True, root=tmp_path, protocol=p, expected=(h,))
    info = E.check_sealed_inputs("sealed", True, root=tmp_path, protocol=p, expected=(h, hr))
    assert info["reach_amendment"]["active"] and info["reach_amendment"]["seal"] == hr


def test_real_sealed_half_refuses_while_the_reach_amendment_is_unsealed():
    if not E.points_path().exists() or not E.protocol_path().exists():
        pytest.skip("no protocol")
    if E.reach_amendment_status()["sealed"]:
        pytest.skip("the reach amendment is sealed")
    with pytest.raises(SystemExit, match="reach amendment"):
        E.check_sealed_inputs("sealed", True, stage="score")
    assert E.resolve_reach_rule("dev")[0] == "radius"


def _dev_doc(seal="r", rule="window", ok=True, code=None):
    return {"reach": {"rule": rule, "amendment": {"seal": seal}, "ranker_cards": {"ok": ok, "n_cards": 5}},
            "code_sha256": code if code is not None else _code_now()}


def _code_now():
    return {"scripts/eval_sealed.py": "1", E.TERRAIN_PATH: E.TERRAIN_SHA256, E.SEA_PIXEL_PATH: E.SEA_PIXEL_SHA256}


def test_sealed_picks_need_dev_picks_under_the_sealed_rule_and_matching_ranker():
    am = {"seal": "r", "active": True}
    rw = {k: float(v) for k, v in E.WINDOW_MIN.items()}
    E.check_sealed_reach(_dev_doc(), _code_now(), am, rw)  # passes
    bad = [
        (None, _code_now(), rw, "no dev picks"),
        (_dev_doc(rule="radius"), _code_now(), rw, "radius rule"),
        (_dev_doc(seal="old"), _code_now(), rw, "not computed under the sealed"),
        (_dev_doc(ok=False), _code_now(), rw, "card check failed"),
        (_dev_doc(code={**_code_now(), "scripts/eval_sealed.py": "0"}), _code_now(), rw, "code changed"),
        (_dev_doc(), _code_now(), None, "holds no drive-time windows"),
        (_dev_doc(), _code_now(), {**rw, "cath_lab": 120.0}, "differ from the pinned"),
        (_dev_doc(code={**_code_now(), E.TERRAIN_PATH: "x"}), {**_code_now(), E.TERRAIN_PATH: "x"}, rw, "pinned"),
    ]
    for dev, code, w, msg in bad:
        with pytest.raises(SystemExit, match=msg):
            E.check_sealed_reach(dev, code, am, w)


def test_ranker_windows_discovery():
    w = {k: v for k, v in E.WINDOW_MIN.items()}
    assert E.ranker_windows(SimpleNamespace(WINDOWS_MIN=w)) == {k: float(v) for k, v in w.items()}
    assert E.ranker_windows(SimpleNamespace(WINDOWS={k: (v, 0.3) for k, v in w.items()}))["emoc"] == 120.0
    svcs = {k: SimpleNamespace(window_min=v) for k, v in w.items()}
    assert E.ranker_windows(SimpleNamespace(SERVICES=svcs))["dialysis"] == 60.0
    assert E.ranker_windows(SimpleNamespace(SERVICES={"emoc": SimpleNamespace()})) is None
    assert E.check_ranker_reach(None) and not E.check_ranker_reach({k: float(v) for k, v in w.items()})
    # vf.services.Window-like objects carry minutes and the allowance
    objs = {k: SimpleNamespace(minutes=v, allowance=E.ALLOWANCE_MIN[k]) for k, v in w.items()}
    mod = SimpleNamespace(WINDOWS=objs)
    assert E.ranker_windows(mod)["mri"] == 180.0 and E.ranker_allowances(mod)["mri"] == 30.0
    assert not E.check_ranker_reach(E.ranker_windows(mod), E.ranker_allowances(mod))
    bad = {**E.ranker_allowances(mod), "dialysis": 30.0}
    assert "allowances" in E.check_ranker_reach(E.ranker_windows(mod), bad)[0]


def test_ranker_gets_the_origin_state_under_the_window_rule_only():
    seen = []

    class Rec(_FakeRank):
        def rank(self, idx, line, res, k=3):
            seen.append(dict(res))
            return super().rank(idx, line, res, k)

    ctx = _win_ctx()
    ctx.rank_mod = Rec()
    E.run_tools(ctx, _q("FLAT"), "window", TERRAIN)
    E.run_tools(ctx, _q("FLAT"), "radius")
    assert seen[0]["state"] == "FLAT" and "state" not in seen[1]
    assert seen[0]["precision_km"] == 2.0 and seen[0]["lat"] == 20.0


def test_card_minutes_and_window_field_names():
    assert E.card_minutes({"minutes": 41.2}) == 41.2
    assert E.card_minutes({"drive": {"minutes": 12}}) == 12.0
    assert E.card_minutes({"parts": {"drive_minutes": 7.5}}) == 7.5
    assert E.card_minutes({"distance_km": 10, "score": 0.4}) is None  # a v0 card reports none
    assert E.card_window({"window_min": 90}) == 90.0
    assert E.card_window({"window": {"minutes": 120}}) == 120.0
    assert E.card_window({}, {"window_min": 60}) == 60.0  # the answer's window
    assert E.card_window({}, {}) is None


def test_dev_preview_never_replaces_the_dev_picks():
    inactive, active = {"active": False}, {"active": True}
    a = SimpleNamespace(reach="window")
    assert E.preview_tag(a, "dev", inactive) == E.PREVIEW_TAG
    assert E.preview_tag(a, "dev", active) == ""
    assert E.preview_tag(SimpleNamespace(reach=None), "dev", inactive) == ""
    assert E.picks_path("dev", E.PREVIEW_TAG).name == "picks_dev_window_preview.json"


def test_estimate_skips_cached_units(tmp_path):
    doc = {"queries": [_query()]}
    full = E.estimate_cost(doc, [], _specs())
    cached = {"q1": ["near_claim|emoc", "mid|emoc", "dup_of_mid|emoc", "far|emoc"],
              "q2": ["dev-1-emoc|near_claim", "dev-1-emoc|mid"]}
    none_left = E.estimate_cost(doc, [], _specs(), cached=cached)
    assert none_left["high (every walk to the cap)"]["usd"] == 0 < full["high (every walk to the cap)"]["usd"]
    # cached_units reads the judge cache: a unit judged before is found
    fn, _ = _fake_call({})
    b = E.Budget(tmp_path / "s.jsonl", 10.0, None, None)
    b.others = 0.0
    r = E.JudgeRunner(_specs(), b, call_fn=fn, cache_dir=tmp_path / "c")
    E.Units(r).judge("q1", "mid|emoc", E.SYSTEM_Q1, E.user_q1("emoc", "REC mid"), {})
    q = _query()
    q["place_label"] = "X post office"
    recs = {f: f"REC {f}" for f in ("near_claim", "mid", "dup_of_mid", "far")}
    cu = E.cached_units({"queries": [q]}, recs, _specs(), tmp_path / "c")
    assert cu["q1"] == ["mid|emoc"] and cu["q2"] == []
