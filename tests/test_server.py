"""API tests for the Databricks App server (dbapp/server.py, WP8).

The server runs the real engine only (vf.rank, vf.locate, vf.services): there is no stub path.
The app state is loaded once per module, so the index loads once. Data tests skip when neither
dbapp/bundle/ nor the shared silver and gold tables are present.
"""
from __future__ import annotations

import importlib
import math
import re
import sys
import time
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

DBAPP = Path(__file__).resolve().parents[1] / "dbapp"
sys.path.insert(0, str(DBAPP))
server = importlib.import_module("server")

import vf  # noqa: E402
from vf import paths  # noqa: E402


def _have_data() -> bool:
    if server.has_bundle():
        return True
    return all((p).exists() for p in (paths.silver_dir() / "facilities.parquet",
                                      paths.gold_dir() / "capability_grades.parquet",
                                      paths.gold_dir() / "facility_flags.parquet",
                                      paths.silver_dir() / "places.parquet"))


needs_data = pytest.mark.skipif(not _have_data(), reason="no bundle and no silver/gold tables")

CARD_KEYS = {"facility_id", "name", "type", "operator", "city", "state", "lat", "lon", "distance_km",
             "location_precision_km", "location_note", "grade", "grade_label", "confidence",
             "confidence_reason", "score", "parts", "evidence", "missing", "sources", "why_here",
             "could_be_wrong", "flags"}
ANSWER_KEYS = {"status", "service", "service_label", "radius_km", "method", "location", "results",
               "beyond_radius", "unverified_nearby", "how_we_decided"}
STATUSES = {"ok", "no_reliable_option", "needs_location_choice", "location_not_found"}

# The brief's five questions, with the places the scenario suite uses (data/labels/scenarios.csv).
BRIEF = [("dialysis", "Jaipur"), ("emoc", "Lucknow"), ("dialysis", "Jokihat, Bihar"),
         ("emoc", "Nashik"), ("trauma", "Kotputli")]


@pytest.fixture(scope="module")
def client():
    mp = pytest.MonkeyPatch()
    mp.setattr(server, "STATE", server.State())
    with TestClient(server.app) as c:
        yield c
    mp.undo()


# ---------------------------------------------------------------------------------------------
# No data needed
# ---------------------------------------------------------------------------------------------

def test_to_jsonable_handles_numpy_nan_and_dataclasses():
    import dataclasses

    import numpy as np
    import pandas as pd

    @dataclasses.dataclass
    class D:
        a: float
        b: list

    out = server.to_jsonable({"x": np.float32(1.5), "y": float("nan"), "z": np.array([1, 2]),
                              "d": D(a=np.int64(3), b=[pd.NA, None]), "t": (1, 2), "i": float("inf")})
    assert out == {"x": 1.5, "y": None, "z": [1, 2], "d": {"a": 3, "b": [None, None]}, "t": [1, 2], "i": None}


def test_server_uses_the_repo_vf():
    # dbapp/ is on sys.path (for `import server`) and holds the deploy copy dbapp/vf; the tests must
    # still exercise src/vf, the tested code.
    assert Path(vf.__file__).resolve().parent == DBAPP.parent / "src" / "vf"
    assert server._VF_FROM == "repo src/vf"


def test_server_has_no_stub_engine():
    src = (DBAPP / "server.py").read_text()
    for name in ("StubGazetteer", "stub_rank", "_STUB_SERVICES", "server_stub", "VF_ENGINE"):
        assert name not in src, name


# ---------------------------------------------------------------------------------------------
# Server basics
# ---------------------------------------------------------------------------------------------

@needs_data
def test_health_reports_what_is_loaded(client):
    h = client.get("/api/health").json()
    assert h["ok"] is True
    assert h["mode"] in ("bundle", "local")
    assert h["n_facilities"] > 9000
    assert set(h["graded_lines"]) >= {"dialysis", "emoc"}
    assert h["flags_source"] != "none"
    assert h["n_trip_points"] > 1000


