# WP6 part 1: rule-based quadrants for emergency obstetric care

Stage: `rule_only`. Model B has not run, so `predicted_outcome`, `residual`, `quadrant_model` and `p_real_gap` are null in `gold/district_quadrants.parquet`. Grades behind the distances: `regex` (WP3 regex baseline until WP4 replaces it). Generated 2026-11-21T21:32:59Z by `scripts/wp6_rule_quadrants.py`.

## Findings

1. **The split.** Of 705 scored districts, 98 are care deserts, 137 data deserts, 135 cost-or-quality barriers and 335 served. Poor access means the district's median post office is 90.3 km or more from the nearest facility with supported evidence of emergency obstetric care (the top third). Poor outcome means institutional births below 87.3% (the bottom third).
2. **Public share (step 4): data deserts deliver more of their births in public facilities.** Median 76.6% of all births (95% interval 63.8 to 83.8, bootstrap over states) against 66.8% in served districts (60.5 to 72.5): a gap of +9.8 points (interval +0.4 to +17.8), Mann-Whitney p = 1.1e-06. As a share of institutional births the medians are 83.4% and 70.0% (p = 5.8e-07). Both groups have high institutional births, so this compares like with like on the outcome.
3. **Within states the gap is smaller but still there.** In means, the overall data-desert minus served gap in the public share of all births is +7.7 points; comparing districts of the same state it is +2.9 points (permutation p = 0.014, 19 states with both kinds). For the public share of institutional births: +8.7 overall, +3.5 within states (p = 0.004). So most of the gap is between states: Odisha 17, Madhya Pradesh 14, Telangana 14, Karnataka 13, Rajasthan 13 hold 52% of the data deserts. Cost-or-quality districts concentrate elsewhere: Uttar Pradesh 43, Bihar 29, Jharkhand 15 of 135.
4. **Public facilities in data deserts do some surgical delivery, less than in served districts.** Median public-facility C-section rate: 14.2% in data deserts, 19.0% in served districts, 8.2% in care deserts (data deserts sit between the two). Against served districts the gap is -4.8 points (interval -10.0 to +2.8); within states it is -1.2 points (p = 0.202). This supports public obstetric surgery existing where the data shows none nearby, but it is not at served-district levels.
5. **The split is moderately fragile.** 68 of 137 data deserts and 45 of 98 care deserts keep their label under all 16 threshold pairs. Moving only the access cut from a third to a half changes 118 labels; moving only the outcome cut to a half changes 115. Median label stability is 0.75; 46 districts keep their label in fewer than half the pairs.
6. **Access and outcome are only loosely linked.** Spearman rho between distance and institutional births is -0.21 (p = 9.4e-09). Distance and the public share of institutional births correlate at rho = 0.42: the farther a district is from evidenced care in this private-heavy dataset, the more of its facility births tend to be in public facilities.
7. **C-section under 5% (step 2): 39 districts.** 24 are care deserts (24% of them), 9 cost-or-quality, 4 data deserts and 2 served. A rate under 5% signals unmet need for surgical obstetric care whatever the institutional birth rate.
8. **Dialysis need versus access (a different kind of comparison).** 59 districts are in the top third of diabetes and blood pressure burden (need index 19.7% or more) and the top third of distance to grade 2+ dialysis (58.1 km or more). Need and distance correlate at rho = -0.26: high-burden districts tend to be closer to listed dialysis. There is no dialysis outcome in NFHS, so this cannot say whether the far ones lack care or lack data.
9. **Coverage.** 705 of 705 NFHS districts are scored. WP2 dropped the raw file's duplicate row Mizoram|Chandel (a copy of Manipur|Chandel), so Mizoram's Saiha has no survey data. 3 of 755 PIN-directory districts are unmatched (MIZORAM|SAIHA, NA|NA, WEST BENGAL|PURBA BARDHAMAN); their 682 geocoded post offices have no NFHS outcome and should show as 'no survey data'. 0 scored districts have a low-sample or suppressed institutional birth value.

## Thresholds and why

