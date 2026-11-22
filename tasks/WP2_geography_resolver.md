# WP2: Geography and location resolver

Re-scoped Nov 21 (D023, D029). Part A is done. NFHS work is dropped: its outputs stay on disk but nothing new may read them (D024). Part B is new.

Size: Part B medium (about 2 hours). Depends on WP1 and Part A. Feeds WP5 and WP8.

## Goal

Turn whatever the user types as a place (PIN, city, district, village or post office, coordinates) into a point, a stated precision, and a plain-language label, or into a short list of candidates when the name is ambiguous.

## Part A: post offices and PIN lookup (done)

Already built by `scripts/wp2_geography_nfhs.py`:

- `silver/post_offices.parquet`: 165,627 offices, about 151,600 geocoded, swapped coordinates repaired, `district_key` attached
- `silver/pin_crosswalk.parquet`: PIN to (state, district) with shares, `is_ambiguous`
- `silver/district_crosswalk.parquet`: PIN-directory district to NFHS name. Still useful as a district name list; the NFHS mapping is no longer needed
- `vf.geo`: `haversine_km`, `post_office_coords`, `placeholder_points`, `group_centres`, `norm_name`, `place_key`, `city_keys`

Do not rerun the NFHS steps or extend them.

## Part B: location resolver

### Outputs

- `silver/places.parquet`: the gazetteer (contract in `docs/07_data_contracts.md`)
- `src/vf/locate.py`: `resolve(query: str, state: str | None = None) -> Resolution`
- `scripts/wp2_places.py`: builds the gazetteer, prints a summary
- `tests/test_locate.py` and `data/labels/resolver_cases.csv` (about 40 hand-written cases)
- `reports/resolver_eval.md`: hit rate and every miss

### Steps

1. Build the gazetteer, one row per place, each with `lat`, `lon`, `precision_km`, `state`, `district`:
   - PIN: point = median of the PIN's geocoded offices that are neither `coord_suspect` nor `coord_placeholder` (fall back to all geocoded offices and say so in `note`). `precision_km` = 90th percentile distance of those offices from the point, at least 2 km.
   - District: point from `vf.geo.group_centres` (head and sub offices), precision = 75th percentile office distance from the centre.
   - Post office name: strip suffixes (`B.O`, `S.O`, `H.O`, `G.P.O`) with the existing place normaliser. One row per (name, state, district); precision = the PIN's precision. Placeholder points get a precision of the district's.
   - City: for every `city_raw` in the facilities table that is not already a district or office name, the median of that city's facilities with `coord_check == ok`, if there are at least 3.
2. Resolver, in this order (D029):
   - Coordinates: `lat, lon` in either order when one value fits India's latitude range and the other its longitude range; reject points outside India with a message.
   - PIN: a 6-digit token. Unknown PIN: say so and fall back to the rest of the text.
   - Name: normalise (case, punctuation, common suffixes such as "district", "city", "nagar" kept as is), then exact match on district, city, office name, in that order; then fuzzy within the given state (rapidfuzz, threshold recorded); then fuzzy nationally.
   - An optional state, typed or picked, filters every step. "Aurangabad, Bihar" parses the state from the text.
   - When the top candidates are in different districts and within 3 points of each other in match score, return `status = "ambiguous"` with up to 5 candidates. Never pick silently.
3. Every `Resolution` carries `status` (`ok`, `ambiguous`, `not_found`, `outside_india`), `lat`, `lon`, `precision_km`, `kind` (`coords`, `pin`, `district`, `city`, `post_office`), `label` (for example "PIN 302001, Jaipur, Rajasthan"), `how` (one plain sentence), and `candidates`.
4. Test cases: write about 40 by hand before tuning anything, covering the brief's places (Jaipur, Lucknow, a rural Bihar village, a Maharashtra district, a highway town), PINs, coordinates in both orders, misspellings ("Lucknaw", "Banglore"), old and new names (Gurgaon and Gurugram, Allahabad and Prayagraj), ambiguous names (Aurangabad, Bilaspur, Hamirpur), and nonsense. Freeze the file, then score.

## Acceptance checks

- [ ] Gazetteer row counts by `kind` printed; every row has a finite point inside India and `precision_km > 0`
- [ ] Every PIN in the directory with a geocoded office resolves
- [ ] Hit rate on the frozen cases reported, with every miss listed and explained; ambiguous cases return candidates, not a guess
- [ ] `resolve` answers in under 50 ms per query after loading
- [ ] `pytest -q tests/test_locate.py` passes

## Pitfalls

- A PIN can span several districts (1,478 ambiguous PINs). Label it with its main district and say "mostly in".
- Branch office coordinates are often a same-named village elsewhere (`coord_suspect`) or a shared district-town point (`coord_placeholder`). Keep them out of PIN centres.
- State values: the PIN directory has `NA` for some rows, and facility `state_raw` has 234 spellings (trap 7). Use the PIN directory's state names.
- Do not present a district-centre point as the user's location. Say "centre of X district, about N km across".
