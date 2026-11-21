# Data dictionary

Three tables, all in `data/raw` as CSV. Load them with `vf.io` (see `src/vf/io.py`), never with a bare `pd.read_csv`: the facilities file needs scrubbing before it parses.

Counts below were computed from the files in `data/raw` on Nov 21, 2026.

## 1. facilities.csv

One row per healthcare facility in India, produced by Virtue Foundation's pipeline: public sources and scraped web pages, then LLM extraction, then entity resolution into one row per facility.

- 10,088 rows, 51 columns, every value a string.
- 10,000 real facility rows (`address_countryCode == 'IN'`): 9,970 intact and 30 truncated.
- 88 junk rows: fragments of multi-line descriptions that the upstream ingest split into separate rows. Drop them.
- Missing values are the literal string `null`. There are no empty cells.
- `vf.io.read_facilities()` returns the 10,000 real rows with a `row_status` column (`ok` or `truncated`).

The 30 truncated rows keep their name, address, PIN, type, and operator, but lose everything from `description` onward: no specialties, no facts, no coordinates.

| # | Column | Filled (of 10,000) | Example | Notes |
| --- | --- | --- | --- | --- |
| 0 | `unique_id` | 10,000 | `b8a5401f-42f1-422a-8cd9-686a15b4cb76` | Row id (UUID). 11 ids appear twice among the real rows. |
| 1 | `source_types` | 9,972 | `["dynamic","overture","constant","mongo_facility...` | JSON array of source labels, one per contributing source record: overture, dynamic, constant, mongo_facility, mongo_ngo. Repeats. NOT aligned one-to-one with facts. |
| 2 | `source_ids` | 9,972 | `["08f42d99986e2b6d03862b16be6f4556","08f3cb3a2a1...` | JSON array of source record ids. Often a different length from source_types. |
| 3 | `source_content_id` | 10,000 | `c2ada12b-11f7-45f2-93b6-94f7e8853c7a` | Internal content record id (UUID). |
| 4 | `name` | 10,000 | `Aravind Eye Hospital` | Facility name. Sometimes includes the address. |
| 5 | `organization_type` | 10,000 | `facility` | Always 'facility' for real rows. |
| 6 | `content_table_id` | 10,000 | `c2ada12b-11f7-45f2-93b6-94f7e8853c7a` | Internal id. |
| 7 | `phone_numbers` | 9,719 | `["+911123233400","+911123232400","+911126142832"...` | JSON array of phone numbers. |
| 8 | `officialPhone` | 9,503 | `+911123233400` | Primary phone. |
| 9 | `email` | 8,545 | `ighofficedwarka@gmail.com` | Contact email. |
| 10 | `websites` | 10,000 | `["aravind.org","https://tgsouthernpower.org/cred...` | JSON array of websites. |
| 11 | `officialWebsite` | 8,393 | `aravind.org` | Primary website domain. |
| 12 | `yearEstablished` | 4,782 | `1991` | Year founded, as a string. |
| 13 | `acceptsVolunteers` | 21 | `false` | true / false. Almost always null: unusable. |
| 14 | `facebookLink` | 9,882 | `https://www.facebook.com/maharaja-agrasen-hospit...` | Facebook page URL. |
| 15 | `address_line1` | 10,000 | `Santosh Nagar Colony` | Street address. |
| 16 | `address_line2` | 9,785 | `Delhi Gate` | Street address, continued. |
| 17 | `address_line3` | 7,022 | `Delhi Gate` | Street address, continued. |
| 18 | `address_city` | 10,000 | `Hyderabad` | City as scraped. Not normalized. |
| 19 | `address_stateOrRegion` | 10,000 | `Telangana` | State as scraped. Dirty: 234 distinct values, including cities and spelling variants. Do not join on it. |
| 20 | `address_zipOrPostcode` | 9,996 | `110002` | PIN code. 6 digits for about 98% of rows. |
| 21 | `address_country` | 10,000 | `India` | Country name. |
| 22 | `address_countryCode` | 10,000 | `IN` | 'IN' for every real row. Anything else marks a junk row. |
| 23 | `countries` | 0 | `` | JSON array of country codes. |
| 24 | `facilityTypeId` | 9,943 | `hospital` | hospital, clinic, dentist, doctor, and a few others. |
| 25 | `operatorTypeId` | 9,313 | `private` | private or public. 88% private. |
| 26 | `affiliationTypeIds` | 1,239 | `["government","government","government","governm...` | JSON array such as ["government"] or ["academic"]. Mostly null. |
| 27 | `description` | 10,000 | `Referral hospital outside the state listed under...` | Free-text description. The 30 truncated rows are cut off inside this field. |
| 28 | `area` | 125 | `20000` | Floor area. Almost always null. |
| 29 | `numberDoctors` | 3,630 | `1` | Doctor count as a string. Present for about 36%. |
| 30 | `capacity` | 2,516 | `650` | Bed count as a string. Present for about 25%. |
| 31 | `specialties` | 9,970 | `["ophthalmology","internalMedicine","internalMed...` | JSON array of camelCase specialty labels from a taxonomy. Repeats heavily within a row: dedupe before counting. |
| 32 | `procedure` | 9,940 | `["Dialysis treatment is provided via a 20-bed Di...` | JSON array of free-text procedure facts. |
| 33 | `equipment` | 9,860 | `["CT scanner","MRI scanner","Dialysis machines: ...` | JSON array of free-text equipment facts. Includes items inferred from photos. |
| 34 | `capability` | 9,963 | `["Eye care hospital","Located in Hyderabad","Ara...` | JSON array of free-text facts. Mixes clinical capability with marketing, ratings, staff names, and location statements. |
| 35 | `recency_of_page_update` | 3,543 | `2027-07-20` | Date the source page was last updated. |
| 36 | `distinct_social_media_presence_count` | 9,965 | `6` | Number of social platforms found. |
| 37 | `affiliated_staff_presence` | 9,957 | `true` | true / false: staff are named on the page. |
| 38 | `custom_logo_presence` | 9,609 | `true` | true / false: page has a custom logo. |
| 39 | `number_of_facts_about_the_organization` | 2,757 | `73` | Count of facts from one source. Mostly null, and NOT the length of the fact arrays. |
| 40 | `post_metrics_most_recent_social_media_post_date` | 4,932 | `2025-12-17` | Date of the latest social post. |
| 41 | `post_metrics_post_count` | 3,784 | `3` | Number of social posts. |
| 42 | `engagement_metrics_n_followers` | 8,884 | `523` | Follower count. |
| 43 | `engagement_metrics_n_likes` | 7,800 | `4` | Like count. |
| 44 | `engagement_metrics_n_engagements` | 4,887 | `6` | Engagement count. |
| 45 | `source` | 9,970 | `kie` | 'kie' for every intact row. Null marks a truncated row. |
| 46 | `coordinates` | 9,970 | `{"coordinates":[79.48544311523438,11.93823528289...` | GeoJSON Point string, {"coordinates":[lon,lat],"type":"Point"}. Note the lon, lat order. |
| 47 | `latitude` | 9,970 | `11.93823528289795` | Latitude as a string. |
| 48 | `longitude` | 9,970 | `79.48544311523438` | Longitude as a string. |
| 49 | `cluster_id` | 9,970 | `000140e6-c506-4f6e-84dd-38b7b44bcbb0` | Entity-resolution cluster id. 11 clusters contain 2 rows. |
| 50 | `source_urls` | 9,970 | `["https://tgsouthernpower.org/creditcardlogin","...` | JSON array of pages the facts came from. Noisy: can include unrelated pages. |

