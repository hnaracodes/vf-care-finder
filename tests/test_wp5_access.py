"""Fast unit tests for scripts/wp5_access.py on toy data (no data files needed)."""
import importlib.util
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "wp5_access.py"
_spec = importlib.util.spec_from_file_location("wp5_access", _PATH)
wp5 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wp5)


def test_haversine_one_degree_on_equator():
    expected = 2 * math.pi * wp5.EARTH_RADIUS_KM / 360
    assert wp5.manual_haversine_km(0, 0, 0, 1) == pytest.approx(expected, rel=1e-12)
    assert float(wp5.haversine_km(0, 0, 0, 1)) == pytest.approx(expected, rel=1e-12)


def test_vectorized_and_manual_haversine_agree():
    rng = np.random.default_rng(0)
    a = rng.uniform([8, 68], [36, 97], size=(50, 2))
    b = rng.uniform([8, 68], [36, 97], size=(50, 2))
    vec = wp5.haversine_km(a[:, 0], a[:, 1], b[:, 0], b[:, 1])
    man = [wp5.manual_haversine_km(*p, *q) for p, q in zip(a, b)]
    np.testing.assert_allclose(vec, man, rtol=0, atol=1e-9)


def test_nearest_matches_brute_force():
    rng = np.random.default_rng(1)
    fac = rng.uniform([8, 68], [36, 97], size=(200, 2))
    ids = np.array([f"f{i:03d}" for i in range(200)], dtype=object)
    q = rng.uniform([8, 68], [36, 97], size=(300, 2))
    d, near = wp5.nearest(fac[:, 0], fac[:, 1], ids, q[:, 0], q[:, 1])
    for j in range(len(q)):
        brute = [wp5.manual_haversine_km(q[j, 0], q[j, 1], a, b) for a, b in fac]
        assert d[j] == pytest.approx(min(brute), abs=1e-6)
        assert near[j] == ids[int(np.argmin(brute))]


def test_nearest_empty_set_gives_nan():
    d, near = wp5.nearest([], [], [], [20.0, 21.0], [78.0, 79.0])
    assert np.isnan(d).all()
    assert list(near) == [None, None]


def _toy_qsets(grades_by_line):
    """grades_by_line: {line: [(facility_id, lat, lon, grade), ...]}"""
    qsets = {}
    for line in wp5.SERVICE_LINES:
        rows = pd.DataFrame(grades_by_line.get(line, []), columns=["facility_id", "lat", "lon", "grade"])
        for k in wp5.MIN_GRADES:
            qsets[(line, k)] = rows[rows["grade"] >= k].sort_values("facility_id").reset_index(drop=True)
    return qsets


def test_distance_is_monotone_in_grade():
    rng = np.random.default_rng(2)
    fac = [(f"f{i}", *rng.uniform([8, 68], [36, 97]), int(rng.integers(1, 4))) for i in range(60)]
    for g in (1, 2, 3):  # every grade present at least once
        fac.append((f"x{g}", 20.0, 78.0, g))
    qsets = _toy_qsets({line: fac for line in wp5.SERVICE_LINES})
    po = pd.DataFrame(rng.uniform([8, 68], [36, 97], size=(400, 2)), columns=["lat", "lon"])
    po.insert(0, "po_id", range(len(po)))
    po["district_key"] = "S|D"
    po["coord_suspect"] = False
    po["coord_placeholder"] = False
    out = wp5.build_po_access(po, qsets)
    for line in wp5.SERVICE_LINES:
        d1, d2, d3 = (out[wp5.dist_col(line, k)] for k in wp5.MIN_GRADES)
        assert (d1 <= d2).all() and (d2 <= d3).all()


