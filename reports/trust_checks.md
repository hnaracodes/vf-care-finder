# WP11 trust checks

Check version `wp11_v2` (fix round), on the current `gold/capability_grades.parquet`: recipe v0.2, with WP4's student model tagging `ob_delivery`, `h24`, `icu` and `ventilator` and regex v0.2 for the rest (rebuilt by WP4 at 03:06 UTC on Nov 22, between the review and this rerun). Script: `scripts/wp11_trust_checks.py`; rules: `src/vf/trust.py`; hand review: `data/labels/flag_review.csv` (322 rows read by an agent, not by Hruday or a clinician: 321 marked correct or wrong, 1 marked unsure).

## Findings first

- 2,471 of 9,989 records (25%) get at least one flag. 3,350 flag rows in all.
- The checks block 905 of the 10,969 facility-line pairs graded 2 or 3 (441 facilities). Blocked pairs drop to the "also nearby, not verified" list.
- On the 24 recipe scenarios, all checks together replace 6 shown results, in 6 scenarios. No scenario changes status (no new abstentions). Implausible claims change none of them; duplicates change one (ICU near Lucknow shows Charak Hospital And Research Centre in place of its second record, Charak Hospital).
- Fix round: the duplicate rule was too loose for names that only nearly match. Of the 19 `near_same_name` pairs that scored under 100, 9 were different facilities. The tightened rule keeps the 8 that were right and drops all 9 wrong ones, at the cost of 1 true pair (see "Fix round" below).
- Precision on the hand review ranges from 100% for the location checks and for text naming another address (15 of 15 each) down to 0% for specialty breadth (0 of 15). Four signals scored under 70% in round 1 and were dropped or weakened (table below).
- Caveat: three of the revised rules (`name_joins_two`, the duplicate kind match, the description check for implausible claims) were changed after reading round 1. Their round-2 numbers on fresh rows are the honest ones. `name_joins_two` had no fresh rows to test.

## What each check does

| Check (`flag`) | Signal | Rule | Severity |
| --- | --- | --- | --- |
| `location_conflict` | `coord_excluded` | WP1 `coord_check = excluded_unresolved`: point over 100 km from its PIN's area, outside its district, city text does not settle it. Location already null | major |
| | `coord_unresolved` | `kept_unresolved`: same, 40 to 100 km | major (blocking) |
| | `moved_to_pin` | `moved_to_pin`: shown at the PIN centre | minor |
| | `city_mismatch` | Point agrees with the PIN (`ok`, `ok_same_district`), but `city_raw` names a place in another district and none of the PIN's places. The city's candidate districts are narrowed to the state the record names | minor |
| | `state_mismatch` | Point agrees with the PIN, `state_raw` names a different state, and the city does not name the PIN's place. Aliases for spellings; pre-2000 and 2014 state splits and the Delhi NCR and Chandigarh tricity pairs count as the same state | minor |
| `possible_merge` | `name_joins_two` | Name is "X <facility noun> & / and Y <facility noun>", and Y has a proper word that is not generic, not a place, and not in X | minor alone |
| | `text_other_address` | A fact or the description gives the facility's own location ("located/based/situated in", or a 6-digit PIN) in a district that is none of the record's own (PIN, point, city). Landmark, "near", referral, registered-office and similar contexts are skipped | minor alone |
| | `shared_website` | Official website (platforms, directories and `gov.in`/`nic.in` excluded) also given by a record in another district | corroborates only |
| | `specialty_breadth` | Specialty count above the 99th percentile for the facility type | computed, not used |
| | two counted signals | | major (blocking) |
| `implausible_claim` | `type_cannot_offer` | `cardiac_surgery`, `neurosurgery`, `icu`, `burns`, `cath_lab` or `nicu` graded 1 or more on a `clinic`, `dentist`, `doctor` or `pharmacy` record, unless the name or the description calls it a hospital or nursing home | major, that line only |
| | `few_beds` | `n_beds` under 10 with `icu`, `cardiac_surgery` or `neurosurgery` graded 1+ | dropped |
| duplicates | `near_same_name` | Within 1 km, distinctive-name score 90 or more (generic words, honorifics, and district, division and state names removed), and the same facility kinds (dental, eye, IVF, diagnostic...) unless the website is the same. Under 95, the two records must not give different websites, and "one name's words inside the other's" counts only when they give the same website | not a flag |
| | `same_site_and_pin` | Same official website and same PIN | not a flag |

