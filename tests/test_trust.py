"""WP11 trust check tests: pure functions on hand-made rows, then a real-data
check of the written flags (skipped when the shared data folder lacks them)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from vf import paths, trust

STATES = {"TAMIL NADU", "UTTAR PRADESH", "DELHI", "HARYANA", "TELANGANA", "ANDHRA PRADESH", "KARNATAKA",
          "MADHYA PRADESH", "MAHARASHTRA", "KERALA", "GUJARAT", "PRADESH"}


# --- location conflict -------------------------------------------------------

def test_state_of_text():
    assert trust.state_of_text("Tamilnadu", STATES) == "TAMIL NADU"
    assert trust.state_of_text("Delhi/ncr", STATES) == "DELHI"
    assert trust.state_of_text("Ghaziabad Uttar Pradesh", STATES) == "UTTAR PRADESH"
    assert trust.state_of_text("Thane", STATES) is None          # a city, not a state
    assert trust.state_of_text("Tamil Nadu Karnataka", STATES) is None  # two states
    assert trust.state_of_text(None, STATES) is None


def test_states_compatible():
    assert trust.states_compatible("ANDHRA PRADESH", "TELANGANA")
    assert trust.states_compatible("DELHI", "HARYANA")
    assert not trust.states_compatible("KERALA", "TAMIL NADU")


def test_location_from_coord_check():
    f = trust.location_conflict("excluded_unresolved", 339.2, "628002", city_raw="Thoothukudi")
    assert (f["severity"], f["signal"]) == ("major", "coord_excluded") and "339 km" in f["reason"]
    assert f["quote"] == "Thoothukudi"
    f = trust.location_conflict("kept_unresolved", 74.0, "380008", city_raw="Kapadvanj")
    assert (f["severity"], f["signal"]) == ("major", "coord_unresolved")
    f = trust.location_conflict("moved_to_pin", 125.0, "400016", city_raw="Mumbai")
    assert (f["severity"], f["signal"]) == ("minor", "moved_to_pin")
    assert trust.location_conflict("kept_city_matches_coords", 500.0, "530040", city_raw="Hyderabad") is None
    assert trust.location_conflict("ok", 1.0, "560034") is None
    assert trust.location_conflict("missing", None, None) is None


def test_location_city_mismatch_minor_when_point_agrees_with_pin():
    f = trust.location_conflict("ok", 2.0, "396445", "Gujarat", "Vadodara", "GUJARAT", {"GUJARAT"},
                                city_agrees_pin=False, city_other_districts={"GUJARAT|VADODARA"},
                                pin_district="GUJARAT|NAVSARI")
    assert (f["severity"], f["signal"], f["quote"]) == ("minor", "city_mismatch", "Vadodara")
    assert "Navsari" in f["reason"] and "Vadodara" in f["reason"]


def test_location_city_names_pin_district_is_not_a_conflict():
    # the city text's only candidate district is the PIN's own
    assert trust.location_conflict("ok", 2.0, "600020", "Tamil Nadu", "Vadapalani", "TAMIL NADU", {"TAMIL NADU"},
                                   city_agrees_pin=False, city_other_districts={"TAMIL NADU|CHENNAI"},
                                   pin_district="TAMIL NADU|CHENNAI") is None


def test_location_state_typo_with_agreeing_city_is_not_flagged():
    # "Cloudnine Hospital, Hyderabad, Chhattisgarh": city and PIN agree, the state is a typo
    assert trust.location_conflict("ok", 1.7, "500034", "Chhattisgarh", "Hyderabad", "MADHYA PRADESH",
                                   {"TELANGANA"}, city_agrees_pin=True, pin_district="TELANGANA|HYDERABAD") is None


def test_location_state_mismatch_and_compatible_states():
    f = trust.location_conflict("ok", 0.4, "673017", "Maharashtra", "Nagpur", "MAHARASHTRA", {"KERALA"},
                                city_agrees_pin=False, city_other_districts={"MAHARASHTRA|NAGPUR"},
                                pin_district="KERALA|KOZHIKODE")
    assert f["signal"] == "state_mismatch+city_mismatch" and f["quote"] == "Maharashtra"
    # Hyderabad written as Andhra Pradesh: an old page, not a conflict
    assert trust.location_conflict("ok", 1.0, "500055", "Andhra Pradesh", "Xyz", "ANDHRA PRADESH",
                                   {"TELANGANA"}, city_agrees_pin=None) is None


# --- possible merge ------------------------------------------------------------

@pytest.mark.parametrize("name, second", [
    ("Indo-US Super Speciality Hospital & Virinchi Hospital", "Virinchi Hospital"),
    ("Asha Poly Clinic & Sheetal Nursing Home", "Sheetal Nursing Home"),
    ("AMC Poly Clinic / Dr Selvam's Sugar Clinic", "Dr Selvam's Sugar Clinic"),
    ("Vasudev Multispeciality Hospital and Apollo Dialysis Clinic", "Apollo Dialysis Clinic"),
])
def test_name_joins_two_positive(name, second):
    assert trust.name_joins_two(name) == second


@pytest.mark.parametrize("name", [
    "Sanjivani Hospital & Research Centre",
    "Shree Ganesh Dental Clinic And Implant Center",
    "Dr. Rajesh Clinic and Children's Hospital",          # possessive is not a proper name
    "Arivu Neuro Clinic and Arivu Dental Clinic",           # one brand, one address
    "Matha Ayurveda Eye Hospital & Panchakarma Centre Eye Hospital Road (Moongode)",  # an address
    "Goodwill Hospital And Maternity Home - Jalandhar",     # place suffix
    "Seethapathy Clinic & Hospital",
    "Apex Hospital",
    None,
])
def test_name_joins_two_negative(name):
    assert trust.name_joins_two(name, place_words={"jalandhar"}) is None


def test_located_places_and_pins():
    assert trust.located_places("Angel Hospital is located in Rajkot, Gujarat, India") == ["Rajkot", "Gujarat", "India"]
    assert trust.located_places("Referenced as a nearby landmark, located in Agra") == []
    assert trust.located_places("Offers dialysis") == []
    assert trust.pins_in_text("Located in Warangal, Telangana, India, PIN 506002") == ["506002"]
    assert trust.pins_in_text("Coordinates: 22.554609, 72.967361") == []
    assert trust.pins_in_text("500000+ happy patients") == []
    assert trust.pins_in_text("CIN: U85100MH2005PTC151001") == []


PLACE_GROUPS = {"RAJKOT": {"GUJARAT|RAJKOT"}, "SURAT": {"GUJARAT|SURAT"},
                "AURANGABAD": {"MAHARASHTRA|AURANGABAD", "BIHAR|AURANGABAD"}}
PIN_GROUPS = {"360001": {"GUJARAT|RAJKOT"}, "395003": {"GUJARAT|SURAT"}}


def test_other_address():
    own = {"GUJARAT|SURAT"}
    got = trust.other_address([("capability", "Angel Hospital is located in Rajkot, Gujarat, India")], own,
                              {"SURAT"}, PLACE_GROUPS, PIN_GROUPS, "395003", {"GUJARAT", "BIHAR", "MAHARASHTRA"})
    assert got == ("Angel Hospital is located in Rajkot, Gujarat, India", ["GUJARAT|RAJKOT"])
    # a PIN of another district
    got = trust.other_address([("description", "Listed under pin code 360001")], own, {"SURAT"},
                              PLACE_GROUPS, PIN_GROUPS, "395003", {"GUJARAT"})
    assert got[1] == ["GUJARAT|RAJKOT"]
    # its own place, its own PIN, and a state-narrowed name in its own district: nothing
    assert trust.other_address([("capability", "Located in Surat, Gujarat"), ("capability", "PIN 395003")], own,
                               {"SURAT"}, PLACE_GROUPS, PIN_GROUPS, "395003", {"GUJARAT"}) is None
    assert trust.other_address([("capability", "Located in Aurangabad, Maharashtra")], {"MAHARASHTRA|AURANGABAD"},
                               set(), PLACE_GROUPS, PIN_GROUPS, None, {"MAHARASHTRA", "BIHAR"}) is None


def test_other_address_state_with_direction_word():
    # regression: place_key("West Bengal") is "BENGAL"; it must be read as a state, not a place
    groups = {"SILIGURI": {"WEST BENGAL|DARJEELING"}, "CHAPRA": {"BIHAR|SARAN"}}
    own = {"WEST BENGAL|KOLKATA"}
    got = trust.other_address([("capability", "Located in Siliguri, West Bengal, India")], own,
                              {"KOLKATA", "BENGAL CHEMICAL"}, groups, {}, None, {"WEST BENGAL", "BIHAR"})
    assert got[1] == ["WEST BENGAL|DARJEELING"]
    # Chapra named with West Bengal is not Chapra in Bihar
    assert trust.other_address([("capability", "Located in Chapra, West Bengal, India")], own, set(), groups, {},
                               None, {"WEST BENGAL", "BIHAR"}) is None


def test_other_address_prefers_district_named_after_place():
    # fix round: "located in Nagpur" must name Nagpur district, not a district with a village called Nagpur
    groups = {"NAGPUR": {"HARYANA|HISAR", "MAHARASHTRA|NAGPUR"}, "GANDHINAGAR": {"GUJARAT|GANDHINAGAR",
              "JAMMU AND KASHMIR|JAMMU"}, "KOTHUR": {"TELANGANA|RANGAREDDY", "KARNATAKA|KOLAR"}}
    st = {"HARYANA", "MAHARASHTRA", "GUJARAT", "JAMMU AND KASHMIR", "DELHI", "TELANGANA", "KARNATAKA"}
    got = trust.other_address([("description", "Shree Dental Care is located in Nagpur")], {"DELHI|NEW DELHI"},
                              set(), groups, {}, None, st)
    assert got[1] == ["MAHARASHTRA|NAGPUR"]
    got = trust.other_address([("capability", "Located in Gandhinagar")], {"DELHI|NEW DELHI"}, set(), groups, {},
                              None, st)
    assert got[1] == ["GUJARAT|GANDHINAGAR"]
    # no district named after the place: one in the record's own state first
    got = trust.other_address([("capability", "Located in Kothur")], {"KARNATAKA|BENGALURU URBAN"}, set(),
                              groups, {}, None, st)
    assert got[1] == ["KARNATAKA|KOLAR"]


def test_merge_flag_rules():
    name = ("The name joins two.", "A Hospital & B Clinic")
    text = ("Text places it elsewhere.", "Located in Rajkot")
    site = ("Shared website.", "x.com")
    breadth = ("Many specialties.", None)
    assert trust.merge_flag({"shared_website": site}) is None           # corroborates only
    assert trust.merge_flag({"specialty_breadth": breadth}) is None     # dropped
    f = trust.merge_flag({"text_other_address": text})
    assert (f["severity"], f["signal"], f["quote"]) == ("minor", "text_other_address", "Located in Rajkot")
    f = trust.merge_flag({"text_other_address": text, "shared_website": site, "specialty_breadth": breadth})
    assert (f["severity"], f["signal"]) == ("major", "text_other_address+shared_website")
    f = trust.merge_flag({"shared_website": site, "name_joins_two": name})
    assert f["signal"] == "name_joins_two+shared_website" and f["quote"] == "A Hospital & B Clinic"


def test_website_key_and_shared():
    assert trust.website_key("https://www.apollohospitals.com/x") == "apollohospitals.com"
    assert trust.website_key("suba-clinic.business.site") is None
    assert trust.website_key("https://www.esic.nic.in") is None
    assert trust.website_key(None) is None
    out = trust.shared_websites(["a", "b", "c", "d"], ["x.com", "x.com", "x.com", "y.com"],
                                ["S|ONE", "S|TWO", "S|ONE", "S|ONE"])
    assert out == {"a": ["b"], "b": ["a", "c"], "c": ["b"]}


def test_specialty_breadth_limits():
    types = ["hospital"] * 100 + ["doctor"] * 5
    lim = trust.specialty_breadth_limits(types, list(range(100)) + [1] * 5)
    assert "doctor" not in lim and 97 <= lim["hospital"] <= 99


# --- implausible claim ------------------------------------------------------------

def test_implausible_claims():
    out = trust.implausible_claims("clinic", "Elite Heart Clinic", None, {"cardiac_surgery": 2, "icu": 0, "mri": 3})
    assert [(f["service_line"], f["signal"], f["severity"]) for f in out] == [
        ("cardiac_surgery", "type_cannot_offer", "major")]
    assert trust.implausible_claims("clinic", "Vijaya Hospital", None, {"icu": 2}) == []       # name says hospital
    assert trust.implausible_claims("clinic", "Sai Ortho Care", None, {"icu": 2},
                                    "Saiorthocare Hospitals Pvt Ltd is in T Nagar") == []      # description says so
    near = trust.implausible_claims("clinic", "Chiguru Child Care Centre", None, {"nicu": 2},
                                    "An out-patient clinic located opposite Janapriya Hospital")
    assert len(near) == 1                                                                      # names another hospital
    assert trust.implausible_claims("hospital", "X", 4, {"icu": 2}) == []                      # beds off by default
    beds = trust.implausible_claims("hospital", "X", 4, {"icu": 2}, use_beds=True)
    assert beds[0]["signal"] == "few_beds" and beds[0]["quote"] == "4 beds"
    assert trust.implausible_claims("dentist", "Y", None, {"burns": 0}) == []


def test_description_says_hospital():
    assert trust.description_says_hospital("Hospital and Medical Care")
    assert not trust.description_says_hospital("Clinic near City Hospital, Pune")
    assert not trust.description_says_hospital(None)


# --- duplicates ----------------------------------------------------------------------

def test_distinctive_name_and_score():
    assert trust.distinctive_name("Sri Manjunatha Hospital") == "manjunatha"
    assert trust.distinctive_name("Apollo Clinic - Zoo Road, Guwahati") == "apollo"
    assert trust.dup_name_score("manjunatha", "manjunath") >= 90
    assert trust.dup_name_score("charak", "charak research") == 90.0
    assert trust.dup_name_score("charak", "charak research", subset=False) < 90
    assert trust.dup_name_score("kamat", "samata") < 90
    assert trust.dup_name_score("", "x") == 0.0
    assert trust.name_kinds("Divine Dental") != trust.name_kinds("Divine Nursing Home")


def _dup_frame():
    return pd.DataFrame({
        "facility_id": ["f1", "f2", "f3", "f4", "f5", "f6", "f7"],
        "name": ["Charak Hospital", "Charak Hospital And Research Centre", "Divine Dental", "Divine Nursing Home",
                 "Zoi Hospitals", "Zoi Hospitals - Attapur", "Other Clinic"],
        "lat": [26.85, 26.8501, 22.50, 22.5001, 17.40, 17.41, np.nan],
        "lon": [80.95, 80.9501, 88.30, 88.3001, 78.40, 78.41, np.nan],
        "pin": ["226001", "226001", "700001", "700001", "500048", "500048", None],
        "site": [None, None, "divinedental.in", "divinenh.in", "zoihospitals.com", "zoihospitals.com", "x.com"],
        "rank_key": [1, 5, 0, 0, 3, 1, 0],
    })


def test_find_duplicates():
    d = trust.find_duplicates(_dup_frame())
    pairs = {(a, b): (s, k) for a, b, s, k in zip(d.facility_id_a, d.facility_id_b, d.signal, d.keep)}
    assert pairs[("f1", "f2")] == ("near_same_name", "f2")                # higher rank_key kept
    assert ("f3", "f4") not in pairs                                       # same word, different kinds
    assert pairs[("f5", "f6")][0] == "same_site_and_pin"                   # 1.5 km apart, same site and PIN
    assert list(d.columns) == trust.DUP_COLUMNS
    assert (d.facility_id_a < d.facility_id_b).all()


def test_find_duplicates_same_site_pin_uses_row_labels():
    # regression: rows without a site before the pair must not shift the pair (round-1 bug)
    df = _dup_frame()
    df.loc[[0, 1], "site"] = None
    d = trust.find_duplicates(df[["facility_id", "name", "lat", "lon", "pin", "site", "rank_key"]])
    sp = d[d.signal.str.contains("same_site_and_pin")]
    assert set(zip(sp.facility_id_a, sp.facility_id_b)) == {("f5", "f6")}


def test_find_duplicates_weak_matches_need_agreeing_sites():
    # fix round: a words-inside match needs the same website; a near spelling needs sites that do not disagree
    df = pd.DataFrame({
        "facility_id": ["a1", "a2", "b1", "b2", "c1", "c2", "d1", "d2", "e1", "e2"],
        "name": ["Hubli Vascular Centre", "Hubli Hijama Centre And Cupping Therapy",
                 "Chirayu Hospital", "Chirayu Health Medi Care",
                 "Shiva Hospital", "Shivay Hospital",
                 "Devmata Hospital", "D evmata Hospital",
                 "Akash Hospital", "Akash Hospital"],
        "lat": [15.35, 15.351, 23.25, 23.251, 24.48, 24.481, 23.20, 23.201, 28.55, 28.551],
        "lon": [75.13, 75.131, 77.40, 77.401, 86.70, 86.701, 77.45, 77.451, 77.20, 77.201],
        "pin": ["580026", "580020", "462001", "462001", "814112", "814112", "462041", "462021", "110017", "110017"],
        "site": ["hublivascular.com", "hublihijamacentre.com", "chirayuhospital.com", "chirayuhospital.com",
                 "shivahospital.co.in", "shivayhospital.com", None, None, "a.com", "b.com"],
        "rank_key": [0] * 10,
    })
    d = trust.find_duplicates(df)
    near = {(a, b) for a, b, s in zip(d.facility_id_a, d.facility_id_b, d.signal) if "near_same_name" in s}
    assert ("a1", "a2") not in near                     # words inside, different sites
    assert ("b1", "b2") in near                         # words inside, same site
    assert ("c1", "c2") not in near                     # near spelling, sites disagree
    assert ("d1", "d2") in near                         # near spelling, no sites
    assert ("e1", "e2") in near                         # exact name stands even when sites differ
    loose = trust.find_duplicates(df, strict=False)
    lnear = {(a, b) for a, b, s in zip(loose.facility_id_a, loose.facility_id_b, loose.signal) if "near_same_name" in s}
    assert {("a1", "a2"), ("c1", "c2")} <= lnear


def test_name_kinds_ultrasound_is_diagnostic():
    assert trust.name_kinds("Pristine Ultrasound") != trust.name_kinds("Pristine Womens Hospital")


# --- real data ----------------------------------------------------------------------

FLAGS = paths.gold_dir() / "facility_flags.parquet"


@pytest.mark.skipif(not FLAGS.exists(), reason="gold/facility_flags.parquet not built")
def test_written_flags_follow_contract():
    fl = pd.read_parquet(FLAGS)
    assert list(fl.columns) == trust.FLAG_COLUMNS
    assert set(fl["flag"]) <= {"location_conflict", "possible_merge", "implausible_claim"}
    assert set(fl["severity"]) <= {"minor", "major"}
    assert fl["reason"].notna().all() and (fl["reason"].str.len() > 20).all()
    assert fl.loc[fl["flag"] == "implausible_claim", "service_line"].notna().all()
    assert fl.loc[fl["flag"] != "implausible_claim", "service_line"].isna().all()
    # text-triggered signals carry their quote
    text_sig = fl["signal"].str.contains("name_joins_two|text_other_address|city_mismatch|state_mismatch")
    assert fl.loc[text_sig, "quote"].notna().all()
    assert not fl.duplicated(["facility_id", "flag", "service_line"]).any()
    assert not fl["reason"].str.contains("—").any()  # no em-dashes in card text