def test_coord_check_centres_on_head_and_sub_offices():
    # District A: 4 sub offices and 2 branch offices at the right place, 7
    # branch offices geocoded about 700 km away (a majority, so a median over
    # all offices would land in the wrong cluster).
    right, wrong = (22.3, 73.2), (16.0, 73.5)
    rows = [("PO", *right)] * 4 + [("BO", *right)] * 2 + [("BO", *wrong)] * 7
    po = pd.DataFrame(rows, columns=["office_type", "lat", "lon"])
    po["district_key"] = "S|A"
    po["state_pin"] = "S"
    po["district_pin"] = "A"
    out = wp5.coord_check(po)
    assert out["coord_suspect"].tolist() == [False] * 6 + [True] * 7


def test_coord_check_groups_unkeyed_offices_by_pin_district_and_skips_na():
    po = pd.DataFrame(
        {
            "office_type": ["PO", "PO", "PO", "BO", "BO", "BO"],
            "lat": [23.2, 23.25, 23.3, 23.2, 12.0, 30.0],
            "lon": [87.9, 87.95, 88.0, 87.9, 77.0, 70.0],
            "district_key": [None] * 6,
            "state_pin": ["WEST BENGAL"] * 5 + ["NA"],
            "district_pin": ["PURBA BARDHAMAN"] * 5 + ["NA"],
        }
    )
    out = wp5.coord_check(po)
    assert out["coord_suspect"].tolist() == [False, False, False, False, True, False]
    assert out["check_group"].isna().tolist() == [False] * 5 + [True]


def test_district_access_aggregates():
    qsets = _toy_qsets({line: [("f1", 20.0, 78.0, 3), ("f2", 25.0, 80.0, 1)] for line in wp5.SERVICE_LINES})
    po_access = pd.DataFrame(
        {
            "po_id": [1, 2, 3, 4, 5, 6],
            "district_key": ["S|A", "S|A", "S|A", "S|A", "S|B", None],
            "lat": [20.0, 20.1, 20.2, 20.3, 21.0, 22.0],
            "lon": [78.0] * 6,
            "coord_suspect": [False, False, False, True, False, False],
            "coord_placeholder": [False] * 6,
        }
    )
    dists = [10.0, 40.0, 70.0, 999.0, 5.0, 1.0]
    for line in wp5.SERVICE_LINES:
        for k in wp5.MIN_GRADES:
            po_access[wp5.dist_col(line, k)] = dists
    fac_district = pd.DataFrame({"facility_id": ["f1", "f2"], "district_key": ["S|A", "S|B"]})
    out = wp5.build_district_access(po_access, fac_district, qsets)
    assert len(out) == 2 * len(wp5.SERVICE_LINES) * len(wp5.MIN_GRADES)
    a1 = out[(out.district_key == "S|A") & (out.service_line == "emoc") & (out.min_grade == 1)].iloc[0]
    assert a1.n_post_offices == 3 and a1.n_post_offices_excluded == 1
    assert a1.n_post_offices_placeholder == 0 and a1.n_placeholder_points == 0
    assert a1.dist_km_p50 == pytest.approx(40.0)
    assert a1.dist_km_p90 == pytest.approx(64.0)  # linear between 40 and 70
    assert a1.share_within_30km == pytest.approx(1 / 3)
    assert a1.share_within_60km == pytest.approx(2 / 3)
    assert a1.n_facilities_in_district == 1
    b2 = out[(out.district_key == "S|B") & (out.service_line == "icu") & (out.min_grade == 2)].iloc[0]
    assert b2.n_facilities_in_district == 0  # f2 is grade 1 only
    assert list(out.columns) == wp5.DISTRICT_ACCESS_SCHEMA.names


def test_facility_district_majority_vote():
    ref = pd.DataFrame(
        {
            "lat": [20.00, 20.01, 20.02, 20.03, 20.04, 25.0],
            "lon": [78.00, 78.00, 78.00, 78.00, 78.00, 80.0],
            "district_key": ["S|A", "S|B", "S|B", "S|B", "S|A", "S|C"],
        }
    )
    fac = pd.DataFrame({"facility_id": ["f1"], "lat": [20.0], "lon": [78.0]})
    out = wp5.assign_facility_districts(fac, ref)
    assert out.loc[0, "district_key"] == "S|B"  # 3 of 5 votes, though the nearest office is in A
    assert out.loc[0, "district_votes"] == 3