Every flag row has a `reason` sentence for the card. Reasons describe the record and never say "the facility is": location reasons state what the record shows ("The record's map point is 64 km from its PIN code's area"), and merge and implausible-claim reasons, which infer something about the facility, use "may" (all 1,975 of them). When a place named in a fact fits several districts, the merge reason names the district called after that place, else one in the record's own state ("located in Nagpur" now reads Nagpur, Maharashtra, not "Hisar, Haryana or Nagpur, Maharashtra"). Text-triggered signals carry the verbatim `quote`: the city or state text, the name, the fact, or the website. `implausible_claim` quotes the facility type. One row per facility per flag (and per line for `implausible_claim`); several signals of one flag are joined in `signal` with `+`.

## Counts (final rules)

| Flag | Severity | Rows | Facilities |
| --- | --- | --- | --- |
| `location_conflict` | major | 405 | 405 |
| `location_conflict` | minor | 970 | 970 |
| `possible_merge` | major | 81 | 81 |
| `possible_merge` | minor | 855 | 855 |
| `implausible_claim` | major | 1,039 | 783 |

| Signal | Rows |
| --- | --- |
| `coord_excluded` | 282 |
| `coord_unresolved` | 123 |
| `moved_to_pin` | 785 |
| `city_mismatch` | 180 |
| `state_mismatch+city_mismatch` | 5 |
| `name_joins_two` | 5 |
| `text_other_address` | 850 |
| `text_other_address+shared_website` | 81 |
| `type_cannot_offer` | 1,039 |

`state_mismatch` never fires alone: every state mismatch that is not explained by an alias, a state split, NCR, or a city that names the PIN's place also has a city mismatch. Before combining, merge signals fired on: `text_other_address` 931 records, `shared_website` 424, `specialty_breadth` 94, `name_joins_two` 5.

Duplicates: 75 pairs (36 `near_same_name`, 25 `same_site_and_pin`, 14 both); 87 under the round-2 rule.

Only `implausible_claim` changed with the new grades (1,032 rows on 780 facilities before, 12 rows added and 5 gone); the location and merge counts are the same as before the fix round. `keep` is the record with more lines graded 2+, then the higher grade sum, then more source domains.

## Precision from the hand review

"Correct" means the record, read in full where needed, really does what the flag says: the location really conflicts, the record really mixes two facilities, the claim really is not credible for that record, the pair really is one facility. Round 1 sampled up to 15 records per signal (all of them when it fired less), 30 duplicate pairs, 20 grade-2+ implausible claims, 20 unflagged records, and 10 grade-2+ rows per evidence strength level. Round 2 drew fresh rows, never read before, for the rules changed after round 1.

| Round | Check | Signal | Correct / reviewed | Precision | Decision |
| --- | --- | --- | --- | --- | --- |
| 1 | location_conflict | `coord_excluded` | 15 / 15 | 100% | keep |
| 1 | location_conflict | `coord_unresolved` | 15 / 15 | 100% | keep |
| 1 | location_conflict | `moved_to_pin` | 15 / 15 | 100% | keep |
| 1 | location_conflict | `city_mismatch` | 14 / 15 | 93% | keep |
| 1 | location_conflict | `state_mismatch+city_mismatch` | 5 / 5 (all hits) | 100% | keep |
| 1 | possible_merge | `text_other_address` | 15 / 15 | 100% | keep |
| 1 | possible_merge | `name_joins_two` | 5 / 9 (all hits) | 56% | revised (see below) |
| 1 | possible_merge | `shared_website` | 8 / 15 | 53% | weakened: corroborates only |
| 1 | possible_merge | `specialty_breadth` | 0 / 15 | 0% | dropped |
| 1 | implausible_claim | `type_cannot_offer`, grade 1+ sample | 14 / 15 | 93% | keep |
| 1 | implausible_claim | `type_cannot_offer`, grade 2+ sample | 13 / 20 | 65% | revised: description check |
| 1 | implausible_claim | `few_beds` | 5 / 11 (all hits) | 45% | dropped |
| 1 | duplicate | `near_same_name` | 14 / 22 | 64% | revised: kind match |
| 1 | duplicate | `same_site_and_pin` | 0 / 8 | 0% | bug, fixed |
| 2 | implausible_claim | `type_cannot_offer`, grade 2+ (revised) | 17 / 20 | 85% | keep |
| 2 | duplicate | `near_same_name` (revised) | 10 / 13 | 77% | tightened in the fix round |
| 2 | duplicate | `same_site_and_pin` (fixed) | 6 / 7 | 86% | keep |
| 2 | possible_merge, major | `text_other_address+shared_website` | 15 / 15 | 100% | keep |
| 3 | possible_merge | `text_other_address`, hits added by the state-name fix | 10 / 10 (all) | 100% | keep |
| 4 | duplicate | `near_same_name` scoring under 100, not read before (census) | 4 / 10, 1 unsure | 40% | tightened (below) |

