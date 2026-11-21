# WP5: Access scores

Size: medium (about 1.5 hours). Depends on WP1, WP2, and grades from WP3 (baseline) or WP4 (final). Feeds WP6, WP7, WP8.

## Goal

For every geocoded post office, the distance to the nearest facility at each grade for each service line, rolled up to districts.

## Inputs

- `silver/facilities.parquet` (coordinates)
- `silver/post_offices.parquet` (coordinates, `district_key`)
- `gold/capability_grades.parquet`

## Outputs

- `gold/post_office_access.parquet`
- `gold/district_access.parquet`
- `scripts/wp5_access.py`, written so it reruns in under a minute when grades change

## Steps

1. Keep facilities with `coord_valid`. For each service line and each minimum grade (1, 2, 3), build the set of qualifying facilities.
2. For each set, find the nearest facility to every geocoded post office with a haversine nearest-neighbor search (scikit-learn `BallTree` with the haversine metric works; inputs in radians, multiply by 6371 for km).
3. Write the per-post-office table with distances and nearest facility ids.
4. Aggregate by `district_key`: count of post offices, median, 75th and 90th percentile distance, share within 30 km and 60 km, and the number of qualifying facilities located in the district.
5. Print a national summary per service line and grade: number of qualifying facilities, median distance, share of post offices within 30 km and 60 km. These are headline numbers for the video.
6. Rerun when WP4 delivers final grades, and print how the headline numbers moved.

## Acceptance checks

- [ ] One row per geocoded post office (about 151,000)
- [ ] For every post office, distance at grade 1 is less than or equal to distance at grade 2, which is less than or equal to grade 3
- [ ] Spot check: three post offices checked by hand against a manual haversine calculation
- [ ] National summary printed for both the baseline and the final grades
- [ ] Districts with no `district_key` are counted and reported, not dropped silently

## Pitfalls

- Truncated facility rows have no coordinates and drop out here. Say how many graded facilities were lost that way.
- Distances are straight lines. Label them that way everywhere.
- The facilities table is a sample, so real distances can be shorter. This caveat goes on the methods card.
- Post offices are not people. Branch offices dominate rural areas, so an unweighted share over-represents villages relative to population. State it, and see WP7 for optional population weights.
