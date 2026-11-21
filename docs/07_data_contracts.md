# Data contracts

The files each work package must produce. These are the interfaces between agents. If you need to change one, update this file in the same commit and add a line to `08_decisions_log.md`, because another agent is coding against it.

Conventions:

- Shared data lives under `vf.paths.data_dir()`: one folder for all worktrees, not versioned.
- Parquet for tables, written atomically (write to a temp name, then rename).
- `facility_id` is the raw `unique_id`. After WP1 it is unique.
- `district_key` is `"<NFHS state_ut>|<NFHS district_name>"`, exactly as spelled in the NFHS file. It is the canonical district id everywhere downstream.
- `service_line` is one of `emoc`, `dialysis`, `icu`.
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
| `lat`, `lon` | float, nullable | null when missing or outside India |
| `coord_valid` | bool | |
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
| `lat`, `lon` | float, nullable | null when missing, malformed, or outside India |
| `coord_valid` | bool | about 151,000 true |
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

`nfhs.parquet`: one row per district, `district_key`, `state_ut`, `district_name`, then every indicator as float (null when suppressed or missing). Keep the raw column names.

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
| `evidence` | list[str] | up to 5 fact texts that justify the grade |
| `source_confidence` | float | 0 to 1, see the recipes doc |
| `method` | str | |
| `recipe_version` | str | |

### `gold/post_office_access.parquet` (WP5)

One row per geocoded post office. Columns: `po_id`, `district_key`, `lat`, `lon`, then for each service line and each minimum grade 1 to 3: `dist_km_<line>_g<k>` and `nearest_facility_<line>_g<k>`.

### `gold/district_access.parquet` (WP5)

One row per district per service line per minimum grade. Columns: `district_key`, `service_line`, `min_grade`, `n_post_offices`, `dist_km_p50`, `dist_km_p75`, `dist_km_p90`, `share_within_30km`, `share_within_60km`, `n_facilities_in_district`.

### `gold/district_quadrants.parquet` (WP6)

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

### `gold/scenarios.json` (WP7)

For each service line and radius: an ordered list of sites with `facility_id` (or `lat`, `lon` for a new site), `kind` (`upgrade` or `new`), `marginal_gain`, `cumulative_coverage`. Two variants per setting: `naive` and `desert_aware`.

## App data (committed, small)

Written by WP7 or a small export script into `app/public/data/` in the checkout, and committed so the app can be hosted. Keep the total under about 15 MB.

| File | Contents |
| --- | --- |
| `meta.json` | Counts, model metrics, generated time, caveat strings, list of binary columns |
| `po_lon.f32`, `po_lat.f32` | Float32 arrays, one value per geocoded post office |
| `po_district.u16` | Index into `districts.json` |
| `po_dist_<line>_g<k>.f32` | Distance in km per post office |
| `facilities.json` | Array of `{id, name, lat, lon, type, operator, city, state, grades: {emoc, dialysis, icu}, evidence: {emoc: [...], ...}, n_domains}` |
| `districts.json` | Array of `{key, state, name, quadrant: {...}, nfhs: {...selected indicators}, access: {...}, residual, crosswalk_method}` |
| `scenarios.json` | Copy of the gold file |
| `story.json` | The scripted beats for the video. Hand-authored by WP9 |

Until real outputs exist, WP8 generates mock versions of these files with the same shapes, using the real post office coordinates so the map already looks right.