@needs_data
def test_services_come_from_vf_services(client):
    from vf import services as S

    d = client.get("/api/services").json()
    assert [s["id"] for s in d["services"]] == list(S.SERVICE_LINES)
    for s in d["services"]:
        assert s["label"] == S.SERVICES[s["id"]].label
        assert s["radius_km"] == S.SERVICES[s["id"]].radius_km
    assert d["fallback"]["id"] == "other"
    assert d["simulate_available"] in (True, False)


@needs_data
@pytest.mark.parametrize("q,status", [("Jaipur", "ok"), ("302001", "ok"), ("26.9124, 75.7873", "ok"),
                                      ("Aurangabad", "ambiguous"), ("xyz", "not_found"),
                                      ("51.5, -0.12", "outside_india")])
def test_resolve_statuses(client, q, status):
    r = client.get("/api/resolve", params={"q": q}).json()
    assert r["status"] == status, r
    if status == "ok":
        assert r["lat"] is not None and r["precision_km"] > 0
    if status == "ambiguous":
        assert len(r["candidates"]) >= 2 and all(c["place_id"] and c["label"] for c in r["candidates"])


@needs_data
def test_errors_are_plain_sentences(client):
    r = client.get("/api/answer", params={"service": "nope", "q": "Jaipur"})
    assert r.status_code == 400 and "not in the list" in r.json()["detail"]
    r = client.get("/api/answer", params={"service": "dialysis", "q": ""})
    assert r.status_code == 400 and "Type a place" in r.json()["detail"]
    r = client.get("/api/answer", params={"service": "other", "q": "Jaipur"})
    assert r.status_code == 400 and "type the care" in r.json()["detail"]


@needs_data
def test_simulate_404_until_probability_table(client):
    if client.get("/api/health").json()["simulate_available"]:
        pytest.skip("a probability table is loaded")
    r = client.get("/api/simulate", params={"service": "dialysis", "q": "Jaipur"})
    assert r.status_code == 404 and "No probabilities are made up" in r.json()["detail"]


@needs_data
def test_root_serves_html(client):
    r = client.get("/")
    assert r.status_code == 200 and "<html" in r.text.lower()


# ---------------------------------------------------------------------------------------------
# Answers: the server returns exactly what vf.rank returns
# ---------------------------------------------------------------------------------------------

def _v1() -> bool:
    return getattr(server.STATE.index, "rank_version", "v0") == "v1"


def _check_card(c: dict) -> None:
    assert CARD_KEYS <= set(c), CARD_KEYS - set(c)
    # v1-final shows no confidence level (D048 (6)): the field stays, as None
    assert c["confidence"] in ((None,) if _v1() else ("high", "medium", "low"))
    for e in c["evidence"]:
        assert e["quote"] and e["field"], e


@needs_data
@pytest.mark.parametrize("service,q", BRIEF)
def test_brief_scenarios_answer_in_time(client, service, q):
    t0 = time.perf_counter()
    r = client.get("/api/answer", params={"service": service, "q": q})
    ms = (time.perf_counter() - t0) * 1000
    assert r.status_code == 200
    a = r.json()
    assert ANSWER_KEYS <= set(a) and a["status"] in STATUSES
    assert a["status"] in ("ok", "no_reliable_option"), a["status"]
    assert a["method"] == "recipe"
    assert ms < 2000, ms
    for c in a["results"] + ([a["beyond_radius"]] if a["beyond_radius"] else []):
        _check_card(c)
        assert c["evidence"], "a recommended card must quote at least one fact"
    t0 = time.perf_counter()
    assert client.get("/api/trip", params={"service": service, "q": q}).status_code == 200
    assert (time.perf_counter() - t0) * 1000 < 2000


def _assert_superset(got, want, where=""):
    """Every key the ranker returned is still there with the same value (extras may be added)."""
    assert isinstance(got, dict), where
    for k, v in want.items():
        assert k in got, f"{where}.{k} missing"
        assert got[k] == v, f"{where}.{k} changed"


