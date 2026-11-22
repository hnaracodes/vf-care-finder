"""Fast unit tests for vf.geo (no data needed)."""
from vf import geo


def test_norm_name():
    assert geo.norm_name("Khandwa (East Nimar) ") == "KHANDWA EAST NIMAR"
    assert geo.norm_name("Janjgir - Champa") == "JANJGIR CHAMPA"
    assert geo.norm_name("Medchal-Malkajgiri") == "MEDCHAL MALKAJGIRI"
    assert geo.norm_name("North & Middle Andaman") == "NORTH AND MIDDLE ANDAMAN"
    assert geo.norm_name("Y.S.R.") == "Y S R"
    assert geo.norm_name("North  District ") == "NORTH DISTRICT"
    assert geo.norm_name(None) == ""


def test_norm_state_aliases():
    assert geo.norm_state("Maharastra") == geo.norm_state("MAHARASHTRA") == "MAHARASHTRA"
    assert geo.norm_state("NCT of Delhi") == geo.norm_state("DELHI")
    assert geo.norm_state("Dadra and Nagar Haveli & Daman and Diu") == geo.norm_state(
        "THE DADRA AND NAGAR HAVELI AND DAMAN AND DIU"
    )
    assert geo.norm_state(" Lakshadweep ") == "LAKSHADWEEP"
    assert geo.norm_state("Jammu & Kashmir") == geo.norm_state("JAMMU AND KASHMIR")


def test_district_key_strips_whitespace_only():
    assert geo.district_key(" Lakshadweep ", "Lakshadweep ") == "Lakshadweep|Lakshadweep"
    assert geo.district_key("Maharastra", "Bid ") == "Maharastra|Bid"
    assert geo.district_key("Sikkim", "North  District ") == "Sikkim|North  District"


def test_looks_truncated():
    assert geo.looks_truncated("Sri Potti Sriramulu Nello ")
    assert not geo.looks_truncated("Anantapur ")


def test_name_score_variants():
    assert geo.name_score("KUSHI NAGAR", "KUSHINAGAR") == (100.0, "no_space")
    assert geo.name_score("DINAJPUR UTTAR", "UTTAR DINAJPUR")[0] == 100.0
    assert geo.name_score("SRI POTTI SRIRAMULU NELLORE", "SRI POTTI SRIRAMULU NELLO", True) == (
        100.0,
        "prefix",
    )
    # prefix only applies when the NFHS name looks truncated
    assert geo.name_score("SRI POTTI SRIRAMULU NELLORE", "SRI POTTI SRIRAMULU NELLO", False)[1] != "prefix"


def test_name_score_containment():
    assert geo.name_score("EAST NIMAR", "KHANDWA EAST NIMAR") == (geo.CONTAINMENT_SCORE, "contained")
    assert geo.name_score("LAKSHADWEEP DISTRICT", "LAKSHADWEEP")[1] == "contained"
    # generic-only overlap is not containment
    assert geo.name_score("NORTH", "NORTH GOA")[1] != "contained"


def test_match_one_to_one():
    res = geo.match_within_state(
        ["PASCHIM BARDHAMAN", "PURBA BARDHAMAN"],
        {"PASCHIM BARDDHAMAN": False},
        threshold=75,
        margin=5,
    )
    by = {r["pin_name"]: r for r in res}
    assert by["PASCHIM BARDHAMAN"]["nfhs_name"] == "PASCHIM BARDDHAMAN"
    assert by["PURBA BARDHAMAN"]["nfhs_name"] is None


def test_match_tie_is_ambiguous():
    res = geo.match_within_state(
        ["WARANGAL"], {"WARANGAL RURAL": False, "WARANGAL URBAN": False}, threshold=75, margin=5
    )
    assert res[0]["status"] == "ambiguous"
    assert res[0]["nfhs_name"] is None


def test_match_below_threshold_and_empty():
    res = geo.match_within_state(["NUH"], {"MEWAT": False}, threshold=75, margin=5)
    assert res[0]["status"] == "below_threshold"
    res = geo.match_within_state(["KAMLE"], {}, threshold=75, margin=5)
    assert res[0]["status"] == "no_candidates"


# ---------------------------------------------------------------------------
# coordinates
# ---------------------------------------------------------------------------

import math  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402


def test_haversine_and_bbox():
    one_deg = 2 * math.pi * geo.EARTH_RADIUS_KM / 360
    assert float(geo.haversine_km(0, 0, 0, 1)) == pytest.approx(one_deg)
    assert np.isnan(geo.haversine_km(np.nan, 78, 20, 78))
    assert geo.in_india_bbox([20.0, 78.0, np.nan], [78.0, 20.0, 78.0]).tolist() == [True, False, False]


