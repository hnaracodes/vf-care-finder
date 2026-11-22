"""Tests for scripts/wp9_findings.py: number tokens, the check mode, optional
sources, and (when the bundle and data exist) the real build."""
import importlib.util
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
_PATH = ROOT / "scripts" / "wp9_findings.py"
_spec = importlib.util.spec_from_file_location("wp9_findings", _PATH)
wf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wf)

BUNDLE = ROOT / "dbapp" / "bundle"


def _finding(key, value, kind="count", display=None):
    return {"key": key, "group": key.split(".")[0], "value": value, "kind": kind,
            "display": display if display is not None else wf.fmt_display(value, kind),
            "source_file": "x", "how_computed": "test"}


def _tokens(line):
    return [(t["token"], t["pct"]) for t in wf.scan_line(line)]


# ---------------------------------------------------------------------------
# formatting and tokens
# ---------------------------------------------------------------------------

def test_canon_strips_commas_and_trailing_zeros():
    assert wf.canon("9,989") == "9989"
    assert wf.canon("0.90") == "0.9"
    assert wf.canon("1.0") == "1"
    assert wf.canon(432) == "432"


def test_fmt_display_by_kind():
    assert wf.fmt_display(9989, "count") == "9,989"
    assert wf.fmt_display(0.863426, "share") == "86%"
    assert wf.fmt_display(0.81277, "share", 1) == "81.3%"
    assert wf.fmt_display(3.5, "km") == "3.5 km"
    assert wf.fmt_display(0.9132, "score") == "0.91"
    assert wf.fmt_display(None, "count") is None


def test_scan_line_finds_measures():
    assert _tokens("BNK Hospital, 3.5 km straight-line, 9,989 records, 86% of them") == [
        ("3.5", False), ("9989", False), ("86", True)]
    assert _tokens("a rate of 40 percent") == [("40", True)]
    assert _tokens("within 50km, 3x the radius, the 3rd card") == [("50", False), ("3", False)]


@pytest.mark.parametrize("line", [
    "Fall 2026, list of 2026",                      # years
    "Search PIN 226001 and 422002",                 # PINs
    "0:04 to 0:11, then 22:45 ET",                  # clock and video times
    "list of 8 April 2026, due Nov 22",             # dates
    "built 2026-11-21T04:09:37Z, or 21/11/2026",    # ISO and numeric dates
    "WP11, D044, v0.2, g3, student 1b, the 3rd card",  # identifiers and ordinal suffixes
    "commit 0b52592 and 63c6cd6",                   # commit hashes
    "see /tmp/claude-501/x-4922/y and https://a.b/c/12",  # paths and URLs
    "read ./out/run_12/x, ~/d/v2 and reports/figures/fig_3.png",  # relative paths, file extension
    "page 35, row 1109, card 3, step 2",            # citations and ordinals
    "table 2 and figure 4, section 3 to 5",         # citations
    "call 108 or 102 or 112",                       # emergency numbers
    "1. First item",                                # list marker
])
def test_scan_line_allowlist(line):
    assert _tokens(line) == [], line


@pytest.mark.parametrize("line,expected", [
    ("Only 1/13 certified hospitals", ["1", "13"]),               # ratios with a slash are scanned
    ("Funnel 9,989/9,671/118/14/9/3", ["9989", "9671", "118", "14", "9", "3"]),
    ("from 99.9%/12.3% nationally", ["99.9", "12.3"]),
    ("Option 2.5 km further, card 3.9 km away", ["2.5", "3.9"]),  # ordinal words do not hide measures
    ("line 400 records missing, step 999, case 12", ["400", "999", "12"]),
    ("It took 45 minutes, 23 days later, a 45-minute drive", ["45", "23", "45"]),  # durations
    ("Rs.2000 per visit, Rs 2000, INR 1900, 2000 rupees", ["2000", "2000", "1900", "2000"]),  # money
    ("250mg, 45min, 10k", ["250", "45", "10"]),                   # glued units
    ("row 12.5 km and page 3%", ["12.5", "3"]),                   # citations need an integer with no unit
])
def test_scan_line_scans_measures_the_allowlist_used_to_hide(line, expected):
    assert [t["token"] for t in wf.scan_line(line)] == expected, line