### How to read the fact fields

`specialties`, `procedure`, `equipment`, and `capability` are JSON arrays stored as strings. Parse with `vf.io.parse_list`.

- `specialties` uses a camelCase taxonomy (`gynecologyAndObstetrics`, `nephrology`, `criticalCareMedicine`). There are 2,933 distinct labels because of noise, and labels repeat inside one row, so always dedupe per facility.
- `procedure`, `equipment`, and `capability` hold 460,132 free-text facts in total (capability 232,564, procedure 167,430, equipment 60,138). 8,548 of them are empty strings.
- Distinct fact strings: capability 168,159, procedure 89,194, equipment 35,145.
- Facts are noisy. Real examples: "Rated 4.3 on JustDial with 72 ratings", "Has 28 years of experience in healthcare", "Wheelchair-accessible parking available", "Hospital bed with rails", "Maintains an unofficial Facebook page".

### Provenance fields

- `source_types` values by number of facilities: overture 9,135, dynamic 6,921, constant 3,387, mongo_facility 1,121, mongo_ngo 228.
- `source_types` is not aligned with the facts. Its length equals the number of facts for only 226 of 9,970 facilities, and equals the length of `source_ids` for only 4,541. Do not treat it as per-fact provenance.
- `source_urls` is the usable signal for corroboration. Distinct domains per facility: 1 domain 22 facilities, 2 domains 194, 3 domains 1,239, 4 domains 1,522, 5 domains 1,374, 6 or more 5,619.
- Top domains: facebook.com 8,849, justdial.com 5,432, practo.com 2,707, mappls.com 1,679, medindia.net 1,548, onefivenine.com 1,469, proptiger.com 1,142, in.linkedin.com 1,096, policyx.com 1,033, lybrate.com 935.
- 8,770 facilities have at least one URL with no parseable domain, and some URLs are clearly unrelated to the facility (a real estate site, a power utility login page). Facts may be contaminated by pages about other entities.