def test_group_centres_prefers_head_and_sub_offices():
    # group A: 3 head/sub offices at (22, 73), 5 branch offices far away at (16, 73)
    lat = [22.0] * 3 + [16.0] * 5 + [10.0, 10.2]
    lon = [73.0] * 8 + [77.0, 77.2]
    town = [True] * 3 + [False] * 5 + [True, False]
    grp = ["A"] * 8 + ["B", "B"]
    c = geo.group_centres(lat, lon, town, grp)
    assert c.loc["A", "basis"] == "head_sub" and c.loc["A", "lat"] == 22.0
    assert c.loc["B", "basis"] == "all" and c.loc["B", "lat"] == pytest.approx(10.1)


def _raw_directory(rows):
    """Toy PIN directory: rows of (pin, office, type, state, district, division, lat, lon) as raw strings."""
    cols = ["pincode", "officename", "officetype", "statename", "district", "divisionname", "latitude", "longitude"]
    return pd.DataFrame([[str(x) for x in r] for r in rows], columns=cols)


def test_post_office_coords_repairs_swaps_only_among_the_districts_offices():
    rows = [(400001 + i, f"Town{i} S.O", "PO", "S", "D", "Div", 18.0 + 0.01 * i, 83.5) for i in range(4)]
    rows += [
        (400010, "Near B.O", "BO", "S", "D", "Div", 83.52, 18.03),    # swapped, lands among D's offices
        (400011, "Sea B.O", "BO", "S", "D", "Div", 82.9, 18.0),       # swapped, 63 km away from any office
        (400012, "Far B.O", "BO", "S", "D", "Div", 77.0, 28.0),       # swapped, lands far from D
        (400013, "Lost B.O", "BO", "NA", "NA", "Div", 83.5, 18.0),    # swapped, no district centre
        (400014, "Bad B.O", "BO", "S", "D", "Div", "NA", "NA"),
    ]
    out, st = geo.post_office_coords(_raw_directory(rows))
    assert out["coord_swap_repaired"].tolist() == [False] * 4 + [True, False, False, False, False]
    assert out.loc[4, ["lat", "lon"]].tolist() == [18.03, 83.52]
    assert out["coord_valid"].tolist() == [True] * 5 + [False] * 4
    assert out.loc[5:, "lat"].isna().all()
    assert (st["coord_swapped_detected"], st["coord_swapped_repaired"], st["coord_swapped_left_null"]) == (4, 1, 3)


def _toy_reference():
    # district X around (20, 78), PIN 411001 (offices XTOWN); district Y around (26, 80), PIN 226001 (YCITY)
    rows = [(411001, f"Xtown{s} S.O", "PO", "STATE1", "XDIST", "Xtown Division", 20.0 + d, 78.0 + d)
            for s, d in (("", 0.0), (" East", 0.01), (" West", -0.01), (" Bazar", 0.02))]
    rows += [(226001, f"Ycity{s} S.O", "PO", "STATE2", "YDIST", "Ycity Division", 26.0 + d, 80.0 + d)
             for s, d in (("", 0.0), (" East", 0.01), (" West", -0.01), (" Bazar", 0.02))]
    # district Z about 60 km north of X, PIN 411050 (offices ZTOWN): inside the 40 to 100 km band
    rows += [(411050, f"Ztown{s} S.O", "PO", "STATE1", "ZDIST", "Ztown Division", 20.55 + d, 78.0 + d)
             for s, d in (("", 0.0), (" East", 0.01), (" West", -0.01), (" Bazar", 0.02))]
    # a PIN whose only sub office is geocoded 800 km away (not "placed"): falls back to its district centre
    rows += [(411099, "Misplaced S.O", "PO", "STATE1", "XDIST", "Xtown Division", 26.5, 80.5)]
    raw = _raw_directory(rows)
    coords, _ = geo.post_office_coords(raw)
    return geo.PinReference(raw, coords)


def test_pin_reference_locations():
    ref = _toy_reference()
    loc = ref.location(["411001", "411099", "999999"])
    assert loc.iloc[0]["basis"] == "head_sub" and loc.iloc[0]["lat"] == pytest.approx(20.005)
    # district centre: median of XDIST's five sub offices, the misplaced one included
    assert loc.iloc[1]["basis"] == "district_centre" and loc.iloc[1]["lat"] == pytest.approx(20.01)
    assert np.isnan(loc.iloc[2]["lat"])
    assert ref.district_at(26.0, 80.0) == "STATE2|YDIST"
    assert "XTOWN" in ref.names_of_pin("411001") and "YCITY" in ref.names_near(26.0, 80.0)


