"""Unit tests for vf.clean. No data needed; all fast."""
from datetime import date

from vf import clean


def test_clean_str():
    got = [clean.clean_str(v) for v in ["null", "", None, " abc ", "a b"]]
    assert got == [None, None, None, "abc", "a b"]


def test_normalize_text():
    assert clean.normalize_text("  ICU   with\t10\nBeds ") == "icu with 10 beds"
    assert clean.normalize_text("a b") == "a b"  # non-breaking space


def test_is_null_item():
    for v in [None, "", "  ", "null", "Null", "N/A", "na", "None", "nan"]:
        assert clean.is_null_item(v), v
    for v in ["ICU", "0", "No"]:
        assert not clean.is_null_item(v), v


def test_parse_items_keeps_raw_positions():
    v = '["ICU", "", null, "  N/A ", " Ventilator ", "ICU"]'
    assert clean.parse_items(v) == [(0, "ICU"), (4, "Ventilator"), (5, "ICU")]


def test_parse_items_bad_input():
    for v in ["null", "", None, "not json", '{"a": 1}']:
        assert clean.parse_items(v) == []


def test_dedupe():
    assert clean.dedupe(["b", "a", "b", "c", "a"]) == ["b", "a", "c"]


def test_parse_pin():
    assert clean.parse_pin("110002") == "110002"
    assert clean.parse_pin(" 613 007 ") == "613007"
    assert clean.parse_pin("641-602") == "641602"
    assert clean.parse_pin("000000") is None
    assert clean.parse_pin("10") is None
    assert clean.parse_pin("4000050") is None
    assert clean.parse_pin("COCHIN-16") is None
    assert clean.parse_pin("null") is None


def test_parse_int():
    assert clean.parse_int("12") == 12
    assert clean.parse_int("0") == 0
    assert clean.parse_int("12.0") == 12
    assert clean.parse_int("12.5") is None
    assert clean.parse_int("abc") is None
    assert clean.parse_int("null") is None
    assert clean.parse_int("7", lo=1600, hi=2026) is None
    assert clean.parse_int("1991", lo=1600, hi=2026) == 1991


def test_parse_bool():
    assert clean.parse_bool("true") is True
    assert clean.parse_bool("False") is False
    assert clean.parse_bool("null") is None
    assert clean.parse_bool("yes") is None


def test_parse_iso_date():
    assert clean.parse_iso_date("2025-03-01") == date(2025, 3, 1)
    assert clean.parse_iso_date("2 months ago") is None
    assert clean.parse_iso_date("April 2025") is None
    assert clean.parse_iso_date("2025") is None
    assert clean.parse_iso_date("2025-02-30") is None
    assert clean.parse_iso_date("2027-07-20", max_date=date(2026, 11, 21)) is None


def test_url_domain():
    assert clean.url_domain("https://www.Practo.com/x?y=1") == "practo.com"
    assert clean.url_domain("http://in.linkedin.com/company/a") == "in.linkedin.com"
    assert clean.url_domain("aravind.org") == "aravind.org"
    assert clean.url_domain("https://example.com.:443/") == "example.com"
    assert clean.url_domain("") is None
    assert clean.url_domain(None) is None
    assert clean.url_domain("http://[bad") is None


def test_distinct_domains():
    urls = ["https://www.facebook.com/a", "https://facebook.com/b", None, "", "https://justdial.com/c"]
    assert clean.distinct_domains(urls) == ["facebook.com", "justdial.com"]


def test_fact_id_is_stable_and_distinct():
    a = clean.fact_id("f1", "capability", 0)
    assert a == clean.fact_id("f1", "capability", 0)
    assert len(a) == 16
    assert a != clean.fact_id("f1", "capability", 1)
    assert a != clean.fact_id("f1", "procedure", 0)
    assert a != clean.fact_id("f2", "capability", 0)