@needs_data
def test_answer_matches_vf_rank(client):
    """/api/answer is vf.rank's answer plus the SPEC 3.1 extras: nothing the ranker returns is renamed
    or changed, except that further_away no longer repeats the beyond_radius card."""
    from vf import rank

    s = server.STATE
    for service, q in [("dialysis", "Jaipur"), ("dialysis", "Jokihat, Bihar")]:
        res = s.resolver.resolve(q)
        direct = server.to_jsonable(rank.rank(s.index, service, res, k=3))
        a = client.get("/api/answer", params={"service": service, "q": q}).json()
        a.pop("timing_ms")
        for k, v in direct.items():
            if k in ("results", "unverified_nearby"):
                assert len(a[k]) == len(v)
                for i, (g, w) in enumerate(zip(a[k], v)):
                    _assert_superset(g, w, f"{k}[{i}]")
            elif k == "beyond_radius":
                if v is None:
                    assert a[k] is None
                else:
                    _assert_superset(a[k], v, k)
            elif k == "further_away":
                br = (direct.get("beyond_radius") or {}).get("facility_id")
                want = [c for c in v if c["facility_id"] != br]
                assert [c["facility_id"] for c in a[k]] == [c["facility_id"] for c in want]
                for i, (g, w) in enumerate(zip(a[k], want)):
                    _assert_superset(g, w, f"{k}[{i}]")
            elif k == "window":
                _assert_superset(a[k], v, k)
            else:
                assert a[k] == v, k


@needs_data
def test_abstention_in_rural_bihar(client):
    a = client.get("/api/answer", params={"service": "dialysis", "q": "Jokihat, Bihar"}).json()
    if a["status"] != "no_reliable_option":
        pytest.skip(f"the data now has a reliable option near Jokihat ({a['status']})")
    assert a["results"] == []
    assert "No reliable option nearby" in a["how_we_decided"]["headline"]


@needs_data
def test_ambiguous_place_asks_then_place_id_answers(client):
    a = client.get("/api/answer", params={"service": "dialysis", "q": "Aurangabad"}).json()
    assert a["status"] == "needs_location_choice"
    cands = a["location"]["candidates"]
    assert len(cands) >= 2
    pick = cands[1]
    b = client.get("/api/answer", params={"service": "dialysis", "q": "Aurangabad", "place_id": pick["place_id"]}).json()
    assert b["status"] in ("ok", "no_reliable_option")
    assert b["location"]["place_id"] == pick["place_id"]
    assert abs(b["location"]["lat"] - pick["lat"]) < 1e-6


@needs_data
@pytest.mark.parametrize("q", ["xyz", "51.5, -0.12"])
def test_unknown_and_outside_india_do_not_rank(client, q):
    a = client.get("/api/answer", params={"service": "dialysis", "q": q}).json()
    assert a["status"] == "location_not_found" and a["results"] == []
    assert a["location"]["how"]


@needs_data
def test_fallback_search_is_keyword_and_low(client):
    a = client.get("/api/answer", params={"service": "other", "text": "cataract surgery", "q": "Jaipur"}).json()
    assert a["method"] == "keyword"
    assert a["status"] in ("ok", "no_reliable_option")
    for c in a["results"]:
        _check_card(c)
        assert c["confidence"] == (None if _v1() else "low")


# ---------------------------------------------------------------------------------------------
# Trip view
# ---------------------------------------------------------------------------------------------

@needs_data
@pytest.mark.parametrize("service,q", [("dialysis", "Jaipur"), ("dialysis", "Jokihat, Bihar")])
def test_trip_targets_and_detour(client, service, q):
    t = client.get("/api/trip", params={"service": service, "q": q}).json()
    a = client.get("/api/answer", params={"service": service, "q": q}).json()
    assert t["status"] == "ok"
    n, F = len(t["points"]), t["facilities"]
    assert n > 0 and len(t["nearest_recommended"]) == len(t["nearest_claim"]) == len(t["extra_km"]) == n
    shown = [c["facility_id"] for c in a["results"]] or ([a["beyond_radius"]["facility_id"]] if a["beyond_radius"] else [])
    assert [f["facility_id"] for f in F[:len(shown)]] == shown
    for r in t["nearest_recommended"]:
        assert -1 <= r < len(shown)
    for c in t["nearest_claim"]:
        assert -1 <= c < len(F)
    sm = t["summary"]
    assert sm["n_points"] == n
    first_unsupported = sum(1 for c in t["nearest_claim"] if c >= 0 and not F[c]["supported"])
    assert sm["n_first_unsupported"] == first_unsupported
    assert sm["n_first_unsupported"] == sm["n_first_claim_only"] + sm["n_first_held_back"]
    detours = [x for x in t["extra_km"] if x is not None and x > 0]
    if sm["n_rerouted"]:
        assert sm["extra_km_mean"] is not None and sm["extra_km_mean"] >= 0
    assert all(x >= -1e-6 for x in detours)


