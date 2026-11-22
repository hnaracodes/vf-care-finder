"""Tests for the location resolver (vf.locate, WP2 Part B).

The first group runs on a tiny synthetic gazetteer and needs no data. The
second group runs on silver/places.parquet and the frozen hand-written cases in
data/labels/resolver_cases.csv, and skips when they are absent.
"""
import math
import time

import numpy as np
import pandas as pd
import pytest

from vf import geo, locate, paths


# ---------------------------------------------------------------------------
# synthetic gazetteer
# ---------------------------------------------------------------------------

def _row(kind, name, state, district, lat, lon, prec=5.0, pin=None, n=3, note=None):
    key = pin if kind == "pin" else (geo.norm_name(name) if kind == "district" else geo.place_key(name))
    pid = f"{kind}:{key}" if kind == "pin" else f"{kind}:{key}|{state}|{district}"
    return {"place_id": pid, "kind": kind, "name": name, "name_norm": key, "state": state,
            "district": district, "pin": pin, "lat": lat, "lon": lon, "precision_km": prec,
            "n_offices": n, "note": note}


def _tiny_rows():
    rows = [
        _row("district", "AURANGABAD", "BIHAR", "AURANGABAD", 24.75, 84.37, 50),
        _row("district", "AURANGABAD", "MAHARASHTRA", "AURANGABAD", 19.88, 75.34, 60),
        _row("district", "LUCKNOW", "UTTAR PRADESH", "LUCKNOW", 26.85, 80.95, 19),
        _row("district", "JAIPUR", "RAJASTHAN", "JAIPUR", 26.91, 75.79, 70),
        _row("district", "GURUGRAM", "HARYANA", "GURUGRAM", 28.46, 77.03, 23),
        _row("post_office", "Hazratganj S.O", "UTTAR PRADESH", "LUCKNOW", 26.85, 80.94, 3, "226001"),
        _row("post_office", "Rampur B.O", "UTTAR PRADESH", "LUCKNOW", 26.95, 80.90, 6, "226002"),
        _row("post_office", "Rampur B.O", "RAJASTHAN", "JAIPUR", 26.80, 75.70, 6, "302002"),
        _row("post_office", "Daudnagar S.O", "BIHAR", "AURANGABAD", 25.03, 84.40, 4, "824113"),
    ]
    pins = [("226001", "UTTAR PRADESH", "LUCKNOW", 26.85, 80.94), ("226002", "UTTAR PRADESH", "LUCKNOW", 26.95, 80.9),
            ("302001", "RAJASTHAN", "JAIPUR", 26.92, 75.80), ("302002", "RAJASTHAN", "JAIPUR", 26.8, 75.7),
            ("824113", "BIHAR", "AURANGABAD", 25.03, 84.40), ("122001", "HARYANA", "GURUGRAM", 28.46, 77.03)]
    for p, s, d, la, lo in pins:
        rows.append(_row("pin", p, s, d, la, lo, 4, p,
                         note="mostly in LUCKNOW (60% of its offices); spans 2 districts" if p == "226002" else None))
    return rows


@pytest.fixture(scope="module")
def tiny():
    return locate.Resolver(pd.DataFrame(_tiny_rows()))


