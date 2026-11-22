# WP1: Silver facilities

**Status Nov 21: done, kept unchanged by the re-scope (D023).** `silver/facilities.parquet` (9,989 rows) and `silver/facts.parquet` exist. The coordinate check against each facility's PIN (`coord_check`, `vf.geo.check_facility_locations`) is the base of WP11's location-conflict flag. Rerun only if WP12 finds the Marketplace tables differ from the local CSV export.

Size: small to medium (about 1 hour). Depends on WP0. Blocks WP3, WP4, WP5.

## Goal

A clean, typed facilities table and a long table of facts.

## Inputs

- `data/raw/facilities.csv` through `vf.io.read_facilities()`
- `docs/02_data_dictionary.md`, `docs/03_data_profile.md`

## Outputs

- `silver/facilities.parquet`
- `silver/facts.parquet`
- `scripts/wp1_silver_facilities.py`

## Steps

1. Load the 10,000 real rows with `vf.io.read_facilities()`. Keep `row_status`.
2. Resolve duplicates: 11 `unique_id` values appear twice. Look at them, decide which row to keep (suggest: the one with more facts), and log the rule.
3. Convert null tokens to real nulls. Type the numeric fields (`numberDoctors`, `capacity`, `yearEstablished`).
4. Coordinates: parse `latitude` and `longitude`; set to null when outside India (`vf.io.in_india`). Record `coord_valid`.
5. PIN: keep only 6-digit values.
6. Parse the list fields with `vf.io.parse_list`; dedupe `specialties`; drop empty facts.
7. Derive `source_domains` from `source_urls` (lowercase, strip `www.`, drop empties) and `n_source_domains`.
8. Map `operatorTypeId` value `government` to `public`.
9. Build `facts.parquet`: one row per fact with `fact_id`, `facility_id`, `field`, `text`, `text_norm`.
10. Optional: try to repair the 30 truncated rows by re-attaching the junk fragments that follow them. Only do this if it is clean; otherwise leave them flagged.

## Acceptance checks

- [ ] `facility_id` is unique
- [ ] Row count is 10,000 minus the duplicates removed, and `row_status` has exactly 30 `truncated` unless repaired
- [ ] No cell in any string column equals `null`
- [ ] `coord_valid` is true for about 9,964 rows
- [ ] `facts.parquet` has about 450,000 rows (460,132 raw facts minus 8,548 empty strings, minus facts on removed duplicates)
- [ ] Deduped specialty counts match the profile: `gynecologyAndObstetrics` about 4,497, `nephrology` about 2,156, `criticalCareMedicine` about 1,677
- [ ] The script prints these numbers when run

## Pitfalls

- `coordinates` is GeoJSON in lon, lat order. Prefer the separate `latitude` and `longitude` columns.
- Some names contain the full address. Leave them; do not try to split.
- Do not normalize `address_stateOrRegion` here. Geography comes from WP2.