@needs_data
def test_trip_names_held_back_targets_apart_kotputli_trauma(client):
    """A blocked grade-2 facility quotes evidence; the counter must not call it 'no evidence' (review WP8B)."""
    t = client.get("/api/trip", params={"service": "trauma", "q": "Kotputli"}).json()
    assert t["status"] == "ok"
    sm, F = t["summary"], t["facilities"]
    first = [F[c] for c in t["nearest_claim"] if c >= 0 and not F[c]["supported"]]
    claim_only = sum(1 for f in first if f["unsupported_reason"] == "claim_only")
    held_back = sum(1 for f in first if f["unsupported_reason"] == "held_back")
    assert claim_only + held_back == len(first) == sm["n_first_unsupported"]
    assert sm["n_first_claim_only"] == claim_only and sm["n_first_held_back"] == held_back
    # the reasons agree with the grades and blocking flags the server loaded
    ld = server.STATE.index.lines["trauma"]
    pos = {fid: i for i, fid in enumerate(server.STATE.index.fac["facility_id"])}
    for f in F:
        i = pos[f["facility_id"]]
        if f["unsupported_reason"] == "held_back":
            assert ld.grade[i] >= 2 and ld.blocked[i]
        elif f["unsupported_reason"] == "claim_only":
            assert ld.grade[i] == 1
        else:
            assert f["supported"]


@needs_data
def test_trip_unavailable_for_fallback_and_ambiguous(client):
    assert client.get("/api/trip", params={"service": "other", "q": "Jaipur"}).json()["status"] == "unavailable"
    assert client.get("/api/trip", params={"service": "dialysis", "q": "Aurangabad"}).json()["status"] == "unavailable"


# ---------------------------------------------------------------------------------------------
# Answer extras (app/design/SPEC.md 3.1) and /api/sim (SPEC 3.2, 3.3, 7.3)
# ---------------------------------------------------------------------------------------------

ARTEFACT_LATS = {15.59, 15.5934}
SIM_CASES = [("emoc", "226001"), ("dialysis", "Jokihat, Bihar"), ("dialysis", "Jaipur"), ("trauma", "Kotputli")]


def _fresh_cache():
    server.STATE.cache = server._LRU(server.CACHE_SIZE)


def _cards(a: dict) -> list:
    return list(a["results"]) + ([a["beyond_radius"]] if a.get("beyond_radius") else []) + list(a.get("further_away") or [])


def test_official_domain_drops_shorteners_and_maps():
    od = server._official_domain
    assert od("bnkmedicare.com") == "bnkmedicare.com"
    assert od("https://www.Example.org/about") == "example.org"
    for bad in ("https://goo.gl/maps/abc", "https://maps.app.goo.gl/xyz", "https://maps.google.com/?q=1",
                "https://www.google.com/maps/place/x", "http://bit.ly/abc", "https://tinyurl.com/abc", "", None,
                "localhost"):
        assert od(bad) is None, bad


def test_requirement_keys_take_the_longest_plain_name():
    names = server._requirement_names("dialysis")
    assert server._req_key("caesarean section (WHO 2009 Table 4, signal function 8)", names) == "caesarean section"
    assert server._req_key("blood bank or transfusion, not necessarily for obstetric patients (WHO)", names) == \
        "blood bank or transfusion"
    assert server._req_key("dialysis unit, machines or haemodialysis named, not a nephrologist alone (PM-JAY)",
                           names) == "dialysis unit, machines or nephrologist"
    assert server._req_key("dialysis", names) == "dialysis"