@pytest.fixture(scope="module")
def tiny2():
    """The tiny gazetteer plus district names that are a direction word and a state, a district name
    inside village names, and a village office spelled close to it (the review's failure cases)."""
    rows = _tiny_rows() + [
        _row("district", "NEW DELHI", "DELHI", "NEW DELHI", 28.60, 77.20, 8),
        _row("district", "NORTH", "DELHI", "NORTH", 28.70, 77.20, 8),
        _row("district", "EAST", "DELHI", "EAST", 28.62, 77.30, 8),
        _row("district", "NORTH GOA", "GOA", "NORTH GOA", 15.55, 73.85, 20),
        _row("district", "EAST DISTRICT", "SIKKIM", "EAST DISTRICT", 27.33, 88.62, 15),
        _row("district", "WEST TRIPURA", "TRIPURA", "WEST TRIPURA", 23.84, 91.30, 20),
        _row("district", "SAGAR", "MADHYA PRADESH", "SAGAR", 23.84, 78.74, 40),
        _row("district", "HYDERABAD", "TELANGANA", "HYDERABAD", 17.39, 78.48, 10),
        _row("district", "24 PARAGANAS SOUTH", "WEST BENGAL", "24 PARAGANAS SOUTH", 22.16, 88.43, 40),
        _row("post_office", "New B.O", "WEST BENGAL", "24 PARAGANAS SOUTH", 22.20, 88.40, 5, "743001"),
        _row("post_office", "Sagar Chandipur B.O", "WEST BENGAL", "24 PARAGANAS SOUTH", 21.90, 88.10, 6, "743373"),
        _row("post_office", "Sagar Kalan B.O", "MADHYA PRADESH", "SAGAR", 23.70, 78.70, 6, "470001"),
    ]
    for p, s, d, la, lo in [("110001", "DELHI", "NEW DELHI", 28.63, 77.22), ("403001", "GOA", "NORTH GOA", 15.50, 73.83),
                            ("737101", "SIKKIM", "EAST DISTRICT", 27.33, 88.61), ("799001", "TRIPURA", "WEST TRIPURA", 23.83, 91.28),
                            ("470001", "MADHYA PRADESH", "SAGAR", 23.84, 78.74), ("500001", "TELANGANA", "HYDERABAD", 17.39, 78.48),
                            ("743001", "WEST BENGAL", "24 PARAGANAS SOUTH", 22.20, 88.40)]:
        rows.append(_row("pin", p, s, d, la, lo, 4, p))
    return locate.Resolver(pd.DataFrame(rows))


def test_coords_both_orders(tiny):
    a = tiny.resolve("26.85, 80.95")
    b = tiny.resolve("80.95, 26.85")
    assert a.status == b.status == "ok" and a.kind == b.kind == "coords"
    assert (a.lat, a.lon) == (b.lat, b.lon) == (26.85, 80.95)
    assert a.district == "LUCKNOW"
    assert "longitude, latitude order" in b.how
    assert 0 < a.precision_km < 2


def test_coords_with_hemisphere_letters(tiny):
    r = tiny.resolve("26.9124° N, 75.7873° E")
    assert r.status == "ok" and r.district == "JAIPUR"


def test_coords_outside_india(tiny):
    assert tiny.resolve("51.5074, -0.1278").status == "outside_india"
    r = tiny.resolve("15.0, 70.0")  # inside the loose box, in the Arabian Sea
    assert r.status == "outside_india" and "outside India's border" in r.how
    r = tiny.resolve("35.0, 79.3")  # inside India's outline (Aksai Chin), far from every office
    assert r.status == "outside_india" and "km from the nearest" in r.how
    r = tiny.resolve("22.0, 80.0")  # far from the tiny gazetteer, but south of the distance rule
    assert r.status == "ok"


def test_pin_plain_spaced_and_with_text(tiny):
    for q in ("302001", "302 001", "PIN 302001", "Jaipur 302001"):
        r = tiny.resolve(q)
        assert r.status == "ok" and r.kind == "pin" and r.label.startswith("PIN 302001"), q


def test_pin_spanning_districts_says_mostly(tiny):
    assert "mostly in" in tiny.resolve("226002").label


def test_unknown_pin_falls_back_to_text(tiny):
    assert tiny.resolve("999999").status == "not_found"
    r = tiny.resolve("999999 Lucknow")
    assert r.status == "ok" and r.district == "LUCKNOW" and "not in the India Post directory" in r.how


def test_five_digits_is_not_a_pin(tiny):
    assert tiny.resolve("12345").status == "not_found"


def test_ambiguous_returns_candidates_not_a_guess(tiny):
    r = tiny.resolve("Aurangabad")
    assert r.status == "ambiguous"
    assert r.lat is None and r.lon is None
    assert {(c["state"], c["district"]) for c in r.candidates} == {("BIHAR", "AURANGABAD"),
                                                                  ("MAHARASHTRA", "AURANGABAD")}


def test_state_in_text_or_picked(tiny):
    assert tiny.resolve("Aurangabad, Bihar").state == "BIHAR"
    assert tiny.resolve("Aurangabad", state="Maharashtra").state == "MAHARASHTRA"
    assert tiny.resolve("Rampur UP").district == "LUCKNOW"
    assert tiny.resolve("Rampur").status == "ambiguous"


