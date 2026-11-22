# WP6: Outcome model and quadrants (Model B) (DROPPED)

**Dropped Nov 21 by the re-scope (D023, D024).** The official challenge asks for a facility-finding tool, not a district map. Do not work on this package. Its code, outputs and tests stay in the repo as history and are not maintained. What it found is in D022 and `reports/wp6_rule_quadrants.md`.

The original spec follows, unchanged, for reference only.

---


Size: medium to large (about 2.5 hours). Depends on WP2 and WP5. Feeds WP7, WP8, WP9.

## Goal

Decide, per district and service line, whether poor access in the data is a real care gap or a data gap, first by simple thresholds and then with a model.

## Inputs

- `silver/nfhs.parquet`, `silver/nfhs_flags.parquet`
- `gold/district_access.parquet`
- `silver/district_crosswalk.parquet` (for match quality)

## Outputs

- `gold/district_quadrants.parquet`
- `gold/model_b_report.json`
- Three ranked lists as CSV in `reports/`: `send_care.csv`, `send_data_collectors.csv`, `cost_or_quality.csv`
- `scripts/wp6_*.py`

## Steps

### Part 1: threshold quadrants (obstetrics first)

1. Access reading: district median distance to the nearest grade 2 or better obstetric facility. Pick the "poor access" threshold from the distribution (for example the top third) and record it.
2. Outcome reading: `institutional_birth_5y_pct`. Pick the "poor outcome" threshold the same way. Also compute `births_delivered_by_csection_5y_pct` below 5% as a second flag for lack of surgical access.
3. Assign `quadrant_rule`. Print the count in each quadrant.
4. Sanity check the data-desert quadrant against `institutional_birth_in_public_facility_5y_pct`: if these districts have a high public share, the story holds. Report the comparison either way.

### Part 2: Model B

5. Target: `institutional_birth_5y_pct`. Features: NFHS development proxies (see the data dictionary) and the access features. Do not include other birth or antenatal indicators as features; they are too close to the target.
6. Cross-validate grouped by state (`GroupKFold`). Fit gradient boosting and a ridge baseline.
7. Ablation: out-of-fold error with development features only, then with access features added. Report both with a spread across folds.
8. Residuals: actual minus out-of-fold prediction from the model that includes access. A large positive residual in a poor-access district means the district does better than its visible supply predicts: a data desert candidate.
9. Turn this into `quadrant_model` and a `p_real_gap` between 0 and 1 for the simulator. Keep the mapping simple and documented.
10. Repeat the target with the C-section rate if time allows.

### Part 3: other service lines

11. Dialysis: there is no utilization outcome, so build need versus access instead. Need is the blood sugar and blood pressure burden. Label this clearly as a different kind of comparison.
12. ICU: access map only.

### Part 4: lists

13. Write the three ranked lists with the numbers a reader needs: district, state, access distance, outcome, residual, crosswalk method, NFHS flag.

## Acceptance checks

- [ ] Thresholds recorded in the report and the decisions log
- [ ] Quadrant counts printed for rule-based and model-based versions, with a cross-table of how many districts changed
- [ ] Ablation reported with fold spread. If access adds nothing, the report says so in the first line
- [ ] No target leakage: feature list reviewed and written into the report
- [ ] Districts with low-sample or suppressed outcome values are flagged, not hidden
- [ ] Three list files exist

## Pitfalls

- About 700 rows. No neural nets, no heavy tuning, no claims from tiny differences.
- Random K-fold leaks across neighboring districts. Group by state.
- Residuals have many causes besides hidden public care. Call these districts candidates, and back the claim with the public-share check in step 4.
- NFHS is from 2019 to 2021 and the facility pages are newer. Put that on the methods card.
- Unmatched districts in the crosswalk cannot be scored. Count them.