def test_demo_param_can_only_turn_masking_on(monkeypatch):
    monkeypatch.delenv("VF_DEMO_MODE", raising=False)
    assert server.demo_on(None) is False and server.demo_on("1") is True
    monkeypatch.setenv("VF_DEMO_MODE", "1")
    assert server.demo_on("0") is True and server.demo_on(None) is True


def _ref_summary(sim: dict) -> dict:
    """SPEC 7.3 counters recomputed with plain Python loops, independent of server.sim_summary."""
    a, b = sim["travel"]["a"], sim["travel"]["b"]
    W = sim["window"]["minutes"]
    V = sim["villages"]
    F = sim["facilities"]
    R = 6371.0088

    def km(la1, lo1, la2, lo2):
        p1, p2 = math.radians(la1), math.radians(la2)
        h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lo2 - lo1) / 2) ** 2
        return 2 * R * math.asin(math.sqrt(min(h, 1.0)))

    ev = [j for j, f in enumerate(F) if f["status"] == "evidence"]
    within = n_inf = n_first = n_co = n_hb = 0
    finite, extras = [], []
    for la, lo, fac in zip(V["lat"], V["lon"], V["factor"]):
        kms = [km(la, lo, f["lat"], f["lon"]) for f in F]
        near = min((fac * a * kms[j] ** b for j in ev), default=math.inf)
        if near <= W:
            within += 1
        if math.isinf(near):
            n_inf += 1
        else:
            finite.append(near)
        first = min(range(len(F)), key=lambda j: (kms[j], j))
        if F[first]["status"] != "evidence":
            n_first += 1
            n_co += F[first]["status"] == "claim_only"
            n_hb += F[first]["status"] == "held_back"
            if not math.isinf(near):
                fj = F[first]
                leg = min((km(fj["lat"], fj["lon"], F[j]["lat"], F[j]["lon"]), j) for j in ev)[0]
                extras.append(fac * a * kms[first] ** b + fac * a * leg ** b - near)
    return {"n_villages": len(V["lat"]), "within": within,
            "avg_minutes": round(sum(finite) / len(finite), 1) if finite else None,
            "n_no_evidence_in_reach": n_inf, "first_no_evidence": n_first, "first_claim_only": n_co,
            "first_held_back": n_hb, "extra_minutes_mean": round(sum(extras) / len(extras), 1) if extras else None}


def test_sim_summary_toy_case():
    # two villages, one evidence place and one claim-only place nearer to village 0
    out = server.sim_summary([0.0, 0.0], [0.0, 1.0], [1.0, 1.0], [0.0, 0.0], [0.05, 0.9],
                             ["claim_only", "evidence"], 120.0, 2.30, 0.842)
    assert out["n_villages"] == 2 and out["within"] == 2 and out["n_no_evidence_in_reach"] == 0
    assert out["first_no_evidence"] == 1 and out["first_claim_only"] == 1 and out["first_held_back"] == 0
    assert out["extra_minutes_mean"] > 0
    none = server.sim_summary([0.0], [0.0], [1.0], [0.0], [0.1], ["claim_only"], 60.0, 2.30, 0.842)
    assert none["within"] == 0 and none["avg_minutes"] is None and none["n_no_evidence_in_reach"] == 1