def test_district_after_comma(tiny):
    r = tiny.resolve("Rampur, Jaipur")
    assert r.status == "ok" and r.district == "JAIPUR" and r.kind == "post_office"


def test_district_label_is_not_a_location(tiny):
    r = tiny.resolve("Lucknow district")
    assert r.kind == "district" and r.label.startswith("Centre of Lucknow district")
    assert "km across" in r.label


def test_misspelling_and_alias(tiny):
    assert tiny.resolve("Lucknaw").district == "LUCKNOW"
    r = tiny.resolve("Gurgaon")
    assert r.district == "GURUGRAM" and "old or alternative name" in r.how


def test_office_suffixes_stripped(tiny):
    r = tiny.resolve("Hazratganj S.O")
    assert r.status == "ok" and r.kind == "post_office"


def test_nonsense(tiny):
    for q in ("", "   ", "asdfghjkl", "hospital near me"):
        assert tiny.resolve(q).status == "not_found", q


@pytest.mark.parametrize("q,state,want", [
    ("New Delhi", None, ("DELHI", "NEW DELHI")),
    ("New Delhi", "Delhi", ("DELHI", "NEW DELHI")),
    ("North Goa", None, ("GOA", "NORTH GOA")),
    ("North Goa", "Goa", ("GOA", "NORTH GOA")),
    ("North Goa, Goa", None, ("GOA", "NORTH GOA")),
    ("East District", None, ("SIKKIM", "EAST DISTRICT")),
    ("East District", "Sikkim", ("SIKKIM", "EAST DISTRICT")),
    ("East District, Sikkim", None, ("SIKKIM", "EAST DISTRICT")),
    ("West Tripura", "Tripura", ("TRIPURA", "WEST TRIPURA")),
    ("North Tripura", "Assam", None),   # no such district here: must not become Delhi's North
    ("North", None, ("DELHI", "NORTH")),
    ("East", None, ("DELHI", "EAST")),
])
def test_direction_and_state_word_districts(tiny2, q, state, want):
    r = tiny2.resolve(q, state)
    if want is None:
        assert not (r.status == "ok" and r.state == "DELHI"), (q, r.state, r.district)
    else:
        assert r.status == "ok" and (r.state, r.district) == want, (q, r.status, r.state, r.district, r.how)


def test_district_inside_village_name_does_not_win(tiny2):
    # one-letter typo of a village office that contains a district name in another state
    r = tiny2.resolve("Sagar Chandpur")
    assert r.status in ("ok", "ambiguous")
    if r.status == "ok":
        assert (r.state, r.district) == ("WEST BENGAL", "24 PARAGANAS SOUTH") and r.kind == "post_office"
    else:
        assert ("WEST BENGAL", "24 PARAGANAS SOUTH") in {(c["state"], c["district"]) for c in r.candidates}


def test_partial_text_match_is_a_choice(tiny2):
    r = tiny2.resolve("Hyderabad, Sindh")
    assert r.status == "ambiguous" and r.lat is None
    assert [c["district"] for c in r.candidates] == ["HYDERABAD"]
    assert "Sindh" in r.how and "Only part of the text" in r.how
    # the words a district explains are not "unused": the office is found
    r = tiny2.resolve("Sagar Kalan Sagar")
    assert r.status == "ok" and r.kind == "post_office" and r.district == "SAGAR"


def test_match_outside_the_given_state_is_a_choice(tiny2):
    r = tiny2.resolve("Lucknaw", state="Bihar")
    assert r.status == "ambiguous" and r.lat is None
    assert ("UTTAR PRADESH", "LUCKNOW") in {(c["state"], c["district"]) for c in r.candidates}
    assert "Nothing close enough was found in Bihar" in r.how
    assert tiny2.resolve("Lucknaw").status == "ok"


def test_district_after_comma_with_state(tiny):
    r = tiny.resolve("Rampur, Jaipur, Rajasthan")
    assert r.status == "ok" and r.district == "JAIPUR" and r.kind == "post_office"


