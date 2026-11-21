# WP7: Simulator

Size: medium (about 2 hours). Depends on WP5 and WP6. Feeds WP8, WP9.

## Goal

Answer "where should the next N upgrades go?" and export everything the map needs.

## Inputs

- `gold/post_office_access.parquet`, `gold/capability_grades.parquet`, `silver/facilities.parquet`
- `gold/district_quadrants.parquet` (`p_real_gap`)

## Outputs

- `gold/scenarios.json`
- App data files in `app/public/data/` (see the contract), replacing WP8's mock files
- `scripts/wp7_simulator.py`, `scripts/wp7_export_app_data.py`

## Steps

1. Define coverage: a post office is covered for a service line if a grade 2 or better facility is within R km. Use R = 30 and R = 60 for obstetrics. Record the choice.
2. Candidate sites: existing facilities at grade 1 for that service line (they claim the service but show no evidence), which models an upgrade. Optionally also new sites at head post office locations.
3. Greedy maximum coverage: repeatedly pick the candidate that newly covers the most weight, for 20 steps. Store the marginal gain and cumulative coverage at each step.
4. Two weightings:
   - naive: every uncovered post office counts 1
   - desert-aware: each post office counts `p_real_gap` of its district, so data deserts are discounted
5. Compare the two top-10 lists. How many naive picks land in districts that Model B calls data deserts? That number is a line in the video.
6. Optional population weights: district census population spread evenly over the district's post offices. This needs an external file; record its source, mark every resulting number as an estimate, and keep the unweighted version too.
7. Export app data (include `po_near_<line>_g<k>.u16` for the patient swarm and patient mode, see `docs/superpowers/specs/2026-11-21-patient-swarm-design.md`): binary arrays for post office coordinates and distances, `facilities.json`, `districts.json`, `scenarios.json`, and `meta.json` with counts, model metrics, and caveat strings. Check the total size.

## Acceptance checks

- [ ] Cumulative coverage is non-decreasing and marginal gains are non-increasing along each greedy sequence
- [ ] Naive and desert-aware sequences both exported for each radius
- [ ] The overlap between the two top-10 lists is reported
- [ ] App data loads in the app and matches the contract; total under about 15 MB
- [ ] `meta.json` carries the generated time and the model versions used

## Pitfalls

- Greedy is an approximation. Say "greedy plan", not "optimal plan".
- An "upgrade" here is a modeling assumption: we do not know that a claimed facility could actually be upgraded. Word it as "candidate sites".
- Keep the in-browser recompute consistent with this script: same radius, same coverage rule, same distance formula. Otherwise the numbers on screen will disagree with the lists.
