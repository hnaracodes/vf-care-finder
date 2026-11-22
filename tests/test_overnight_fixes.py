"""Regression tests for the overnight fix round (reports/overnight_test_and_fix.md)."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dbapp"))
server = importlib.import_module("server")

from vf import questions as Q  # noqa: E402

_has_bundle = (ROOT / "dbapp" / "bundle" / "manifest.json").exists()
needs_bundle = pytest.mark.skipif(not _has_bundle, reason="no dbapp/bundle")


@pytest.fixture(scope="module")
def client():
    with TestClient(server.app) as c:
        yield c


def test_mask_numbers_keeps_iso_dates_masks_phones():
    assert Q.mask_numbers("retrieved 2026-11-21") == "retrieved 2026-11-21"
    assert Q.mask_numbers("call 0522-2345678") == "call [phone number]"
    assert Q.mask_numbers("+91 98765 43210") == "[phone number]"


def test_negated_match():
    assert server.negated_match("Animal bites (Excluding Snake Bite)", "snake bite")
    assert server.negated_match("Treats all poisoning except snake bite", "snake bite")
    assert not server.negated_match("Snake bite treatment available", "snake bite")
    assert not server.negated_match("Dialysis unit, no waiting", "dialysis")


def test_keyword_words_and_did_you_mean():
    assert server.keyword_words("a") == []
    assert server.keyword_words("snake bite") == ["snake", "bite"]
    assert server.did_you_mean("dialysis")["service"] == "dialysis"
    assert server.did_you_mean("heart attack")["service"] == "cath_lab"
    assert server.did_you_mean("snake bite") is None


@needs_bundle
def test_junk_free_text_is_refused(client):
    r = client.get("/api/answer", params={"service": "other", "q": "302001", "text": "a"})
    assert r.status_code == 400 and "3 or more letters" in r.json()["detail"]


@needs_bundle
def test_keyword_marks_negated_snake_bite_at_barshi(client):
    a = client.get("/api/answer", params={"service": "other", "q": "Barshi", "text": "snake bite"}).json()
    top = a["results"][0]
    assert top["match_negated"] is True
    assert top["quotes"][0]["negated"] is True


@needs_bundle
def test_why_not_rows_carry_laqshya_line_and_masked_call(client):
    a = client.get("/api/answer", params={"service": "emoc", "q": "Nashik"}).json()
    items = {it["name"]: it for it in a["why_not"]["items"]}
    dh = items.get("District Hospital, Nashik")
    assert dh is not None
    assert any("LaQshya" in l["text"] for l in dh["outside_lines"])
    assert dh["call"]["has_phone"] and dh["call"]["tel"] is None and "x" in dh["call"]["phone"]
    # outside-list citations keep their retrieval date (no "[phone number]" in a date)
    assert all("[phone number]" not in (o.get("citation") or "") for o in a.get("outside_nearby") or [])


@needs_bundle
def test_why_here_does_not_claim_closer_when_further(client):
    a = client.get("/api/answer", params={"service": "emoc", "q": "226001"}).json()
    c0, c1 = a["results"][0], a["results"][1]
    if c0["distance_km"] > c1["distance_km"]:
        assert "closer in straight-line km" not in (c0.get("why_here") or "")


@needs_bundle
def test_resolver_integer_coords_and_delhi(client):
    r = client.get("/api/resolve", params={"q": "30,79"}).json()
    assert r["status"] == "ok" and r["kind"] == "coords"
    assert client.get("/api/resolve", params={"q": "0,0"}).json()["status"] == "outside_india"
    d = client.get("/api/resolve", params={"q": "Delhi"}).json()
    assert d["status"] == "ok" and "New Delhi" in d["label"]