def test_india_border_km():
    d = locate.india_border_km([26.91, 31.5204, 26.4525, 24.3745, 27.7172, 11.6234, 31.6340, float("nan")],
                               [75.79, 74.3587, 87.2718, 88.6042, 85.3240, 92.7265, 74.8720, 1.0])
    assert d[0] == 0 and d[6] == 0                       # Jaipur, Amritsar
    assert d[5] <= locate.BORDER_SLACK_KM                # Port Blair, on the coast
    assert (d[1:5] > locate.BORDER_SLACK_KM).all()       # Lahore, Biratnagar, Rajshahi, Kathmandu
    assert math.isnan(d[7])


def test_score_case():
    ok = locate.Resolution("ok", 1, 2, 3, "district", state="BIHAR", district="PATNA")
    assert locate.score_case(ok, "ok", "district", "BIHAR|PATNA")[0]
    assert not locate.score_case(ok, "ok", "pin", "BIHAR|PATNA")[0]
    assert not locate.score_case(ok, "ok", "", "BIHAR|GAYA")[0]
    amb = locate.Resolution("ambiguous", candidates=[{"state": "A", "district": "X"}, {"state": "B", "district": "X"}])
    assert locate.score_case(amb, "ambiguous", "", "A|X;B|X")[0]
    assert not locate.score_case(amb, "ambiguous", "", "A|X;C|X")[0]


def test_nice():
    assert locate.nice("UTTAR PRADESH") == "Uttar Pradesh"
    assert locate.nice("THE DADRA AND NAGAR HAVELI AND DAMAN AND DIU") == "Dadra and Nagar Haveli and Daman and Diu"


# ---------------------------------------------------------------------------
# real gazetteer and frozen cases
# ---------------------------------------------------------------------------

PLACES = paths.silver_dir() / "places.parquet"
CASES = paths.labels_dir() / "resolver_cases.csv"
needs_data = pytest.mark.skipif(not (PLACES.exists() and CASES.exists()), reason="places.parquet or cases absent")

#: Cases that miss after the freeze, each explained in reports/resolver_eval.md.
KNOWN_MISSES = {
    "27": "Hydrabad is also an exact post office name in Unnao; returned as ambiguous with Hyderabad first",
}


@pytest.fixture(scope="module")
def real():
    return locate.default_resolver()


def _cases():
    if not CASES.exists():
        return []
    return [pytest.param(r, id=f"{r['case_id']}-{r['query'][:20]}",
                         marks=[pytest.mark.xfail(reason=KNOWN_MISSES[r["case_id"]], strict=True)]
                         if r["case_id"] in KNOWN_MISSES else [])
            for r in locate.load_cases().to_dict("records")]


@needs_data
@pytest.mark.parametrize("case", _cases())
def test_frozen_case(real, case):
    res = real.resolve(case["query"], case["state"] or None)
    hit, why = locate.score_case(res, case["expected_status"], case["expected_kind"], case["expected_districts"])
    assert hit, why


@needs_data
def test_gazetteer_rows_valid(real):
    p = real.places
    assert set(p["kind"]) == {"pin", "district", "post_office", "city"}
    assert np.isfinite(p[["lat", "lon", "precision_km"]].to_numpy()).all()
    assert geo.in_india_bbox(p["lat"], p["lon"]).all()
    assert (p["precision_km"] >= 2).all()
    assert p["place_id"].is_unique


@needs_data
def test_every_geocoded_pin_resolves(real):
    po = pd.read_parquet(paths.silver_dir() / "post_offices.parquet", columns=["pin", "coord_valid"])
    pins = sorted(set(po.loc[po["coord_valid"], "pin"]))
    bad = [p for p in pins if (r := real.resolve(p)).status != "ok" or r.kind != "pin"]
    assert not bad, f"{len(bad)} PINs fail, for example {bad[:5]}"


@needs_data
def test_latency_under_50ms(real):
    queries = list(locate.load_cases()["query"]) + ["Rampur", "Hydrabad", "qwertyuiop zxcv", "Bengaluru"]
    real.resolve("warm up")
    worst = 0.0
    for q in queries:
        t = time.perf_counter()
        real.resolve(q)
        worst = max(worst, (time.perf_counter() - t) * 1000)
    assert worst < 50, f"slowest query {worst:.1f} ms"


@needs_data
def test_ambiguous_cases_have_no_point(real):
    for q in ("Aurangabad", "Bilaspur", "Hamirpur"):
        r = real.resolve(q)
        assert r.status == "ambiguous" and r.lat is None and 2 <= len(r.candidates) <= locate.MAX_CANDIDATES
        assert all(math.isfinite(c["lat"]) for c in r.candidates)