What went wrong, and what changed:

- `specialty_breadth`: every sampled record was a large real hospital (Amrita Kochi, Lisie, Batra Delhi) or a clinic with a long list and no other sign of a merge. Breadth is not merge evidence here. It is still computed and listed in the sidecar, and used nowhere.
- `shared_website`: chain branches share a site and most of their facts are their own (CARE Raipur, Motherhood Mohali). It was right when the facts name another branch (Narayana Shalimar with Barasat facts, Ujala Cygnus Haldwani with "Located in Kashipur"), which `text_other_address` already catches. So it now only raises a merge flag to major next to another signal. As that combination it was 15 of 15 in round 2.
- `name_joins_two`: the 4 wrong hits were a possessive read as a proper name ("Children's"), one brand at one address ("Arivu Neuro Clinic and Arivu Dental Clinic", "Shyam Nidan Clinic And Shyam Diagnostics"), and an address ("Eye Hospital Road"). The revised rule keeps exactly the 5 correct hits. That 5 of 5 was measured on the rows used to fix the rule, and no fresh hits exist, so read it as "at most 5 records, hand-checked", not as a precision.
- `type_cannot_offer` at grade 2+: 4 of the 7 wrong ones were records whose description calls them a hospital ("Saiorthocare Hospitals Pvt Ltd", "Hospital and Medical Care", "Hospital with Emergency and Critical Care"). The rule now skips a record whose name or description calls it a hospital or nursing home, unless the description only names one nearby ("opposite Janapriya Hospital"). On fresh grade-2+ rows: 17 of 20. The 3 wrong were a 75-bed neonatal centre and two small surgical centres with an ICU. Precision on grade-1 claims is higher (14 of 15), but grade 1 is never recommendable anyway.
- `few_beds`: `n_beds` is often the ICU bed count ("Gunjan Hospital & ICU Unit", 4) or simply wrong (ILS Hospitals, 8; a record whose facts say 25 beds has `n_beds` 4). Dropped; the function keeps it behind `use_beds=True`.
- `near_same_name`: the wrong pairs shared one common word across different kinds of facility ("Divine Dental" and "Divine Nursing Home", "Deep Hospital" and "Deep dental clinic") or a surname ("Shah Piles Hospital" and "Dr Shah Hospital"). Pairs now need the same facility kinds unless they share a website, and "smile", "smiles" and address words no longer count as distinctive. Fresh pairs: 10 of 13. Still wrong: "Akash Hospital" and "Aakash Medical Centre", a women's hospital and an ultrasound centre both called Pristine, and two records sharing only "North Bengal".
- Fix round, `near_same_name`: see the next section.
- `same_site_and_pin`: round 1 paired records by position in a filtered frame, so the pairs were random. Fixed, with a regression test. Fresh pairs: 6 of 7; the wrong one is two hospitals of one group sharing a website and PIN (CMRI and B.M. Birla Heart Research Centre, Kolkata).
- `city_mismatch`: the one wrong flag was Rela Hospital, whose city reads "Chennai" while its PIN is in Chengalpattu district, part of greater Chennai.

### Fix round: names that only nearly match

An independent review found that the round-2 rule let weak name matches through. Any pair where one distinctive name's words were all inside the other's, with one word of 4+ letters, scored 90 and passed. Shared town words ("Hubli", "North Bengal") or a shared first word ("Asirvatham", "Venkateswara") were enough. The 10 of 13 for round 2 came mostly from pairs scoring 100, which hid this weak band.

I read all 19 `near_same_name` pairs scoring under 100 under the round-2 rule (8 already marked in rounds 1 and 2, 11 new in round 4): 9 the same facility, 9 different, 1 unsure.

