# Data contracts

The files each work package must produce. These are the interfaces between agents. If you need to change one, update this file in the same commit and add a line to `08_decisions_log.md`, because another agent is coding against it.

**Contract v2 (Nov 21, re-scope D023).** New: `silver/places.parquet` (WP2 B), `gold/facility_flags.parquet` and `gold/facility_duplicates.parquet` (WP11), the app bundle (WP12). Changed: `service_line` takes 12 values; `capability_grades` gains structured evidence and evidence strength. Retired (D024, nothing new may read them): `silver/nfhs*.parquet`, `silver/district_crosswalk*` as an NFHS mapping, `gold/post_office_access.parquet`, `gold/district_access.parquet`, `gold/district_quadrants.parquet`, `gold/scenarios.json`, and the "App data (retired)" section at the end.

Conventions:

- Shared data lives under `vf.paths.data_dir()`: one folder for all worktrees, not versioned.
- Parquet for tables, written atomically (write to a temp name, then rename).
- `facility_id` is the raw `unique_id`. After WP1 it is unique.
- `district_key` is `"<NFHS state_ut>|<NFHS district_name>"`, exactly as spelled in the NFHS file. It is the canonical district id everywhere downstream.
- `service_line` is one of `emoc`, `nicu`, `dialysis`, `icu`, `cardiac_surgery`, `cath_lab`, `mri`, `ct`, `trauma`, `burns`, `neurosurgery`, `oncology` (D025). Display names, radii and recipe text live in `vf.services`, nowhere else.
- Distances are straight-line kilometres.
- Each producing script also writes `<output>.meta.json` with row count, created time, git commit, and recipe or model version.

## Silver (cleaned inputs)

### `silver/facilities.parquet` (WP1)

One row per real facility. Expected about 9,989 rows after removing duplicate ids, including the truncated ones.

