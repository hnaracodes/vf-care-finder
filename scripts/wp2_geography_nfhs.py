#!/usr/bin/env python
"""WP2: geography and NFHS.

Builds, under vf.paths.silver_dir():

  post_offices.parquet          one row per raw post office, coordinates cleaned
                                (rows with lat and lon swapped are repaired when the
                                corrected point lands in their district), district_key
                                attached
  pin_crosswalk.parquet         one row per (pin, state_pin, district_pin)
  district_crosswalk.parquet    one row per PIN-directory district -> NFHS district
  district_crosswalk_review.csv every non-exact row, for a human to skim
  nfhs.parquet                  NFHS-5 indicators as floats (null if suppressed), one
                                row per real district: the duplicate row Mizoram|Chandel
                                (a copy of Manipur|Chandel) is dropped
  nfhs_flags.parquet            same shape, ok / low_sample / suppressed / missing

each with a <output>.meta.json sidecar. Prints a summary and the acceptance
checks from tasks/WP2_geography_nfhs.md.

Run:  .venv/bin/python scripts/wp2_geography_nfhs.py
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from vf import geo, io, paths

RECIPE_VERSION = "wp2-v2"
SCRIPT = "scripts/wp2_geography_nfhs.py"

#: Fuzzy match acceptance. Chosen from the score distribution of the 145
#: non-exact PIN districts: every pair at or above 75 was a true spelling
#: variant except WARANGAL, which ties Warangal Rural and Warangal Urban and is
#: caught by the margin rule.
FUZZY_THRESHOLD = 75.0
FUZZY_MARGIN = 5.0

ACCESSED = "accessed 2026-11-21"
UNVERIFIED = "from model knowledge, unverified"


def _wiki(page: str) -> str:
    return f"Source: Wikipedia, {page} ({ACCESSED})."


# ---------------------------------------------------------------------------
# Hand-made mapping, reviewed by the WP2 agent.
# (state as in PIN directory, district as in PIN directory,
#  NFHS state_ut, NFHS district_name, method, note)
# Keys are compared after geo.norm_state / geo.norm_name.
# ---------------------------------------------------------------------------
HAND: list[tuple[str, str, str, str, str, str]] = [
    # ---- manual: renames, abbreviations, spellings the fuzzy step cannot see
    ("ANDHRA PRADESH", "SPSR NELLORE", "Andhra Pradesh", "Sri Potti Sriramulu Nello", "manual",
     "Same district. SPSR abbreviates Sri Potti Sriramulu; the NFHS name is cut at 25 characters."),
    ("CHHATTISGARH", "KOREA", "Chhattisgarh", "Koriya", "manual", "Same district, spelling variant."),
    ("GUJARAT", "DANG", "Gujarat", "The Dangs", "manual", "Same district, spelling variant."),
    ("HARYANA", "GURUGRAM", "Haryana", "Gurgaon", "manual",
     f"Same district, renamed Gurugram in 2016 ({UNVERIFIED})."),
    ("HARYANA", "NUH", "Haryana", "Mewat", "manual",
     f"Same district, renamed Nuh in 2016 ({UNVERIFIED})."),
    ("JAMMU AND KASHMIR", "POONCH", "Jammu & Kashmir", "Punch", "manual", "Same district, spelling variant."),
    ("JHARKHAND", "EAST SINGHBUM", "Jharkhand", "Purbi Singhbhum", "manual",
     "Same district. Purbi means east; PIN spelling drops an H."),
    ("JHARKHAND", "WEST SINGHBHUM", "Jharkhand", "Pashchimi Singhbhum", "manual",
     "Same district. Pashchimi means west."),
    ("KARNATAKA", "BALLARI", "Karnataka", "Bellary", "manual",
     f"Same district, official spelling changed in 2014 ({UNVERIFIED})."),
    ("KARNATAKA", "BELAGAVI", "Karnataka", "Belgaum", "manual",
     f"Same district, official spelling changed in 2014 ({UNVERIFIED})."),
    ("KARNATAKA", "BENGALURU URBAN", "Karnataka", "Bangalore", "manual",
     "Same district. NFHS 'Bangalore' is Bangalore Urban; 'Bangalore Rural' is matched separately."),
    ("KARNATAKA", "KALABURAGI", "Karnataka", "Gulbarga", "manual",
     f"Same district, renamed in 2014 ({UNVERIFIED})."),
    ("KARNATAKA", "MYSURU", "Karnataka", "Mysore", "manual",
     f"Same district, official spelling changed in 2014 ({UNVERIFIED})."),
    ("KARNATAKA", "VIJAYAPURA", "Karnataka", "Bijapur", "manual",
     f"Same district, renamed in 2014 ({UNVERIFIED})."),
    ("MAHARASHTRA", "BEED", "Maharastra", "Bid", "manual", "Same district, spelling variant."),
    ("ODISHA", "SONEPUR", "Odisha", "Subarnapur", "manual",
     f"Same district. Sonepur is its headquarters town and older name ({UNVERIFIED})."),
    ("PUNJAB", "S A S NAGAR", "Punjab", "Sahibzada Ajit Singh Nagar", "manual",
     "Same district, abbreviation (S.A.S. Nagar, Mohali)."),
    ("TAMIL NADU", "TUTICORIN", "Tamil Nadu", "Thoothukkudi", "manual",
     "Same district, English and Tamil names."),
    ("TELANGANA", "HANUMAKONDA", "Telangana", "Warangal Urban", "manual",
     "Warangal Urban was renamed Hanumakonda in June 2021, with some mandals moved, so boundaries differ. "
     "Source: Wikipedia, List of districts of Telangana, citing 'KCR renames Warangal Urban Hanumakonda; "
     f"Warangal Rural becomes Warangal' ({ACCESSED})."),
    ("TELANGANA", "WARANGAL", "Telangana", "Warangal Rural", "manual",
     "Warangal Rural was renamed Warangal in June 2021, with some mandals moved, so boundaries differ "
     "(Warangal city now sits in this district). Fuzzy step tied Rural and Urban and left it ambiguous. "
     f"Source: Wikipedia, List of districts of Telangana ({ACCESSED})."),
    ("UTTAR PRADESH", "AMROHA", "Uttar Pradesh", "Jyotiba Phule Nagar", "manual",
     f"Same district, renamed Amroha in 2012 ({UNVERIFIED})."),
    ("UTTAR PRADESH", "AYODHYA", "Uttar Pradesh", "Faizabad", "manual",
     f"Same district, renamed Ayodhya in 2018 ({UNVERIFIED})."),
    ("UTTAR PRADESH", "HATHRAS", "Uttar Pradesh", "Mahamaya Nagar", "manual",
     f"Same district, renamed Hathras in 2012 ({UNVERIFIED})."),
    ("UTTAR PRADESH", "KASGANJ", "Uttar Pradesh", "Kanshiram Nagar", "manual",
     f"Same district, renamed Kasganj in 2012 ({UNVERIFIED})."),
    ("UTTAR PRADESH", "PRAYAGRAJ", "Uttar Pradesh", "Allahabad", "manual",
     f"Same district, renamed Prayagraj in 2018 ({UNVERIFIED})."),
    ("WEST BENGAL", "24 PARAGANAS NORTH", "West Bengal", "North Twenty Four Pargana", "manual",
     "Same district, North 24 Parganas; NFHS name cut at 25 characters."),
    ("WEST BENGAL", "24 PARAGANAS SOUTH", "West Bengal", "South Twenty Four Pargana", "manual",
     "Same district, South 24 Parganas; NFHS name cut at 25 characters."),
    ("WEST BENGAL", "COOCHBEHAR", "West Bengal", "Koch Bihar", "manual", "Same district, spelling variant."),
    ("WEST BENGAL", "HOOGHLY", "West Bengal", "Hugli", "manual", "Same district, spelling variant."),
    ("WEST BENGAL", "HOWRAH", "West Bengal", "Haora", "manual", "Same district, spelling variant."),
    ("WEST BENGAL", "MEDINIPUR EAST", "West Bengal", "Purba Medinipur", "manual",
     "Same district. Purba means east."),
    ("WEST BENGAL", "MEDINIPUR WEST", "West Bengal", "Paschim Medinipur", "manual",
     "Same district. Paschim means west. Jhargram was carved out in 2017 (mapped separately)."),

    # ---- parent_district: districts that NFHS-5 reports inside an older district
    # Andhra Pradesh, reorganised 4 April 2022 (NFHS-5 has the 13 old districts)
    ("ANDHRA PRADESH", "ALLURI SITHARAMA RAJU", "Andhra Pradesh", "Visakhapatnam", "parent_district",
     "Created 2022 from Paderu division of Visakhapatnam and Rampachodavaram division of East Godavari. "
     f"Split parent; Visakhapatnam taken as the larger part ({UNVERIFIED}). "
     + _wiki("Alluri Sitharama Raju district")),
    ("ANDHRA PRADESH", "ANAKAPALLI", "Andhra Pradesh", "Visakhapatnam", "parent_district",
     "Created 2022 from Anakapalli and Narsipatnam divisions of Visakhapatnam. " + _wiki("Anakapalli district")),
    ("ANDHRA PRADESH", "ANNAMAYYA", "Andhra Pradesh", "Y.S.R.", "parent_district",
     "Created 2022 from parts of YSR Kadapa and Chittoor. Split parent; YSR taken because the headquarters "
     f"Rayachoti was in YSR Kadapa ({UNVERIFIED}). " + _wiki("Annamayya district")),
    ("ANDHRA PRADESH", "BAPATLA", "Andhra Pradesh", "Guntur", "parent_district",
     "Created 2022 from parts of Guntur and Prakasam. Split parent; Guntur taken (Bapatla and Repalle areas) "
     f"({UNVERIFIED}). " + _wiki("Bapatla district")),
    ("ANDHRA PRADESH", "ELURU", "Andhra Pradesh", "West Godavari", "parent_district",
     "Created 2022 from West Godavari and Krishna. Split parent; West Godavari taken (Eluru town) "
     f"({UNVERIFIED}). " + _wiki("Eluru district")),
    ("ANDHRA PRADESH", "KAKINADA", "Andhra Pradesh", "East Godavari", "parent_district",
     "Created 2022 from Kakinada and Peddapuram divisions of East Godavari. " + _wiki("Kakinada district")),
    ("ANDHRA PRADESH", "KONASEEMA", "Andhra Pradesh", "East Godavari", "parent_district",
     "Created 2022, carved out of East Godavari. " + _wiki("Konaseema district")),
    ("ANDHRA PRADESH", "NANDYAL", "Andhra Pradesh", "Kurnool", "parent_district",
     "Created 2022, bifurcated from Kurnool. " + _wiki("Nandyal district")),
    ("ANDHRA PRADESH", "NTR", "Andhra Pradesh", "Krishna", "parent_district",
     "Created 2022 from Vijayawada division of Krishna. " + _wiki("NTR district")),
    ("ANDHRA PRADESH", "PALNADU", "Andhra Pradesh", "Guntur", "parent_district",
     "Created 2022 from Gurazala, Sattenapalli and Narasaraopet divisions of Guntur. " + _wiki("Palnadu district")),
    ("ANDHRA PRADESH", "PARVATHIPURAM MANYAM", "Andhra Pradesh", "Vizianagaram", "parent_district",
     "Created 2022 from Parvathipuram division of Vizianagaram plus part of Palakonda division of Srikakulam. "
     + _wiki("Parvathipuram Manyam district")),
    ("ANDHRA PRADESH", "SRI SATHYA SAI", "Andhra Pradesh", "Anantapur", "parent_district",
     "Created 2022 from Dharmavaram, Penukonda and Kadiri divisions of Anantapur. " + _wiki("Sri Sathya Sai district")),
    ("ANDHRA PRADESH", "TIRUPATI", "Andhra Pradesh", "Chittoor", "parent_district",
     "Created 2022 from Tirupati division of Chittoor and Gudur and Sullurupeta divisions of SPSR Nellore. "
     f"Split parent; Chittoor taken ({UNVERIFIED}). " + _wiki("Tirupati district")),
    # Arunachal Pradesh (NFHS-5 has 20 districts)
    ("ARUNACHAL PRADESH", "KAMLE", "Arunachal Pradesh", "Lower Subansiri", "parent_district",
     "Created 2017 from Lower Subansiri and Upper Subansiri. Split parent; Lower Subansiri taken "
     f"({UNVERIFIED}). " + _wiki("List of districts of Arunachal Pradesh")),
    ("ARUNACHAL PRADESH", "LOWER SIANG", "Arunachal Pradesh", "West Siang", "parent_district",
     "Created 2017 from West Siang and East Siang. Split parent; West Siang taken (headquarters Likabali) "
     f"({UNVERIFIED}). " + _wiki("List of districts of Arunachal Pradesh")),
    ("ARUNACHAL PRADESH", "LEPARADA", "Arunachal Pradesh", "West Siang", "parent_district",
     "Created 2018 from Lower Siang, which was carved from West Siang and East Siang in 2017. West Siang "
     f"taken because Basar was in West Siang ({UNVERIFIED}). " + _wiki("List of districts of Arunachal Pradesh")),
    ("ARUNACHAL PRADESH", "PAKKE KESSANG", "Arunachal Pradesh", "East Kameng", "parent_district",
     "Created 2018, carved out of East Kameng. " + _wiki("List of districts of Arunachal Pradesh")),
    ("ARUNACHAL PRADESH", "SHI YOMI", "Arunachal Pradesh", "West Siang", "parent_district",
     "Created 2018 by splitting West Siang. " + _wiki("List of districts of Arunachal Pradesh")),
    # one-off new districts
    ("ASSAM", "BAJALI", "Assam", "Barpeta", "parent_district",
     "Declared a district in January 2021, carved out of Barpeta. " + _wiki("Bajali district")),
    ("CHHATTISGARH", "GAURELLA PENDRA MARWAHI", "Chhattisgarh", "Bilaspur", "parent_district",
     "Created February 2020, carved out of Bilaspur. " + _wiki("Gaurela-Pendra-Marwahi district")),
    ("KARNATAKA", "VIJAYNAGAR", "Karnataka", "Bellary", "parent_district",
     "Vijayanagara, notified 2020 and carved out of Ballari (Bellary) in 2021. " + _wiki("Vijayanagara district")),
    ("MADHYA PRADESH", "NIWARI", "Madhya Pradesh", "Tikamgarh", "parent_district",
     "Created October 2018, formerly part of Tikamgarh. " + _wiki("Niwari district")),
    ("NAGALAND", "NOKLAK", "Nagaland", "Tuensang", "parent_district",
     "Created from the Noklak subdivision of Tuensang (2017 declared, 2021 per the article lead). "
     + _wiki("Noklak district")),
    ("PUNJAB", "MALERKOTLA", "Punjab", "Sangrur", "parent_district",
     "Created June 2021, carved out of Sangrur. " + _wiki("Malerkotla district")),
    # Manipur: 7 districts created December 2016; NFHS-5 reports the 9 older ones
    ("MANIPUR", "JIRIBAM", "Manipur", "Imphal East", "parent_district",
     "Created December 2016, formerly a subdivision of Imphal East. " + _wiki("Jiribam district")),
    ("MANIPUR", "KAKCHING", "Manipur", "Thoubal", "parent_district",
     "Created December 2016 from Thoubal. " + _wiki("Kakching district")),
    ("MANIPUR", "KAMJONG", "Manipur", "Ukhrul", "parent_district",
     "Created 2016 from Ukhrul. " + _wiki("Kamjong district")),
    ("MANIPUR", "KANGPOKPI", "Manipur", "Senapati", "parent_district",
     "Created December 2016 from the Sadar Hills subdivisions of Senapati. " + _wiki("Kangpokpi district")),
    ("MANIPUR", "NONEY", "Manipur", "Tamenglong", "parent_district",
     "Created 2016 by bifurcating Tamenglong. " + _wiki("Noney district")),
    ("MANIPUR", "PHERZAWL", "Manipur", "Churachandpur", "parent_district",
     "Created 2016-17 from the Tipaimukh and Thanlon subdivisions of Churachandpur. " + _wiki("Pherzawl district")),
    ("MANIPUR", "TENGNOUPAL", "Manipur", "Chandel", "parent_district",
     "Created December 2016 by splitting Chandel. " + _wiki("Tengnoupal district")),
    # Mizoram: 3 districts created 2019
    ("MIZORAM", "HNAHTHIAL", "Mizoram", "Lunglei", "parent_district",
     "Created 2019 from Lunglei. " + _wiki("List of districts of Mizoram")),
    ("MIZORAM", "KHAWZAWL", "Mizoram", "Champhai", "parent_district",
     "Created 2019 from Champhai. " + _wiki("List of districts of Mizoram")),
    ("MIZORAM", "SAITUAL", "Mizoram", "Aizawl", "parent_district",
     f"Created 2019 from Aizawl and Champhai. Split parent; Aizawl taken ({UNVERIFIED}). "
     + _wiki("List of districts of Mizoram")),
    # Sikkim: 2 districts created 2021
    ("SIKKIM", "PAKYONG", "Sikkim", "East District", "parent_district",
     "Created 2021 from the Pakyong subdivision of East Sikkim. " + _wiki("List of districts of Sikkim")),
    ("SIKKIM", "SORENG", "Sikkim", "West District", "parent_district",
     "Created 2021 from the Soreng subdivision of West Sikkim. " + _wiki("List of districts of Sikkim")),
    # Tamil Nadu: 6 districts created 2019-2020
    ("TAMIL NADU", "CHENGALPATTU", "Tamil Nadu", "Kancheepuram", "parent_district",
     "Created November 2019, bifurcated from Kanchipuram. " + _wiki("List of districts of Tamil Nadu")),
    ("TAMIL NADU", "KALLAKURICHI", "Tamil Nadu", "Viluppuram", "parent_district",
     "Created November 2019, bifurcated from Viluppuram. " + _wiki("List of districts of Tamil Nadu")),
    ("TAMIL NADU", "MAYILADUTHURAI", "Tamil Nadu", "Nagapattinam", "parent_district",
     "Created 2020 from Nagapattinam. " + _wiki("List of districts of Tamil Nadu")),
    ("TAMIL NADU", "RANIPET", "Tamil Nadu", "Vellore", "parent_district",
     "Created November 2019, split from Vellore. " + _wiki("List of districts of Tamil Nadu")),
    ("TAMIL NADU", "TENKASI", "Tamil Nadu", "Tirunelveli", "parent_district",
     "Created November 2019, bifurcated from Tirunelveli. " + _wiki("List of districts of Tamil Nadu")),
    ("TAMIL NADU", "TIRUPATHUR", "Tamil Nadu", "Vellore", "parent_district",
     "Created November 2019, split from Vellore. " + _wiki("List of districts of Tamil Nadu")),
    # Telangana: 2 districts created 17 February 2019
    ("TELANGANA", "MULUGU", "Telangana", "Jayashankar Bhupalapally", "parent_district",
     "Created February 2019, carved out of Jayashankar Bhupalapally. Source: PTI via Business Standard, "
     f"'Telangana gets two new districts', and The News Minute ({ACCESSED})."),
    ("TELANGANA", "NARAYANPET", "Telangana", "Mahabubnagar", "parent_district",
     "Created February 2019, carved out of Mahabubnagar. Source: PTI via Business Standard, "
     f"'Telangana gets two new districts', and The News Minute ({ACCESSED})."),
    # West Bengal: NFHS-5 has 20 districts; these 3 sit inside their parents
    ("WEST BENGAL", "ALIPURDUAR", "West Bengal", "Jalpaiguri", "parent_district",
     "Created June 2014 by bifurcating Jalpaiguri; NFHS-5 reports it inside Jalpaiguri. " + _wiki("Alipurduar district")),
    ("WEST BENGAL", "JHARGRAM", "West Bengal", "Paschim Medinipur", "parent_district",
     "Created April 2017 by bifurcating Paschim Medinipur. " + _wiki("Jhargram district")),
    ("WEST BENGAL", "KALIMPONG", "West Bengal", "Darjiling", "parent_district",
     "Created February 2017, carved out of Darjeeling. " + _wiki("Kalimpong district, citing Times of India 14 Feb 2017")),
]

#: PIN-directory districts deliberately left unmatched, with the reason.
UNMATCHED_NOTES: dict[tuple[str, str], str] = {
    ("NA", "NA"): (
        "State and district are the string 'NA' in the PIN directory, so this is not a district. Its post "
        "offices get a district_key through their PIN when the PIN's other offices all map to one district."
    ),
    ("MIZORAM", "SAIHA"): (
        "No usable NFHS row. NFHS-5 reports Saiha (Siaha), but in this file Mizoram's eighth row is labelled "
        "'Chandel' and all 107 of its values are an exact copy of Manipur|Chandel. Not mapped, so Saiha does "
        "not inherit Manipur's numbers."
    ),
    ("WEST BENGAL", "PURBA BARDHAMAN"): (
        "No NFHS row. NFHS-5 published Purba Barddhaman separately (the IIPS West Bengal district fact sheet "
        "compendium lists both Paschim and Purba Barddhaman, 20 districts in all), but this file has only "
        "Paschim Barddhaman. A sibling is not a parent, so it is not substituted."
    ),
}

#: Fuzzy matches the WP2 agent read and confirmed (state, PIN district) -> NFHS district.
#: Any accepted fuzzy match not listed here is printed as a warning on rerun.
FUZZY_REVIEWED: dict[tuple[str, str], str] = {
    ("ANDAMAN AND NICOBAR ISLANDS", "SOUTH ANDAMANS"): "South Andaman",
    ("ANDHRA PRADESH", "VISAKHAPATANAM"): "Visakhapatnam",
    ("ASSAM", "KAMRUP METRO"): "Kamrup Metropolitan",
    ("ASSAM", "MARIGAON"): "Morigaon",
    ("BIHAR", "PURBI CHAMPARAN"): "Purba Champaran",
    ("CHHATTISGARH", "KABIRDHAM"): "Kabeerdham",
    ("CHHATTISGARH", "KANKER"): "Uttar Bastar Kanker",
    ("CHHATTISGARH", "KONDAGAON"): "Kodagaon",
    ("GUJARAT", "ARVALLI"): "Aravali",
    ("GUJARAT", "CHHOTAUDEPUR"): "Chhota Udaipur",
    ("HARYANA", "CHARKI DADRI"): "Charkhi Dadri",
    ("JAMMU AND KASHMIR", "BANDIPORA"): "Bandipore",
    ("JAMMU AND KASHMIR", "BARAMULLA"): "Baramula",
    ("JAMMU AND KASHMIR", "BUDGAM"): "Badgam",
    ("JAMMU AND KASHMIR", "SHOPIAN"): "Shupiyan",
    ("JHARKHAND", "KODERMA"): "Kodarma",
    ("JHARKHAND", "SAHEBGANJ"): "Sahibganj",
    ("KARNATAKA", "BENGALURU RURAL"): "Bangalore Rural",
    ("KARNATAKA", "CHAMARAJANAGARA"): "Chamarajanagar",
    ("KARNATAKA", "CHIKKAMAGALURU"): "Chikmagalur",
    ("KARNATAKA", "DAVANGERE"): "Davanagere",
    ("KARNATAKA", "SHIVAMOGGA"): "Shimoga",
    ("KARNATAKA", "TUMAKURU"): "Tumkur",
    ("LAKSHADWEEP", "LAKSHADWEEP DISTRICT"): "Lakshadweep",
    ("MADHYA PRADESH", "EAST NIMAR"): "Khandwa (East Nimar)",
    ("MADHYA PRADESH", "KHARGONE"): "Khargone (West Nimar)",
    ("MADHYA PRADESH", "NARSINGHPUR"): "Narsimhapur",
    ("MAHARASHTRA", "AHMEDNAGAR"): "Ahmadnagar",
    ("MAHARASHTRA", "BULDHANA"): "Buldana",
    ("MAHARASHTRA", "GONDIA"): "Gondiya",
    ("MAHARASHTRA", "RAIGAD"): "Raigarh",
    ("MEGHALAYA", "EAST JAINTIA HILLS"): "East Jantia Hills",
    ("MEGHALAYA", "RI BHOI"): "Ribhoi",
    ("ODISHA", "BOUDH"): "Baudh",
    ("ODISHA", "DEOGARH"): "Debagarh",
    ("ODISHA", "NABARANGPUR"): "Nabarangapur",
    ("PUDUCHERRY", "PONDICHERRY"): "Puducherry",
    ("PUNJAB", "FIROZEPUR"): "Firozpur",
    ("PUNJAB", "SRI MUKTSAR SAHIB"): "Muktsar",
    ("RAJASTHAN", "CHITTORGARH"): "Chittaurgarh",
    ("RAJASTHAN", "DHOLPUR"): "Dhaulpur",
    ("RAJASTHAN", "JALORE"): "Jalor",
    ("RAJASTHAN", "JHUNJHUNU"): "Jhunjhunun",
    ("TAMIL NADU", "KANCHIPURAM"): "Kancheepuram",
    ("TAMIL NADU", "VILLUPURAM"): "Viluppuram",
    ("TELANGANA", "KUMURAM BHEEM ASIFABAD"): "Komaram Bheem Asifabad",
    ("UTTAR PRADESH", "BARABANKI"): "Bara Banki",
    ("UTTAR PRADESH", "BHADOHI"): "Sant Ravidas Nagar (Bhadohi)",
    ("UTTAR PRADESH", "KUSHI NAGAR"): "Kushinagar",
    ("UTTAR PRADESH", "MAHARAJGANJ"): "Mahrajganj",
    ("UTTAR PRADESH", "SANT KABEER NAGAR"): "Sant Kabir Nagar",
    ("UTTAR PRADESH", "SHRAVASTI"): "Shrawasti",
    ("UTTAR PRADESH", "SIDDHARTH NAGAR"): "Siddharthnagar",
    ("UTTARAKHAND", "HARIDWAR"): "Hardwar",
    ("UTTARAKHAND", "PAURI GARHWAL"): "Garhwal",
    ("UTTARAKHAND", "RUDRA PRAYAG"): "Rudraprayag",
    ("UTTARAKHAND", "UDAM SINGH NAGAR"): "Udham Singh Nagar",
    ("UTTARAKHAND", "UTTAR KASHI"): "Uttarkashi",
    ("WEST BENGAL", "DARJEELING"): "Darjiling",
    ("WEST BENGAL", "DINAJPUR DAKSHIN"): "Dakshin Dinajpur",
    ("WEST BENGAL", "DINAJPUR UTTAR"): "Uttar Dinajpur",
    ("WEST BENGAL", "PASCHIM BARDHAMAN"): "Paschim Barddhaman",
    ("WEST BENGAL", "PURULIA"): "Puruliya",
}

#: NFHS rows dropped from nfhs.parquet and nfhs_flags.parquet: not real districts.
NFHS_DUPLICATE_ROWS = {
    "Mizoram|Chandel": {
        "copy_of": "Manipur|Chandel",
        "why": "exact copy of Manipur|Chandel across all 107 value columns; it stands where Mizoram's Saiha "
               "row should be, so Saiha has no NFHS data in this file",
    },
}

FACILITIES_PUBLIC_SHARE_PCT = 4.7  # docs/03_data_profile.md, public facilities as a share of rows


# ---------------------------------------------------------------------------
# writing helpers
# ---------------------------------------------------------------------------

def _git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], cwd=paths.code_root(), capture_output=True, text=True, check=True
        ).stdout.strip()
    except Exception:
        return ""


GIT_COMMIT = _git("rev-parse", "HEAD") or None
GIT_DIRTY = bool(_git("status", "--porcelain")) if GIT_COMMIT else None
CREATED_AT = datetime.now(timezone.utc).isoformat(timespec="seconds")


def _atomic_write(path: Path, write_fn) -> None:
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        write_fn(tmp)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def write_output(df: pd.DataFrame, name: str, extra: dict | None = None) -> Path:
    out_dir = paths.ensure(paths.silver_dir())
    path = out_dir / name
    if name.endswith(".parquet"):
        _atomic_write(path, lambda p: df.to_parquet(p, index=False))
    elif name.endswith(".csv"):
        _atomic_write(path, lambda p: df.to_csv(p, index=False))
    else:
        raise ValueError(name)
    meta = {
        "output": f"silver/{name}",
        "rows": int(len(df)),
        "columns": list(map(str, df.columns)),
        "created_at": CREATED_AT,
        "git_commit": GIT_COMMIT,
        "git_dirty": GIT_DIRTY,
        "recipe_version": RECIPE_VERSION,
        "script": SCRIPT,
        "inputs": ["raw/india_post_pincode_directory.csv", "raw/nfhs5_district_health_indicators.csv"],
    }
    if extra:
        meta.update(extra)
    meta_path = out_dir / f"{name}.meta.json"
    _atomic_write(meta_path, lambda p: p.write_text(json.dumps(meta, indent=2, default=str) + "\n"))
    return path


# ---------------------------------------------------------------------------
# steps
# ---------------------------------------------------------------------------

def build_post_offices(raw: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    lat = raw["latitude"].map(io.to_float)
    lon = raw["longitude"].map(io.to_float)
    numeric = lat.notna() & lon.notna()
    in_box = pd.Series(geo.in_india_bbox(lat.astype("float64"), lon.astype("float64")), index=raw.index)
    # swapped rows are repaired when the corrected point lands among their district's offices
    coords, swap_stats = geo.post_office_coords(raw)
    valid = coords["coord_valid"]
    po = pd.DataFrame(
        {
            "po_id": np.arange(len(raw), dtype="int64"),
            "office_name": raw["officename"].str.strip(),
            "office_type": raw["officetype"].str.strip(),
            "delivery": raw["delivery"].str.strip(),
            "pin": raw["pincode"].str.strip(),
            "state_pin": raw["statename"],
            "district_pin": raw["district"],
            "lat": coords["lat"].astype("float64"),
            "lon": coords["lon"].astype("float64"),
            "coord_valid": valid.astype(bool),
            "coord_swap_repaired": coords["coord_swap_repaired"].astype(bool),
        }
    )
    stats = {
        "rows": len(po),
        "coord_numeric": int(numeric.sum()),
        "coord_na_or_malformed": int((~numeric).sum()),
        "coord_outside_india": int((numeric & ~in_box).sum()),
        "coord_outside_india_but_valid_if_swapped": swap_stats["coord_swapped_detected"],
        "coord_in_india_as_given": int(in_box.sum()),
        "coord_valid": int(valid.sum()),
        "pin_not_6_digits": int((~po["pin"].str.fullmatch(r"\d{6}")).sum()),
        **swap_stats,
    }
    return po, stats


def build_pin_crosswalk(po: pd.DataFrame) -> pd.DataFrame:
    g = (
        po.groupby(["pin", "state_pin", "district_pin"], dropna=False)
        .size()
        .reset_index(name="n_offices")
    )
    total = g.groupby("pin")["n_offices"].transform("sum")
    g["share"] = g["n_offices"] / total
    g = g.sort_values(["pin", "n_offices", "state_pin", "district_pin"], ascending=[True, False, True, True])
    g["rank"] = g.groupby("pin").cumcount().add(1).astype("int64")
    g["is_ambiguous"] = g.groupby("pin")["pin"].transform("size") > 1
    g["n_offices"] = g["n_offices"].astype("int64")
    return g.reset_index(drop=True)[
        ["pin", "state_pin", "district_pin", "n_offices", "share", "rank", "is_ambiguous"]
    ]


def build_nfhs(raw: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    """Values and flags for every raw NFHS row (706), duplicate rows included.
    ``drop_duplicate_rows`` removes them before anything is matched or written."""
    ind_cols = [c for c in raw.columns if c not in ("district_name", "state_ut")]
    state = raw["state_ut"].str.strip()
    dist = raw["district_name"].str.strip()
    keys = [geo.district_key(s, d) for s, d in zip(state, dist)]
    if len(set(keys)) != len(keys):
        raise SystemExit("NFHS district_key is not unique")
    base = pd.DataFrame({"district_key": keys, "state_ut": state, "district_name": dist})
    vals, flags = {}, {}
    for c in ind_cols:
        parsed = raw[c].map(io.parse_nfhs_value)
        vals[c] = parsed.map(lambda t: t[0]).astype("float64")
        flags[c] = parsed.map(lambda t: t[1]).astype("object")
    values = pd.concat([base, pd.DataFrame(vals)], axis=1)
    flag_df = pd.concat([base, pd.DataFrame(flags)], axis=1)
    return values, flag_df, ind_cols


def drop_duplicate_rows(values: pd.DataFrame, flags: pd.DataFrame, ind_cols: list[str]) -> tuple[
        pd.DataFrame, pd.DataFrame, dict]:
    """Drop NFHS_DUPLICATE_ROWS after confirming each is still an exact copy of its source row."""
    v = values.set_index("district_key")
    info = {}
    for key, spec in NFHS_DUPLICATE_ROWS.items():
        src, why = spec["copy_of"], spec["why"]
        if key not in v.index or src not in v.index:
            raise SystemExit(f"duplicate NFHS row {key} or its source {src} not found; update NFHS_DUPLICATE_ROWS")
        a, b = v.loc[key, ind_cols], v.loc[src, ind_cols]
        same = int(((a == b) | (a.isna() & b.isna())).sum())
        if same != len(ind_cols):
            raise SystemExit(f"{key} is no longer an exact copy of {src} ({same} of {len(ind_cols)} columns)")
        info[key] = {"copy_of": src, "identical_value_columns": same, "why": why}
    keep = ~values["district_key"].isin(NFHS_DUPLICATE_ROWS)
    return (values[keep].reset_index(drop=True), flags[keep].reset_index(drop=True), info)


def build_district_crosswalk(po: pd.DataFrame, nfhs: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    # PIN-directory districts, one row per raw (state, district) pair
    pd_ = (
        po.groupby(["state_pin", "district_pin"])
        .agg(n_post_offices=("po_id", "size"), n_geocoded=("coord_valid", "sum"))
        .reset_index()
    )
    pd_["s_norm"] = pd_["state_pin"].map(geo.norm_state)
    pd_["d_norm"] = pd_["district_pin"].map(geo.norm_name)
    if pd_.duplicated(["s_norm", "d_norm"]).any():
        raise SystemExit("two raw PIN districts normalize to the same name; extend the crosswalk grain")

    nf = nfhs[["district_key", "state_ut", "district_name"]].copy()
    nf["s_norm"] = nf["state_ut"].map(geo.norm_state)
    nf["d_norm"] = nf["district_name"].map(geo.norm_name)
    nf["trunc"] = nf["district_name"].map(geo.looks_truncated)
    nf_by_norm = {(r.s_norm, r.d_norm): r.district_key for r in nf.itertuples()}
    nf_name_by_key = dict(zip(nf["district_key"], nf["d_norm"]))
    nf_trunc_by_key = dict(zip(nf["district_key"], nf["trunc"]))
    if len(nf_by_norm) != len(nf):
        raise SystemExit("two NFHS districts normalize to the same name")

    # hand table, validated against both files
    hand = {}
    for st_pin, d_pin, st_nf, d_nf, method, note in HAND:
        k = (geo.norm_state(st_pin), geo.norm_name(d_pin))
        target = geo.district_key(st_nf, d_nf)
        if target not in nf_name_by_key:
            raise SystemExit(f"hand table target not in NFHS: {target}")
        if k in hand:
            raise SystemExit(f"duplicate hand entry {k}")
        hand[k] = (target, method, note)
    pin_keys = set(zip(pd_["s_norm"], pd_["d_norm"]))
    for k in list(hand) + list(UNMATCHED_NOTES):
        if k not in pin_keys:
            raise SystemExit(f"hand/unmatched entry not in PIN directory: {k}")

    rec = {}
    # 1. exact
    for r in pd_.itertuples():
        k = (r.s_norm, r.d_norm)
        if k in nf_by_norm:
            if k in hand:
                raise SystemExit(f"hand entry for an exact match: {k}")
            rec[k] = dict(district_key=nf_by_norm[k], match_method="exact", match_score=100.0, note=None)

    # 2. fuzzy within state, among NFHS districts not claimed by an exact match
    claimed = {v["district_key"] for v in rec.values()}
    fuzzy_info = {}
    for s in sorted(pd_["s_norm"].unique()):
        pins = [d for (ss, d) in pin_keys if ss == s and (ss, d) not in rec]
        if not pins:
            continue
        cands = {
            r.d_norm: r.trunc for r in nf.itertuples() if r.s_norm == s and r.district_key not in claimed
        }
        for res in geo.match_within_state(sorted(pins), cands, FUZZY_THRESHOLD, FUZZY_MARGIN):
            fuzzy_info[(s, res["pin_name"])] = res
    nf_key_by_state_norm = {(r.s_norm, r.d_norm): r.district_key for r in nf.itertuples()}

    unreviewed = []
    for k, res in fuzzy_info.items():
        prop_key = nf_key_by_state_norm.get((k[0], res["nfhs_name"])) if res["nfhs_name"] else None
        best_key = nf_key_by_state_norm.get((k[0], res["best_nfhs_name"])) if res["best_nfhs_name"] else None
        res["proposed_key"], res["best_key"] = prop_key, best_key
        if k in hand:
            target, method, note = hand[k]
            if prop_key and prop_key != target:
                note = f"{note} Corrects fuzzy proposal {prop_key} (score {res['score']:.0f})."
            s, _ = geo.name_score(k[1], nf_name_by_key[target], nf_trunc_by_key[target])
            rec[k] = dict(district_key=target, match_method=method, match_score=round(s, 1), note=note)
        elif prop_key:
            reviewed = FUZZY_REVIEWED.get(k)
            if reviewed is not None and geo.district_key(prop_key.split("|")[0], reviewed) != prop_key:
                raise SystemExit(f"fuzzy result {k}->{prop_key} contradicts the reviewed list ({reviewed})")
            status = "reviewed by WP2 agent: same district, spelling variant" if reviewed else "NOT REVIEWED"
            if not reviewed:
                unreviewed.append((k, prop_key))
            rec[k] = dict(
                district_key=prop_key, match_method="fuzzy", match_score=round(res["score"], 1),
                note=f"Auto fuzzy ({res['how']} {res['score']:.0f}, runner-up {res['runner_up_score']:.0f}); {status}.",
            )
        else:
            note = UNMATCHED_NOTES.get(k)
            if note is None:
                note = f"No exact, fuzzy ({res['status']}) or hand match."
            rec[k] = dict(district_key=None, match_method="unmatched", match_score=None, note=note)

    xw = pd_.copy()
    for col in ("district_key", "match_method", "match_score", "note"):
        xw[col] = [rec[(s, d)][col] for s, d in zip(xw["s_norm"], xw["d_norm"])]
    xw["match_score"] = xw["match_score"].astype("float64")
    # review helpers: best automatic candidate for every non-exact row
    xw["best_fuzzy_candidate"] = [
        fuzzy_info.get((s, d), {}).get("best_key") for s, d in zip(xw["s_norm"], xw["d_norm"])
    ]
    xw["best_fuzzy_score"] = [
        fuzzy_info.get((s, d), {}).get("best_score") for s, d in zip(xw["s_norm"], xw["d_norm"])
    ]
    for k in FUZZY_REVIEWED:
        if k not in rec or rec[k]["match_method"] != "fuzzy":
            print(f"  note: reviewed fuzzy pair {k} no longer produced by the fuzzy step", file=sys.stderr)
    return xw, {"unreviewed_fuzzy": unreviewed}


def attach_district_key(po: pd.DataFrame, xw: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    m = po.merge(
        xw[["state_pin", "district_pin", "district_key"]], on=["state_pin", "district_pin"], how="left",
        validate="many_to_one",
    )
    assert len(m) == len(po)
    # Offices whose state and district are the string NA: borrow the district of
    # the other offices with the same PIN, only when they all agree.
    is_na = (m["state_pin"] == "NA") & (m["district_pin"] == "NA")
    known = m.loc[~is_na & m["district_key"].notna(), ["pin", "district_key"]]
    per_pin = known.groupby("pin")["district_key"].agg(lambda s: s.unique().tolist())
    single = per_pin[per_pin.map(len) == 1].map(lambda l: l[0])
    fill = m.loc[is_na, "pin"].map(single)
    m.loc[is_na, "district_key"] = fill
    stats = {
        "na_state_offices": int(is_na.sum()),
        "na_state_filled_via_pin": int(fill.notna().sum()),
        "na_state_pin_ambiguous": int(m.loc[is_na, "pin"].map(per_pin).map(lambda l: isinstance(l, list) and len(l) > 1).sum()),
        "na_state_pin_unknown": int(m.loc[is_na, "pin"].map(lambda p: p not in per_pin.index).sum()),
    }
    return m, stats


def coordinate_quality(po: pd.DataFrame) -> dict:
    """Report-only checks on coordinates that pass the India bounding box.

    Nothing is nulled here (the contract defines coord_valid by the bounding
    box); these counts tell WP5 how many points are probably misplaced.
    """
    v = po[po["coord_valid"] & po["district_key"].notna()]
    med = v.groupby("district_key")[["lat", "lon"]].transform("median")
    lat1, lon1 = np.radians(v["lat"]), np.radians(v["lon"])
    lat2, lon2 = np.radians(med["lat"]), np.radians(med["lon"])
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    d = 2 * 6371.0 * np.arcsin(np.sqrt(a))
    geo_po = po[po["coord_valid"]]
    shared = geo_po.groupby(["lat", "lon"])["po_id"].transform("size")
    ph = geo.placeholder_points(geo_po["lat"], geo_po["lon"], geo_po["pin"])
    ph_points = geo_po.loc[ph["placeholder"].to_numpy(), ["lat", "lon"]].drop_duplicates()
    return {
        "offices_over_100km_from_district_median": int((d > 100).sum()),
        "offices_over_200km_from_district_median": int((d > 200).sum()),
        "offices_on_a_point_shared_by_10_or_more": int((shared >= 10).sum()),
        "offices_on_a_placeholder_point": int(ph["placeholder"].sum()),
        "placeholder_points": int(len(ph_points)),
        "placeholder_rule": (
            f"a point shared by {geo.PLACEHOLDER_MIN_OFFICES}+ geocoded offices from {geo.PLACEHOLDER_MIN_PINS}+ "
            "PINs (vf.geo.placeholder_points); WP5 flags these offices coord_placeholder and counts each such "
            "point once per district"
        ),
    }


def public_share(nfhs: pd.DataFrame, flags: pd.DataFrame) -> dict:
    inst_c, pub_c, w_c = (
        "institutional_birth_5y_pct",
        "institutional_birth_in_public_facility_5y_pct",
        "households_surveyed",
    )
    keep = ~nfhs["district_key"].isin(NFHS_DUPLICATE_ROWS)
    d = nfhs.loc[keep, ["district_key", inst_c, pub_c, w_c]].dropna()
    w = d[w_c]
    inst = float((d[inst_c] * w).sum() / w.sum())
    pub = float((d[pub_c] * w).sum() / w.sum())
    return {
        "n_districts": int(len(d)),
        "excluded_duplicate_rows": sorted(NFHS_DUPLICATE_ROWS),
        "flags_ok_inst": int((flags.loc[keep, inst_c] == "ok").sum()),
        "flags_ok_pub": int((flags.loc[keep, pub_c] == "ok").sum()),
        "pub_le_inst_all_districts": bool((d[pub_c] <= d[inst_c] + 1e-9).all()),
        "weighted_institutional_births_pct": round(inst, 1),
        "weighted_public_facility_births_pct_of_all_births": round(pub, 1),
        "public_share_of_institutional_births_pct": round(100 * pub / inst, 1),
        "unweighted_public_share_of_institutional_births_pct": round(
            100 * float(d[pub_c].mean()) / float(d[inst_c].mean()), 1
        ),
        "weight": f"{w_c} (rough: NFHS samples a similar number of households per district, "
                  "so this is close to a district average, not a population-weighted national figure)",
    }


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    print("WP2 geography and NFHS")
    raw_pins = io.read_pincodes_raw()
    raw_nfhs = io.read_nfhs_raw()

    po, po_stats = build_post_offices(raw_pins)
    pinxw = build_pin_crosswalk(po)
    nfhs_all, flags_all, ind_cols = build_nfhs(raw_nfhs)
    nfhs, flags, dropped = drop_duplicate_rows(nfhs_all, flags_all, ind_cols)
    xw, xw_stats = build_district_crosswalk(po, nfhs)
    po, fill_stats = attach_district_key(po, xw)
    po_stats.update(fill_stats)
    po_stats.update(coordinate_quality(po))

    # ---- summaries
    # the profile's flag counts are over the raw file (706 rows); the outputs drop the duplicate row
    flag_counts_raw = pd.Series(flags_all[ind_cols].to_numpy().ravel()).value_counts().to_dict()
    flag_counts = pd.Series(flags[ind_cols].to_numpy().ravel()).value_counts().to_dict()
    method_counts = xw["match_method"].value_counts().reindex(
        ["exact", "fuzzy", "manual", "parent_district", "unmatched"], fill_value=0
    )
    nfhs_matched = set(xw["district_key"].dropna())
    nfhs_unmatched = sorted(set(nfhs["district_key"]) - nfhs_matched)
    geocoded = po["coord_valid"]
    geo_with_key = int((geocoded & po["district_key"].notna()).sum())
    share_geo_key = geo_with_key / int(geocoded.sum())
    po_by_method = (
        po.merge(xw[["state_pin", "district_pin", "match_method"]], on=["state_pin", "district_pin"])
        .loc[lambda d: d["coord_valid"]]
        .groupby("match_method")
        .size()
        .to_dict()
    )
    ps = public_share(nfhs, flags)
    n_pin = pinxw["pin"].nunique()
    n_amb = int(pinxw.loc[pinxw["is_ambiguous"], "pin"].nunique())

    # ---- write outputs
    po_out = po[
        ["po_id", "office_name", "office_type", "delivery", "pin", "state_pin", "district_pin",
         "lat", "lon", "coord_valid", "coord_swap_repaired", "district_key"]
    ]
    write_output(po_out, "post_offices.parquet", {"stats": po_stats})
    write_output(pinxw, "pin_crosswalk.parquet", {"n_pins": n_pin, "n_ambiguous_pins": n_amb})
    xw_out = xw[["state_pin", "district_pin", "district_key", "match_method", "match_score", "note"]]
    xw_meta = {
        "match_method_counts": {k: int(v) for k, v in method_counts.items()},
        "fuzzy_threshold": FUZZY_THRESHOLD,
        "fuzzy_margin": FUZZY_MARGIN,
        "match_score": "name similarity 0-100 (vf.geo.name_score) between the PIN name and the chosen NFHS name",
        "nfhs_districts_matched": len(nfhs_matched),
        "nfhs_districts_total": len(nfhs),
        "nfhs_districts_without_pin_district": nfhs_unmatched,
        "share_geocoded_post_offices_with_district_key": round(share_geo_key, 4),
        "unreviewed_fuzzy": [list(k) + [v] for k, v in xw_stats["unreviewed_fuzzy"]],
    }
    write_output(xw_out, "district_crosswalk.parquet", xw_meta)
    review = (
        xw.loc[xw["match_method"] != "exact",
               ["match_score", "match_method", "state_pin", "district_pin", "district_key", "note",
                "best_fuzzy_candidate", "best_fuzzy_score", "n_post_offices", "n_geocoded"]]
        .sort_values(["match_score", "match_method", "state_pin", "district_pin"], na_position="first")
    )
    write_output(review, "district_crosswalk_review.csv", {"sorted_by": "match_score ascending, unmatched first"})
    nfhs_meta = {
        "flag_counts": {k: int(v) for k, v in flag_counts.items()},
        "flag_counts_raw_file": {k: int(v) for k, v in flag_counts_raw.items()},
        "district_key": "state_ut|district_name, leading/trailing whitespace stripped, spelling as in NFHS",
        "raw_rows": int(len(nfhs_all)),
        "dropped_rows": dropped,
        "missing_districts": ["West Bengal: Purba Barddhaman (published in NFHS-5, absent from this file)",
                              "Mizoram: Saiha (its row in the raw file is a copy of Manipur|Chandel, dropped)"],
        "national_public_share": ps,
    }
    write_output(nfhs, "nfhs.parquet", nfhs_meta)
    write_output(flags, "nfhs_flags.parquet", {"flag_counts": nfhs_meta["flag_counts"],
                                               "flag_counts_raw_file": nfhs_meta["flag_counts_raw_file"],
                                               "raw_rows": int(len(nfhs_all)), "dropped_rows": dropped})

    # ---- print summary
    print(f"\nPost offices: {len(po):,} rows; coord_valid {po_stats['coord_valid']:,} "
          f"(NA/malformed {po_stats['coord_na_or_malformed']:,}, outside India {po_stats['coord_outside_india']:,}, "
          f"of which {po_stats['coord_swapped_detected']} have lat/lon swapped: "
          f"{po_stats['coord_swapped_repaired']} repaired, {po_stats['coord_swapped_left_null']} left null)")
    print("  swapped rows repaired by district: " + ", ".join(
        f"{k} {v}" for k, v in list(po_stats["coord_swapped_repaired_by_district"].items())[:8]))
    print("  swapped rows left null by district: " + ", ".join(
        f"{k} {v}" for k, v in list(po_stats["coord_swapped_left_null_by_district"].items())[:8]))
    print(f"PIN crosswalk: {len(pinxw):,} rows, {n_pin:,} PINs, {n_amb:,} ambiguous (more than one district)")
    print(f"NFHS: {len(nfhs_all)} raw rows, {len(nfhs)} written (dropped {', '.join(dropped)}: exact copy of "
          f"{', '.join(v['copy_of'] for v in dropped.values())}) x {len(ind_cols)} indicator columns; "
          f"flags {flag_counts} (raw file {flag_counts_raw})")
    print("\nDistrict crosswalk (one row per PIN-directory district):")
    for k, v in method_counts.items():
        print(f"  {k:<16}{v:>5}")
    print(f"  {'total':<16}{int(method_counts.sum()):>5}")
    print(f"NFHS districts matched by at least one PIN district: {len(nfhs_matched)} of {len(nfhs)}; "
          f"not matched: {nfhs_unmatched}")
    print(f"Geocoded post offices with a district_key: {geo_with_key:,} of {int(geocoded.sum()):,} "
          f"= {100 * share_geo_key:.2f}%")
    print(f"  geocoded offices by match_method: {po_by_method}")
    print(f"  NA-state offices: {po_stats['na_state_offices']}, given a key through their PIN: "
          f"{po_stats['na_state_filled_via_pin']}")
    print(f"  coordinate quality (report only, coordinates kept; for WP5): "
          f"{po_stats['offices_over_100km_from_district_median']:,} geocoded offices >100 km and "
          f"{po_stats['offices_over_200km_from_district_median']:,} >200 km from their district's median point; "
          f"{po_stats['offices_on_a_point_shared_by_10_or_more']:,} sit on a point shared by 10+ offices, "
          f"{po_stats['offices_on_a_placeholder_point']:,} of them on {po_stats['placeholder_points']} placeholder "
          f"points shared by {geo.PLACEHOLDER_MIN_OFFICES}+ offices from {geo.PLACEHOLDER_MIN_PINS}+ PINs")
    if xw_stats["unreviewed_fuzzy"]:
        print(f"  WARNING unreviewed fuzzy matches: {xw_stats['unreviewed_fuzzy']}")
    print("\nNational public-facility share of institutional births (ROUGH):")
    print(f"  weighted by {ps['weight']}")
    print(f"  institutional births {ps['weighted_institutional_births_pct']}% of all births; "
          f"in public facilities {ps['weighted_public_facility_births_pct_of_all_births']}% of all births")
    print(f"  => public facilities deliver {ps['public_share_of_institutional_births_pct']}% of institutional births "
          f"(unweighted district average: {ps['unweighted_public_share_of_institutional_births_pct']}%), "
          f"versus {FACILITIES_PUBLIC_SHARE_PCT}% public rows in the facilities table")
    print(f"  {ps['n_districts']} districts; excluded duplicate row(s): {ps['excluded_duplicate_rows']}")

    # ---- acceptance checks
    checks = [
        ("post_offices has 165,627 rows", len(po_out) == 165_627, f"{len(po_out):,}"),
        ("about 151,000 coord_valid", abs(po_stats["coord_valid"] - 151_000) < 1_000, f"{po_stats['coord_valid']:,}"),
        ("1,478 ambiguous PINs", n_amb == 1_478, f"{n_amb:,}"),
        ("no NFHS cell unparsed", flag_counts_raw.get("unparsed", 0) == 0, f"{flag_counts_raw.get('unparsed', 0)}"),
        ("4,125 suppressed cells in the raw file", flag_counts_raw.get("suppressed", 0) == 4_125,
         f"{flag_counts_raw.get('suppressed', 0):,}"),
        ("5,068 low-sample cells in the raw file", flag_counts_raw.get("low_sample", 0) == 5_068,
         f"{flag_counts_raw.get('low_sample', 0):,}"),
        ("nfhs.parquet has 705 districts: the 706 raw rows minus the duplicate Mizoram|Chandel",
         len(nfhs) == 705 and not nfhs["district_key"].isin(NFHS_DUPLICATE_ROWS).any()
         and list(flags["district_key"]) == list(nfhs["district_key"]), f"{len(nfhs)}"),
        ("swapped post office coordinates: repaired + left null = detected",
         po_stats["coord_swapped_repaired"] + po_stats["coord_swapped_left_null"]
         == po_stats["coord_swapped_detected"] == int(po_out["coord_swap_repaired"].sum())
         + po_stats["coord_swapped_left_null"],
         f"{po_stats['coord_swapped_repaired']} + {po_stats['coord_swapped_left_null']} = "
         f"{po_stats['coord_swapped_detected']}"),
        ("suppressed cells are null", bool(nfhs[ind_cols].where(flags[ind_cols] == "suppressed").isna().all().all()), ""),
        ("crosswalk covers all 755 PIN districts", len(xw_out) == 755, f"{len(xw_out)}"),
        (">= 95% of geocoded POs have a district_key", share_geo_key >= 0.95, f"{100 * share_geo_key:.2f}%"),
        ("review file exists", (paths.silver_dir() / "district_crosswalk_review.csv").exists(), f"{len(review)} rows"),
        ("all fuzzy matches reviewed", not xw_stats["unreviewed_fuzzy"], f"{len(xw_stats['unreviewed_fuzzy'])} unreviewed"),
    ]
    print("\nAcceptance checks:")
    ok = True
    for name, passed, detail in checks:
        ok &= bool(passed)
        print(f"  [{'PASS' if passed else 'FAIL'}] {name} {('(' + detail + ')') if detail else ''}")
    print(f"\nOutputs in {paths.silver_dir()}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