def test_scan_line_units_and_ratio_runs():
    ts = wf.scan_line("73 km, 12 ms, 86 percent, 356 of 414 points and 9/3/1, then 5")
    assert [(t["token"], t["unit"]) for t in ts] == [("73", "km"), ("12", "ms"), ("86", "%"), ("356", None),
                                                    ("414", None), ("9", None), ("3", None), ("1", None),
                                                    ("5", None)]
    r = [t["ratio"] for t in ts]
    assert r[:3] == [None] * 3 and r[3] == r[4] is not None and r[5] == r[6] == r[7] is not None
    assert r[3] != r[5] and r[8] is None


def test_accepted_tokens_rounding_and_shares():
    share = wf.accepted_tokens(_finding("a.share", 0.863426, "share"))
    assert ("86", "%") in share and ("86.3", "%") in share and ("0.86", None) in share
    assert ("86", None) not in share
    km = wf.accepted_tokens(_finding("a.km", 72.6, "km"))
    assert ("72.6", "km") in km and ("73", "km") in km and ("73", None) not in km
    small = wf.accepted_tokens(_finding("a.km", 0.951519, "km", display="0.95 km"))
    assert ("0.95", "km") in small and ("1", "km") in small  # half-up: 0.95 -> 1.0
    assert ("0", "km") not in small
    assert wf.accepted_tokens(_finding("a.count", 9989)) >= {("9989", None)}
    ci = wf.accepted_tokens(_finding("t.f1", 0.9132, "score", display="0.91 (0.90 to 0.93)"))
    assert {("0.91", None), ("0.9", None), ("0.93", None)} <= ci


def test_null_findings_allow_nothing():
    f = _finding("x.y", None)
    f["display"] = None
    assert wf.accepted_tokens(f) == set()


# ---------------------------------------------------------------------------
# check mode
# ---------------------------------------------------------------------------

FND = {"findings": [
    _finding("facilities.records", 9989),
    _finding("emoc.share", 0.863426, "share"),
    _finding("query.x.card1.distance_km", 3.5, "km"),
    _finding("tagger.f1", 0.9132, "score", display="0.91 (0.90 to 0.93)"),
    {**_finding("sealed_eval.results", None), "display": None, "reason": "not run"},
]}


def test_check_text_passes_known_and_lists_unknown():
    lines = [("line 1", "9,989 records; 86% have no blood fact; card 1 is 3.5 km away."),
             ("line 2", "Macro F1 0.91 (0.90 to 0.93) on PIN 226001 in 2026."),
             ("line 3", "It beats 12 baselines by 7.4 points."),
             ("line 4", "The record says 'over 400 cases a month' findings:skip")]
    r = wf.check_text(lines, FND)
    assert [u["number"] for u in r["unknown"]] == ["12", "7.4"]
    assert all(u["where"] == "line 3" for u in r["unknown"])
    assert wf.check_text(lines, FND, allow=["12", "7.4"])["unknown"] == []


def test_check_percent_needs_a_share():
    r = wf.check_text([("l", "9989% of records")], FND)
    assert [u["number"] for u in r["unknown"]] == ["9989%"]


def test_check_unit_must_match_the_finding_kind():
    fnd = {"findings": [_finding("q.card1.distance_km", 3.5, "km"), _finding("q.results", 9),
                        _finding("t.ms", 12.0, "ms")]}
    assert wf.check_text([("l", "3.5 km away, 3.5 of them, 9 cards, 12 ms")], fnd)["unknown"] == []
    r = wf.check_text([("l", "the radius is 9 km, 3.5% of them, 12 km")], fnd)
    assert [u["number"] for u in r["unknown"]] == ["9 km", "3.5%", "12 km"]


RATIO_FND = {"findings": [
    _finding("facilities.records", 9989), _finding("facilities.usable_locations", 9671),
    _finding("emoc.records_naming_csection", 432), _finding("emoc.csection_records_without_blood", 373),
    _finding("trust.review.unsure", 1), _finding("query.a.gate.recommendable.n_blocked", 13),
    _finding("query.c.gate.usable_location.n", 9671), _finding("query.c.gate.usable_location.of", 9989),
    _finding("query.c.gate.within_reach.n", 118), _finding("query.c.gate.recommendable.n", 14),
    _finding("query.c.gate.within_radius.n", 9), _finding("query.c.results", 3),
    _finding("query.c.card1.n_quotes", 40), _finding("query.b.card2.n_quotes", 78),
]}