| Column | Type | Notes |
| --- | --- | --- |
| `facility_id` | str | unique |
| `cluster_id` | str, nullable | |
| `row_status` | str | `ok` or `truncated` |
| `name` | str | |
| `facility_type` | str, nullable | from `facilityTypeId` |
| `operator_type` | str, nullable | `private`, `public`; map `government` to `public` |
| `lat`, `lon` | float, nullable | the location to use: the scraped point, or the PIN's location when `coord_check` is `moved_to_pin`; null when missing, outside India, or `excluded_unresolved` |
| `coord_valid` | bool | true when `lat`, `lon` are present (about 9,670) |
| `coord_check` | str | result of the check against the facility's own PIN (WP1, `vf.geo.check_facility_locations`): `ok` (within 40 km of the PIN's location), `ok_same_district` (farther, but in the PIN's district), then for points farther than 40 km and outside the PIN's district, settled by the city text: `moved_to_pin` (names only the PIN's place), `kept_city_matches_coords` (names only the point's place), `kept_unresolved` (names both or neither, 40 to 100 km: kept), `excluded_unresolved` (names both or neither, over 100 km: null); `no_pin_check` (no PIN, or a PIN the directory cannot locate), `missing` |
| `coord_pin_km` | float, nullable | km from the scraped point to the PIN's location |
| `lat_raw`, `lon_raw` | float, nullable | the scraped point; null when missing or outside India (about 9,953 present) |
| `pin` | str, nullable | 6 digits or null |
| `city_raw`, `state_raw` | str, nullable | as scraped, for display only |
| `specialties` | list[str] | deduped |
| `capability`, `procedure`, `equipment` | list[str] | empty strings removed |
| `description` | str, nullable | |
| `n_doctors`, `n_beds` | Int64, nullable | |
| `year_established` | Int64, nullable | |
| `official_website` | str, nullable | |
| `source_urls` | list[str] | |
| `source_domains` | list[str] | distinct, lowercased, `www.` removed |
| `n_source_domains` | int | |
| `page_updated` | date, nullable | from `recency_of_page_update` |
| `staff_named` | bool, nullable | from `affiliated_staff_presence` |

### `silver/facts.parquet` (WP1)

One row per fact, long format. About 450,000 rows.

| Column | Type | Notes |
| --- | --- | --- |
| `fact_id` | str | stable hash of `facility_id`, `field`, position |
| `facility_id` | str | |
| `field` | str | `capability`, `procedure`, or `equipment` |
| `text` | str | original text |
| `text_norm` | str | lowercased, whitespace collapsed; the dedup key for labeling |

### `silver/post_offices.parquet` (WP2)

| Column | Type | Notes |
| --- | --- | --- |
| `po_id` | int | row number in the raw file |
| `office_name`, `office_type`, `delivery` | str | |
| `pin` | str | |
| `state_pin`, `district_pin` | str | as in the PIN directory |
| `lat`, `lon` | float, nullable | null when missing, malformed, or outside India; rows with latitude and longitude swapped in the raw file are swapped back when the corrected point lands among their district's offices (`vf.geo.post_office_coords`), else null |
| `coord_valid` | bool | about 151,600 true |
| `coord_swap_repaired` | bool | true for the 579 rows whose swapped coordinates were swapped back |
| `district_key` | str, nullable | via the crosswalk |

### `silver/pin_crosswalk.parquet` (WP2)

One row per (pin, state_pin, district_pin). Columns: `pin`, `state_pin`, `district_pin`, `n_offices`, `share` (of that PIN's offices), `rank`, `is_ambiguous`.

### `silver/district_crosswalk.parquet` (WP2)

One row per PIN-directory district.

| Column | Type | Notes |
| --- | --- | --- |
| `state_pin`, `district_pin` | str | |
| `district_key` | str, nullable | the NFHS district it maps to |
| `match_method` | str | `exact`, `fuzzy`, `manual`, `parent_district`, `unmatched` |
| `match_score` | float, nullable | |
| `note` | str, nullable | why, for anything not exact |

Also write `silver/district_crosswalk_review.csv` listing every non-exact match for a human to skim.

### `silver/nfhs.parquet` and `silver/nfhs_flags.parquet` (WP2)

`nfhs.parquet`: one row per district, `district_key`, `state_ut`, `district_name`, then every indicator as float (null when suppressed or missing). Keep the raw column names. 705 rows: the raw file's 706 minus `Mizoram|Chandel`, an exact copy of `Manipur|Chandel` standing where Mizoram's Saiha should be. The sidecar lists dropped rows under `dropped_rows`; readers need no exclusion list of their own.

`nfhs_flags.parquet`: same shape, each indicator cell holding `ok`, `low_sample`, `suppressed`, or `missing`.

## Labels

### `labels/gold_facts.csv` (WP3)

Columns: `text_norm`, `tags` (pipe-separated, empty for none), `labeled_by` (`hruday` or `agent`), `note`. About 400 rows. Never used for training.

### `labels/teacher_facts.parquet` (WP3)

Columns: `text_norm`, `tags` (list[str]), `model`, `recipe_version`, `labeled_at`. Target 20,000 to 30,000 distinct facts.

## Gold (results)

### `gold/fact_tags.parquet` (WP4)

One row per fact. Columns: `fact_id`, `facility_id`, `tags` (list[str]), `method` (`regex`, `teacher`, or `student`), `model_version`.

### `gold/capability_grades.parquet` (WP3 baseline, then WP4)

One row per facility per service line. WP3 writes it first with `method = regex` so WP5 is never blocked. WP4 overwrites it with `method = student`.

| Column | Type | Notes |
| --- | --- | --- |
| `facility_id` | str | |
| `service_line` | str | |
| `grade` | int | 0 to 3 |
| `grade_label` | str | `none`, `claimed`, `supported`, `corroborated` |
| `tags_present` | list[str] | |
| `evidence` | list[str] | up to 5 fact texts that justify the grade (kept for v0.1 readers) |
| `evidence_items` | list[struct] | v2: one item per satisfied recipe group: `{requirement, tag, fact_id, field, text}`. `requirement` is the plain-language group name from the recipe ("caesarean section", "blood bank or transfusion"). `text` is the original fact text, verbatim from `silver/facts.parquet`; `field` is `capability`, `procedure`, `equipment` or `description` |
| `missing_for_next` | list[str] | v2: plain-language requirements of the next grade up that no fact satisfies; empty at grade 3 |
| `n_supporting_facts` | int | v2: distinct facts carrying any tag that counts toward this line's grade |
| `evidence_strength` | str | v2: `strong`, `moderate`, `thin`, or `none` at grade 0 (rule in `06_capability_recipes.md`, v0.2) |
| `source_confidence` | float | 0 to 1, see the recipes doc |
| `method` | str | |
| `recipe_version` | str | |

### `gold/facility_flags.parquet` (WP11)

One row per facility per flag raised; a facility with no flags has no rows.

| Column | Type | Notes |
| --- | --- | --- |
| `facility_id` | str | |
| `flag` | str | `location_conflict`, `possible_merge`, `implausible_claim` |
| `service_line` | str, nullable | set for `implausible_claim`; null when the flag is about the whole record |
| `severity` | str | `minor` (penalty and warning) or `major` (blocking for recommendation) |
| `signal` | str | which rule fired, for example `coord_unresolved`, `state_mismatch`, `name_joins_two`, `shared_website`, `type_cannot_offer` |
| `reason` | str | one plain sentence for the card: "The record's location is 140 km from its PIN code's area." |
| `quote` | str, nullable | the record text that triggered it, verbatim |
| `check_version` | str | |

### `gold/service_probability.parquet` (WP13)

One row per facility per service line, for facilities graded 1 or higher. Absent until WP13 ships; the ranker falls back to the v0 score (D039).

| Column | Type | Notes |
| --- | --- | --- |
| `facility_id`, `service_line` | str | |
| `p_offers` | float | calibrated probability that an independent source confirms the facility offers the service (a lower bound on the true chance, D040) |
| `p_low`, `p_high` | float | 90% interval (bootstrap over training labels) |
| `top_factors` | list[struct] | up to 3 `{feature, direction, plain}` items that most moved this facility's probability, for the card |
| `model_version` | str | |
| `n_labels_line` | int | independent labels behind this line's model; the app shows "not calibrated" for a line under the minimum WP13 sets |

### `gold/facility_duplicates.parquet` (WP11)

Pairs `facility_id_a`, `facility_id_b` (a < b), `distance_km`, `name_score`, `signal`, `keep` (the id WP5 shows; the other is "also listed as").

### `silver/places.parquet` (WP2 B)

The gazetteer for the location resolver. One row per place.

| Column | Type | Notes |
| --- | --- | --- |
| `place_id` | str | `<kind>:<key>` |
| `kind` | str | `pin`, `district`, `post_office`, `city` |
| `name`, `name_norm` | str | display name, and the normalised match key |
| `state`, `district` | str | PIN-directory spelling |
| `pin` | str, nullable | for `pin` and `post_office` |
| `lat`, `lon` | float | inside India |
| `precision_km` | float | at least 2; how far the true place may be from the point |
| `n_offices` | int | offices behind the point |
| `note` | str, nullable | for example "centre from all offices: no head or sub office" |

### The app bundle (WP12, read by WP8)

`dbapp/bundle/` in the checkout, committed only if under about 50 MB and free of raw text dumps beyond the quoted evidence; otherwise built at deploy time by `scripts/wp12_bundle.py`.

| File | Contents |
| --- | --- |
| `manifest.json` | row counts, recipe and check versions, git commit, created time, default weights and radii |
| `facilities.parquet` | `facility_id`, `name`, `facility_type`, `operator_type`, `lat`, `lon` (null when unusable), `location_precision_km`, `location_note`, `city_raw`, `state_raw`, `pin`, `official_website`, `source_domains`, `n_source_domains`, `source_confidence` |
| `grades.parquet` | `capability_grades` columns for all 12 lines |
| `flags.parquet`, `duplicates.parquet` | as above |
| `places.parquet` | as above |
| `post_offices.parquet` | `lat`, `lon`, `pin` for non-suspect geocoded offices, one per placeholder point; for the map context and the trip view |
| `keyword_index/` | fitted TF-IDF vectoriser and matrix over distinct fact texts plus specialty names, and the fact id map, for the `search` fallback |

### `gold/capability_grades_v03.parquet` (WP3, recipe v0.3, D048)

Same columns as `capability_grades` plus `level_note` (str, nullable: the emoc FRU-level note, set at grade 3 when anaesthesia and round-the-clock facts also exist; it never changes a grade) and `level_note_items` (list[struct], like `evidence_items`). `recipe_version` = `v0.3`. A rule-level regrade of the frozen `gold/fact_tags.parquet` (sha256 recorded in the sidecar): tags are not recomputed, and `vf.recipes.RECIPE_VERSION` stays `v0.2` (v0.3 is `RULE_VERSION_V03`). Wording-dependent requirements (`nicu_named`, `nicu_level3`, `ventilator_newborn`, `neonatal_resp_not_sncu`, `blood_maternal`, `dialysis_unit_named`, `icu_adult`, `ventilator_icu`) are rule-internal qualifiers computed from a fact's own tags and text; they never appear in `tags_present` or item tags. This file is what the v1-final ranker gates on.

### `gold/post_office_access.parquet` (WP5, retired)

One row per geocoded post office. Columns: `po_id`, `district_key`, `lat`, `lon`, then for each service line and each minimum grade 1 to 3: `dist_km_<line>_g<k>` and `nearest_facility_<line>_g<k>`, then `coord_suspect` (bool), then `coord_placeholder` (bool).

`coord_suspect` is true for an office more than 200 km from its district's centre (the median of the district's head and sub offices when it has 3 or more, else of all its offices; offices without a `district_key` are grouped by PIN-directory state and district, and state `NA` ones are never suspect). About 5,900 offices, mostly branch offices geocoded to a same-named village elsewhere. Their coordinates and distances are kept but wrong. WP5's district roll-up and national headline numbers leave them out, so any consumer computing a statistic from this table (WP7 coverage, WP8 `po_*.f32` arrays and swarm) must filter `coord_suspect == false` too, or show suspect offices separately.

`coord_placeholder` is true for an office whose exact point is shared by 10 or more geocoded offices from 3 or more PINs (`vf.geo.placeholder_points`). Different PINs are different delivery areas, so such a point (usually the district town) is a placeholder, not where those offices are: in Bihar's Samastipur 358 of 376 offices, from 35 PINs, sit on one point. About 10,000 offices on about 300 points. Their rows keep the point's distances. WP5's district roll-up and national headline numbers count each placeholder point once per district instead of once per office, so a consumer computing a statistic from this table must do the same (keep one row per `district_key` and placeholder point), or show placeholder offices separately. The two flags are independent; filter `coord_suspect` first.

### `gold/district_access.parquet` (WP5, retired)

One row per district per service line per minimum grade. Columns: `district_key`, `service_line`, `min_grade`, `n_post_offices`, `dist_km_p50`, `dist_km_p75`, `dist_km_p90`, `share_within_30km`, `share_within_60km`, `n_facilities_in_district`, `n_post_offices_excluded`, `n_post_offices_placeholder`, `n_placeholder_points`.

`n_post_offices` counts the district's offices with `coord_suspect == false`; `n_post_offices_excluded` is its `coord_suspect` offices, left out of every number. `n_post_offices_placeholder` is how many of the `n_post_offices` sit on placeholder points (`coord_placeholder`), and `n_placeholder_points` how many distinct such points they sit on. Every percentile and share is over the non-suspect offices with each placeholder point counted once, that is over `n_post_offices - n_post_offices_placeholder + n_placeholder_points` values. WP6 adds a reliability note when more than half of a district's `n_post_offices` are placeholder offices.

### `gold/district_quadrants.parquet` (WP6, retired)

One row per district per service line.

| Column | Type | Notes |
| --- | --- | --- |
| `district_key`, `service_line` | str | |
| `access_value` | float | the access measure used |
| `outcome_value` | float | the NFHS outcome used |
| `outcome_flag` | str | NFHS flag for that cell |
| `predicted_outcome` | float | Model B out-of-fold prediction |
| `residual` | float | actual minus predicted |
| `quadrant_rule` | str | from thresholds |
| `quadrant_model` | str | from the model residual |
| `p_real_gap` | float | 0 to 1, used by the simulator |
| `crosswalk_method` | str | how this district was matched |

Also `gold/model_b_report.json`: features, cross-validation scheme, error with and without access features, and the thresholds used.

### `gold/scenarios.json` (WP7, retired)

For each service line and radius: an ordered list of sites with `facility_id` (or `lat`, `lon` for a new site), `kind` (`upgrade` or `new`), `marginal_gain`, `cumulative_coverage`. Two variants per setting: `naive` and `desert_aware`.

## App data (retired)

The India-wide map's data files. Retired by D023 and D024; kept so the old app still builds.

Written by WP7 or a small export script into `app/public/data/` in the checkout, and committed so the app can be hosted. Keep the total under about 15 MB.

| File | Contents |
| --- | --- |
| `meta.json` | Counts, model metrics, generated time, caveat strings, `mock` (true only for WP8's mock set), and the list of binary columns as `binary: [{file, dtype, length}]`. Full key list in `app/README.md` |
| `po_lon.f32`, `po_lat.f32` | Float32 arrays, one value per geocoded post office |
| `po_district.u16` | Index into `districts.json`; 65535 when the post office has no district |
| `po_dist_<line>_g<k>.f32` | Distance in km per post office, k = 1 to 3 |
| `po_near_<line>_g<k>.u16` | Index into `facilities.json` of the nearest facility at grade k or better, per post office, k = 1 to 3; 65535 when there is none. Used by patient mode and the patient swarm |
| `facilities.json` | Array of `{id, name, lat, lon, type, operator, city, state, grades: {emoc, dialysis, icu}, evidence: {emoc: [...], ...}, n_domains}`, plus optional `tags: {emoc: [...], ...}` for the evidence panel |
| `districts.json` | Array of `{key, state, name, quadrant: {...}, nfhs: {...selected indicators}, access: {...}, residual, crosswalk_method}` |
| `scenarios.json` | Copy of the gold file |
| `story.json` | The scripted beats for the video. Hand-authored by WP9 |

Until real outputs exist, WP8 generates mock versions of these files with the same shapes, using the real post office coordinates so the map already looks right.
