"""API tests for the Databricks App server (dbapp/server.py, WP8).

The server runs the real engine only (vf.rank, vf.locate, vf.services): there is no stub path.
The app state is loaded once per module, so the index loads once. Data tests skip when neither
dbapp/bundle/ nor the shared silver and gold tables are present.
"""
from __future__ import annotations

import importlib
import sys
import time
from pathlib import Path

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
    assert r.status_code == 400 and "Unknown service" in r.json()["detail"]
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

def _check_card(c: dict) -> None:
    assert CARD_KEYS <= set(c), CARD_KEYS - set(c)
    assert c["confidence"] in ("high", "medium", "low")
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


@needs_data
def test_answer_matches_vf_rank(client):
    from vf import rank

    s = server.STATE
    res = s.resolver.resolve("Jaipur")
    direct = server.to_jsonable(rank.rank(s.index, "dialysis", res, k=3))
    a = client.get("/api/answer", params={"service": "dialysis", "q": "Jaipur"}).json()
    a.pop("timing_ms")
    assert a == direct


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
        assert c["confidence"] == "low"


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