## 2. india_post_pincode_directory.csv

Every post office in India. Source: data.gov.in, All India Pincode Directory. License: Government Open Data License, India.

- 165,627 rows, 11 columns.
- Row grain is post office, not PIN code. 19,586 distinct PINs, 755 distinct (state, district) pairs, 37 distinct state values (one of them is the string `NA`).
- 1,478 PINs map to more than one (state, district). A direct join on `pincode` fans out. Deduplicate or aggregate first.

| Column | Notes |
| --- | --- |
| `circlename` | Postal circle, roughly a state. |
| `regionname` | Postal region. |
| `divisionname` | Postal division. |
| `officename` | Post office name, with suffix B.O, S.O, or H.O. |
| `pincode` | 6-digit PIN. |
| `officetype` | BO 140,270 (branch, mostly rural), PO 24,546, HO 811 (head office, towns). |
| `delivery` | Delivery 157,901, Non Delivery 7,726. |
| `district` | District name in UPPERCASE. |
| `statename` | State name in UPPERCASE. |
| `latitude` | String. 153,614 numeric, 12,007 are `NA`, 6 are malformed (for example `28.430354° N`). |
| `longitude` | String, same issues. |

Only 151,010 post offices have coordinates inside a loose India bounding box (lat 6 to 38, lon 68 to 98). About 2,600 numeric coordinates are garbage. Filter with `vf.io.in_india`.

## 3. nfhs5_district_health_indicators.csv

National Family Health Survey 5 (field period 2019 to 2021), district fact sheets. Source: data.gov.in. License: Government Open Data License, India.

- 706 rows (districts), 109 columns, 36 states and union territories.
- Columns 0 and 1 are `district_name` and `state_ut`. Columns 2 to 4 are sample sizes. The rest are indicators, mostly percentages.
- Cell formats: a number (65,643 cells, sometimes with a trailing space), a number in parentheses such as `(29.5)` (5,068 cells, a low-sample estimate based on 25 to 49 unweighted cases), or `*` (4,125 cells, suppressed: treat as missing, never as zero).
- Parse every cell with `vf.io.parse_nfhs_value`, which returns `(value, flag)`.
- District names look truncated at 25 characters (`SRI POTTI SRIRAMULU NELLO`), and one state is misspelled (`Maharastra`).