@needs_data
def test_answer_extras_lucknow_emoc(client):
    a = client.get("/api/answer", params={"service": "emoc", "q": "226001"}).json()
    assert a["status"] == "ok" and a["results"]
    assert a["demo_masked"] is False
    o = a["origin"]
    assert o["short_name"] == "PIN 226001" and o["state_display"] == "Uttar Pradesh"
    assert o["loose"] is False and o["too_coarse"] is False
    assert a["window"]["hours_text"] == "2 hours" and a["window"]["reach_text"]
    items = a["why_not"]["items"]
    assert [it["letter"] for it in items] == [chr(65 + i) for i in range(len(items))]
    assert all(isinstance(it["in_sim"], bool) for it in items)
    steps = a["funnel"]["steps"]
    assert steps[-1]["step"] == "shown" and steps[-1]["n"] == len(a["results"])
    assert all(set(st) <= {"step", "label", "n", "n_distinct"} for st in steps)
    c0 = a["results"][0]
    assert c0["name_short"] and c0["time"]["short"].startswith("about ")
    assert c0["state_display"] == "Uttar Pradesh" and c0["crosses_state"] is False
    assert 1 <= len(c0["questions"]) <= 3 and all(q["question"] for q in c0["questions"])
    if server.STATE.book is not None:
        assert c0["call"]["has_phone"] and re.search(r"x{4,}", c0["call"]["phone"])
        assert c0["call"]["tel"] is None  # no reveal asked
        assert not re.search(r"\d{6,}", c0["call"]["phone"].replace(" ", ""))
    for c in _cards(a):
        texts = [q["text"] for q in c["quotes"]]
        assert len(texts) == len(set(texts)) and texts
        for q in c["quotes"]:
            assert q["field"] and isinstance(q["requirement_keys"], list)
            for s_, e_ in q["marks"]:
                assert 0 <= s_ < e_ <= len(q["text"])
    assert any(q["marks"] for q in c0["quotes"]), "the C-section quote names the service"
    sim = client.get("/api/sim", params={"service": "emoc", "q": "226001"}).json()
    reach = next(st for st in steps if st["step"] == "claim_within_reach")
    assert reach["n"] == sim["summary"]["n_listing_records"]
    # cards, letters and in_sim agree with the sim payload
    F = sim["facilities"]
    for r, c in enumerate(a["results"], start=1):
        f = F[c["sim_index"]]
        assert f["facility_id"] == c["facility_id"] and f["card_rank"] == r and f["status"] == "evidence"
    by_id = {f["facility_id"]: f for f in F}
    for it in items:
        f = by_id.get(it["facility_id"])
        assert it["in_sim"] == bool(f and f["status"] in ("claim_only", "held_back"))
        if f is not None:
            assert f["why_not_letter"] == it["letter"]


@needs_data
def test_answer_reveal_gives_tel_only_outside_demo(client, monkeypatch):
    if server.STATE.book is None:
        pytest.skip("no phone book (raw facilities file) loaded")
    monkeypatch.delenv("VF_DEMO_MODE", raising=False)
    a = client.get("/api/answer", params={"service": "emoc", "q": "226001", "reveal": "1"}).json()
    call = a["results"][0]["call"]
    assert call["tel"] and call["tel"].startswith("tel:") and re.search(r"x{4,}", call["phone"])
    d = client.get("/api/answer", params={"service": "emoc", "q": "226001", "reveal": "1", "demo": "1"}).json()
    assert d["demo_masked"] is True and d["results"][0]["call"]["tel"] is None


@needs_data
def test_jokihat_dialysis_beyond_window_listed_once(client):
    a = client.get("/api/answer", params={"service": "dialysis", "q": "Jokihat, Bihar"}).json()
    if a["status"] != "no_reliable_option":
        pytest.skip(f"the data now has a reliable option near Jokihat ({a['status']})")
    br = a["beyond_radius"]
    assert br is not None
    assert br["facility_id"] not in [c["facility_id"] for c in a["further_away"]]
    assert br["state_display"] == "West Bengal" and br["crosses_state"] is True
    assert a["origin"]["state_display"] == "Bihar"
    for c in _cards(a):
        assert c.get("public_list") is None  # LaQshya lines are for emoc only
    steps = a["funnel"]["steps"]
    assert steps[-1]["n"] == 0


