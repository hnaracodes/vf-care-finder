# WP2: Geography and NFHS

Size: medium (about 1.5 hours). Depends on WP0. Runs in parallel with WP1. Blocks WP5, WP6.

## Goal

Clean post offices, a PIN lookup that does not fan out, a district crosswalk between the PIN directory and NFHS, and a numeric NFHS table with flags.

## Inputs

- `data/raw/india_post_pincode_directory.csv` through `vf.io.read_pincodes_raw()`
- `data/raw/nfhs5_district_health_indicators.csv` through `vf.io.read_nfhs_raw()`

## Outputs

- `silver/post_offices.parquet`
- `silver/pin_crosswalk.parquet`
- `silver/district_crosswalk.parquet` and `silver/district_crosswalk_review.csv`
- `silver/nfhs.parquet` and `silver/nfhs_flags.parquet`
- `scripts/wp2_geography_nfhs.py`

## Steps

1. Post offices: parse coordinates with `vf.io.to_float`, null out anything outside India, set `coord_valid`. Expect about 151,000 valid.
2. PIN crosswalk: for each PIN, count offices per (state, district), compute shares and rank. Mark PINs with more than one district as ambiguous (expect 1,478).
3. NFHS: parse every indicator cell with `vf.io.parse_nfhs_value` into a value table and a flag table. Build `district_key`.
4. District crosswalk, in this order:
   - normalize names (uppercase, `&` to AND, strip punctuation), fix the three known state mismatches (`Maharastra`, `NCT of Delhi`, `Dadra and Nagar Haveli and Daman and Diu`)
   - exact match on (state, district): expect about 565 of 706 NFHS districts
   - fuzzy match within state (rapidfuzz), with a score threshold you choose and record. NFHS names look cut off at 25 characters, so prefix matching helps
   - districts created after the survey: map the new PIN-directory district to its parent NFHS district, method `parent_district`. A short hand-made mapping is fine; record the source of each
   - anything left is `unmatched`
5. Write the review file with every non-exact match, sorted by score, so a human can skim it in two minutes.
6. Attach `district_key` to each post office.
7. Compute and print the national share of institutional births in public facilities (weighted by `households_surveyed` as a rough weight, and say that it is rough). The story compares this with the 4.7% public share in the facilities table.

## Acceptance checks

- [ ] `post_offices.parquet` has 165,627 rows and about 151,000 with `coord_valid`
- [ ] No NFHS indicator cell is parsed as `unparsed`
- [ ] Suppressed cells are null, not zero: 4,125 suppressed and 5,068 low-sample cells in total
- [ ] Crosswalk summary printed: counts by `match_method`, and the share of geocoded post offices that received a `district_key`
- [ ] At least 95% of geocoded post offices have a `district_key`. If not, say why
- [ ] Review file exists

## Pitfalls

- One state value in the PIN directory is the string `NA`.
- Do not use polygons unless the name matching fails badly. The map does not need them (see D009).
- Never silently drop unmatched districts. They must show up in counts.