| Under the final rule | Same facility | Different | Unsure |
| --- | --- | --- | --- |
| kept | 8 (Manjunath, Vaidyaratnam, Subam, Devmata, Vishwaraj, Panjabrao Deshmukh, Nair, Chirayu) | 0 | 0 |
| dropped | 1 (Aware Gleneagles Global, Saroor Nagar and LB Nagar: one rebranded hospital with its old and new websites) | 9 (Akash and Aakash, Medica North Bengal and North Bengal Neuro, Hubli Vascular and Hubli Hijama, Shiva and Shivay, Arthur Asirvatham and Asirvatham Speciality, Sri Vijaya Durga and Vijaya, two Venkateswara hospitals, Iqraa clinic and IQRAA hospital, Hi-Tech and Kansal's Hi-Tech Dental) | 1 (Sindhi Hospital and R K Sindhi Sangh Maternity Hospital, 30 m apart) |

The change (`trust.DUP_SURE_SCORE = 95`): a pair scoring under 95 must not give two different official websites, and the words-inside rule counts only when both give the same website. A name that matches exactly still stands when the websites differ: 17 of the 17 reviewed kept pairs at 100 are right, and scraped websites are often wrong. "Ultrasound" and "sonography" now mark a diagnostic kind, which separates the last wrong pair at 100 (Pristine Womens Hospital and Pristine Ultrasound).

Every reviewed `near_same_name` pair the final rule keeps is right (25 of 25: 17 at 100, 8 under). The rule was set after reading these pairs, and no unread pair is left under 100, so 25 of 25 is not a fresh precision. 25 kept pairs at 100 are unread. The reviewer read the 33 pairs at 100 and found them right.

Place words: duplicate names now drop district, division and state names (before, only district names). This changed no pair. Town and office names are not dropped. Across India they are 19,551 words that include brands found as a place somewhere ("Yashoda", "Amrita", "Manipal", "Ganga"), and dropping them lost 10 true pairs and added a wrong one. Taken only from the record's own district, they strip deity and person names that are also local place names: "Sri Vijaya Durga" and "Vijaya Super Speciality" then scored 100. The website rule alone handles every town-word case the review found.

Unflagged sample: 17 of 20 records had no problem the checks should have caught. The 3 misses: a Kolkata record whose facts say "Located in Siliguri, West Bengal, India", a Delhi dental record named "City Dental Centre- Middle East" with a `.ae` website, and St. John's Hospital Bangalore with the website of M S Ramaiah (`msrmh.com`). A website that belongs to another organisation is not detected: no check compares the website's owner with the record's name. The Siliguri miss was a bug: `vf.geo.place_key` drops direction words, so "West Bengal" became "BENGAL", was not seen as a state, and matched the record's own place names. States are now recognised before `place_key`. That added 10 West Bengal records (round 3: 10 of 10 correct, the Siliguri record among them) and removed 1 wrong flag ("Located in Chapra, West Bengal" had been read as Chapra in Bihar).

## Effect on results

Facility-line pairs graded 2 or 3 that each check touches (record-level flags touch every line of the record):

| Check | Facilities | Facility-lines graded 2+ |
| --- | --- | --- |
| `location_conflict` major (blocking; 282 already have no location) | 199 | 513 |
| `location_conflict` minor (penalty 0.10) | 432 | 1,272 |
| `possible_merge` major (blocking) | 51 | 181 |
| `possible_merge` minor (penalty 0.15) | 457 | 1,230 |
| `implausible_claim` (blocking, that line) | 205 | 255 |
| duplicates: the record not kept | 39 | 126 |

Scenario suite (`data/labels/scenarios.csv`, the 24 recipe scenarios, resolver `vf.locate`), each check alone against no flags at all:

| Check | Results replaced | Scenarios changed | Status changed |
| --- | --- | --- | --- |
| `location_conflict` | 3 | 5 | 0 |
| `possible_merge` | 4 | 4 | 0 |
| `implausible_claim` | 0 | 0 | 0 |
| duplicates | 1 | 1 | 0 |
| all | 6 | 6 | 0 |

The facilities that leave a top 3 with all checks on, and why:

- C-section near Lucknow: Nova Hospital (its point was moved to the PIN, and a fact says "Nova Hospital is located in Rampur") and St. Joseph Hospital Lucknow ("Located in Nehru Nagar, Ghaziabad").
- ICU near Lucknow: Charak Hospital, a duplicate of Charak Hospital And Research Centre (same website and PIN), which is shown in its place.
- High-risk delivery near Nashik: Om Hospital ("Om Hospital operates two locations in Pune: Bhosari (411039) and Gokhale Nagar Road (411016)").
- NICU near Pune: Siddhivinayak Hospital (unresolved location, and "Located in Amravati, Maharashtra, India").
- Cath lab near Indore: Citi Hospital (unresolved location, and "City Hospital is located in Jabalpur").

The 6 changed scenarios carry WP5 hand checks whose fingerprints no longer match, so `scripts/wp5_scenarios.py` will show them as stale. Two records WP5 noted as missed by its stub flags are now flagged: Sushma Hospital (city Moradabad, PIN and point in Lucknow) and Alchemist Hospital (city Gurgaon, PIN and point near Chandigarh), both `city_mismatch`, minor.

## Thin evidence (WP3's `evidence_strength`, not a flag)

Grade 2+ rows by strength, per line:

| Line | thin | moderate | strong |
| --- | --- | --- | --- |
| emoc | 259 | 132 | 41 |
| nicu | 316 | 255 | 135 |
| dialysis | 388 | 389 | 259 |
| icu | 887 | 1,071 | 315 |
| cardiac_surgery | 202 | 233 | 119 |
| cath_lab | 89 | 122 | 480 |
| mri | 306 | 261 | 367 |
| ct | 451 | 375 | 596 |
| trauma | 387 | 282 | 463 |
| burns | 57 | 15 | 73 |
| neurosurgery | 388 | 417 | 397 |
| oncology | 149 | 161 | 132 |
| all | 3,879 | 3,713 | 3,377 |

35% of grade-2+ rows are thin; for `emoc` it is 60% (259 of 432), so most C-section evidence rests on a single fact or a single-source record.

Hand read, 10 per level: thin 10 of 10 and moderate 10 of 10 matched the facts shown. Strong 8 of 10: one was a merged record ("The Liver Unit and Monarch Liver Lab, ... Ernakulam Medical Centre") whose strong trauma evidence belongs to the parent hospital, and one was "Heart Transplant" at a Panipat hospital with a 6-bed ICU, where strong overstates a likely scraped claim. One thin MRI grade rests on a photo caption ("resembles a medical imaging scanner (MRI/CT)"); thin is right, but the grade itself should not be 2.

## Known limits and follow-ups

- The grade-2+ samples for `type_cannot_offer` and evidence strength were read on the grades before WP4's rebuild. The flag rules did not change, and 1,027 of the 1,039 implausible-claim rows are the same rows as before, but those samples were not redrawn.
- The review is by an agent reading the record text, not by checking the facilities in the world. "Correct" means the record shows the problem, not that the facility does.
- `type_cannot_offer` trusts `facility_type`; a real hospital labelled `clinic` with no "hospital" in its name or description is still blocked (3 of 20 on fresh grade-2+ rows).
- Major merge flags block chain branches whose own record mixes in another branch (Sankara Eye Bangalore, Rainbow Vijayawada). The record is mixed, which is what the flag says, but the hospital likely offers the service. If that is too strict for the product, `possible_merge` major could become a minor flag with a larger penalty; that is a WP5 call.
- For WP1: the PIN directory places PIN 799001 (Agartala) in Telangana, so all 6 Agartala records with that PIN are `excluded_unresolved`. And `vf.geo.CITY_ALIASES` maps TUTICORIN to THOOTHUKUDI but not back, so Government Medical College Thoothukudi is excluded although its city names its PIN's place.
- A website owned by another organisation (St. John's with `msrmh.com`) is not detected.

## Rerun

```bash
python scripts/wp11_trust_checks.py                  # flags, duplicates, review precision, scenario impact
python scripts/wp11_trust_checks.py --no-scenarios   # skip the scenario run
python scripts/wp11_trust_checks.py --round4         # append unread near_same_name pairs scoring under 100
pytest -q tests/test_trust.py
```

`--sample` redraws the round-1 sample, `--round2` appends a fresh round and `--round4` the fix-round census; existing marks are kept by `review_id`. The script never marks a row itself.