- Poor access: `dist_km_p50 >= 90.30` km, the 2/3 quantile of the 705 district medians (emoc, min_grade 2). Why: the work package's default (top third); the distribution has a long right tail (median 63.0 km, 3/4 quantile 106.6 km), and the top third keeps the far tail without calling half the country poor access.
- Poor outcome: `institutional_birth_5y_pct < 87.30`, the 1/3 quantile. Why: the distribution is skewed toward 100% (median 92.2%), so a fixed cut such as 80% would call only 124 districts poor; the bottom third mirrors the access cut and sits close to the published NFHS-5 national figure of 88.6% (as sourced by WP2).
- C-section flag: `births_delivered_by_csection_5y_pct < 5`. Why: 5% is the lower end of the 5 to 15% range in the UN handbook for monitoring emergency obstetric care (WHO, UNFPA, UNICEF, AMDD, 2009); below it, some women who need a C-section are not getting one.
- Dialysis: high need is the top third of the need index (19.68%); poor access the top third of district median distance to grade 2+ dialysis (58.10 km).
- Quantiles are numpy linear interpolation over the scored districts, recomputed on every run, so the km value moves if WP4's grades replace the regex baseline. The values above are this run's.

## How fragile is the split

Quadrant counts for each pair of cuts. `changed` counts districts whose label differs from the chosen split (tertile, tertile).

| access_cut | access_km | outcome_cut | outcome_pct | care_desert | data_desert | cost_or_quality | served | changed |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| median | 63.0 | median | 92.2 | 197 | 156 | 151 | 201 | 213 |
| median | 63.0 | tertile | 87.3 | 138 | 215 | 95 | 257 | 118 |
| median | 63.0 | quartile | 83.7 | 103 | 250 | 70 | 282 | 168 |
| median | 63.0 | fixed_80pct | 80.0 | 82 | 271 | 42 | 310 | 211 |
| tertile | 90.3 | median | 92.2 | 137 | 98 | 211 | 259 | 115 |
| tertile | 90.3 | tertile | 87.3 | 98 | 137 | 135 | 335 | 0 |
| tertile | 90.3 | quartile | 83.7 | 73 | 162 | 100 | 370 | 60 |
| tertile | 90.3 | fixed_80pct | 80.0 | 58 | 177 | 66 | 404 | 109 |
| quartile | 106.63 | median | 92.2 | 109 | 68 | 239 | 289 | 168 |
| quartile | 106.63 | tertile | 87.3 | 75 | 102 | 158 | 370 | 58 |
| quartile | 106.63 | quartile | 83.7 | 58 | 119 | 115 | 413 | 110 |
| quartile | 106.63 | fixed_80pct | 80.0 | 45 | 132 | 79 | 449 | 157 |
| fixed_60km | 60.0 | median | 92.2 | 204 | 164 | 144 | 193 | 227 |
| fixed_60km | 60.0 | tertile | 87.3 | 144 | 224 | 89 | 248 | 133 |
| fixed_60km | 60.0 | quartile | 83.7 | 107 | 261 | 66 | 271 | 181 |
| fixed_60km | 60.0 | fixed_80pct | 80.0 | 85 | 283 | 39 | 298 | 223 |

Districts that keep their label under all 16 pairs: Care desert 45 of 98, Data desert 68 of 137, Cost or quality barrier 39 of 135, Served 193 of 335.

## Step 4: public share of births by quadrant

Medians, with 95% intervals from 2,000 bootstrap resamples of whole states.

| quadrant | n | public, % of all births | public, % of institutional | public C-section rate % | institutional births % (median) |
| --- | --- | --- | --- | --- | --- |
| care_desert | 98 | 67.8 (53.0 to 73.1) | 91.1 (87.1 to 92.6) | 8.2 (5.5 to 10.7) | 76.7 |
| data_desert | 137 | 76.6 (63.8 to 83.8) | 83.4 (66.2 to 89.1) | 14.2 (10.3 to 24.7) | 95.3 |
| cost_or_quality | 135 | 59.2 (55.4 to 64.9) | 77.5 (72.1 to 84.7) | 6.0 (4.4 to 10.0) | 80.1 |
| served | 335 | 66.8 (60.5 to 72.5) | 70.0 (62.6 to 76.9) | 19.0 (12.7 to 28.0) | 96.5 |

Pairwise comparisons (first group minus second). Within-state: weighted mean of per-state differences, p from 10,000 permutations that shuffle labels inside each state.