@pytest.mark.parametrize("line", [
    "373 of the 432 records name a C-section",       # one parent key
    "432 of 9,989 records",                          # a whole-dataset denominator pairs with anything
    "Funnel 9,989/9,671/118/14/9/3",                 # one query's gate funnel and its results
    "118 out of 9,989",
])
def test_check_ratio_from_one_group_passes(line):
    assert wf.check_text([("l", line)], RATIO_FND)["unknown"] == [], line


@pytest.mark.parametrize("line,number", [
    ("Only 1 of 13 current certificates names a C-section", "1 of 13"),  # two unrelated findings
    ("Only 1/13 certified hospitals", "1/13"),
    ("40 of 78 links were correct", "40 of 78"),
])
def test_check_ratio_borrowing_unrelated_findings_fails(line, number):
    r = wf.check_text([("l", line)], RATIO_FND)
    assert [u["number"] for u in r["unknown"]] == [number] and r["unknown"][0]["reason"].startswith("ratio")
    assert wf.check_text([("l", line)], RATIO_FND, allow=["13", "78"])["unknown"] == []


def test_check_placeholders_strict_and_render():
    fnd = {"findings": FND["findings"] + [_finding("laqshya.x", 1)]}
    ok = "{{facilities.records}} records; {{laqshya.x}} of them"
    assert wf.check_text([("l", ok)], fnd, strict=True) == {"checked": 2, "found": 2, "unknown": []}
    r = wf.check_text([("l", "{{sealed_eval.results}} and {{no.such}}, 9,989 records")], fnd, strict=True)
    assert [(u["number"], u["reason"]) for u in r["unknown"]] == [
        ("{{sealed_eval.results}}", "finding is null"), ("{{no.such}}", "no such finding"),
        ("9,989", "strict: bare number; name its finding with {{key}}")]
    assert wf.check_text([("l", "9,989 records")], fnd)["unknown"] == []  # not strict: the token guard
    assert wf.check_text([("l", "9,989 records")], fnd, strict=True, allow=["9989"])["unknown"] == []
    assert wf.render(ok, fnd) == "9,989 records; 1 of them"
    with pytest.raises(KeyError):
        wf.render("{{sealed_eval.results}}", fnd)


def test_check_file_json_scans_strings_only(tmp_path):
    story = {"beats": [{"t": 4.5, "caption": "9,989 records, 3.5 km"}, {"t": 11, "caption": "42 clinics"}]}
    p = tmp_path / "story.json"
    p.write_text(json.dumps(story))
    r = wf.check_file(p, FND)
    assert [u["number"] for u in r["unknown"]] == ["42"]
    assert r["unknown"][0]["where"] == "$.beats[1].caption"


def test_cli_check_exit_codes(tmp_path, capsys):
    fp = tmp_path / "findings.json"
    fp.write_text(json.dumps(FND))
    good, bad = tmp_path / "good.md", tmp_path / "bad.md"
    good.write_text("We read 9,989 records.\n")
    bad.write_text("We read 9,989 records and 31,337 web pages.\n")
    assert wf.main(["--findings", str(fp), "--check", str(good)]) == 0
    assert wf.main(["--findings", str(fp), "--check", str(good), str(bad)]) == 1
    out = capsys.readouterr().out
    assert "NOT IN FINDINGS '31,337'" in out and "bad.md" in out and "token guard" in out
    assert wf.main(["--findings", str(fp), "--check", str(good), "--strict"]) == 1
    tpl = tmp_path / "tpl.md"
    tpl.write_text("We read {{facilities.records}} records.\n")
    assert wf.main(["--findings", str(fp), "--check", str(tpl), "--strict"]) == 0
    assert wf.main(["--findings", str(tmp_path / "missing.json"), "--check", str(good)]) == 2


# ---------------------------------------------------------------------------
# collector, optional sources, writing
# ---------------------------------------------------------------------------

def test_findings_collector(tmp_path):
    F = wf.Findings(tmp_path)
    src = tmp_path / "a.csv"
    src.write_text("x\n")
    F.add("g.n", np.int64(5), source=src, kind="count", how="h")
    F.add("g.s", np.float64(0.25), source=src, kind="share", how="h")
    F.null("g.z", source=src, reason="missing")
    with pytest.raises(ValueError):
        F.add("g.n", 6, source=src, how="h")
    a, b, z = F.items
    assert a["value"] == 5 and type(a["value"]) is int and a["display"] == "5"
    assert b["display"] == "25%" and type(b["value"]) is float
    assert z["value"] is None and z["display"] is None and z["reason"] == "missing"
    assert set(a) >= {"key", "value", "display", "source_file", "how_computed"}