def test_check_facility_locations_rule():
    ref = _toy_reference()
    # (lat, lon, pin, city) -> expected coord_check
    cases = [
        (20.01, 78.01, "411001", "Anywhere", "ok"),
        (26.0, 80.0, "411001", "Xtown", "moved_to_pin"),            # city names the PIN's place
        (26.0, 80.0, "411001", "Ycity", "kept_city_matches_coords"),  # city names the point's place
        (26.0, 80.0, "411001", "Elsewhere", "excluded_unresolved"),   # names neither
        (26.0, 80.0, None, "Xtown", "no_pin_check"),
        (26.0, 80.0, "999999", "Xtown", "no_pin_check"),
        (np.nan, np.nan, "411001", "Xtown", "missing"),
        (26.0, 80.0, "411099", "Xtown", "moved_to_pin"),            # misplaced PIN office does not hide the conflict
    ]
    lat, lon, pin, city, want = zip(*cases)
    out = geo.check_facility_locations(lat, lon, pin, city, ref)
    assert out["coord_check"].tolist() == list(want)
    assert out.loc[1, "lat"] == pytest.approx(20.005)          # moved to the PIN's location
    assert out.loc[2, ["lat", "lon"]].tolist() == [26.0, 80.0]  # kept
    assert np.isnan(out.loc[3, "lat"]) and np.isnan(out.loc[6, "lat"])
    assert out.loc[0, "coord_pin_km"] < geo.FACILITY_PIN_KM < out.loc[1, "coord_pin_km"]


def test_check_facility_locations_band_between_check_km_and_far_km():
    # PIN 411001 sits at (20.005, 78.005); (20.55, 78.0) is about 60 km away in district Z
    ref = _toy_reference()
    cases = [
        (20.3, 78.0, "411001", "Ztown", "ok"),                       # 33 km: within the check distance
        (20.55, 78.0, "411001", "Xtown", "moved_to_pin"),            # 60 km, city names only the PIN's place
        (20.55, 78.0, "411001", "Ztown", "kept_city_matches_coords"),  # names only the point's place
        (20.55, 78.0, "411001", "Elsewhere", "kept_unresolved"),     # names neither: kept, not nulled, under 100 km
        (19.55, 78.0, "411001", "Elsewhere", "ok_same_district"),    # 50 km, but still in the PIN's district
    ]
    lat, lon, pin, city, want = zip(*cases)
    out = geo.check_facility_locations(lat, lon, pin, city, ref)
    assert out["coord_check"].tolist() == list(want)
    d = out["coord_pin_km"]
    assert d[0] < geo.FACILITY_PIN_CHECK_KM < d[1:].min() and d.max() < geo.FACILITY_PIN_KM
    assert out.loc[1, ["lat", "lon"]].tolist() == pytest.approx([20.005, 78.005])  # moved to the PIN
    for i in (0, 2, 3, 4):                                       # every other point is kept as scraped
        assert out.loc[i, ["lat", "lon"]].tolist() == [lat[i], lon[i]]
    # the old rule (act only beyond 100 km) leaves the whole band alone
    old = geo.check_facility_locations(lat, lon, pin, city, ref, check_km=geo.FACILITY_PIN_KM)
    assert old["coord_check"].tolist() == ["ok"] * 5


def test_placeholder_points():
    # 10 offices of 3 PINs on one point (placeholder); 12 offices of one PIN on another (not: one PIN);
    # 9 offices of 3 PINs on a third (not: too few); one office with no coordinates
    lat = [25.0] * 10 + [26.0] * 12 + [27.0] * 9 + [np.nan]
    lon = [85.0] * 10 + [86.0] * 12 + [87.0] * 9 + [np.nan]
    pin = ["1", "2", "3"] + ["1"] * 7 + ["4"] * 12 + ["5", "6", "7"] * 3 + ["8"]
    out = geo.placeholder_points(lat, lon, pin)
    assert out["placeholder"].tolist() == [True] * 10 + [False] * 22
    assert out.loc[0, ["n_at_point", "pins_at_point"]].tolist() == [10, 3]
    assert out.loc[10, ["n_at_point", "pins_at_point"]].tolist() == [12, 1]
    assert out.loc[31, ["n_at_point", "pins_at_point"]].tolist() == [0, 0]


def test_city_keys_and_names_match():
    assert geo.city_keys("Bangalore") == {"BANGALORE", "BENGALURU"}
    assert geo.city_keys("NA") == set() and geo.city_keys(None) == set()
    assert geo.names_match({"AGRA"}, {"AGRA CANTT"})
    assert geo.names_match({"TUMAKURU"}, {"TUMAKURU"})
    assert not geo.names_match({"AGRA"}, {"NAGPUR"})
    assert not geo.names_match(set(), {"AGRA"})