Columns the plan depends on:

| Purpose | Column |
| --- | --- |
| Utilization outcome | `institutional_birth_5y_pct` |
| Public share of births | `institutional_birth_in_public_facility_5y_pct` |
| Surgical access signal | `births_delivered_by_csection_5y_pct` |
| Private and public C-section rates | `births_in_a_private_fac_that_were_delivered_by_csection_5y_pct`, `births_in_a_public_fac_that_were_delivered_by_csection_5y_pct` |
| Skilled attendance | `births_attended_by_skilled_hp_5y_10_pct` |
| Antenatal care | `mothers_who_had_at_least_4_anc_visits_lb5y_pct` |
| Cost | `average_out_of_pocket_expenditure_per_delivery_in_a_public_fac`, `hh_member_covered_health_insurance_pct` |
| Development proxies | `hh_electricity_pct`, `hh_use_improved_sanitation_pct`, `households_using_clean_fuel_for_cooking_pct`, `women_age_15_49_who_are_literate_pct`, `women_age_15_49_with_10_or_more_years_of_schooling_pct` |
| Diabetes burden | `w15_plus_with_high_or_very_high_gt_140_mg_dl_blood_sugar_or_pct`, `m15_plus_with_high_or_very_high_gt_140_mg_dl_blood_sugar_or_pct` |
| Hypertension burden | `w15_plus_with_high_bp_sys_gte_140_mmhg_and_or_dia_gte_90_mm_pct`, `m15_plus_with_high_bp_sys_gte_140_mmhg_and_or_dia_gte_90_mm_pct` |
| Screening and tobacco (stretch ideas) | `women_age_30_49_years_ever_undergone_an_oral_cancer_exam_pct`, `w15_plus_who_use_any_kind_of_tobacco_pct`, `m15_plus_who_use_any_kind_of_tobacco_pct` |
| Sample size | `households_surveyed` |

All 109 columns, with how many of the 706 districts have each kind of value:

