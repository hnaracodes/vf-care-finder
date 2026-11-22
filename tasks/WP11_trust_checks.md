# WP11: Trust checks

New in the re-scope (D023). Size: medium (about 2.5 hours). Depends on WP1 and WP3 (v0.1 grades are enough to start). Feeds WP5, WP8.

## Goal

For every facility, flags that say when the record should not be trusted at face value, each with the text that triggered it and a measured precision. The brief names three: merged records, contradictory locations, thin support.

## Inputs

- `silver/facilities.parquet` (`coord_check`, `coord_pin_km`, `pin`, `city_raw`, `state_raw`, `name`, `facility_type`, `specialties`, `official_website`, `source_domains`, `description`)
- `silver/facts.parquet`, `gold/capability_grades.parquet`
- `silver/pin_crosswalk.parquet`, `silver/post_offices.parquet` (the PIN's state and district)

## Outputs

- `gold/facility_flags.parquet` (contract in `docs/07_data_contracts.md`): one row per facility per flag raised
- `gold/facility_duplicates.parquet`: pairs of records that are likely the same facility
- `scripts/wp11_trust_checks.py`
- `data/labels/flag_review.csv`: the hand-reviewed sample
- `reports/trust_checks.md`: what each check does, how often it fires, its precision on the sample, and examples

## Checks

### 1. Location conflict (`location_conflict`)

WP1 already compared each point to its PIN (`coord_check`). Build on it:

- `excluded_unresolved` (282): severity `major`; location unusable.
- `kept_unresolved` (123): `major`, blocking.
- `moved_to_pin` (785): `minor`; location is the PIN centre, precision from the gazetteer.
- New: `state_raw` names a different state from the PIN's (use the PIN directory's state; match with `vf.geo.norm_state` and a short alias list), or `city_raw` names a different district. `minor` if the point agrees with the PIN, `major` if neither agrees.

### 2. Possible merged record (`possible_merge`)

Signals, each recorded separately in `reason`:

- The name joins two facility names ("X Hospital & Y Clinic", "X Hospital / Y Nursing Home").
- A fact or the description names a different facility that exists in the table in another city, or names two cities or PINs as the facility's own address.
- `official_website` is shared by records in different districts (chain or merged listing).
- Specialty count far above its facility type (above the 99th percentile for its type).

Severity `minor` (penalty, warning). It becomes `major` only when two signals fire together.

### 3. Likely duplicate (`duplicate_of`)

Pairs within 1 km with name similarity above a threshold you set and record, or the same official website and PIN. Not a trust problem by itself, but WP5 must not show both on two cards.

### 4. Implausible claim (`implausible_claim`)

Per facility and service line: `cardiac_surgery`, `neurosurgery`, `icu`, `burns`, `cath_lab`, `nicu` graded 1 or higher on a record whose `facility_type` is `clinic`, `dentist`, `doctor` or `pharmacy`. Blocking for that line. Also flag `n_beds` below 10 for `icu`, `cardiac_surgery`, `neurosurgery` when `n_beds` is present. Read 20 before trusting this: some "clinics" in the table are real hospitals.

### 5. Thin evidence

Not a flag row: it lives in `capability_grades.evidence_strength` (WP3). This WP reports its distribution per line and checks that it behaves (10 hand-read examples per level).

## Steps

1. Implement each check as a pure function in `src/vf/trust.py` with unit tests on hand-made rows.
2. Run, print counts per check and severity.
3. Review: sample 30 flagged records per check (fewer if the check fires less) and 20 unflagged, read them, mark `correct` or `wrong` in `data/labels/flag_review.csv`. Report precision with counts. If a check's precision is under 70%, weaken it to a warning or drop it, and say so.
4. Report how many facilities graded 2+ on any line each check touches, and how many would leave `results` in the scenario suite.

## Acceptance checks

- [ ] `gold/facility_flags.parquet` written; every row has `reason` and, where a text triggered it, `quote`
- [ ] Counts per check and severity printed
- [ ] Precision per check from the hand review, with counts, in `reports/trust_checks.md`
- [ ] `pytest -q tests/test_trust.py` passes

## Pitfalls

- A chain hospital's shared website is not a merge. Flag the shared site, then look at whether the facts name the other branch's city.
- `state_raw` has 234 spellings (trap 7). Alias, do not group.
- Flags say "the record may be", never "the facility is".