def test_flatten_section(tmp_path):
    p = tmp_path / "eval.json"
    p.write_text(json.dumps({"precision": 0.93, "n": 120, "ok": True, "name": "x",
                             "coverage": {"ours": 0.4}, "rows": list(range(30)), "pair": [1, 2]}))
    F = wf.Findings(tmp_path)
    assert wf.flatten_section(F, "sealed_eval", p) == 5
    d = {f["key"]: f for f in F.items}
    assert d["sealed_eval.precision"]["kind"] == "share" and d["sealed_eval.precision"]["display"] == "93%"
    assert d["sealed_eval.n"]["kind"] == "count"
    assert "sealed_eval.ok" not in d and "sealed_eval.rows.0" not in d  # bools and long lists skipped
    assert d["sealed_eval.pair.1"]["value"] == 2


def test_missing_optional_sources_are_explicit_nulls(tmp_path, monkeypatch):
    monkeypatch.setattr(wf, "ROOT", tmp_path)  # no report or label files exist here
    F = wf.Findings(tmp_path)
    wf.laqshya_section(F, tmp_path, pd.DataFrame(), tmp_path, None)
    wf.sealed_section(F, tmp_path, tmp_path, None)
    wf.v0v1_section(F, tmp_path)
    wf.recipe_v03_section(F, tmp_path)
    nulls = [f["key"] for f in F.items if f["value"] is None]
    assert nulls == ["laqshya.link_candidates", "sealed_eval.sealed.queries", "sealed_eval.dev.queries",
                     "sealed_eval.sealed.ranker.not_sensible_reasons_merge",
                     "sealed_eval.dev_rubric_check.listing_only.k", "v0v1.r1.prefer.v1", "v0v1.r2.prefer.v1",
                     "recipe_v03.changed_lines"]
    assert all(f["reason"] for f in F.items)


def test_laqshya_from_reviewed_links(tmp_path):
    links = pd.DataFrame({"facility_uid": ["u1", "u2", "u3", "u4", "u5"], "facility_id": ["f1", "f2", "f3", "f4", "f4"],
                          "review": ["correct", "correct", "wrong", "correct", "unsure"],
                          "has_current_maternity_ot": [True, False, True, True, True],
                          "caesarean_named": [True, False, False, False, False],
                          "emoc_grade": [2, 1, 0, 1, 1]})
    lp = tmp_path / "laqshya_links.parquet"
    links.to_parquet(lp)
    F = wf.Findings(tmp_path)
    wf.laqshya_section(F, tmp_path, pd.DataFrame(), tmp_path, None)
    d = {f["key"]: f["value"] for f in F.items}
    assert d["laqshya.link_candidates"] == 5
    assert d["laqshya.links_correct"] == 3 and d["laqshya.links_wrong"] == 1 and d["laqshya.links_unsure"] == 1
    assert d["laqshya.matcher_precision.k"] == 3 and d["laqshya.matcher_precision.n"] == 4  # unsure left out
    assert d["laqshya.records_with_correct_link"] == 3
    assert d["laqshya.records_current_maternity_ot"] == 2  # f1 and f4; u3 is wrong
    assert d["laqshya.records_current_maternity_ot_naming_caesarean"] == 1


def test_link_rule_bounds_and_agreement_helpers():
    assert round(wf._cp_lower(79, 81), 3) == 0.924  # AERB auto band passes the 0.90 rule
    assert round(wf._cp_lower(14, 14), 3) == 0.807  # MoHFW: clean but too few pairs
    assert wf._cp_lower(0, 5) == 0.0 and wf._cp_lower(1, 0) is None
    assert wf._cohen_kappa([True, False, True, False], [True, False, True, False]) == 1.0
    assert wf._sign_test(6, 1) == 0.125 and wf._sign_test(0, 0) is None
    lo, hi = wf._wilson(26, 30)
    assert round(lo, 2) == 0.70 and round(hi, 2) == 0.95