| # | Column | ok | low sample | suppressed |
| --- | --- | --- | --- | --- |
| 0 | `district_name` | text | | |
| 1 | `state_ut` | text | | |
| 2 | `households_surveyed` | 706 | 0 | 0 |
| 3 | `women_15_49_interviewed` | 706 | 0 | 0 |
| 4 | `men_15_54_interviewed` | 706 | 0 | 0 |
| 5 | `female_population_age_6_years_and_above_ever_schooled_pct` | 706 | 0 | 0 |
| 6 | `population_below_age_15_years_pct` | 706 | 0 | 0 |
| 7 | `sex_ratio_total_f_per_1000_m` | 706 | 0 | 0 |
| 8 | `sex_ratio_at_birth_5y_f_per_1000_m` | 704 | 2 | 0 |
| 9 | `child_u5_whose_birth_was_civil_reg_pct` | 706 | 0 | 0 |
| 10 | `deaths_in_the_last_3_years_civil_reg_pct` | 678 | 27 | 1 |
| 11 | `hh_electricity_pct` | 706 | 0 | 0 |
| 12 | `hh_improved_water_pct` | 706 | 0 | 0 |
| 13 | `hh_use_improved_sanitation_pct` | 706 | 0 | 0 |
| 14 | `households_using_clean_fuel_for_cooking_pct` | 706 | 0 | 0 |
| 15 | `households_using_iodized_salt_pct` | 706 | 0 | 0 |
| 16 | `hh_member_covered_health_insurance_pct` | 706 | 0 | 0 |
| 17 | `child_5y_who_attended_pre_primary_school_during_the_school_pct` | 548 | 155 | 3 |
| 18 | `women_age_15_49_who_are_literate_pct` | 706 | 0 | 0 |
| 19 | `women_age_15_49_with_10_or_more_years_of_schooling_pct` | 706 | 0 | 0 |
| 20 | `w20_24_married_before_age_18_years_pct` | 705 | 1 | 0 |
| 21 | `births_in_the_5_years_preceding_the_survey_that_are_birth_3_pct` | 705 | 0 | 1 |
| 22 | `w15_19_who_were_already_mothers_or_pregnant_at_the_time_of_pct` | 705 | 1 | 0 |
| 23 | `w15_24_who_use_menstrual_hygiene_pct` | 706 | 0 | 0 |
| 24 | `fp_cm_w15_49_any_method_pct` | 706 | 0 | 0 |
| 25 | `fp_cm_w15_49_modern_method_pct` | 706 | 0 | 0 |
| 26 | `fp_cm_w15_49_f_steril_pct` | 706 | 0 | 0 |
| 27 | `fp_cm_w15_49_m_steril_pct` | 706 | 0 | 0 |
| 28 | `fp_cm_w15_49_iud_pct` | 706 | 0 | 0 |
| 29 | `fp_cm_w15_49_pill_pct` | 706 | 0 | 0 |
| 30 | `fp_cm_w15_49_condom_pct` | 706 | 0 | 0 |
| 31 | `fp_cm_w15_49_injectables_pct` | 706 | 0 | 0 |
| 32 | `fp_unmet_total_cm_w15_49_7_pct` | 706 | 0 | 0 |
| 33 | `fp_unmet_spacing_cm_w15_49_7_pct` | 706 | 0 | 0 |
| 34 | `health_worker_ever_talked_to_female_non_users_about_family_pct` | 706 | 0 | 0 |
| 35 | `current_users_ever_told_about_side_effects_of_current_metho_pct` | 638 | 66 | 2 |
| 36 | `mothers_who_had_an_anc_visit_in_the_first_trimester_lb5y_pct` | 705 | 1 | 0 |
| 37 | `mothers_who_had_at_least_4_anc_visits_lb5y_pct` | 705 | 1 | 0 |
| 38 | `mothers_whose_last_birth_was_protected_against_neo_tetanus_pct` | 705 | 1 | 0 |
| 39 | `mothers_who_consumed_ifa_for_100_days_or_more_when_they_wer_pct` | 705 | 1 | 0 |
| 40 | `mothers_who_consumed_ifa_for_180_days_or_more_when_they_wer_pct` | 705 | 1 | 0 |
| 41 | `registered_pregnancies_for_which_the_mother_received_a_mcp_pct` | 705 | 1 | 0 |
| 42 | `mothers_who_received_pnc_from_a_doctor_nurse_lhv_anm_midwif_pct` | 705 | 1 | 0 |
| 43 | `average_out_of_pocket_expenditure_per_delivery_in_a_public_fac` | 691 | 14 | 1 |
| 44 | `children_born_at_home_who_were_taken_to_a_health_facility_f_pct` | 143 | 141 | 422 |
| 45 | `children_who_received_pnc_from_a_doctor_nurse_lhv_anm_midwi_pct` | 705 | 1 | 0 |
| 46 | `institutional_birth_5y_pct` | 706 | 0 | 0 |
| 47 | `institutional_birth_in_public_facility_5y_pct` | 706 | 0 | 0 |
| 48 | `home_birth_that_were_conducted_by_skilled_hp_5y_10_pct` | 706 | 0 | 0 |
| 49 | `births_attended_by_skilled_hp_5y_10_pct` | 706 | 0 | 0 |
| 50 | `births_delivered_by_csection_5y_pct` | 706 | 0 | 0 |
| 51 | `births_in_a_private_fac_that_were_delivered_by_csection_5y_pct` | 434 | 122 | 150 |
| 52 | `births_in_a_public_fac_that_were_delivered_by_csection_5y_pct` | 700 | 6 | 0 |
| 53 | `child_12_23m_fully_vaccinated_based_on_information_from_eit_pct` | 461 | 232 | 13 |
| 54 | `child_12_23m_fully_vaccinated_based_on_information_from_vax_pct` | 361 | 323 | 22 |
| 55 | `child_12_23m_who_have_received_bcg_pct` | 461 | 232 | 13 |
| 56 | `child_12_23m_who_have_received_3_doses_of_polio_vaccine_pct` | 461 | 232 | 13 |
| 57 | `child_12_23m_who_have_received_3_doses_of_penta_or_dpt_vacc_pct` | 461 | 232 | 13 |
| 58 | `child_12_23m_who_have_received_the_first_dose_of_mcv_mcv_pct` | 461 | 232 | 13 |
| 59 | `child_24_35m_who_have_received_a_second_dose_of_mcv_mcv_pct` | 461 | 232 | 13 |
| 60 | `child_12_23m_who_have_received_3_doses_of_rotavirus_vaccine_pct` | 461 | 232 | 13 |
| 61 | `child_12_23m_who_have_received_3_doses_of_penta_or_hepatiti_pct` | 461 | 232 | 13 |
| 62 | `child_9_35m_who_received_a_vit_a_in_the_last_6_months_pct` | 700 | 5 | 1 |
| 63 | `child_12_23m_who_received_most_of_their_vaccinations_in_a_p_pct` | 438 | 252 | 16 |
| 64 | `child_12_23m_who_received_most_of_their_vaccinations_in_a_2_pct` | 438 | 252 | 16 |
| 65 | `prev_diarrhoea_2wk_child_u5_pct` | 706 | 0 | 0 |
| 66 | `children_with_diarrhoea_2wk_who_received_oral_rehydration_s_pct` | 52 | 162 | 492 |
| 67 | `children_with_diarrhoea_2wk_who_received_zinc_child_u5_pct` | 52 | 162 | 492 |
| 68 | `children_with_diarrhoea_2wk_taken_to_a_health_facility_or_h_pct` | 52 | 162 | 492 |
| 69 | `children_prev_symptoms_of_acute_respiratory_infection_ari_2_pct` | 706 | 0 | 0 |
| 70 | `children_with_fever_or_symptoms_of_ari_2wk_taken_to_a_healt_pct` | 218 | 264 | 224 |
| 71 | `children_under_age_3_years_breastfed_within_one_hour_of_bir_pct` | 705 | 1 | 0 |
| 72 | `child_u6m_exclusively_breastfed_pct` | 98 | 347 | 261 |
| 73 | `child_6_8m_receiving_solid_or_semi_solid_food_and_breastmil_pct` | 2 | 62 | 642 |
| 74 | `breastfeeding_child_6_23m_receiving_an_adequate_diet16_17_pct` | 565 | 136 | 5 |
| 75 | `non_breastfeeding_child_6_23m_receiving_an_adequate_diet16_pct` | 4 | 59 | 643 |
| 76 | `total_child_6_23m_receiving_an_adequate_diet16_17_pct` | 654 | 51 | 1 |
| 77 | `child_u5_who_are_stunted_height_for_age_18_pct` | 705 | 1 | 0 |
| 78 | `child_u5_who_are_wasted_weight_for_height_18_pct` | 705 | 1 | 0 |
| 79 | `child_u5_who_are_severe_wasted_weight_for_height_19_pct` | 705 | 1 | 0 |
| 80 | `child_u5_who_are_underweight_weight_for_age_18_pct` | 705 | 1 | 0 |
| 81 | `child_u5_who_are_overweight_weight_for_height_20_pct` | 705 | 1 | 0 |
| 82 | `women_age_15_49_years_whose_bmi_bmi_is_underweight_bmi_lt_1_pct` | 706 | 0 | 0 |
| 83 | `women_age_15_49_years_who_are_overweight_obese_bmi_gte_25_0_pct` | 706 | 0 | 0 |
| 84 | `women_age_15_49_years_who_have_high_risk_whr_gte_0_85_pct` | 706 | 0 | 0 |
| 85 | `child_6_59m_who_are_anaemic_lt_11_0_g_dl_22_pct` | 704 | 2 | 0 |
| 86 | `non_pregnant_w15_49_who_are_anaemic_lt_12_0_g_dl_22_pct` | 706 | 0 | 0 |
| 87 | `pregnant_w15_49_who_are_anaemic_lt_11_0_g_dl_22_pct` | 151 | 421 | 134 |
| 88 | `all_w15_49_who_are_anaemic_pct` | 706 | 0 | 0 |
| 89 | `all_w15_19_who_are_anaemic_pct` | 703 | 3 | 0 |
| 90 | `women_age_15_years_and_above_with_high_141_160_mg_dl_blood_pct` | 706 | 0 | 0 |
| 91 | `w15_plus_with_very_high_gt_160_mg_dl_blood_sugar_pct` | 706 | 0 | 0 |
| 92 | `w15_plus_with_high_or_very_high_gt_140_mg_dl_blood_sugar_or_pct` | 706 | 0 | 0 |
| 93 | `m15_plus_with_high_141_160_mg_dl_blood_sugar_pct` | 706 | 0 | 0 |
| 94 | `men_age_15_years_and_above_with_very_high_gt_160_mg_dl_bloo_pct` | 706 | 0 | 0 |
| 95 | `m15_plus_with_high_or_very_high_gt_140_mg_dl_blood_sugar_or_pct` | 706 | 0 | 0 |
| 96 | `w15_plus_with_mildly_high_bp_sys_140_159_mmhg_and_or_dia_90_pct` | 706 | 0 | 0 |
| 97 | `w15_plus_with_moderately_or_severely_high_bp_sys_gte_160_mm_pct` | 706 | 0 | 0 |
| 98 | `w15_plus_with_high_bp_sys_gte_140_mmhg_and_or_dia_gte_90_mm_pct` | 706 | 0 | 0 |
| 99 | `m15_plus_with_mildly_high_bp_sys_140_159_mmhg_and_or_dia_90_pct` | 706 | 0 | 0 |
| 100 | `m15_plus_with_moderately_or_severely_high_bp_sys_gte_160_mm_pct` | 706 | 0 | 0 |
| 101 | `m15_plus_with_high_bp_sys_gte_140_mmhg_and_or_dia_gte_90_mm_pct` | 706 | 0 | 0 |
| 102 | `women_age_30_49_years_ever_undergone_a_cervical_screen_pct` | 706 | 0 | 0 |
| 103 | `women_age_30_49_years_ever_undergone_a_breast_exam_pct` | 706 | 0 | 0 |
| 104 | `women_age_30_49_years_ever_undergone_an_oral_cancer_exam_pct` | 706 | 0 | 0 |
| 105 | `w15_plus_who_use_any_kind_of_tobacco_pct` | 706 | 0 | 0 |
| 106 | `m15_plus_who_use_any_kind_of_tobacco_pct` | 706 | 0 | 0 |
| 107 | `w15_plus_who_consume_alcohol_pct` | 706 | 0 | 0 |
| 108 | `m15_plus_who_consume_alcohol_pct` | 706 | 0 | 0 |

## Joining the tables

- NFHS has 706 districts, the PIN directory has 755. Boundaries changed after the survey.
- After uppercasing and stripping punctuation, only 565 of the 706 NFHS (state, district) pairs match the PIN directory exactly, and 613 match on district name alone.
- State names disagree in three cases: NFHS `Maharastra` vs `MAHARASHTRA`, NFHS `NCT of Delhi` vs `DELHI`, and `Dadra and Nagar Haveli and Daman and Diu` vs `THE DADRA AND NAGAR HAVELI AND DAMAN AND DIU`.
- The organizers ask teams to document how uncertain matches are handled and not to present inferred geography as exact. WP2 owns this.