def test_facilities_lost_splits_pin_check_exclusions_and_moves():
    fac = pd.DataFrame({
        "facility_id": ["a", "b", "c", "d"],
        "row_status": ["ok", "ok", "truncated", "ok"],
        "coord_valid": [True, False, False, True],
        "coord_check": ["ok", "excluded_unresolved", "missing", "moved_to_pin"],
    })
    grades = pd.DataFrame([(f, line, 2) for f in "abcd" for line in wp5.SERVICE_LINES],
                          columns=["facility_id", "service_line", "grade"])
    lost = wp5.facilities_lost(grades, fac)["emoc"]["2"]
    assert lost == {"graded": 4, "with_coords": 2, "lost_no_coords": 2, "lost_truncated_rows": 1,
                    "lost_excluded_by_pin_check": 1, "moved_to_pin": 1}


def test_placeholder_points_count_once_per_district():
    # District A: 2 offices on their own points (10 and 50 km), 8 offices on one placeholder point (1 km),
    # 1 more placeholder office on that point but suspect. District B shares the same placeholder point
    # with 3 offices, plus one office of its own (90 km).
    qsets = _toy_qsets({line: [("f1", 20.0, 78.0, 3)] for line in wp5.SERVICE_LINES})
    rows = [("S|A", 20.5, 78.5, 10.0, False, False), ("S|A", 20.6, 78.6, 50.0, False, False)]
    rows += [("S|A", 25.0, 85.0, 1.0, False, True)] * 8 + [("S|A", 25.0, 85.0, 1.0, True, True)]
    rows += [("S|B", 25.0, 85.0, 1.0, False, True)] * 3 + [("S|B", 26.0, 86.0, 90.0, False, False)]
    po_access = pd.DataFrame(rows, columns=["district_key", "lat", "lon", "d", "coord_suspect", "coord_placeholder"])
    po_access.insert(0, "po_id", range(len(po_access)))
    for line in wp5.SERVICE_LINES:
        for k in wp5.MIN_GRADES:
            po_access[wp5.dist_col(line, k)] = po_access["d"]
    fac_district = pd.DataFrame({"facility_id": ["f1"], "district_key": ["S|A"]})
    out = wp5.build_district_access(po_access, fac_district, qsets)
    a = out[(out.district_key == "S|A") & (out.service_line == "emoc") & (out.min_grade == 2)].iloc[0]
    # 10 used offices, 8 on one placeholder point: the percentiles rest on 10, 50 and the point's 1 km
    assert (a.n_post_offices, a.n_post_offices_excluded) == (10, 1)
    assert (a.n_post_offices_placeholder, a.n_placeholder_points) == (8, 1)
    assert a.dist_km_p50 == pytest.approx(10.0)  # counted per office it would be 1.0
    assert a.share_within_30km == pytest.approx(2 / 3)
    b = out[(out.district_key == "S|B") & (out.service_line == "emoc") & (out.min_grade == 2)].iloc[0]
    assert (b.n_post_offices, b.n_post_offices_placeholder, b.n_placeholder_points) == (4, 3, 1)
    assert b.dist_km_p50 == pytest.approx(45.5)  # median of 1 and 90
    # rollup_offices keeps one row per district and placeholder point, and every non-placeholder office
    roll = wp5.rollup_offices(po_access)
    assert len(roll) == 2 + 1 + 1 + 1
    # the national summary uses the same rows
    lost = {line: {str(k): {"graded": 1, "with_coords": 1, "lost_no_coords": 0} for k in wp5.MIN_GRADES}
            for line in wp5.SERVICE_LINES}
    rec = wp5.national_summary(po_access, lost)[0]
    assert rec["n_values"] == 5 and rec["n_post_offices"] == 14 and rec["n_post_offices_placeholder"] == 11
    assert rec["median_km"] == pytest.approx(10.0)
    assert rec["placeholders_counted_per_office"]["median_km"] == pytest.approx(1.0)