@needs_data
def test_every_district_name_resolves_to_itself(real):
    """Every district name, typed alone and with its state picked, gives that district, or an
    ambiguous answer that lists it."""
    d = real.places[real.places["kind"] == "district"]
    bad = []
    for name, st, di in zip(d["name"], d["state"], d["district"]):
        for given in (None, st):
            r = real.resolve(name, given)
            got = ({(r.state, r.district)} if r.status == "ok" else
                   {(c["state"], c["district"]) for c in r.candidates} if r.status == "ambiguous" else set())
            if (st, di) not in got:
                bad.append((name, given, r.status, r.state, r.district))
    assert not bad, f"{len(bad)} district names miss, for example {bad[:5]}"


@needs_data
def test_pin_and_office_rows_near_own_district(real):
    """Every PIN and post office row lies within max(200 km, its precision) of its own district's
    centre (200 km is the coord_suspect rule in scripts/wp2_places.py). Island states are exempt:
    their districts span more than 200 km of sea."""
    p = real.places
    p = p[~p["state"].isin(["LAKSHADWEEP", "ANDAMAN AND NICOBAR ISLANDS"])]
    d = p[p["kind"] == "district"].set_index("group")
    x = p[p["kind"].isin(["pin", "post_office"])]
    dd = d.reindex(x["group"].to_numpy())
    km = geo.haversine_km(x["lat"], x["lon"], dd["lat"].to_numpy(), dd["lon"].to_numpy())
    far = x[km > np.fmax(200.0, x["precision_km"].to_numpy())]
    assert far.empty, f"{len(far)} rows far from their district, for example {far['place_id'].head(5).tolist()}"


@needs_data
def test_gazetteer_rows_inside_india_outline(real):
    p = real.places
    out = p[locate.india_border_km(p["lat"], p["lon"]) > locate.BORDER_SLACK_KM]
    assert out.empty, f"{len(out)} rows outside India's outline: {out['place_id'].head(5).tolist()}"


@needs_data
@pytest.mark.parametrize("name,lat,lon", [
    ("Lahore", 31.5204, 74.3587), ("Gujranwala", 32.1877, 74.1945), ("Sialkot", 32.4945, 74.5229),
    ("Kasur", 31.1156, 74.4467), ("Rajshahi", 24.3745, 88.6042), ("Sylhet", 24.8949, 91.8687),
    ("Biratnagar", 26.4525, 87.2718), ("Dhaka", 23.8103, 90.4125), ("Kathmandu", 27.7172, 85.3240),
])
def test_foreign_cities_are_outside_india(real, name, lat, lon):
    assert real.resolve(f"{lat}, {lon}").status == "outside_india", name


@needs_data
@pytest.mark.parametrize("name,lat,lon", [
    ("Amritsar", 31.6340, 74.8723), ("Agartala", 23.8315, 91.2868), ("Port Blair", 11.6234, 92.7265),
    ("Kavaratti", 10.5626, 72.6369), ("Minicoy", 8.2833, 73.0500), ("Jaipur", 26.9124, 75.7873),
    ("Siliguri", 26.7271, 88.3953),
])
def test_indian_border_and_island_points_resolve(real, name, lat, lon):
    assert real.resolve(f"{lat}, {lon}").status == "ok", name


@needs_data
@pytest.mark.parametrize("q,wrong", [
    ("Rampur Chourai", ("UTTAR PRADESH", "RAMPUR")),
    ("Kheri Redan", ("UTTAR PRADESH", "KHERI")),
    ("Hyderabad, Sindh", ("TELANGANA", "HYDERABAD")),
    ("New Delhi", ("WEST BENGAL", "24 PARAGANAS NORTH")),
    ("North Goa", ("DELHI", "NORTH")),
])
def test_review_regressions(real, q, wrong):
    r = real.resolve(q)
    assert not (r.status == "ok" and (r.state, r.district) == wrong), (q, r.how)


@needs_data
def test_village_district_state_after_commas(real):
    r = real.resolve("Singhwara, Darbhanga, Bihar")
    assert r.status == "ok" and r.kind == "post_office" and r.district == "DARBHANGA"
