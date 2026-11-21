"""Unit tests for the shared loaders.

The first group needs no data. The second group runs only when data/raw exists.
"""
import pytest

from vf import io, paths


def test_is_null():
    assert io.is_null(None)
    assert io.is_null("null")
    assert io.is_null("")
    assert io.is_null("  ")
    assert io.is_null(float("nan"))
    assert not io.is_null("0")
    assert not io.is_null("kie")


def test_parse_list_basic():
    assert io.parse_list('["a","b","a",""]') == ["a", "b", "a"]
    assert io.parse_list('["a","b","a",""]', dedupe=True) == ["a", "b"]


def test_parse_list_bad_input():
    assert io.parse_list("null") == []
    assert io.parse_list("") == []
    assert io.parse_list("not json") == []
    assert io.parse_list('{"a": 1}') == []
    assert io.parse_list(None) == []


def test_to_float():
    assert io.to_float("83.2 ") == 83.2
    assert io.to_float("-4") == -4.0
    assert io.to_float("NA") is None
    assert io.to_float("28.4 32628") is None
    assert io.to_float("null") is None


def test_parse_nfhs_value():
    assert io.parse_nfhs_value("83.2 ") == (83.2, "ok")
    assert io.parse_nfhs_value("(29.5)") == (29.5, "low_sample")
    assert io.parse_nfhs_value("*") == (None, "suppressed")
    assert io.parse_nfhs_value("") == (None, "missing")
    assert io.parse_nfhs_value("abc") == (None, "unparsed")


def test_in_india():
    assert io.in_india(19.36, 79.54)
    assert not io.in_india(0.0, 0.0)
    assert not io.in_india(None, 79.5)


needs_raw = pytest.mark.skipif(
    not io.raw_path("facilities").exists(), reason="data/raw not present"
)


@needs_raw
def test_facilities_shape():
    df = io.read_facilities_raw()
    assert df.shape == (10088, 51)
    counts = io.facility_row_status(df).value_counts().to_dict()
    assert counts == {"ok": 9970, "junk": 88, "truncated": 30}


@needs_raw
def test_facilities_filtered():
    assert len(io.read_facilities()) == 10000
    ok = io.read_facilities(include_truncated=False)
    assert len(ok) == 9970
    assert (ok["source"] == "kie").all()


@needs_raw
def test_pincodes_and_nfhs_shape():
    pins = io.read_pincodes_raw()
    assert pins.shape == (165627, 11)
    assert pins["pincode"].nunique() == 19586
    nfhs = io.read_nfhs_raw()
    assert nfhs.shape == (706, 109)
    flags = {io.parse_nfhs_value(v)[1] for v in nfhs["institutional_birth_5y_pct"]}
    assert "unparsed" not in flags


def test_paths_are_consistent():
    assert paths.raw_dir().name == "raw"
    assert paths.raw_dir().parent == paths.data_dir()
