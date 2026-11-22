# WP5: Ranking engine

Re-scoped Nov 21 (D023, D027, D028). The old WP5 (post office access tables for the India map) is done and retired: `gold/post_office_access.parquet` and `gold/district_access.parquet` are not maintained. Its distance code (`vf.geo.haversine_km`) is what carries over.

Size: medium to large (about 3 hours). Depends on WP2 Part B (resolver), WP3 v0.2 grades, WP11 flags. Can start at once against v0.1 grades and stub flags. Feeds WP8, WP9.

## Goal

Given a service and a resolved location, return the top 3 recommendable facilities with every score part and the facts behind it, or an abstention with what is known instead. Pure Python, no I/O at query time, the same code in tests, scripts and the app.

## Inputs

- `gold/capability_grades.parquet` (v2 contract: structured evidence, `evidence_strength`)
- `gold/facility_flags.parquet` (WP11)
- `silver/facilities.parquet` (location, name, type, operator, source domains)
- `vf.locate.Resolution` (WP2)
- `docs/04_thesis_and_plan.md`, "The ranking" (gates, formula, weights, abstention, confidence labels)

## Outputs

- `src/vf/rank.py`: `load_index(bundle_dir) -> Index`, `rank(index, service, resolution, k=3) -> Answer`, `search(index, text, resolution, k=3) -> Answer` (fallback)
- `src/vf/services.py`: the 12 lines with display names, radii, and plain-language descriptions of each recipe for the "how we decided" panel
- `data/labels/scenarios.csv`: the scenario suite (query, service, place, expected kind of answer)
- `scripts/wp5_scenarios.py`: runs every scenario, writes `reports/ranking_scenarios.md`
- `tests/test_rank.py`

## The `Answer` shape

```
Answer
  status: "ok" | "no_reliable_option" | "needs_location_choice" | "location_not_found"
  service, service_label, radius_km, method ("recipe" or "keyword")
  location: the Resolution (label, how, precision_km)
  results: up to 3 Card, in rank order
  beyond_radius: the nearest recommendable Card outside the radius, if status is no_reliable_option
  unverified_nearby: up to 3 claim-only or blocked facilities inside the radius, each with the reason
  how_we_decided: gates applied (with counts at each gate), formula, weights, recipe text
Card
  facility_id, name, type, operator, city, state, lat, lon
  distance_km, location_precision_km, location_note
  grade, grade_label, confidence ("high" | "medium" | "low"), confidence_reason
  score, parts: {evidence, distance, trust, penalties: [{name, value, reason, quote}]}
  evidence: [{requirement, quote, field, fact_id}]   # one row per satisfied recipe group
  missing: requirements of the next grade up not found ("no fact mentions a blood bank")
  sources: {n_domains, domains, official_website}
  why_here: one sentence on why it beat the next card
  could_be_wrong: one or two sentences from its flags and evidence strength
```

Every string on a card is either a quoted fact, a field from the record, or a template filled from the parts above (D030).

## Steps

1. Index: precompute per facility the coordinates (null for `excluded_unresolved`), the grade row per line, and the flags. Distances at query time: `haversine_km` against all 10,000 points (numpy, under 5 ms).
2. Gates, with counts kept for the panel: usable location; within 3 times the radius (at most 50); recommendable (grade 2+, no blocking flag).
3. Score recommendable candidates with the formula and default weights in the plan doc. Weights and radii live in `vf.services`, one place.
4. Abstention, `beyond_radius`, `unverified_nearby`, as in D028.
5. Templates for `why_here`, `missing`, `could_be_wrong`, `confidence_reason`. Plain words. No grade numbers without their label.
6. Fallback `search`: TF-IDF over distinct fact texts plus specialty names (sklearn), top facilities by best matching fact within the radius, each card quoting its matching facts. Confidence never above "low"; method `keyword`. Write the index into the bundle.
7. Scenario suite. Write it before tuning anything: the brief's five queries (dialysis near Jaipur; emergency C-section near Lucknow; dialysis from a rural Bihar village; high-risk delivery in a Maharashtra district; trauma near a highway town), at least one per service line, at least three remote places where abstention is the honest answer, two fallback queries, and two ambiguous places. Run it, and for each result read the quoted facts and mark it `right`, `defensible`, or `wrong` with a note.
8. Change weights only by reading the scenario output, never to make a specific facility win. Log every change in the decisions log.
9. Probability mode (D039): if `gold/service_probability.parquet` exists in the bundle, score = `p_offers` x exp(-km / radius) and recommendable means `p_offers` at or above the threshold in `vf.services` (set by WP13 from measured precision). Card parts become "chance an independent source confirms it" and "reach". Otherwise use the v0 score and set `how_we_decided.score_status = "provisional"`. Both modes must pass the same tests.
10. Simulation metric (D038): `simulate(index, service, resolution, policy, n_origins)` returns, for origins drawn from nearby post offices, the expected straight-line km until reaching a facility that offers the service, when each visited facility offers it with probability `p_offers`, for policies `nearest_claim`, `nearest_evidence`, `ranked`. Closed form (sequential expectation over the ordered list), no random draws, so the numbers are reproducible. Only available in probability mode.

## Acceptance checks

- [ ] `pytest -q tests/test_rank.py` passes, including: gates exclude grade 0 and 1 from `results`; a blocked facility never appears in `results`; every card's quotes exist verbatim in `silver/facts.parquet`; parts sum to the score; abstention fires on a synthetic empty region
- [ ] `rank` answers in under 100 ms after loading
- [ ] `reports/ranking_scenarios.md`: every scenario, its answer, and the hand check, with totals of right, defensible and wrong. Wrong ones are explained, not hidden
- [ ] The abstention rate over the scenario suite is reported

## Pitfalls

- Do not let distance beat evidence by accident: a thin grade 2 next door should not outrank a strong grade 3 five km further unless the weights say so on purpose. Check this case in tests.
- A facility relocated to its PIN centre (`moved_to_pin`) has a fuzzy location. Show the precision; do not show its distance to one decimal place.
- Two records for one real hospital can take two of the three cards. If WP11 marks them as likely duplicates, show one card and say "also listed as".
- Straight-line km everywhere, labeled that way.