| measure | pair | median diff | mean diff | 95% interval | Mann-Whitney p | within-state diff | within-state p |
| --- | --- | --- | --- | --- | --- | --- | --- |
| public % of all births | data_desert vs served | +9.8 | +7.7 | +0.4 to +17.8 | 1.1e-06 | +2.9 | 0.014 |
| public % of all births | data_desert vs care_desert | +8.8 | +10.0 | -2.7 to +22.5 | 7.8e-07 | +13.2 | 1.0e-04 |
| public % of all births | data_desert vs cost_or_quality | +17.4 | +14.6 | +3.9 to +27.1 | 6.6e-14 | +11.1 | 1.0e-04 |
| public % of all births | care_desert vs served | +1.0 | -2.3 | -12.3 to +7.8 | 0.361 | -5.3 | 2.0e-04 |
| public % of institutional | data_desert vs served | +13.4 | +8.7 | +0.1 to +19.8 | 5.8e-07 | +3.5 | 0.004 |
| public % of institutional | data_desert vs care_desert | -7.8 | -9.8 | -24.0 to -1.7 | 2.6e-05 | -0.4 | 0.769 |
| public % of institutional | data_desert vs cost_or_quality | +5.9 | +1.9 | -11.6 to +13.5 | 0.055 | -0.8 | 0.746 |
| public % of institutional | care_desert vs served | +21.2 | +18.5 | +13.3 to +28.4 | 3.3e-21 | +6.7 | 1.0e-04 |
| public C-section rate | data_desert vs served | -4.8 | -1.8 | -10.0 to +2.8 | 0.045 | -1.2 | 0.202 |
| public C-section rate | data_desert vs care_desert | +6.0 | +11.2 | +2.3 to +16.4 | 1.2e-09 | +3.9 | 1.0e-04 |
| public C-section rate | data_desert vs cost_or_quality | +8.2 | +11.9 | +3.2 to +17.1 | 9.3e-15 | +4.2 | 0.007 |
| public C-section rate | care_desert vs served | -10.8 | -13.0 | -19.4 to -5.0 | 2.4e-19 | -4.8 | 1.0e-04 |

Reading: the story holds at the national level and still holds within states, at a smaller size. Care deserts also lean on public facilities (most births there that happen in a facility happen in a public one), so a high public share alone does not mark a data desert; the comparison that matters is with served districts, which have similar institutional birth rates.

## C-section under 5%

| quadrant | districts | C-section under 5% | share | median C-section % |
| --- | --- | --- | --- | --- |
| care_desert | 98 | 24 | 24.5% | 10.0 |
| data_desert | 137 | 4 | 2.9% | 19.8 |
| cost_or_quality | 135 | 9 | 6.7% | 11.5 |
| served | 335 | 2 | 0.6% | 26.1 |

Data deserts with a C-section rate under 5% (good institutional births, little surgical delivery): Rajasthan|Pratapgarh (3.6%), Uttar Pradesh|Banda (4.2%), Meghalaya|South Garo Hills (4.7%), Rajasthan|Barmer (4.9%).

## The lists

All three come from `quadrant_rule`. Columns: rank, district, state, distance (median km to grade 2+ EmOC, share of post offices within 60 km, grade 2+ facilities located in the district), outcome and its NFHS flag, public share, C-section rates, `residual` (empty until Model B), crosswalk method, `label_stability` (share of the 16 threshold pairs that give the same label) and plain-language reliability notes.

### `reports/send_care.csv` (98 districts, ranked by institutional births, lowest first (ties: farther first))

| rank | district | state | emoc_g2_dist_km_p50 | institutional_birth_pct | public_facility_birth_pct | csection_pct | label_stability | crosswalk_method |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Mon | Nagaland | 90.3 | 21.4 | 20.2 | 1.4 | 0.75 | exact |
| 2 | Phek | Nagaland | 209.1 | 32.2 | 29.2 | 1.7 | 1.0 | exact |
| 3 | Kiphire | Nagaland | 177.2 | 34.8 | 33.8 | 2.1 | 1.0 | exact |
| 4 | Tuensang | Nagaland | 141.4 | 34.8 | 34.1 | 1.5 | 1.0 | exact+parent_district |
| 5 | Zunheboto | Nagaland | 165.9 | 35.0 | 32.2 | 7.6 | 1.0 | exact |
| 6 | Longleng | Nagaland | 109.6 | 38.7 | 37.8 | 3.6 | 1.0 | exact |
| 7 | West Khasi Hills | Meghalaya | 96.0 | 41.7 | 36.1 | 4.7 | 0.75 | exact |
| 8 | Peren | Nagaland | 203.1 | 43.5 | 38.7 | 2.4 | 1.0 | exact |
| 9 | Wokha | Nagaland | 166.3 | 43.6 | 24.2 | 10.1 | 1.0 | exact |
| 10 | Ukhrul | Manipur | 268.7 | 44.6 | 21.0 | 6.4 | 1.0 | exact+parent_district |

