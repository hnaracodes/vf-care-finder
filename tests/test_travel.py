"""Drive-time estimate (vf.travel, score spec v1-final D048)."""
from __future__ import annotations

import math

import numpy as np
import pytest

from vf import paths, travel as T


def test_formula_and_constants():
    assert (T.A_MIN, T.B_EXP, T.BAND_LOG, T.LOG_SD) == (2.30, 0.842, 0.52, 0.26)
    assert T.travel_minutes(30, "BIHAR") == pytest.approx(1.02 * 2.30 * 30 ** 0.842)
    assert T.travel_minutes(0, "BIHAR") == 0.0
    assert T.travel_minutes(10, None) == pytest.approx(2.30 * 10 ** 0.842)  # unknown state: factor 1.00
    arr = T.travel_minutes(np.array([1.0, 10.0, np.nan]), "KERALA")
    assert arr[0] == pytest.approx(0.83 * 2.30) and math.isnan(arr[2])


def test_minutes_rise_with_km_for_one_origin():
    km = np.linspace(0, 300, 301)
    for st in ("DELHI", "ARUNACHAL PRADESH", "RAJASTHAN"):
        t = T.travel_minutes(km, st)
        assert (np.diff(t) > 0).all()


def test_band_is_95_percent_on_log_scale():
    lo, hi = T.band(40.0)
    assert lo == pytest.approx(40 * math.exp(-0.52)) and hi == pytest.approx(40 * math.exp(0.52))
    assert T.band(None) == (None, None)


def test_islands_have_no_minutes():
    for st in ("LAKSHADWEEP", "ANDAMAN AND NICOBAR ISLANDS", "Andaman & Nicobar Islands"):
        f = T.state_factor(st)
        assert f.sea and f.value is None and T.SEA_NOTE in f.text
        assert T.travel_minutes(20, st) is None
        assert T.window_km(120, st) is None
    assert np.isnan(T.travel_minutes(np.array([5.0]), "LAKSHADWEEP")).all()
    assert "sea travel not modelled" in T.minutes_label(None, None, None, sea=True)


def test_state_names_and_aliases():
    assert T.norm_state("Uttar Pradesh") == "UTTAR PRADESH"
    assert T.norm_state("Orissa") == "ODISHA"
    assert T.norm_state("Jammu & Kashmir") == "JAMMU AND KASHMIR"
    assert T.norm_state("Dadra and Nagar Haveli and Daman and Diu") == "THE DADRA AND NAGAR HAVELI AND DAMAN AND DIU"
    assert T.norm_state("null") is None and T.norm_state("Atlantis") is None
    assert T.state_factor("Atlantis").value == 1.0
    assert T.state_factor("ARUNACHAL PRADESH", terrain=False).value == 1.0
    assert T.state_factor("LAKSHADWEEP", terrain=False).sea


def test_table_matches_reference_csv():
    p = paths.code_root() / "reference" / "terrain_multiplier.csv"
    if not p.exists():
        pytest.skip("reference table absent")
    assert T.read_factor_table(p) == T._FACTORS_BUILTIN
    assert T.factor_table() == T._FACTORS_BUILTIN


def test_window_km_inverts_the_formula():
    for st in ("DELHI", "BIHAR", "ARUNACHAL PRADESH"):
        for w in (60, 90, 120, 240):
            km = T.window_km(w, st)
            assert T.travel_minutes(km, st) == pytest.approx(w)
    # the amendment draft's table (reports/eval_sealed_amendment_reach_draft.md): emoc 120 min
    assert round(T.window_km(120, factor=0.80)) == 143
    assert round(T.window_km(120, factor=1.00)) == 110
    assert round(T.window_km(120, factor=3.08)) == 29


def test_loose_origin_rule_and_range():
    f = 1.0
    k = T.window_km(60, factor=f)  # about 48 km
    assert not T.origin_is_loose(k / 4 - 0.1, 60, f)
    assert T.origin_is_loose(k / 4 + 0.1, 60, f)
    assert not T.origin_is_loose(None, 60, f)
    t, lo, hi = T.minutes_range(20.0, f, 15.0, loose=True)
    assert lo == pytest.approx(T.travel_minutes(5.0, factor=f) * math.exp(-0.52))
    assert hi == pytest.approx(T.travel_minutes(35.0, factor=f) * math.exp(0.52))
    lab = T.minutes_label(t, lo, hi, loose=True, place="Jaipur")
    assert "depends on where in Jaipur you start" in lab and "real trips take longer" in lab
    t2, lo2, hi2 = T.minutes_range(20.0, f)
    assert (lo2, hi2) == pytest.approx(T.band(t2))


def test_labels_are_plain():
    assert T.fmt_minutes(8.4) == "8 min" and T.fmt_minutes(23) == "25 min" and T.fmt_minutes(95) == "1 h 35 min"
    assert T.fmt_minutes(0.2) == "1 min" and T.fmt_minutes(240) == "4 h"
    assert T.fmt_hours(60) == "1 hour" and T.fmt_hours(120) == "2 hours" and T.fmt_hours(90) == "1.5 hours"
    lab = T.minutes_label(25.0, 15.0, 42.0)
    assert lab == "about 25 min estimated drive, free-flow; real trips take longer"
    assert "—" not in T.MODEL_TEXT and "0.26" in T.MODEL_TEXT and "934" in T.MODEL_TEXT
    # a place wider than the window reaches: no multi-hour range, a plain "unclear" line
    lab = T.minutes_label(25.0, 1.0, 370.0, loose=True, place="Kotputli", span_km=327)
    assert lab.startswith("drive time unclear: Kotputli spans about 327 km") and " h " not in lab


def test_origin_factor_uses_state_then_lookup():
    assert T.origin_factor({"state": "ASSAM", "lat": 1, "lon": 2}).value == 1.11
    f = T.origin_factor({"state": None, "lat": 27.0, "lon": 93.0}, lambda la, lo: "ARUNACHAL PRADESH")
    assert f.value == 3.08 and f.source == "state table"
    assert T.origin_factor({"lat": 1.0, "lon": 2.0}).source.startswith("state unknown")
