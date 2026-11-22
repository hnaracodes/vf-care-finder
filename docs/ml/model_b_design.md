# Model B design: the district outcome model (WP6 part 2)

Status: design only, revision 2. Nothing here has been trained. First written Nov 21, 2026 (git `369e7af`). Revised the same afternoon after a review (the "Review log" at the end lists each critique and what changed). Written for the agent that builds WP6 part 2 tonight.

Where the numbers come from: read-only probes, not committed. Revision 2 uses WP5's real `gold/district_access.parquet` (`wp5-v1`, written Sat 15:05, regex grades `v0`), joined to `silver/nfhs.parquet`, `silver/post_offices.parquet` and the crosswalk: 705 districts in 36 states. The placebo distance (section 4) was computed the same way WP5 computes access. Figures marked "earlier probe" come from revision 1's probes on the raw files and were not rerun. The correlogram in section 2 used an exploration-only fuzzy district match (657 of 706 districts). **All numbers here are previews. The builder recomputes every number it reports and never copies one from this document.**

How the thresholds were fixed: the guards, δ, the label rules and the validation checks were fixed before Model B was fit, but after previews of the rule quadrants and (in revision 2) of the M1 vs M3 comparison. They are not pre-registered in the strict sense. Say so in the report.

Related: `docs/ml/ml_ideas.md` idea 1 (spatial and hierarchical data-desert calls) extends section 8. This design keeps a sklearn-only core that fits tonight.

---

## 1. The recommendation in brief

1. **Target:** `institutional_birth_5y_pct` (IB) in percentage points (pp), predictions clipped to 0 to 100.
2. **Features:** 9 NFHS development indicators and 2 geography features, fixed by a leakage review. Health insurance moves out of the model: it identifies states (state R² 0.91) and is a public-programme footprint. Access is **one** feature, log distance to C-section evidence (EmOC grade 2+), winsorised at the 99th percentile. A **placebo** access feature built the same way with no EmOC evidence (distance to any facility) runs next to it.
3. **Validation, two co-primary tests:** leave-one-state-out (LOGO) for the predictive gain, and a within-state fixed-effects regression with state-clustered standard errors for the sign and size of the access effect.
4. **Models:** ridge (default) and histogram gradient boosting with a monotone access constraint. Ridge wins unless boosting beats it by more than the noise.
5. **Verdict (tier 1, fixed now):** "adds signal" needs all three: the LOGO gain's state-bootstrap interval above 0, a negative within-state coefficient (one-sided p < 0.05), and a result that beats the placebo. Final features are M3 only on "adds signal", otherwise M1.
6. **The call:** `p_real_gap = Φ((expected_if_served − actual − 3) / σ(ŷ))`, with σ depending on the predicted level. Absolute rules: a district can be a data desert only if its IB is at or above the rule's poor-outcome cut (preview 87.3). IB below 75 forces a confident gap. C-section below 5% counts as a poor outcome in both the rule and the model. IB of 95 or more can never be a model gap. Labels do not depend on σ.
7. **Validation of the call:** the IB-adjusted public share and IB-adjusted public C-section rate by quadrant, overall and within states, each next to a placebo version. Claim only what beats the placebo.
8. **The preview says access adds nothing.** Plan for it: the report leads with the null result, the model quadrants differ from the rule only through the development adjustment (26 districts in the preview), and the video avoids the word "surgical" for data deserts.

---

## 2. What the data says, and what it means for the design

| Fact | Number | Consequence for the design |
| --- | --- | --- |
| The target is complete | IB flag is `ok` in all 706 NFHS rows | No outcome imputation |
| The target piles up at the ceiling | Median 92.2, mean 88.7, SD 12.0 (706 rows). 138 districts at 98 or above, 92 at 99 or above, 45 at exactly 100. Ridge M1 predicts above 100 for 42 districts (then clipped) | Clip predictions. A ceiling rule for the call (section 8). Fractional logit as a tier 3 check |
| State explains most of the variance | State means explain 64.2% of IB variance (705 rows). 58% of the M1 residual variance is between states | Hold out whole states. Report state-level and district-level calls separately (`level`, tier 1) |
| Borders matter more than distance | Mean IB gap between districts 50 to 100 km apart: 6.4 pp within a state, 12.7 pp across a border (earlier probe) | Group by state, not by distance blocks |
| Few independent units | 36 states, 1 to 75 districts each. 28 states have 5 or more districts | For between-state structure the effective sample is near 36. Simple models, a bootstrap over states, clustered standard errors |
| Survey noise is real but modest | Suppression flags imply about 180 to 420 births per district. Binomial SE about 2 to 4 pp at IB 70 to 85, under 1 pp at 98+ (assumes about 300 births and a design effect of 1.5, neither verified) | Justifies the margin δ = 3 pp. Not used in σ any more |
| The null baseline | Mean of the other states: LOGO MAE 9.02 pp, RMSE 12.17 (705 rows) | Report every model against it, and against a one-index baseline (MAE 7.10) |
| Leakage is tempting | Spearman ρ with IB: skilled attendance 0.90, postnatal care 0.83, 4+ antenatal visits 0.65 | An explicit exclusion list and a test |
| Insurance identifies states | `hh_member_covered_health_insurance_pct` state R² 0.909 (Rajasthan 88%, Andaman & Nicobar 2%). For comparison, the excluded `households_surveyed` has 0.25 | Out of M1 (rule 3). Tier 1 sensitivity with it back in: 8 labels flip, Rajasthan's mean residual falls from 8.4 to 6.9 pp |
| The facility sample is thin and uneven | EmOC facilities per 1,000 post offices (WP5 `n_facilities_in_district`, states with more than 2,000 post offices): claimed (grade 1+) Himachal Pradesh 7.3, Odisha 7.4, Haryana 116 (16 times more); C-section evidence (grade 2+) Odisha 0.25, Haryana 17.2 (69 times more) | Access partly measures web presence, differently in each state. A density caption on the map |
| The sample barely contains the public system | 18 of 443 geocoded grade 2+ facilities are public. Public facilities handle 74% of institutional births (district mean) | Expect attenuation toward "no gain" |
| Far from C-section evidence means few private clinics | Raw ρ of log distance to grade 2+ with the private share of births −0.47, with IB −0.24, with the overall C-section rate −0.34. Within states, after development: +0.10 with IB, −0.03 with the C-section rate, 0.00 with the public C-section rate, −0.05 with the private share | Any public-share check is partly circular with the access axis. Placebo rows everywhere |
| Evidence-graded access is mostly density | ρ = 0.65 between distance to grade 2+ and to any facility, 0.91 for grade 1+. Grade 2 distance beyond grade 1 distance: ρ = −0.01 with IB | Test against a placebo, and expect little |
| The access features are collinear | log distance to grade 2+ vs share within 60 km: ρ = −0.95 | One feature per concept |
| Access varies between states | 45 to 49% of access variance is between states; 33 to 43% of what is left after development | LOGO mixes sampling regimes. The within-state test is the cleaner one |
| **Access adds nothing in the preview** | LOGO MAE, ridge: M1 5.99, M1 + placebo 6.02, M3 6.05 (Δ −0.057, 95% CI −0.142 to −0.004). Boosting: 6.07, 6.02, 6.05 (Δ +0.025, CI −0.19 to +0.22). Within states, grade 2 distance has the wrong sign: +0.67 pp per SD, t = +2.15 | Preview verdict "no". Section 11 is the likely path |

Mean IB by development tercile (rows) and distance-to-grade-2 tercile (columns), earlier probe:

| | Near | Mid | Far |
| --- | --- | --- | --- |
| Low development | 81.6 | 82.8 | 82.5 |
| Mid development | 90.2 | 91.5 | 84.9 |
| High development | 96.3 | 96.3 | 90.1 |

The "far, developed" cell is mostly Northeast states (31.5% of far-tercile districts, against 14.6% overall). Between states and within states the readings disagree. That is why the within-state test is co-primary.

---

## 3. Target

| Option | For | Against | Verdict |
| --- | --- | --- | --- |
| IB in pp | Complete, direct, easy to explain | Ceiling. Counts any facility birth, including normal deliveries at clinics without surgery | **Primary** |
| Fractional logit | Respects the 0 to 100 bound | Residuals harder to explain | Tier 3 sensitivity |
| C-section rate | Closest to what grade 2 EmOC means | Private overuse (up to 82%). Hospitals that advertise C-sections cluster where many are done, so under LOGO it would credit "access" with private overuse | Tier 3, **within states only, capped at 15%** (`min(cs, 15)`, 277 districts are below 15; ρ with IB 0.56). Also a shared outcome flag (below 5%) |
| Skilled attendance | Complete | ρ = 0.90 with IB | Rejected |
| Binary "poor outcome" | Simple | Throws away information | Rejected |

Model IB in pp with clipping: residuals read directly ("9 points better than expected") and MAE in pp is the number the video quotes.

**What IB does and does not show.** IB is a birth-access outcome. It does not show surgical capacity. So a "data desert" in this project is a data desert for **facility births**: births reach facilities the dataset does not show. It is not evidence of hidden C-section capacity (section 8, V2). Keep the word "surgical" off screen for data deserts. The lists carry the C-section rate so a reader can see which data deserts also show surgical reach.

