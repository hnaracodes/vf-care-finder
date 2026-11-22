"""Fast unit test for the NFHS duplicate-row drop in scripts/wp2_geography_nfhs.py (toy data)."""
import importlib.util
from pathlib import Path

import pandas as pd
import pytest

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "wp2_geography_nfhs.py"
_spec = importlib.util.spec_from_file_location("wp2_geography_nfhs", _PATH)
wp2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wp2)


def _toy(copy_ok=True):
    keys = ["Manipur|Chandel", "Mizoram|Chandel", "Mizoram|Aizawl"]
    vals = pd.DataFrame({"district_key": keys, "state_ut": ["Manipur", "Mizoram", "Mizoram"],
                         "district_name": ["Chandel", "Chandel", "Aizawl"],
                         "x": [1.0, 1.0 if copy_ok else 2.0, 3.0], "y": [None, None, 5.0]})
    flags = vals[["district_key", "state_ut", "district_name"]].assign(x="ok", y="suppressed")
    return vals, flags


def test_drop_duplicate_rows_removes_the_copy_from_both_tables():
    vals, flags = _toy()
    v, f, info = wp2.drop_duplicate_rows(vals, flags, ["x", "y"])
    assert list(v["district_key"]) == list(f["district_key"]) == ["Manipur|Chandel", "Mizoram|Aizawl"]
    assert info["Mizoram|Chandel"]["copy_of"] == "Manipur|Chandel"


def test_drop_duplicate_rows_refuses_a_row_that_is_no_longer_a_copy():
    vals, flags = _toy(copy_ok=False)
    with pytest.raises(SystemExit):
        wp2.drop_duplicate_rows(vals, flags, ["x", "y"])