def test_v0_mark_noise_ignores_the_abstention_label_convention(tmp_path):
    rows = []
    for rnd, marks in (("v0v1_blind", ["right", "wrong", "abstention_right", ""]),
                       ("v0v1_blind_r2", ["right", "defensible", "right", "right"])):
        d = tmp_path / "labels" / rnd
        d.mkdir(parents=True)
        js = pd.DataFrame({"scenario_id": ["a", "b", "c", "d"] * 2, "answer_label": ["X"] * 8,
                           "version": ["v0"] * 4 + ["v1"] * 4, "mark": marks + ["right"] * 4, "note": "",
                           "prefer_label": "X", "prefer_version": ["v1", "tie", "v0", "v1"] * 2, "prefer_why": ""})
        js.to_csv(d / "judgments.csv", index=False)
    F = wf.Findings(tmp_path)
    wf.v0v1_section(F, tmp_path)
    d = {f["key"]: f["value"] for f in F.items}
    # a: same; b: changed; c: convention only, left out; d: unmarked in round 1, left out
    assert d["v0v1.judge_noise.v0_marks_comparable"] == 2 and d["v0v1.judge_noise.v0_marks_changed"] == 1
    assert d["v0v1.r2.prefer.v1"] == 2 and d["v0v1.r2.prefer.v0"] == 1 and d["v0v1.r2.prefer.tie"] == 1


def test_typicality_tie_rule():
    low = np.array([True, True, False, True, False])
    tied = [[0], [1, 2], [3, 0], [4], [2, 3]]  # points: low, mixed tie, all-low tie, high, mixed tie
    c = wf.typicality_counts(tied, low)
    assert c["points_all"] == 2 and c["records_all"] == {0, 3}  # every tied record low: points 0 and 2
    assert c["points_any"] == 4 and c["records_any"] == {0, 1, 3}  # upper bound
    assert c["points_tied"] == 3


def test_public_view_keeps_no_names_or_paths():
    obj = {"meta": {"version": "v"}, "findings": [
        {**_finding("cold_open.foil.row1.facts", 141), "how_computed": "row 1, Some Clinic", "source_file": "/x"}]}
    pv = wf.public_view(obj)
    assert pv["findings"] == {"cold_open.foil.row1.facts": {"value": 141, "display": "141", "kind": "count"}}
    assert "Some Clinic" not in json.dumps(pv) and "/x" not in json.dumps(pv)


def test_write_atomic_with_sidecar(tmp_path):
    obj = {"meta": {"script": "s", "version": "v", "n_null": 0, "sources": {}, "code_uncommitted": False,
                    "script_sha256": "ab" * 32}, "findings": FND["findings"][:2]}
    out = tmp_path / "sub" / "findings.json"
    wf.write_atomic(obj, out)
    assert json.loads(out.read_text())["findings"][0]["key"] == "facilities.records"
    meta = json.loads((out.parent / "findings.json.meta.json").read_text())
    assert meta["rows"] == 2 and len(meta["sha256"]) == 64
    assert meta["code_uncommitted"] is False and meta["script_sha256"] == "ab" * 32  # not hard-coded
    assert sorted(p.name for p in out.parent.iterdir()) == ["findings.json", "findings.json.meta.json"]


# ---------------------------------------------------------------------------
# the real build (skips without the bundle and the data folder)
# ---------------------------------------------------------------------------

def _have_data():
    try:
        from vf import paths
        return (BUNDLE / "facilities.parquet").exists() and (paths.data_dir() / "silver").exists()
    except Exception:
        return False


needs_data = pytest.mark.skipif(not _have_data(), reason="needs dbapp/bundle and the shared data folder")


@pytest.fixture(scope="module")
def built():
    return wf.build(BUNDLE)


@needs_data
def test_build_every_finding_is_complete(built):
    items = built["findings"]
    keys = [f["key"] for f in items]
    assert len(keys) == len(set(keys))
    assert built["meta"]["n_findings"] == len(items)
    for f in items:
        assert {"key", "value", "display", "source_file", "how_computed"} <= set(f), f["key"]
        if f["value"] is None:
            assert f.get("reason"), f["key"]
        else:
            assert isinstance(f["value"], (int, float)) and not isinstance(f["value"], bool), f["key"]
            assert f["display"] and f["how_computed"] and f["source_file"], f["key"]
            p = Path(f["source_file"])
            assert p.is_absolute() or (ROOT / p).exists() or (ROOT.parent / p).exists(), f["source_file"]