No sample weights: NFHS sampled about 900 households per district (median 908).

---

## 4. Features and the leakage review

### The rule

A feature is out if it measures:

1. births or delivery care (the target's own family),
2. antenatal or postnatal care (the same pregnancy pathway),
3. contact with the public health system, or the footprint of public health programmes,
4. the survey design.

Rule 3 removes strong predictors (death registration ρ = 0.67, female sterilisation 0.52, measles vaccine 0.46). They are footprints of the public system whose invisibility we are trying to detect. With them in the model, a district with a strong hidden public system would be predicted to do well, its residual would shrink, and the data-desert call would disappear.

Features are chosen by category, never by correlation with the target. The correlations below are for understanding only.

### Core development block (9 features, all in M1)

| Feature | ρ with IB | Within-state r | Leakage verdict |
| --- | --- | --- | --- |
| `hh_electricity_pct` | +0.53 | +0.33 | Clean |
| `hh_improved_water_pct` | +0.18 | +0.23 | Clean |
| `hh_use_improved_sanitation_pct` | +0.32 | +0.28 | Clean |
| `households_using_clean_fuel_for_cooking_pct` | +0.59 | +0.45 | Clean |
| `women_age_15_49_who_are_literate_pct` | +0.40 | +0.49 | Clean |
| `women_age_15_49_with_10_or_more_years_of_schooling_pct` | +0.52 | +0.51 | Clean. VIF 4.4 with literacy, which ridge handles |
| `w20_24_married_before_age_18_years_pct` | −0.28 | −0.31 | Clean |
| `population_below_age_15_years_pct` | −0.72 | −0.57 | Allowed with care (household roster, not birth records). Tier 3 run without it |
| `child_u5_who_are_stunted_height_for_age_18_pct` | −0.45 | −0.36 | Clean |

**Moved out: `hh_member_covered_health_insurance_pct`.** Most coverage comes from government schemes, which are public-programme footprints (rule 3), and its state R² of 0.909 makes it a state identifier. In M1 it "explains" state-level public effort and shrinks state-level data-desert residuals (Rajasthan, a top data-desert state, drops from +8.4 to +6.9 pp). In the preview, removing it also lowers LOGO MAE (6.10 to 5.99, interval wide). Tier 1 keeps a one-line sensitivity run with it back in and reports the label flips (preview: 8). This choice was made after seeing that MAE, so the report says so.

### Geography block (2 features, in M1, from `silver/post_offices.parquet`)

| Feature | ρ with IB | Within-state r | Why |
| --- | --- | --- | --- |
| `po_branch_share`: share of post offices that are branch offices (`office_type == "BO"`) | −0.41 | −0.08 | Rurality. Keeps the model from crediting rurality to access |
| `log_n_post_offices` | +0.25 | +0.12 | District size, number of settlements |

### Excluded: 64 columns (copy into an `EXCLUDED` constant and test against it)

| Category | Why out | Columns |
| --- | --- | --- |
| Target and delivery care (8 plus the target) | The target, a part of it, or at delivery | `institutional_birth_in_public_facility_5y_pct`, `home_birth_that_were_conducted_by_skilled_hp_5y_10_pct`, `births_attended_by_skilled_hp_5y_10_pct`, `births_delivered_by_csection_5y_pct`, `births_in_a_private_fac_that_were_delivered_by_csection_5y_pct`, `births_in_a_public_fac_that_were_delivered_by_csection_5y_pct`, `average_out_of_pocket_expenditure_per_delivery_in_a_public_fac`, `children_born_at_home_who_were_taken_to_a_health_facility_f_pct` |
| Birth events (5) | Same births, or delivery-room practice | `sex_ratio_at_birth_5y_f_per_1000_m`, `child_u5_whose_birth_was_civil_reg_pct`, `births_in_the_5_years_preceding_the_survey_that_are_birth_3_pct`, `w15_19_who_were_already_mothers_or_pregnant_at_the_time_of_pct`, `children_under_age_3_years_breastfed_within_one_hour_of_bir_pct` |
| Antenatal and postnatal care (9) | Same pregnancy pathway | `mothers_who_had_an_anc_visit_in_the_first_trimester_lb5y_pct`, `mothers_who_had_at_least_4_anc_visits_lb5y_pct`, `mothers_whose_last_birth_was_protected_against_neo_tetanus_pct`, `mothers_who_consumed_ifa_for_100_days_or_more_when_they_wer_pct`, `mothers_who_consumed_ifa_for_180_days_or_more_when_they_wer_pct`, `registered_pregnancies_for_which_the_mother_received_a_mcp_pct`, `mothers_who_received_pnc_from_a_doctor_nurse_lhv_anm_midwif_pct`, `children_who_received_pnc_from_a_doctor_nurse_lhv_anm_midwi_pct`, `pregnant_w15_49_who_are_anaemic_lt_11_0_g_dl_22_pct` |
| Public health system contact (34) | Rule 3 | All 11 `child_12_23m_*` and `child_24_35m_*` vaccination columns, `child_9_35m_who_received_a_vit_a_in_the_last_6_months_pct`, all 8 `fp_cm_w15_49_*` method columns, `fp_unmet_total_cm_w15_49_7_pct`, `fp_unmet_spacing_cm_w15_49_7_pct`, `health_worker_ever_talked_to_female_non_users_about_family_pct`, `current_users_ever_told_about_side_effects_of_current_metho_pct`, the 3 `women_age_30_49_years_ever_undergone_*` screening columns, `deaths_in_the_last_3_years_civil_reg_pct`, the 3 `children_with_diarrhoea_2wk_*` treatment columns, `children_with_fever_or_symptoms_of_ari_2wk_taken_to_a_healt_pct`, `child_5y_who_attended_pre_primary_school_during_the_school_pct`, `w15_24_who_use_menstrual_hygiene_pct` |
| Infant feeding (5) | Mostly suppressed, partly a programme footprint | `child_u6m_exclusively_breastfed_pct`, `child_6_8m_receiving_solid_or_semi_solid_food_and_breastmil_pct`, `breastfeeding_child_6_23m_receiving_an_adequate_diet16_17_pct`, `non_breastfeeding_child_6_23m_receiving_an_adequate_diet16_pct`, `total_child_6_23m_receiving_an_adequate_diet16_17_pct` |
| Survey design (3) | Encode state sample allocation | `households_surveyed`, `women_15_49_interviewed`, `men_15_54_interviewed` |

### Allowed but held back (33 columns, tier 3 only)

`hh_member_covered_health_insurance_pct` (see above; its tier 1 sensitivity run is the exception), `female_population_age_6_years_and_above_ever_schooled_pct`, `sex_ratio_total_f_per_1000_m`, `households_using_iodized_salt_pct`, `prev_diarrhoea_2wk_child_u5_pct`, `children_prev_symptoms_of_acute_respiratory_infection_ari_2_pct`, the other child anthropometry columns (wasted, severe wasted, underweight, overweight), the 3 women's BMI and waist-hip columns, the 4 non-pregnancy anaemia columns, the 12 blood sugar and blood pressure columns, and the 4 tobacco and alcohol columns.

### Access block: one feature per concept (from WP5 `gold/district_access.parquet`, `service_line == "emoc"`)

| Feature | Built from | Preview ρ with IB / within-state r | Role |
| --- | --- | --- | --- |
| `acc_g2_logp50` | `log1p(dist_km_p50)` at `min_grade == 2`, winsorised | −0.24 / −0.16 | **The access feature.** Distance to C-section evidence, the same reading the quadrants use |
| `acc_any_logp50` (placebo) | Median over the district's post offices of `log1p` distance to the nearest facility of any kind, winsorised | −0.29 / −0.22 | **Placebo.** Same construction, no EmOC evidence at all. Not an access feature: it answers "would plain sample density do the same?" |
| `acc_g1_logp50` | `log1p(dist_km_p50)` at `min_grade == 1` | −0.29 / −0.22 | Tier 2 only (claims-level reach, ρ 0.91 with the placebo) |

Dropped: `acc_g2_share60` (ρ = −0.95 with `acc_g2_logp50`; together their coefficients split with opposite signs and the counterfactual would depend on an arbitrary split). Also left out: `n_facilities_in_district` (web presence, 202 zeros), grade 3 (43 facilities), `dist_km_p75/p90`, `share_within_30km`, dialysis and ICU access.

**Placebo construction** (WP6 computes it, because it is a control, not an access feature; it uses WP5's post office set and method): post offices from `gold/post_office_access.parquet` with `district_key` not null and `coord_suspect` false; facilities from `silver/facilities.parquet` with `coord_valid` (9,953, the set WP5 uses); haversine `BallTree`; district median. About 10 lines (section 12).

**Winsorising:** cap every log distance at its 99th percentile over the 705 districts. The cap uses no outcome values, so it cannot leak. Preview cap for grade 2+: 267 km. It caps 8 districts: the 3 Andaman & Nicobar districts (1,152 to 1,540 km, IB 97.7 to 99.5), Lakshadweep (275 km), and 4 Manipur districts (just above the cap). Without it, the islands are high-leverage points that tilt any slope toward "far is fine".

**Islands:** Andaman & Nicobar Islands and Lakshadweep (4 districts) get `island = True`. They stay in the model with capped distances and keep their quadrant label, but the lists put them last with the note "island: distance to mainland facilities is not comparable".

Leakage review of the access block: facility locations and grades only, no NFHS values. Its risk is confounding.

### How the facility sample shapes the access features

1. **Distances are upper bounds.** The true nearest C-section facility is often closer, most where the sample is thinnest.
2. **Thinness differs by state.** Grade 2+ density varies 69-fold across large states. Between states, "far from care in the data" partly means "a state with few web pages".
3. **The sample misses the public system.** 18 of 443 grade 2+ facilities are public.
4. **"Far" means "few private clinics".** With an 88% private sample, distance to grade 2+ tracks the private share of births (ρ −0.47). Any check based on public share is partly the access axis seen again. Hence the placebo.
5. **Noisy features bias the gain toward zero** (attenuation). A null means "no detectable signal in this sample's access", not "access does not matter".
6. **Location errors:** 16.2% of facilities sit more than 50 km from their own PIN's post offices (`docs/ml/ml_ideas.md`).
7. **Grades will change.** Record `grades_method` and `recipe_version` from the WP5 meta file (now `regex`, `v0`). Rerun when WP4's grades land.
8. **Crosswalk quality varies:** 575 exact, 59 fuzzy, 30 manual, 41 parent-district. Carry the least certain method per district.

---

## 5. Validation design

### Co-primary 1: leave one state out (LOGO), for the predictive gain

Each district's out-of-fold (OOF) prediction comes from a model trained on the other 35 states. Neither the state's policy nor its sampling regime can leak into its own prediction. LOGO gives every residual, `p_real_gap`, the pooled MAE and the state bootstrap. Caveat: LOGO mostly measures transfer between states, where web density varies 16 to 69 times. That is why it is not the only test.

### Co-primary 2: within-state fixed effects, for the access effect

Demean IB and every feature by state, run OLS of IB on the 11 M1 features plus **one** access feature, and use cluster-robust (CR1) standard errors by state with a t distribution on G − 1 = 35 degrees of freedom. Report the coefficient in pp per within-state SD. Test one-sided: farther from C-section evidence should mean lower IB. Run it for `acc_g2_logp50` and, separately, for the placebo. A few lines of numpy, under a second (section 12).

Preview (controls: 9 development + 2 geography): `acc_g2_logp50` +0.67 pp per SD (SE 0.31, t = +2.15, one-sided p = 0.98). Placebo −0.08 (t = −0.28). `acc_g1_logp50` −0.12 (t = −0.44). Within states, after development, districts farther from C-section evidence do slightly **better**, not worse.

This replaces revision 1's within-state ridge with scaled state dummies and KMeans-cluster CV. That model would also have been broken by the pipeline: a `StandardScaler` in front of it undoes the ×10 scaling of the dummies. The demeaned regression gives the same within-state answer with nothing to tune.

### Fold spread

WP6's acceptance asks for a fold spread. Tier 1 reports it from the LOGO folds: for the 28 states with 5 or more districts, the mean ± SD of the per-state MAE difference (M1 minus M3), and the number of states where M3 is better. Tier 2 adds 20 repeats of `GroupKFold(5, shuffle=True, random_state=r)` (sklearn 1.9.1) for mean ± SD over 100 folds.

### Not used tonight

- Distance-based spatial blocking: similarity follows state borders, so blocks smaller than a state leak the state effect.
- Buffered LOGO (dropping training districts within 50 km of the held-out state): tier 3.

### Tuning rules

- Ridge: `GridSearchCV` over `alpha` in `np.logspace(-2, 3, 11)`, inner `GroupKFold(5)` on the training states, fit with `groups=`, `n_jobs=1`. Not `RidgeCV`'s leave-one-out, which leaks neighbours.
- Boosting: fixed hyperparameters (section 6), no tuning.
- Standardisation inside the pipeline. Features fixed by section 4.
- Threads: `os.environ.setdefault("OMP_NUM_THREADS", "2")` at the top of the script, before importing numpy or sklearn. The laptop is shared with other agents.

---

## 6. Models

### The ladder (tier 1)

| Id | Features | Question it answers |
| --- | --- | --- |
| M0 | none (training mean) | Reference |
| DEV1 | one development index: `StandardScaler → PCA(1) → Ridge` on the 11 M1 features, PCA fit inside each fold | How much does a single index capture? |
| M1 | 9 development + 2 geography | Development alone |
| P | M1 + `acc_any_logp50` (placebo) | Does plain sample density help? |
| M3 | M1 + `acc_g2_logp50` | Does distance to C-section evidence help? **Headline: M1 vs M3** |
| M1+ins | M1 + insurance | Sensitivity for the moved feature |

Tier 2 adds M2 = M1 + `acc_g1_logp50`, and M4 = M1 + g1 + g2 ("does evidence add beyond claims?").

Preview LOGO MAE (pp):

| Rung | Ridge | Boosting |
| --- | --- | --- |
| M0 | 9.02 | |
| DEV1 | 7.10 | |
| M1 | 5.99 | 6.07 |
| P (placebo) | 6.02 | 6.02 |
| M3 | 6.05 | 6.05 |
| M1+ins | 6.10 | 6.10 |

One index takes MAE from 9.02 to 7.10, which is 63% of M1's total gain.

### Learners

| Model | Verdict |
| --- | --- |
| Ridge, standardised features | **Required, default.** Coefficients read as "pp per SD", contributions are exact additive explanations |
| `HistGradientBoostingRegressor` | **Required.** Fixed: `learning_rate=0.05, max_iter=300, max_leaf_nodes=8, min_samples_leaf=20, l2_regularization=1.0, early_stopping=False, random_state=0`, `monotonic_cst` −1 for `acc_g2_logp50` and `acc_any_logp50` (and `acc_g1_logp50` in tier 2), 0 elsewhere |
| Fractional logit, spline ridge, region features | Tier 3 sensitivity |
| Spatial lag of y | **Rejected**: leaks the target and explains away regional hidden care |
| Neural nets, stacking, heavy tuning | **Rejected** |

**Final learner (fixed now):** ridge, unless boosting's M1 LOGO MAE is lower with a 95% state-bootstrap interval for the difference that excludes zero. Preview: ridge (5.99 vs 6.07).

**Final features (fixed now):** M3 only if the verdict is "adds signal". Otherwise M1.

**Sign-constrained ridge for the counterfactual.** If the final model is M3 ridge, its access coefficient must be ≤ 0 (farther cannot raise IB). With a single access feature this needs no optimiser: if a fold's fitted coefficient is positive, the constrained optimum has the coefficient at exactly 0, which is the same ridge (same alpha, same scaler) refit without the access column. Under KKT this is exact for one bounded coefficient. In the preview the coefficient is positive in 64% of LOGO folds, so the constraint matters. Boosting gets the same property from `monotonic_cst`. The ablation uses the unconstrained fits. Only the final model's `predicted_outcome` and `expected_if_served` use the constrained one.

---

## 7. The ablation, the verdict, and telling a real gain from noise

### Metrics

MAE in pp (primary), RMSE and pooled OOF R² for completeness, and within-state Spearman between OOF predictions and actual values.

### Tier 1 checks, all on the same LOGO predictions

1. **State bootstrap:** resample the 36 states 2,000 times and recompute the pooled MAE difference from the fixed LOGO predictions. 2.5th and 97.5th percentiles.
2. **Within-state FE test** (section 5), for `acc_g2_logp50` and for the placebo.
3. **Placebo comparison:** Δ(M1→P) next to Δ(M1→M3), and t(placebo) next to t(g2).
4. **Per-state spread:** mean ± SD of the per-state Δ over the 28 states with 5 or more districts, and how many improve.

Preview: Δ(M1→M3) ridge −0.057 (CI −0.142 to −0.004), boosting +0.025 (CI −0.19 to +0.22). Δ(M1→P) ridge −0.027 (CI −0.059 to +0.003), boosting +0.051 (CI −0.14 to +0.25). Revision 1's three-feature block was worse still (ridge −0.090, CI −0.179 to −0.017).

### Verdict (tier 1, fixed now)

With the final learner (section 6):

- **A:** the state-bootstrap 95% interval for Δ(M1→M3) has its lower bound above 0.
- **B:** the within-state coefficient of `acc_g2_logp50` is negative with one-sided p < 0.05.
- **C:** M3 beats the placebo on both tests: Δ(M1→M3) > Δ(M1→P), and t(g2) < t(placebo).

| Verdict | Condition | Final features | Report wording |
| --- | --- | --- | --- |
| **adds signal** | A and B and C | M3 | "Distance to C-section evidence improves out-of-state error by X pp (CI), and within states farther districts do worse, beyond what plain facility density shows." |
| **weak** | A or B, but not all three | M1 | Say which check failed. If only C failed: "the density of the web sample carries signal; C-section evidence adds nothing beyond it." |
| **no** | neither A nor B | M1 | Section 11 |

The three levels do not overlap, and all are decidable in tier 1. The preview gives "no" (A fails, B has the wrong sign). Also report Δ as a share of M1's MAE. Tier 2 results go next to the verdict, and if they disagree the report says so. They do not change the verdict, so the final model does not flip at 03:00.

### Tier 2: within-state permutation test

Residualise `acc_g2_logp50` on the M1 features (state-demeaned). Shuffle the residual **within each state**, add the fitted part back, and recompute both the FE t and the LOGO Δ (ridge with alpha fixed at the median the real run chose). B = 500 for the FE t (instant), B = 200 for LOGO. p = (1 + number of null values at least as extreme) / (B + 1). Why within states: shuffling across all districts destroys the feature's state clustering (37% of its development-adjusted variance is between states), so the null spread would be too narrow. For the between-state part, the placebo is the empirical reference: there is no honest permutation with 36 clusters. Report which null was used.

### Tier 2: power check under measurement error

Without this, a null cannot be interpreted, and a check on a clean feature overstates power.

1. True access A*: the fitted part of `acc_g2_logp50` from development, plus its leftover shuffled within states.
2. Observed access: A = A* + noise, with the noise variance set so the reliability of the leftover is R ∈ {1.0, 0.7, 0.5}.
3. Plant the effect on the truth: `y_syn = clip(y − β · (A* − mean(A*)) / ln 2, 0, 100)` for β ∈ {0, 1, 2, 3} pp per doubling of true distance.
4. Fit with the observed A and apply the **full tier 1 verdict** (A, B and C, with the real placebo). Ridge with fixed alpha, 20 runs per cell, about a minute.
5. Report: "an effect of N pp per doubling of true distance, at reliability R, would have been called 'adds signal' X% of the time." At β = 0 the rate should be at most about 5%.

The reviewer's mini check (bootstrap rule only, 8 runs): with a clean feature, β = 2 was detected 8 of 8 times and β = 1 once in 8. At reliability 0.5, β = 2 was detected 4 of 8 times and β = 3 6 of 8.

---

## 8. From predictions to `quadrant_model` and `p_real_gap`

### Why expected-if-served, and what it becomes in the null case

The call compares each district's actual IB with the IB it would be expected to have if the data showed it as well served (`expected_if_served`, ŷ_served). A plain residual from the access model is circular (it already subtracts the access penalty). The likelihood-ratio option collapses to 0.5 when the access effect is small.

**In the null case (final features M1), ŷ_served = ŷ.** The shortfall is then simply "below development peers", and the call carries no access information. Left unbounded, that excuses poor districts: in the preview, revision 1's rules would have turned 33 rule care deserts into model "data deserts" (mean IB 83.6, predicted 81.4; Assam 9, Madhya Pradesh 8), such as Karimganj (IB 76.4, predicted 75.0) and Cachar (79.2 vs 72.6). The thesis defines a data desert as poor access **and a good outcome**. Hence the absolute rules below: the model can only ever add gaps to the rule's reading, never remove them.

### Definitions (all from the LOGO fold that did not see the district's state)

- `predicted_outcome` ŷ: OOF prediction of the final model (contract column).
- `residual` r = y − ŷ (contract column).
- **Served reference** (only when the final features are M3): within each tercile of `po_branch_share`, take the third of districts with the lowest `acc_g2_logp50` and use their median. Comparing a rural district with rural districts that are near care keeps the counterfactual on the support of the data.
- `expected_if_served` ŷ_served: predict again with `acc_g2_logp50` set to `min(own, reference)`, using the sign-constrained final model, so ŷ_served ≥ ŷ for both learners. Equal to ŷ when the final features are M1.
- `shortfall` = ŷ_served − y.

### σ as a function of the predicted level

Model error is far from constant. Preview residual SD by ŷ band: 13.1 pp below 80, 10.4 at 80 to 90, 6.2 at 90 to 95, and 3.9 above 95. Revision 1's single σ (k-scaled survey noise; preview σ_i 6.8 to 7.3 pp, not the 10 it assumed) passed the overall 80% check but covered 58% below ŷ = 80 and 96% above 95. It was overconfident exactly where the care deserts are.

```text
for each ŷ quintile j (edges from the training districts' ŷ):
    scale_j = quantile(|r| in bin j, 0.80) / 1.2816        # 80% of residuals inside ±1.2816 σ
σ(ŷ) = linear interpolation of scale_j between the bins' median ŷ, flat beyond the end bins
σ_i  = that curve evaluated at ŷ_i, with the curve fit on the OTHER 35 states' OOF residuals
```

Preview curve: 10.8 pp at ŷ ≈ 79, 8.6 at 85, 6.8 at 89, 4.8 at 93, 2.4 at 98.5. Coverage of the 80% interval: 79% below 80, 78% at 80 to 90, 77% at 90 to 95, 84% above 95, and 79% overall.

The N_BIRTHS / DEFF survey-noise term is dropped from σ. It varied σ by only 2% and added two unverified assumptions. Survey noise survives only as the justification for δ, and N_BIRTHS (300) and DEFF (1.5) are listed under assumptions.

**The labels do not depend on σ.** p_raw ≥ 0.5 exactly when shortfall ≥ δ. σ affects only the continuous `p_real_gap` (WP7's weights) and the `confident` flag.

### `p_real_gap` and the absolute rules

```text
IB_CUT  = the rule's poor-outcome cut (bottom third of IB; preview 87.3), read from quadrant_rule's record
IB_HIGH = 95                       fewer than one birth in twenty at home
δ       = 3 pp

p_raw = Φ((shortfall − δ) / σ_i)
p = p_raw
if IB ≥ IB_HIGH:             p = min(p, 0.3)    # ceiling: the model cannot call a gap here
if IB < IB_CUT or CS < 5:    p = max(p, 0.5)    # floor: a poor absolute outcome is always a gap
if IB < 75:                  p = max(p, 0.9)    # strong floor: one birth in four or more at home
gap = p ≥ 0.5
```

The floors come after the ceiling, so the C-section flag wins in the rare district with IB ≥ 95 and C-section below 5%.

Why each rule:

- **Floor at the rule cut:** a district can be a data desert only if its outcome is good in absolute terms. Districts between 75 and the cut get p = 0.5 at least: a gap, but not a confident one.
- **Strong floor below 75:** more than one birth in four at home is a confident gap, whatever development predicts.
- **C-section below 5%:** no sign of surgical obstetric care in the outcome. This is WP6's second flag. It is applied **the same way in `quadrant_rule`**, so every difference between the rule and the model comes from the development adjustment alone. It touches 6 districts with IB at or above the cut (for example Barmer, IB 93.3, C-section 4.9%).
- **Ceiling at 95:** ridge predicts above 100 for 42 districts. Before this rule, 8 of the model's 34 label changes had IB ≥ 95, 7 of them with ŷ clipped at 100. Nellore (IB 97.0, C-section 42%) would have become a "care desert". At this level a 3 pp shortfall sits inside the model's error (residual SD 3.9). The fractional logit (tier 3) is the principled fix. The ceiling stops the worst on-screen error tonight. Preview: it caps 19 districts, 8 of which would otherwise be gaps.

Worked examples with the preview σ curve: at ŷ ≈ 85 (σ 8.6), a shortfall of +15 gives p = 0.92, 0 gives 0.36, and −10 gives 0.07. At ŷ ≈ 98.5 (σ 2.4), a shortfall of +5 for a district at IB 93.5 gives 0.80.

`guard` column values (first that applies): `ib_below_75`, `ib_below_rule_cut`, `csection_below_5`, `ib_ceiling` (only when the cap changed p), `none`. Preview counts: 81, 152, 6, 19, 447. Keep the unadjusted value as `p_real_gap_raw`.

Preview distribution: mean 0.35, median 0.23, 38% at or above 0.5, 75% confident.

### `quadrant_model`

```text
access_poor = acc_g2 dist_km_p50 >= ACCESS_CUT     # the same cut as quadrant_rule (top third; preview 84.4 km)
care_desert     = access_poor and gap
data_desert     = access_poor and not gap          # implies IB >= IB_CUT and CS >= 5
cost_or_quality = not access_poor and gap
served          = not access_poor and not gap
```

`quadrant_rule` uses the same access cut and `gap_rule = IB < IB_CUT or CS < 5`. So the model can move a district only from data desert to care desert, or from served to cost_or_quality, and only through the development adjustment.

Preview (rule with the C-section flag: care 108, data 127, cost 131, served 339; model: care 109, data 126, cost 156, served 314): **26 districts change**. 1 data desert becomes a care desert, and 25 served districts become cost_or_quality (below their development peers despite good access and IB of 87 to 95). No model data desert has IB below the cut. **In the null case these changes come from the development adjustment, not from access. Say that on the methods card.**

Flags (tier 1): `confident` = p ≤ 0.3 or p ≥ 0.7. (`stable` moves to tier 2: the same label in at least 80% of 20 LOGO refits on bootstrapped training states, times both learners. Revision 1 mixed repeated 5-fold labels with LOGO-based p.)

### State-level or district-level call (`level`, tier 1)

58% of residual variance is between states. The top 5 states (Madhya Pradesh 17, Odisha 17, Telangana 13, Karnataka 12, Rajasthan 12) hold 56% of the preview's data deserts, and 7 states (53 districts) have at least 80% of their districts access-poor. Without a split, the map shows states, not districts.

```text
σ_w² = pooled within-state variance of r
τ²   = max(var(state means of r) − mean(σ_w² / n_s), 0)
u_s  = n_s τ² / (n_s τ² + σ_w²) × mean_s(r)        # state effect, shrunk toward 0 for small states
r_within = r − u_s
level = "state" if u_s > 0 and u_s ≥ r_within else "district"
```

Preview: 62 of the 126 data deserts are state-level and 64 district-level. `level` and `residual_within` go in the sidecar (section 12) and, if the orchestrator agrees, in `districts.json`. Captions: a state-level data desert is "this state's public system is invisible in the data". A district-level one is "this district stands out even within its state". Add a caption or layer with grade 2+ facilities per 1,000 post offices by state (`state_g2_per_1000_po` in the sidecar), so viewers can see where sample thinness drives "access-poor".

### Validating the call

Fixed before the model is fit. Do not tune anything to improve them. All are computed on labels rebuilt **without the C-section rule** (the IB floors and the ceiling only), so that no C-section variable both sets labels and validates them. Each check is reported for the rule, the model and a **placebo** quadrant set: the same outcome reading with access-poor defined by `acc_any_logp50` (top third). A claim stands only where the real access beats the placebo.

"IB-adjusted" means minus the mean of districts in the same IB decile. "Within state" means also demeaned by state.

- **V1 (public share):** IB-adjusted public fraction (public births ÷ IB) by quadrant. Compare data deserts with served districts. Wording: "where the web sample shows few clinics, the public system carries births". It is **not** evidence of hidden EmOC.
- **V2 (public C-section rate, matched IB):** IB-adjusted public C-section rate. Compare at matched IB: data_desert vs served, and care_desert vs cost_or_quality. Never data vs care, whose IB differs by 22 points (Spearman of IB with the public C-section rate is 0.62).
- **V3 (label changes):** for the districts that changed, their IB-adjusted V1 and V2 values against the group they left and the group they joined.
- **V4 (tier 2):** share of districts with C-section below 5% by quadrant, on the same unflagged labels.

Preview (IB-adjusted, overall / within state):

| Quadrant set | Quadrant | n | Mean IB | Public fraction, adj. | Public C-section, adj. (pp) |
| --- | --- | --- | --- | --- | --- |
| Rule | care_desert | 105 | 72.5 | +0.062 / +0.010 | +0.83 / +1.12 |
| | data_desert | 130 | 94.5 | +0.075 / +0.023 | −0.56 / −1.10 |
| | cost_or_quality | 128 | 77.9 | −0.059 / −0.032 | −0.45 / +1.60 |
| | served | 342 | 95.5 | −0.026 / 0.000 | +0.13 / −0.53 |
| Model | care_desert | 106 | 72.6 | +0.060 / +0.010 | +0.86 / +1.09 |
| | data_desert | 129 | 94.6 | +0.077 / +0.023 | −0.59 / −1.10 |
| | cost_or_quality | 153 | 80.0 | −0.066 / −0.037 | +1.12 / +1.99 |
| | served | 317 | 95.9 | −0.020 / +0.005 | −0.59 / −0.88 |
| Placebo | data_desert | 128 | 94.1 | +0.089 / +0.027 | −2.25 / −1.13 |
| | served | 318 | 96.1 | −0.024 / +0.004 | +0.08 / −0.86 |

Mann-Whitney, data deserts vs served on the IB-adjusted public fraction: rule p = 4e-10 overall and 0.016 within states; model 3e-9 and 0.039; placebo 5e-11 and 0.031.

Reading the preview:

- V1 holds in direction (data deserts lean more on public facilities than their IB peers, mostly between states). The placebo does as well or better, so V1 measures sample geometry, not EmOC evidence.
- V2 does **not** support hidden surgical capacity. At matched IB, the public C-section rate in data deserts equals that of served districts overall and is lower within states. Revision 1's "sharpest check" (19.8 vs 22.3 vs 9.2) was an IB effect. Drop the line "public facilities in data deserts perform surgery the sample does not show" unless the final, adjusted V2 shows data deserts above served districts within states.
- Of the 126 model data deserts, 22 have an overall C-section rate below 10% and 12 a public C-section rate below 5%. The lists show these.

Limits: there is no ground truth for "real gap". `p_real_gap` is a score calibrated for interval coverage by predicted level and checked against proxies. It is not a probability validated against true labels. Say that on the methods card.

---

## 9. Uncertainty reporting

- **Model level:** MAE for each rung and learner, with the 95% state-bootstrap interval. Δ(M1→M3) and Δ(M1→P) with intervals. The FE coefficients with CR1 SE, t and the one-sided p. The per-state spread. Tier 2: the within-state permutation p, the power table and the 20 × 5 repeats.
- **District level:** 80% interval ŷ ± 1.2816 σ_i, with OOF coverage **in each ŷ quintile** (not by actual IB, which selects on the residual). Also `p_real_gap` and `p_real_gap_raw`.
- **Label level:** quadrant counts for the rule and the model, the cross-table, the number changed, and the number each absolute rule touched.
- **Assumptions:** N_BIRTHS = 300 and DEFF = 1.5 (behind δ only), δ = 3, IB_HIGH = 95, the label cut 0.5. Check DEFF against the NFHS-5 sampling-error appendix if time allows.

---

## 10. Explaining the model (video and methods card)

No `shap` package is needed.

- **Ridge contributions:** coefficient × standardised value. A waterfall for one data desert and one care desert (tier 2).
- **Access effect:** in the null case, a partial-dependence plot of a wrong-signed or constrained feature confuses. Draw the within-state added-variable plot instead: development-adjusted, state-demeaned IB against development-adjusted, state-demeaned `acc_g2_logp50`, with the placebo panel beside it. This is the FE test drawn as a picture. Draw the partial dependence with ICE lines only if the verdict is "adds signal".
- **Importance:** `permutation_importance` on held-out folds (tier 2).

Figures, matplotlib, `reports/figures/`:

| File | Content | Tier |
| --- | --- | --- |
| `wp6_b1_ablation.png` | MAE per rung (M0, DEV1, M1, P, M3, M1+ins), ridge and boosting side by side, interval bars, dashed null line | 1 |
| `wp6_b2_expected_vs_actual.png` | x: `expected_if_served`, y: actual IB, colour: `quadrant_model`, the diagonal with a ±δ band, horizontal lines at IB_CUT and 95, hollow markers for districts set by a guard, 5 named examples | 1 |
| `wp6_b3_permutation_and_power.png` | Within-state null histogram with the observed t; detection rate against β, one line per reliability | 2 |
| `wp6_b4_within_state_access.png` | Added-variable plots: g2 and placebo | 2 |
| `wp6_b5_waterfall_<district>.png` | Ridge contributions, 2 districts | 2 |
| `wp6_b6_validation.png` | V1 and V2 by quadrant: rule, model, placebo | 2 |
| `wp6_b7_rule_vs_model.png` | Cross-table heatmap | 2 |
| `wp6_b8_state_vs_district.png` | Shrunk state effects with intervals | 2 |

---

## 11. If access adds nothing (the preview's outcome)

What it means: once development is known, the web-visible facility sample tells us nothing more about where women give birth in a facility. Within states, districts far from C-section evidence do slightly better. The likely reason is that the sample mostly shows private hospitals, while public facilities handle about three quarters of institutional births. That is consistent with the data-desert thesis, but it is not proof of it. The placebo shows the same public-share pattern with no EmOC evidence at all.

What to do:

1. Report's first line: "Adding distance to C-section evidence did not improve out-of-state error (MAE X vs Y pp, 95% interval for the difference A to B), and within states it had the wrong sign (t = T)."
2. Tier 2 power line: "an effect of N pp per doubling of true distance, at reliability R, would have been detected X% of the time."
3. Final features M1. The model quadrants differ from the rule only through the development adjustment (preview: 26 districts, almost all served → cost_or_quality). The absolute rules keep low-outcome districts from becoming data deserts.
4. The methods card says: "In the null case, every change from the rule comes from the development adjustment, not from access."
5. Video wording for beat 6, which is defensible with the floor (every data desert has IB ≥ the cut): "In these districts, [P]% of births happen in a facility, yet the dataset shows no facility with C-section evidence within [X] km. Births are reaching care the data cannot see." Do not say "surgical care exists". For state-level data deserts: "this state's public system is invisible in the data".
6. Do not add features or tune until access "works". Allowed follow-ups: the tier 2 checks, and the tier 3 within-state capped C-section target.

If the placebo helps but grade 2 does not: the sample's signal is web density, not evidence of surgical care. Say so. It matters for Model A's pitch too.

---

## 12. Build plan

### Files

- `scripts/wp6_outcome_model.py`: stages `rule` (WP6 part 1, if not built separately), `model`, `lists`, `all`. It writes `gold/district_quadrants.parquet` once, with both rule and model columns. Flags: `--tier 1|2|3`.
- `tests/test_wp6_outcome_model.py`
- Outputs, each with a `.meta.json` sidecar, written atomically:
  - `gold/district_quadrants.parquet`: **contract columns only** (no contract change tonight).
  - `gold/model_b_district_detail.parquet`: the sidecar with the extra columns (below).
  - `gold/model_b_report.json`.
  - `reports/send_care.csv`, `reports/send_data_collectors.csv`, `reports/cost_or_quality.csv`.
  - `reports/figures/wp6_b1_*.png`, `wp6_b2_*.png` (tier 1).

### Tier 1 (the minimum that meets WP6; build this first)

1. District table, the placebo distance, winsorising, island flag, `EXCLUDED` guard and tests.
2. Ridge and boosting for M0, DEV1, M1, P, M3, M1+ins under LOGO. State bootstrap, per-state spread, within-state FE test for g2 and the placebo, verdict.
3. Final model. The counterfactual if M3. σ by ŷ band. `p_real_gap` with the absolute rules. Labels, `confident`, `level`.
4. V1 and V2 (IB-adjusted, overall and within state, rule vs model vs placebo).
5. Outputs, the three lists, figures b1 and b2.

### Tier 2

Within-state permutation test, power check under measurement error, `stable` flag, 20 × 5 repeated CV, M2 and M4 rungs, the exact-match-only rerun, V3 and V4, figures b3 to b8, and a wild cluster bootstrap for the FE test (optional).

### Tier 3

Within-state capped C-section target (`min(cs, 15)`, FE regression only, never under LOGO), fractional logit, held-back features, region features, the run without `population_below_age_15_years_pct`, the likelihood-ratio diagnostic, and buffered LOGO.

### Step 0. Preconditions

Check that `silver/nfhs.parquet`, `silver/nfhs_flags.parquet`, `silver/post_offices.parquet`, `silver/district_crosswalk.parquet`, `silver/facilities.parquet`, `gold/district_access.parquet` and `gold/post_office_access.parquet` exist. All existed at Sat 15:05. Record `grades_method` and `recipe_version` from the WP5 meta file. If WP5's files go missing, run M1 only, write the report with `"access": "pending"`, and return "blocked: gold/district_access.parquet" for the access parts. Never compute the access feature itself (that is WP5's), only the placebo.

### Step 1. District table (705 rows, one per `district_key`)

```python
import os
os.environ.setdefault("OMP_NUM_THREADS", "2")          # before numpy/sklearn: shared laptop

CORE = ["hh_electricity_pct", "hh_improved_water_pct", "hh_use_improved_sanitation_pct",
        "households_using_clean_fuel_for_cooking_pct", "women_age_15_49_who_are_literate_pct",
        "women_age_15_49_with_10_or_more_years_of_schooling_pct", "w20_24_married_before_age_18_years_pct",
        "population_below_age_15_years_pct", "child_u5_who_are_stunted_height_for_age_18_pct"]
INSURANCE = "hh_member_covered_health_insurance_pct"     # held back; tier 1 sensitivity only
GEO = ["po_branch_share", "log_n_post_offices"]
ACC, PLACEBO = "acc_g2_logp50", "acc_any_logp50"
LADDER = {"M1": CORE + GEO, "P": CORE + GEO + [PLACEBO], "M3": CORE + GEO + [ACC],
          "M1_ins": CORE + GEO + [INSURANCE]}           # DEV1 uses M1's columns through PCA(1)
MONO = {ACC: -1, PLACEBO: -1, "acc_g1_logp50": -1}

# t: the district table, indexed by district_key (NFHS rows joined to post office features)
t = t.drop(index="Mizoram|Chandel", errors="ignore")    # exact duplicate of Manipur|Chandel; WP5 excludes it too.
                                                         # Drop it explicitly so an NFHS-only fallback cannot leak it across folds
# access (WP5): pivot emoc rows; keep the raw km for the cut and the lists
e = access[access.service_line == "emoc"].set_index(["district_key", "min_grade"])
t["access_km"] = e.xs(2, level="min_grade").dist_km_p50
t[ACC] = np.log1p(t.access_km)

# placebo: WP5's post office set, every facility with valid coordinates, same haversine method
poa = pd.read_parquet(gold / "post_office_access.parquet")
poa = poa[poa.district_key.notna() & ~poa.coord_suspect]
fac = pd.read_parquet(silver / "facilities.parquet").query("coord_valid")
tree = BallTree(np.radians(fac[["lat", "lon"]].to_numpy()), metric="haversine")
poa["d_any"] = tree.query(np.radians(poa[["lat", "lon"]].to_numpy()), k=1)[0][:, 0] * 6371.0
t[PLACEBO] = np.log1p(poa.groupby("district_key").d_any.median())

for c in [ACC, PLACEBO, "acc_g1_logp50"]:                # winsorise; uses no outcome values
    if c in t: t[c] = np.minimum(t[c], t[c].quantile(0.99))
t["island"] = t.state_ut.isin(["Andaman & Nicobar Islands", "Lakshadweep"])
assert len(t) == 705 and "Mizoram|Chandel" not in t.index
```

Also: `po_branch_share` and `log_n_post_offices` from `silver/post_offices.parquet` (`coord_valid` and `district_key` not null); the least certain `match_method` per district (exact < fuzzy < manual < parent_district); the NFHS target, flags, features and validation columns; state facility density from WP5's `n_facilities_in_district` and `n_post_offices` at grades 1 and 2. Assert that no feature is in `EXCLUDED`, that every NFHS indicator falls in exactly one category, and that the core features have no nulls. Check the state names against the NFHS file before relying on the island list.

### Step 2. Learners and the OOF harness

```python
def make_ridge(dev1=False):
    steps = [StandardScaler()] + ([PCA(n_components=1)] if dev1 else []) + [Ridge()]
    return GridSearchCV(make_pipeline(*steps), {"ridge__alpha": np.logspace(-2, 3, 11)},
                        cv=GroupKFold(5), scoring="neg_mean_absolute_error", n_jobs=1)

def make_gbm(cols):
    return HistGradientBoostingRegressor(learning_rate=0.05, max_iter=300, max_leaf_nodes=8,
        min_samples_leaf=20, l2_regularization=1.0, early_stopping=False, random_state=0,
        monotonic_cst=[MONO.get(c, 0) for c in cols])

def oof(make, X, y, groups, splits, X_served=None, constrain_col=None):
    """make: a zero-argument factory, e.g. make_ridge or (lambda: make_gbm(cols)).
    constrain_col: index of the access column whose ridge coefficient must be <= 0 (final model only)."""
    pred = np.full(len(y), np.nan); pred_s = np.full(len(y), np.nan); coefs = []
    for tr, te in splits:
        assert not set(groups[te]) & set(groups[tr])          # never see the held-out state
        m = make()
        if isinstance(m, GridSearchCV):
            m.fit(X[tr], y[tr], groups=groups[tr])
            coefs.append(m.best_estimator_[-1].coef_)
            if constrain_col is not None and coefs[-1][constrain_col] > 0:
                # one bounded coefficient: the constrained optimum sets it to 0, i.e. the same ridge
                # (same alpha, same scaling) without that column. Then served = predicted.
                keep = [j for j in range(X.shape[1]) if j != constrain_col]
                m0 = make_pipeline(StandardScaler(), Ridge(alpha=m.best_params_["ridge__alpha"]))
                m0.fit(X[tr][:, keep], y[tr])
                pred[te] = m0.predict(X[te][:, keep]); pred_s[te] = pred[te]
                continue
        else:
            m.fit(X[tr], y[tr])
        pred[te] = m.predict(X[te])
        if X_served is not None:
            pred_s[te] = m.predict(X_served[te])
    return np.clip(pred, 0, 100), np.clip(pred_s, 0, 100), coefs
```

Splits: `logo = list(LeaveOneGroupOut().split(X, y, groups))`. Call boosting as `oof(lambda: make_gbm(cols), ...)`.

### Step 3. Ablation and verdict

```python
def state_bootstrap(y, pa, pb, groups, B=2000, seed=0):    # Δ = MAE(a) − MAE(b); > 0 means b is better
    per = pd.DataFrame({"g": groups, "d": np.abs(y - pa) - np.abs(y - pb)}).groupby("g").d.agg(["sum", "size"])
    rng = np.random.default_rng(seed)
    out = [per.iloc[rng.integers(0, len(per), len(per))].pipe(lambda s: s["sum"].sum() / s["size"].sum())
           for _ in range(B)]
    return np.percentile(out, [2.5, 97.5])

def within_state_test(t, controls, feature, y, groups):
    """OLS on state-demeaned data, CR1 SEs clustered by state, t on G-1 df.
    Returns pp per within-state SD and the one-sided p for 'farther -> lower IB'."""
    def dm(v):
        v = np.asarray(v, float); return v - pd.Series(v).groupby(groups).transform("mean").to_numpy()
    X = np.column_stack([dm(t[c]) for c in controls + [feature]]); X = X / X.std(0)
    yw = dm(y); b = np.linalg.lstsq(X, yw, rcond=None)[0]; e = yw - X @ b
    XtXi = np.linalg.inv(X.T @ X); meat = np.zeros((X.shape[1], X.shape[1]))
    for idx in pd.Series(np.arange(len(yw))).groupby(groups).indices.values():
        s = X[idx].T @ e[idx]; meat += np.outer(s, s)
    G = len(set(groups)); n, k = X.shape
    V = XtXi @ meat @ XtXi * G / (G - 1) * (n - 1) / (n - k)
    se = np.sqrt(V[-1, -1]); tt = b[-1] / se
    return {"coef": b[-1], "se": se, "t": tt, "p_one_sided": stats.t.cdf(tt, G - 1)}

A = ci_M3[0] > 0
B = fe_g2["coef"] < 0 and fe_g2["p_one_sided"] < 0.05
C = delta_M3 > delta_P and fe_g2["t"] < fe_placebo["t"]
verdict = "adds_signal" if (A and B and C) else ("weak" if (A or B) else "no")
final_cols = LADDER["M3"] if verdict == "adds_signal" else LADDER["M1"]
```

### Steps 4 to 6. Final model, σ, `p_real_gap`, labels

```python
def sigma_curve(yhat_tr, r_tr, yhat_te, n_bins=5):
    # quintile edges; if ties at the clipped 100 ever empty a bin, merge it into its neighbour
    edges = np.quantile(yhat_tr, np.linspace(0, 1, n_bins + 1))
    b = np.clip(np.searchsorted(edges, yhat_tr, side="right") - 1, 0, n_bins - 1)
    centre = [np.median(yhat_tr[b == j]) for j in range(n_bins)]
    scale = [np.quantile(np.abs(r_tr[b == j]), 0.80) / 1.2816 for j in range(n_bins)]
    return np.interp(yhat_te, centre, scale)                 # flat beyond the end bins

sigma = np.empty(len(y))
for tr, te in logo:                                          # never uses the district's own state
    sigma[te] = sigma_curve(yhat[tr], r[tr], yhat[te])

p_raw = stats.norm.cdf((shortfall - DELTA) / sigma)
p = np.where(ib >= IB_HIGH, np.minimum(p_raw, 0.3), p_raw)
p = np.where((ib < IB_CUT) | (cs < 5), np.maximum(p, 0.5), p)
p = np.where(ib < 75, np.maximum(p, 0.9), p)
gap = p >= 0.5
```

Then the access cut (shared with `quadrant_rule`), the quadrants, `confident`, `guard`, the state/district split and `level`.

### Step 7. Validation

V1 and V2 as in section 8, on the labels without the C-section flag, for the rule, the model and the placebo, overall and within state.

### Step 8. Outputs

Atomic writes (temp name, then `os.replace`), each with `.meta.json`: row count, created time, `git rev-parse HEAD`, model version (for example `model_b_v2_ridge_M1`), the input sha256 values, grades method and recipe version, and the thresholds (`ACCESS_CUT`, `IB_CUT`, `IB_HIGH`, δ, the guard rules).

`gold/district_quadrants.parquet` (contract): `district_key`, `service_line` (`emoc`), `access_value` (grade 2 p50 km), `outcome_value` (IB), `outcome_flag`, `predicted_outcome`, `residual`, `quadrant_rule`, `quadrant_model`, `p_real_gap`, `crosswalk_method`.

`gold/model_b_district_detail.parquet` (sidecar, one row per `district_key`): `expected_if_served`, `shortfall`, `sigma`, `pi80_lo`, `pi80_hi`, `p_real_gap_raw`, `guard`, `confident`, `residual_state`, `residual_within`, `level`, `island`, `acc_g2_logp50`, `acc_any_logp50`, `csection_pct`, `csection_below_10`, `public_fraction`, `public_csection_pct`, `state_g1_per_1000_po`, `state_g2_per_1000_po`.

`gold/model_b_report.json`, first keys in this order:

```json
{
  "headline": "one plain sentence: does access add, by how much, with interval, and the within-state sign",
  "access_adds_signal": "adds_signal | weak | no",
  "verdict_checks": {"A_bootstrap_ci_above_0": false, "B_within_state_negative": false, "C_beats_placebo": false},
  "n_districts": 705, "n_states": 36,
  "target": "institutional_birth_5y_pct",
  "features": {"dev": [], "geo": [], "access": [], "placebo": [], "held_back": [], "excluded": {"category": ["cols"]}, "leakage_rule": "..."},
  "cv": {"primary": "LeaveOneGroupOut(state)", "within_state": "state-demeaned OLS, CR1 by state, t(G-1)", "inner": "GroupKFold(5) on training states"},
  "metrics": {"M0": {}, "DEV1": {}, "M1": {"ridge": {"mae": 0, "mae_ci95": [0, 0], "rmse": 0, "r2": 0}, "gbm": {}}, "P": {}, "M3": {}, "M1_ins": {}},
  "ablation": {"M1_vs_M3": {"delta_mae": 0, "ci95": [0, 0], "per_state_mean_sd": [0, 0], "states_improved": "k of 28"}, "M1_vs_P": {}},
  "within_state": {"acc_g2_logp50": {}, "acc_any_logp50": {}},
  "final_model": {}, "p_real_gap": {}, "thresholds": {}, "guards": {}, "quadrants": {}, "validation": {},
  "insurance_sensitivity": {"labels_flipped": 0}, "assumptions": {}, "inputs": {}
}
```

Lists (`reports/*.csv`). Columns: district, state, `access_km`, IB, `predicted_outcome`, `expected_if_served`, `shortfall`, `residual`, `p_real_gap`, `guard`, `quadrant_rule`, `quadrant_model`, `confident`, `level`, `island`, C-section rate, public fraction, public C-section rate, `crosswalk_method`, `outcome_flag`. Sort `send_care` and `cost_or_quality` by IB ascending (most home births first), since with the floor these lists are driven by absolute outcomes. Sort `send_data_collectors` by `access_km` descending, with island rows last. Keep unconfident rows, with the flag.

### Tests (`tests/test_wp6_outcome_model.py`)

- No feature in `EXCLUDED` (insurance is not in M1), and every NFHS column is in exactly one category.
- The LOGO harness never trains on the held-out state (the assertion in `oof`, plus a toy test).
- `expected_if_served >= predicted_outcome` for **both** learners, including a toy case where ridge's unconstrained access coefficient is positive.
- `within_state_test` recovers a planted negative coefficient on toy data with 10 groups.
- `p_real_gap` lies in [0, 1] and does not fall as the shortfall grows (holding IB fixed). IB < 75 gives p ≥ 0.9. IB below the cut or C-section < 5 gives p ≥ 0.5. IB ≥ 95 with C-section ≥ 5 gives p ≤ 0.3.
- No `data_desert` has IB below `IB_CUT` or C-section below 5.
- The quadrant mapping matches the 2 × 2 table on four toy rows.
- (Tier 2) The power check at β = 0 gives "adds_signal" in at most 2 of 20 runs.

### Acceptance (WP6 part 2)

- [ ] The report's first line states the verdict with Δ, its interval and the within-state sign.
- [ ] The ablation is reported with LOGO intervals, the placebo row and the per-state fold spread.
- [ ] The feature list, the leakage review and the insurance decision are in the report.
- [ ] Rule and model quadrant counts are printed, with the cross-table, the number changed, and the number each absolute rule touched.
- [ ] V1 and V2 are reported IB-adjusted, overall and within state, next to the placebo.
- [ ] Coverage of the 80% interval is between 70% and 90% **in each ŷ quintile**.
- [ ] Unscored, guarded and island districts are counted.

---

## 13. Time estimate

Compute is not the constraint. In the preview, the whole tier 1 ladder (6 rungs × 2 learners under LOGO, plus DEV1) ran in 26 s on 2 threads. The cost is builder time.

| Tier | Work | Time |
| --- | --- | --- |
| 1 | District table, placebo, winsorising, leakage guard, first tests | 30 min |
| 1 | Harness, ladder, bootstrap, per-state spread, FE test, verdict | 45 min |
| 1 | Final model, σ curve, `p_real_gap` and rules, labels, `level` | 25 min |
| 1 | V1 and V2 with placebo, outputs, sidecar, lists, figures b1 and b2 | 40 min |
| | **Tier 1 total** | **about 2 h 20 min** |
| 2 | Permutation, power, stability, repeated CV, M2/M4, exact-only rerun, V3/V4, figures b3 to b8 | about 1 h 45 min |
| 3 | Capped C-section within states, fractional logit, extra features, buffered LOGO, likelihood ratio | about 1 h 15 min |

This covers WP6 part 2 only. Part 1 (rule), part 3 (dialysis and ICU) and the lists need their own time. If the clock is short, ship tier 1 and stop.

---

## 14. Risks

| Risk | Effect | Mitigation |
| --- | --- | --- |
| WP4 changes the grades | Access and the verdict move | Rerun after WP5's rerun. Record method and recipe. Report both verdicts if both exist |
| Access adds nothing (the preview) | A weaker headline | Section 11. The placebo and power check make it interpretable |
| The model excuses poor districts | "Data desert" on a district with many home births | Floor at the rule cut, tested |
| The ceiling | 97% districts called care deserts | IB_HIGH = 95 rule. Fractional logit in tier 3 |
| State effects dominate | The map shows states | `level` in tier 1, state-level captions, the density caption |
| V1 or V2 read as evidence of hidden EmOC | Overclaiming | IB adjustment, placebo rows, "facility births" wording |
| Collinear or wrong-signed access | A misleading counterfactual | One access feature, sign-constrained ridge, monotone boosting |
| Island leverage | The slope tilts toward "far is fine" | Winsorising, `island` flag |
| Forking paths | A gain found by chance | Primary contrast fixed: M1 vs M3, final learner, LOGO plus FE. The revision-2 choices made after previews are disclosed |
| Non-exact crosswalk matches (130 districts) | Access pooled over the wrong area | `crosswalk_method` column, exact-only rerun (tier 2) |
| Shared laptop | Slow runs for every agent | Threads capped at 2, `n_jobs=1` |
| NFHS is 2019 to 2021, the facility pages are newer | Facilities opened after the survey | Methods card caveat. Biases toward "no gain" |

---

## 15. Decisions for Hruday

Defaults are marked. An unattended builder takes the default and logs it.

1. **How the model makes the call.** Expected-if-served shortfall (**default**). In the null case it equals the development-adjusted residual, bounded by the absolute rules.
2. **Absolute rules.** **On (default):** data deserts need IB ≥ the rule cut; IB < 75 gives p ≥ 0.9; C-section < 5% is a poor outcome in both rule and model; IB ≥ 95 cannot be a model gap.
3. **Margins.** δ = 3 pp, label cut p = 0.5, confident outside 0.3 to 0.7, IB_HIGH = 95 (**default**).
4. **Which residual drives the call.** LOGO residuals plus `level` in tier 1 (**default**). The orchestrator decides whether `level` and `residual_within` go into `districts.json`.
5. **Contract.** Sidecar `gold/model_b_district_detail.parquet`, no contract change tonight (**default**, changed from revision 1).
6. **Unscored post offices in WP7.** Not weight 1.0: that is the maximum, not a neutral value (median `p_real_gap` is 0.23). 560 of the 682 unscored geocoded post offices are in Purba Bardhaman, which has no NFHS row. Default: give each unscored post office its state's median `p_real_gap` (preview: West Bengal 0.33, Mizoram 0.11 for Saiha's 22), the national median for the 100 in state "NA", and flag them `p_imputed`. WP7 implements it.
7. **Insurance.** Out of M1, with a tier 1 sensitivity run (**default**).
8. **Final features.** M3 only on "adds signal" (**default**).
9. **No new packages.** sklearn, scipy and numpy cover everything (**default**).
10. **If access adds nothing:** keep the model quadrants, lead the methods card with the null result, and use the "facility births" wording (**default**).

---

## Review log

The review of revision 1 (Sat about 15:10 ET) rebuilt the preview inputs and probed the design. Its verdict: "Major revisions before building." Every point below was checked again on WP5's real `gold/district_access.parquet`, which landed at 15:05, just after the review's own rebuild.

| # | Severity | Critique | What changed |
| --- | --- | --- | --- |
| 1 | critical | In the null case the call excuses poor districts: 30 rule care deserts become model data deserts (mean IB 83.7) | **Changed.** Floor: a data desert needs IB ≥ the rule cut. The model can only add gaps (data → care, served → cost_or_quality). Rule counts reported. Methods-card line on the development adjustment added. On the WP5 file the old rules would have moved 33. Now 0 data deserts are below the cut. Also added a ceiling rule (IB ≥ 95 cannot be a model gap) after the probe found 8 of 34 moves at IB ≥ 95, such as Nellore at 97.0. The care-desert and cost lists now sort by IB |
| 2 | critical | V2 is confounded by IB, and the C-section guard builds part of it in | **Changed.** V2 is IB-decile adjusted, overall and within state, with matched comparisons (data vs served, care vs cost). It is computed on labels without the C-section flag. The "public facilities perform surgery" line is dropped unless the adjusted V2 supports it. The preview confirms it does not |
| 3 | critical | V1 is circular with the access axis. Distance to any facility reproduces it | **Changed.** Placebo access (distance to any facility) added to the ablation, the FE test, V1 and V2. V1 is reworded as sample geometry. Preview: the placebo matches or beats g2 on V1 |
| 4 | major | A single σ passes overall but fails by band (57% to 98.5%). The survey term is cosmetic | **Changed.** σ is a function of ŷ: quintile q80 scale, interpolated, curve fit leaving the state out. Acceptance per ŷ quintile (70 to 90%). N_BIRTHS/DEFF dropped from σ and kept only behind δ. The doc states that labels do not depend on σ. Worked examples redone. Preview coverage by band: 77 to 84% |
| 5 | major | Collinear access block, wrong-signed coefficients, clamp undefined | **Changed.** One access feature (`acc_g2_logp50`). `acc_g2_share60` dropped. The expected sign is required via the one-sided FE test. Sign-constrained ridge (exact refit without the column when the coefficient is positive). The `expected_if_served ≥ predicted_outcome` test now covers ridge |
| 6 | major | The primary test sits where access is most contaminated. The KMeans state-dummy model breaks under StandardScaler | **Changed.** The within-state FE regression with CR1 SEs is co-primary in tier 1. The KMeans/dummy model is removed |
| 7 | major | The permutation null is too narrow (it destroys state clustering) | **Changed.** Permutation within states (tier 2), with the placebo as the between-state reference. Report which null was used |
| 8 | major | The power check overstates power and skips the real decision rule | **Changed.** Latent true access observed at reliability 1.0, 0.7 and 0.5. The full tier 1 verdict is applied. Reported per β and R |
| 9 | major | The verdict cannot be met in tier 1, "weak" switches on M3, and the levels overlap | **Changed.** Tier 1 verdict from the bootstrap CI (A), the FE sign test (B) and the placebo (C). The levels form a partition. M3 only on "adds signal". Tier 2 results are reported but do not flip the verdict |
| 10 | major | Insurance breaks rule 3 and identifies states (R² 0.909) | **Changed.** Moved to the held-back set. Rule 3 extended to programme footprints. A tier 1 sensitivity run reports flips (preview 8). Disclosed that the preview MAE was seen before the decision |
| 11 | major | The outcome does not match the service line (IB is not surgical) | **Partly changed.** The data desert is defined as a data desert for facility births. "Surgical" stays off screen. The lists carry the C-section rate and a below-10% flag (22 of 126 in the preview). The reviewer's dual-outcome rule (capped C-section at or above expectation) is **not** adopted tonight: it needs a second model, which the next point says must be within-state only. It is tier 3 |
| 12 | major | The C-section tags and the NFHS C-section rate form a loop. One family both sets labels and validates them | **Changed.** The C-section target is tier 3, within states only, capped at 15. The C-section flag stays as a label rule (WP6 asks for it), now in both rule and model, but V2 and V4 use labels without it |
| 13 | major | WP7's weight 1.0 for unscored post offices is not neutral. Purba Bardhaman dominates. Mizoram\|Chandel duplicate | **Changed.** State median `p_real_gap` with a `p_imputed` flag (decision 6). The Purba Bardhaman gap is noted for the orchestrator (the crosswalk already records it). Mizoram\|Chandel is asserted absent (WP5 already excludes it) |
| 14 | major | Too much scope for the deadline | **Changed.** Tier 1 cut to the reviewer's list, plus the M1+ins row and `confident`. Sidecar instead of a contract change. Repeated CV, stability, permutation, power, buffered LOGO and tier 3 moved later. Threads capped. WP5's file now exists, which removes the input risk. Fold spread for WP6's acceptance comes from per-state LOGO differences |
| 15 | major | The map will show states, not districts | **Changed.** `level` and `residual_within` in tier 1 (sidecar). Recommended for `districts.json`. State-level caption. A state density figure (`state_g2_per_1000_po`) for a caption or layer |
| 16 | minor | Island leverage and off-support counterfactuals | **Changed.** Log distances winsorised at p99. `island` flag, islands listed last. Served reference taken within branch-share terciles |
| 17 | minor | "Pre-registered" overclaims, and some numbers do not reproduce | **Changed.** Wording is now "fixed before Model B was fit, after previews". Corrected: σ (6.8 to 7.3 under the old formula, now a curve), M0 MAE 9.02 on 705 rows, grade 2 beyond grade 1 ρ −0.01, IB < 75 count 81 and guard union 97 on 705 rows (82 and 98 came from 706 rows, which include the Mizoram duplicate). Assumptions listed. The report must recompute every number |
| 18 | minor | The `oof` sketch has a signature trap, `stable` mixes schemes, and there is no one-index baseline | **Changed.** `oof` takes a zero-argument factory and documents the lambda for boosting. `stable` moves to tier 2, based on LOGO with bootstrapped training states. DEV1 (one development index) is added to the ladder (preview MAE 7.10) |