@needs_data
@pytest.mark.parametrize("service,q", SIM_CASES)
def test_sim_payload_matches_the_rules(client, service, q):
    s = server.STATE
    sim = client.get("/api/sim", params={"service": service, "q": q}).json()
    assert sim["status"] == "ok" and sim["version"] == "sim_v1"
    V = sim["villages"]
    assert V["n"] == len(V["lat"]) == len(V["lon"]) == len(V["pin"]) == len(V["factor"])
    assert 0 < V["n"] <= server.SIM_MAX_VILLAGES and V["radius_km"] == 50
    assert not (set(V["lat"]) & ARTEFACT_LATS)
    assert len(set(zip(V["lat"], V["lon"]))) == V["n"]
    assert all(f is not None and f > 0 for f in V["factor"])
    F = sim["facilities"]
    assert [f["i"] for f in F] == list(range(len(F))) and len(F) <= server.SIM_MAX_FACILITIES
    assert all(f["status"] in ("evidence", "claim_only", "held_back") for f in F)
    assert all(f["strong"] == (f["status"] == "evidence" and f["grade"] >= 3) for f in F)
    assert [f["km_from_origin"] for f in F] == sorted(f["km_from_origin"] for f in F)
    assert len({f["facility_id"] for f in F}) == len(F)
    sm = sim["summary"]
    ref = _ref_summary(sim)
    for k, v in ref.items():
        if isinstance(v, float):
            assert sm[k] == pytest.approx(v, abs=0.05), k
        else:
            assert sm[k] == v, k
    assert sm["first_no_evidence"] == sm["first_claim_only"] + sm["first_held_back"]
    assert sm["n_facilities"] == len(F) == sm["n_evidence"] + sm["n_claim_only"] + sm["n_held_back"]
    assert sm["n_listing_records"] - sm["n_merged_duplicates"] == sm["n_facilities"]
    # the set is rank.gate_masks in_reach, one record per duplicate group
    from vf import rank
    res = s.resolver.resolve(q)
    d = rank._distances(s.index, res.lat, res.lon)
    g = rank.gate_masks(s.index, service, d, res)
    fids = s.index.fac["facility_id"].to_numpy()
    in_reach = {str(fids[p]) for p in np.flatnonzero(g["in_reach"])}
    rec = {str(fids[p]) for p in np.flatnonzero(g["in_reach"] & g["rec_mask"])}
    assert len(in_reach) == sm["n_listing_records"]
    assert {f["facility_id"] for f in F} <= in_reach
    assert {f["facility_id"] for f in F if f["status"] == "evidence"} <= rec
    a = client.get("/api/answer", params={"service": service, "q": q}).json()
    funnel_rec = next(st for st in a["funnel"]["steps"] if st["step"] == "recommendable")["n"]
    merged_rec = len(rec - {f["facility_id"] for f in F})
    assert sm["n_evidence"] == funnel_rec - merged_rec
    w = sim["window"]
    assert w["minutes"] == a["window"]["minutes"] and w["allowance"] == a["window"]["allowance"]
    assert w["hours_text"] == a["window"]["hours_text"]
    assert sim["travel"]["a"] == 2.30 and sim["travel"]["b"] == 0.842 and sim["travel"]["text"]
    assert sim["origin"]["factor"] == a["window"]["factor"]
    labels = sim["context"]["labels"]
    assert 0 < len(labels) <= server.LABEL_MAX
    assert all(l["kind"] in ("district", "city", "post_office") and l["prio"] in (1, 2, 3) for l in labels)


@needs_data
def test_sim_lucknow_counts_and_time(client):
    _fresh_cache()
    client.get("/api/answer", params={"service": "dialysis", "q": "Jaipur"})  # warm the why_not lookups
    _fresh_cache()
    t0 = time.perf_counter()
    r = client.get("/api/sim", params={"service": "emoc", "q": "226001"})
    ms = (time.perf_counter() - t0) * 1000
    sim = r.json()
    assert r.status_code == 200 and sim["status"] == "ok"
    assert 300 <= sim["villages"]["n"] <= 3000
    assert sim["summary"]["within"] <= sim["villages"]["n"]
    assert ms < 300, ms
    t0 = time.perf_counter()
    client.get("/api/sim", params={"service": "emoc", "q": "226001"})
    assert (time.perf_counter() - t0) * 1000 < 100  # cached


@needs_data
def test_sim_unavailable_for_other_ambiguous_and_unknown(client):
    for params in ({"service": "other", "q": "Jaipur"}, {"service": "dialysis", "q": "Aurangabad"},
                   {"service": "dialysis", "q": "xyz"}):
        r = client.get("/api/sim", params=params)
        assert r.status_code == 200 and r.json()["status"] == "unavailable" and r.json()["reason"], params
    r = client.get("/api/sim", params={"service": "nope", "q": "Jaipur"})
    assert r.status_code == 400 and "not in the list" in r.json()["detail"]
    r = client.get("/api/sim", params={"service": "dialysis", "q": ""})
    assert r.status_code == 400