### `reports/send_data_collectors.csv` (137 districts, ranked by distance to the nearest grade 2+ EmOC facility, farthest first)

| rank | district | state | emoc_g2_dist_km_p50 | institutional_birth_pct | public_facility_birth_pct | csection_pct | label_stability | crosswalk_method |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Nicobars | Andaman & Nicobar Islands | 1540.2 | 97.8 | 96.7 | 11.5 | 1.0 | exact |
| 2 | South Andaman | Andaman & Nicobar Islands | 1218.8 | 99.5 | 83.8 | 37.1 | 1.0 | fuzzy |
| 3 | North & Middle Andaman | Andaman & Nicobar Islands | 1151.8 | 97.7 | 95.0 | 12.9 | 1.0 | exact |
| 4 | Thoubal | Manipur | 276.2 | 87.8 | 72.4 | 20.2 | 0.75 | exact+parent_district |
| 5 | Lakshadweep | Lakshadweep | 274.8 | 99.6 | 65.3 | 31.3 | 1.0 | fuzzy |
| 6 | Imphal East | Manipur | 267.6 | 91.7 | 66.7 | 35.8 | 0.75 | exact+parent_district |
| 7 | Imphal West | Manipur | 263.5 | 95.7 | 67.6 | 44.5 | 1.0 | exact |
| 8 | Bishnupur | Manipur | 261.7 | 89.5 | 68.6 | 38.2 | 0.75 | exact |
| 9 | Dantewada | Chhattisgarh | 233.9 | 90.5 | 84.1 | 5.1 | 0.75 | exact |
| 10 | Leh(Ladakh) | Ladakh | 213.6 | 96.4 | 95.7 | 43.1 | 1.0 | exact |

### `reports/cost_or_quality.csv` (135 districts, ranked by institutional births, lowest first (ties: nearer first))

| rank | district | state | emoc_g2_dist_km_p50 | institutional_birth_pct | public_facility_birth_pct | csection_pct | label_stability | crosswalk_method |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | South West Khasi Hills | Meghalaya | 87.0 | 41.7 | 37.6 | 4.2 | 0.5 | exact |
| 2 | West Jaintia Hills | Meghalaya | 86.1 | 42.2 | 31.9 | 6.2 | 0.5 | exact |
| 3 | Kishanganj | Bihar | 75.3 | 54.6 | 41.5 | 12.1 | 0.5 | exact |
| 4 | Ribhoi | Meghalaya | 43.0 | 56.9 | 45.6 | 8.7 | 1.0 | fuzzy |
| 5 | Dumka | Jharkhand | 56.7 | 60.3 | 51.3 | 8.5 | 1.0 | exact |
| 6 | Deoghar | Jharkhand | 25.5 | 61.3 | 47.2 | 7.8 | 1.0 | exact |
| 7 | Purba Champaran | Bihar | 26.7 | 61.5 | 43.0 | 5.4 | 1.0 | fuzzy |
| 8 | Shahjahanpur | Uttar Pradesh | 74.4 | 63.3 | 47.8 | 8.6 | 0.5 | exact |
| 9 | East Khasi Hills | Meghalaya | 87.1 | 63.4 | 48.4 | 13.9 | 0.5 | exact |
| 10 | Sitamarhi | Bihar | 50.0 | 64.4 | 49.9 | 8.5 | 1.0 | exact |

4 island districts (Andaman and Nicobar, Lakshadweep) sit in `send_data_collectors` at ranks 1, 2, 3, 5: the nearest listed facility is across the sea. Their distances are real in the data but say more about geography than about missing records. Their rows carry an island note.