@needs_data
def test_build_counts_agree_with_the_bundle(built):
    d = {f["key"]: f["value"] for f in built["findings"]}
    fac = pd.read_parquet(BUNDLE / "facilities.parquet")
    assert d["facilities.records"] == len(fac)
    assert d["facilities.usable_locations"] + d["facilities.no_usable_location"] == len(fac)
    from vf import services as S
    for line in S.SERVICE_LINES:
        assert sum(d[f"grades.{line}.grade_{k}"] for k in range(4)) == len(fac), line
        assert d[f"grades.{line}.grade_2plus"] == d[f"grades.{line}.grade_2"] + d[f"grades.{line}.grade_3"]
        assert d[f"grades.{line}.recommendable"] <= d[f"grades.{line}.grade_2plus"]
    assert d["emoc.specialty_label_records_grade_2plus"] <= d["emoc.specialty_label_records"]
    assert d["emoc.grade_2plus"] <= d["emoc.records_naming_csection"]  # grade 2 needs a caesarean fact
    assert d["trust.blocked_pairs"] <= d["grades.pairs_grade_2plus"]
    lo, f1, hi = (d[f"tagger.shipped.macro_f1{s}"] for s in ("_ci_low", "", "_ci_high"))
    assert lo <= f1 <= hi
    assert d["resolver.hits"] <= d["resolver.cases"]
    assert not built["meta"]["warnings"], built["meta"]["warnings"]


@needs_data
def test_build_query_funnels_narrow(built):
    d = {f["key"]: f["value"] for f in built["findings"]}
    qids = {k.split(".")[1] for k in d if k.startswith("query.")}
    assert {"brief_csection_lucknow", "brief_dialysis_jaipur", "cold_open_226001"} <= qids
    for q in qids:
        g = f"query.{q}.gate"
        if f"{g}.within_reach.n" not in d:
            continue
        assert d[f"{g}.usable_location.n"] >= d[f"{g}.within_reach.n"] >= d[f"{g}.recommendable.n"] \
            >= d[f"{g}.within_radius.n"], q
        assert d[f"query.{q}.results"] <= 3


@needs_data
def test_build_provenance(built):
    m = built["meta"]
    assert m["code_uncommitted"] in (True, False)
    assert m["script_sha256"] == wf._sha256(_PATH)


@needs_data
def test_build_typicality_bounds(built):
    d = {f["key"]: f["value"] for f in built["findings"]}
    t = "cold_open.typicality"
    assert d[f"{t}.points_nearest_listing_no_caesarean"] <= d[f"{t}.points_upper_bound"] <= d[f"{t}.points_within_50km"]
    assert d[f"{t}.distinct_records"] <= d[f"{t}.distinct_records_upper_bound"]
    assert d[f"{t}.points_upper_bound"] - d[f"{t}.points_nearest_listing_no_caesarean"] <= d[f"{t}.points_with_tied_nearest"]


@needs_data
def test_build_names_left_out_of_negative_rows(built):
    from vf import io
    raw = io.read_facilities_raw().set_index("unique_id")["name"]
    for f in built["findings"]:
        if re.match(r"cold_open\.foil\.row\d\.distance_km$|query\.[^.]+\.unverified\d+\.", f["key"]):
            m = re.search(r"record ([0-9a-f-]{36})", f["how_computed"])
            assert m, f["key"]
            name = raw.get(m.group(1))
            assert name and name not in f["how_computed"], f["key"]


@needs_data
def test_build_distance_below_precision_is_caveated(built):
    by = {f["key"]: f for f in built["findings"]}
    for k, f in by.items():
        if not k.startswith("query.") or not k.endswith(".distance_km"):
            continue
        q = k.split(".")[1]
        prec = by.get(f"query.{q}.location_precision_km")
        if prec and f["value"] < prec["value"]:
            assert "CAVEAT" in f["how_computed"], k
            assert by[f"query.{q}.distances_below_precision"]["value"] >= 1, k
    g = by.get("query.remote_emoc_gadchiroli.beyond_radius.distance_km")
    if g is not None and g["value"] < by["query.remote_emoc_gadchiroli.location_precision_km"]["value"]:
        assert any("remote_emoc_gadchiroli: beyond_radius" in c for c in built["meta"]["caveats"])


@needs_data
def test_build_check_mode_round_trip(built):
    shown = [f["display"] for f in built["findings"] if f["value"] is not None]
    lines = [(f"line {i}", s) for i, s in enumerate(shown, 1)]
    assert wf.check_text(lines, built)["unknown"] == []
    r = wf.check_text(lines + [("last", "and 98,765.4 invented km")], built)
    assert [u["number"] for u in r["unknown"]] == ["98,765.4"]


@needs_data
def test_build_is_deterministic(built):
    again = wf.build(BUNDLE, cold_open=False, queries=False)
    a = {f["key"]: f["value"] for f in built["findings"]}
    for f in again["findings"]:
        assert a.get(f["key"]) == f["value"], f["key"]