@needs_data
def test_demo_mode_masks_names_and_phone_links(client, monkeypatch):
    monkeypatch.setenv("VF_DEMO_MODE", "1")
    a = client.get("/api/answer", params={"service": "emoc", "q": "226001", "reveal": "1", "demo": "0"}).json()
    assert a["demo_masked"] is True
    for c in _cards(a):
        if c.get("call"):
            assert c["call"]["tel"] is None
    items = a["why_not"]["items"]
    assert items and all(it["name_masked"] for it in items)
    assert all("(name hidden)" in it["name"] for it in items)
    # unverified_nearby is not shown by the UI, but the payload must not carry real names either
    for c in a.get("unverified_nearby") or []:
        assert c.get("name_masked") and "(name hidden)" in c["name"]
    sim = client.get("/api/sim", params={"service": "emoc", "q": "226001"}).json()
    for f in sim["facilities"]:
        if f["card_rank"] is None:
            assert f["name_masked"] and "name hidden" in f["name"]
        else:
            assert not f["name_masked"]


@needs_data
def test_extras_failures_leave_fields_out_and_never_500(client, monkeypatch):
    s = server.STATE
    _fresh_cache()

    def boom(*a, **k):
        raise RuntimeError("why_not broke")
    monkeypatch.setattr(server.WN, "why_not_strip", boom)
    monkeypatch.setattr(s, "book", None)
    r = client.get("/api/answer", params={"service": "dialysis", "q": "Jaipur"})
    assert r.status_code == 200
    a = r.json()
    assert "why_not" not in a and a["results"] and all("call" not in c for c in a["results"])
    assert "funnel" in a and a["results"][0]["questions"]
    h = client.get("/api/health").json()
    assert "answer.why_not" in h["extras_errors"]
    _fresh_cache()
    s.extras_errors.pop("answer.why_not", None)


@needs_data
def test_v0_ranker_is_tolerated(client):
    s = server.STATE
    old = s.index.rank_version
    try:
        s.index.rank_version = "v0"
        _fresh_cache()
        a = client.get("/api/answer", params={"service": "emoc", "q": "226001"}).json()
        assert a["status"] in ("ok", "no_reliable_option")
        assert a["origin"]["loose"] is False
        assert a["window"]["minutes"] == 120 and a["window"]["hours_text"] == "2 hours"
        assert all(c["time"] is None for c in _cards(a))
        sim = client.get("/api/sim", params={"service": "emoc", "q": "226001"}).json()
        assert sim["status"] == "ok" and sim["window"]["minutes"] == 120
        assert sim["summary"]["n_villages"] == sim["villages"]["n"]
    finally:
        s.index.rank_version = old
        _fresh_cache()


@needs_data
def test_search_cards_get_extras(client):
    a = client.get("/api/answer", params={"service": "other", "text": "cataract surgery", "q": "Jaipur"}).json()
    assert a["method"] == "keyword" and "why_not" not in a
    for c in a["results"]:
        assert c["name_short"] and "questions" in c and c["sim_index"] is None


@needs_data
def test_health_lists_extras(client):
    h = client.get("/api/health").json()
    assert set(h["extras"]) >= {"record_info", "phone_book", "public_list", "outside_lists", "why_not", "travel"}
    assert isinstance(h["extras_errors"], dict) and h["sim_version"] == "sim_v1"


@needs_data
def test_why_not_rows_carry_minutes_like_the_cards(client):
    a = client.get("/api/answer", params={"service": "emoc", "q": "226001"}).json()
    items = a["why_not"]["items"]
    with_km = [it for it in items if it.get("distance_km") is not None]
    assert with_km and all(isinstance(it.get("minutes"), float) and it["time_short"].startswith("about ") for it in with_km)
    assert all("minutes" not in it or it["minutes"] is None for it in items if it.get("distance_km") is None)
