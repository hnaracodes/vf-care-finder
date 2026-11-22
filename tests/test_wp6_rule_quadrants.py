"""Fast unit tests for scripts/wp6_rule_quadrants.py on toy data (no data files needed)."""
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "wp6_rule_quadrants.py"
_spec = importlib.util.spec_from_file_location("wp6_rule_quadrants", _PATH)
wp6 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wp6)


def test_assign_quadrant_boundaries():
    # access cut 50 km (>= is poor), outcome cut 80% (< is poor)
    access = [50.0, 50.0, 49.9, 49.9, np.nan]
    outcome = [79.9, 80.0, 79.9, 80.0, 90.0]
    q = wp6.assign_quadrant(access, outcome, 50.0, 80.0)
    assert list(q[:4]) == ["care_desert", "data_desert", "cost_or_quality", "served"]
    assert q[4] is None


def test_resolve_cut_quantile_and_fixed():
    v = np.arange(1, 101, dtype=float)
    assert wp6.resolve_cut(("q", 0.5), v) == pytest.approx(50.5)
    assert wp6.resolve_cut(("fixed", 60.0), v) == 60.0


def test_crosswalk_method_by_district_orders_and_skips_unmatched():
    cw = pd.DataFrame({
        "district_key": ["A|x", "A|x", "B|y", "C|z", None, "D|w"],
        "match_method": ["parent_district", "exact", "fuzzy", "manual", "unmatched", "unmatched"],
    })
    m = wp6.crosswalk_method_by_district(cw)
    assert m["A|x"] == "exact+parent_district"
    assert m["B|y"] == "fuzzy"
    assert m["C|z"] == "manual"
    assert "D|w" not in m.index


def test_within_state_diff_removes_state_composition():
    # state S1 has high values, S2 low; a sits mostly in S1, b mostly in S2,
    # but inside each state a is exactly 2 above b
    state = np.array(["S1"] * 4 + ["S2"] * 4)
    is_a = np.array([1, 1, 1, 0, 1, 0, 0, 0], dtype=bool)
    is_b = ~is_a
    x = np.where(state == "S1", 50.0, 10.0) + np.where(is_a, 2.0, 0.0)
    raw = x[is_a].mean() - x[is_b].mean()
    ws, used = wp6.within_state_diff(x, is_a, is_b, state)
    assert used == 2
    assert ws == pytest.approx(2.0)
    assert raw > 10  # the naive difference is dominated by state composition


def test_within_state_diff_ignores_states_with_one_group():
    state = np.array(["S1", "S1", "S2", "S2"])
    is_a = np.array([True, False, True, True])
    is_b = ~is_a
    x = np.array([5.0, 3.0, 100.0, 200.0])
    ws, used = wp6.within_state_diff(x, is_a, is_b, state)
    assert used == 1
    assert ws == pytest.approx(2.0)


def test_within_state_perm_p_detects_and_does_not_invent_effects():
    rng = np.random.default_rng(0)
    state = np.repeat([f"S{i}" for i in range(6)], 20)
    is_a = np.tile(np.r_[np.ones(10, bool), np.zeros(10, bool)], 6)
    base = rng.normal(0, 1, len(state)) + np.repeat(np.arange(6) * 10.0, 20)
    _, p_null, _ = wp6.within_state_perm_p(base, is_a, ~is_a, state, n_perm=2000, seed=1)
    _, p_eff, _ = wp6.within_state_perm_p(base + 3.0 * is_a, is_a, ~is_a, state, n_perm=2000, seed=1)
    assert p_null > 0.05
    assert p_eff < 0.001


def test_reliability_notes_flag_placeholder_districts():
    # A: 376 offices, 358 on one placeholder point -> 19 values; B: 166 offices, 106 on one point -> 61 values;
    # C: no placeholders, 12 offices; D: 100 offices, 40 on placeholder points (under half);
    # E: 7 offices, 1 alone on a placeholder point (the count does not drop)
    d = pd.DataFrame({
        "state_ut": ["S"] * 5,
        "outcome_flag": ["ok"] * 5,
        "crosswalk_method": ["exact"] * 5,
        "acc_n_post_offices": [376, 166, 12, 100, 7],
        "acc_n_post_offices_excluded": [0] * 5,
        "acc_n_post_offices_placeholder": [358, 106, 0, 40, 1],
        "acc_n_placeholder_points": [1, 1, 0, 2, 1],
        "households_surveyed": [900] * 5,
        "label_stability": [1.0] * 5,
    })
    assert wp6.access_values(d).tolist() == [19, 61, 12, 62, 7]
    notes = wp6.reliability_notes(d).tolist()
    assert "access from only 19 post office locations" in notes[0]
    assert "95% of post offices sit on 1 placeholder point" in notes[0]
    assert "64% of post offices sit on 1 placeholder point" in notes[1] and "access from only" not in notes[1]
    assert notes[2] == "access from only 12 post offices"
    assert notes[3] == ""
    assert notes[4] == "access from only 7 post offices"