## Dialysis need versus access (step 11)

Need index: mean of four NFHS percentages: women and men 15+ with high or very high blood sugar or on medicine, women and men 15+ with elevated blood pressure or on medicine. Access: district median distance to grade 2+ dialysis. Cells: lower_need_good_access 293, high_need_good_access 177, lower_need_poor_access 176, high_need_poor_access 59. States with the most high-need, poor-access districts: Telangana 16, Arunachal Pradesh 8, Odisha 6, Andhra Pradesh 5, Sikkim 3, Andaman & Nicobar Islands 3. Full table: `reports/wp6_dialysis_need_vs_access.csv`. This is a burden-versus-supply comparison, not a care-versus-data call: NFHS has no dialysis outcome.

## Coverage and flags

- NFHS districts: 705 in `silver/nfhs.parquet` (706 raw rows; dropped by WP2: Mizoram|Chandel (a copy of Manipur|Chandel)); scored 705. Every scored district has an access value and an outcome (0 missing access, 0 missing outcome).
- Facility locations: WP1 checks each facility's coordinates against its own PIN. Of the facilities more than 40 km from their PIN and outside its district, 785 were moved to it because the city text named only the PIN's place, 184 were kept because it named only the coordinates' place, 123 within 100 km were kept because it named both or neither, and 282 beyond 100 km were left out for the same reason. Of the 432 grade 2+ EmOC facilities, 34 were moved and 14 left out (1 more has no coordinates at all).
- PIN-directory districts: 755; unmatched 3 (MIZORAM|SAIHA, NA|NA, WEST BENGAL|PURBA BARDHAMAN). Their geocoded post offices (682) cannot be scored. Purba Bardhaman is a large district with no NFHS row: 'no survey data', not 'no care'.
- Crosswalk method of the scored districts: exact 575, fuzzy 59, exact+parent_district 35, manual 30, fuzzy+parent_district 4, manual+parent_district 2. 41 districts pool post offices from 47 PIN districts created after the survey; their geography is inferred, not exact.
- Outcome flags for `institutional_birth_5y_pct`: ok 705. No district needed a low-sample or suppressed flag on this indicator; the code carries the flag through anyway, and the lists show it.
- Other reliability flags in the lists: 38 districts have fewer than 20 post office locations behind their access value; 25 lost more than 25% of their offices to WP5's coordinate check; 28 have more than half their offices on placeholder points (one point shared by 10 or more offices from 3 or more PINs, usually the district town; WP5 counts each such point once, so their access value rests on the offices placed elsewhere); 8 were surveyed with fewer than 600 households (NFHS-5 sampled about 900 per district).

## Caveats

- Rule quadrants only. A data desert here means poor visible access plus good outcomes; Model B will adjust the outcome reading for development, which can move districts between quadrants.
- The access reading uses grades from web pages (currently the regex baseline). No evidence of a service is not evidence that it is absent, and the facilities table is a sample that is 88% private, so true distances are often shorter.
- Distances are straight lines from post offices, not travel times from where people live.
- NFHS-5 is from 2019 to 2021; the facility pages are newer.
- The public share is consistent with hidden public care in data deserts, but it also rises with rurality. It supports the reading; it does not prove that specific public facilities are missing from the data.
- Districts are not independent: state explains most of the variation in institutional births. The intervals resample whole states, and the within-state comparison is the stricter test. Mann-Whitney p values treat districts as independent and are optimistic.
- Thresholds are relative (thirds of this distribution). Counts depend on them; see the fragility table and `label_stability`.
- District boundaries changed after the survey; 41 districts pool post offices of later splits, and name matches outside 'exact' are inferred.

## Files

- `gold/district_quadrants.parquet` (+ `.meta.json`, stage `rule_only`)
- `reports/send_care.csv`, `reports/send_data_collectors.csv`, `reports/cost_or_quality.csv`, `reports/wp6_dialysis_need_vs_access.csv` (+ `.meta.json` each)
- `reports/figures/wp6_01_quadrants_emoc.png`
- `reports/figures/wp6_02_threshold_sensitivity.png`
- `reports/figures/wp6_03_public_share_by_quadrant.png`
- `reports/figures/wp6_04_quadrants_by_state.png`
- `reports/figures/wp6_05_dialysis_need_vs_access.png`
