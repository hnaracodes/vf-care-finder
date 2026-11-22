# WP11 trust checks

Check version `wp11_v3` (per-line merge rule, Nov 22), on `gold/capability_grades_v03.parquet`, the recipe v0.3 grades the shipped v1 ranker reads (D048, D052). The script now reads v0.3 when it exists. Grade 1+ is identical under v0.2 and v0.3, so the location, record-level merge and implausible-claim flags and the duplicate pairs are unchanged from `wp11_v2`. Script: `scripts/wp11_trust_checks.py`; rules: `src/vf/trust.py`; hand review: `data/labels/flag_review.csv` (452 rows read by an agent, not by Hruday or a clinician; rounds 1 to 4 are the v2 review, round 5 is the per-line rule).

## Findings first

- **New rule: a possible merge is now major for a line when that line's own supporting evidence names, or sits among facts about, another facility or city.** It blocks 107 more facility-lines graded 2+ (79 facilities) that no other check blocked, and 170 in all (63 were already blocked by another major flag). On a fresh hand-read sample of 30 newly blocked lines, 26 were right, 1 wrong and 3 unsure (87%; 95% interval 70% to 95%). The first version of the rule scored 18 of 30 (60%) and the second 22 of 30 (73%), so it was narrowed twice (see "Per-line merge rule").
- It fixes the Gadchiroli error from the blind v0 against v1 comparison (D058): the merged "Government Medical College And Hospital Chandrapur" record is now blocked for C-section, and the v1 ranker abstains there, as v0 did. It also drops Dhanwantari Hospital, the merged 12-bed record that the judges flagged, from the top of NICU near Pune.
- 2,471 of 9,989 records (25%) get at least one flag (unchanged: the per-line rule only adds lines to records that already have a merge flag). 3,664 flag rows in all.
- The checks block 1,010 of the 10,957 facility-line pairs graded 2 or 3 under recipe v0.3 (520 facilities); 903 (441 facilities) before the per-line rule. Blocked pairs drop to the "also nearby, not verified" list.
- On the 24 recipe scenarios (v1 ranker, v0.3 grades), all checks together replace 6 shown results, in 7 scenarios, and change one status: Gadchiroli C-section now abstains. Against the `wp11_v2` flags, the per-line rule changes exactly two scenarios: Gadchiroli, and NICU near Pune (Dhanwantari Hospital leaves; KEM, Inamdar and Deenanath Mangeshkar are shown).
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
| | `evidence_names_other` | Per line, on a record that already has a merge flag: a supporting fact for the line names another institution (a capitalised name ending in Hospital, Medical College, Institute, Nursing Home, Clinic and similar, whose distinctive words are not the record's own name, initials or website) or a place whose every reading is over 40 km from where the record is shown. Skipped when the fact also names the record's own place or institution, describes a unit inside it or run with it ("on-site", "unit of", "partnership"), a listing ("Top ... Hospitals", "listed among"), or a doctor's past post or training; and skipped when another supporting fact of the line names the record itself | major, that line only |
| | `evidence_beside_other` | Per line, same records: a supporting fact that names no place or institution sits within 2 facts (same field, list order) of a fact that names a place over 40 km away, and no supporting fact of the line sits next to a fact naming the record's own place | major, that line only |
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
| `possible_merge` | major | 395 | 287 |
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
| `evidence_names_other` (per line) | 53 |
| `evidence_beside_other` (per line) | 261 |
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
| 5a | possible_merge, per line | first rule, any record, newly blocked lines graded 2+ | 18 / 30, 1 unsure | 60% | narrowed |
| 5b | possible_merge, per line | second rule, any record, fresh lines | 22 / 30, 2 unsure | 73% | narrowed to merge-flagged records |
| 5c | possible_merge, per line | final rule, fresh lines | 26 / 30, 3 unsure | 87% | keep |

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

### Per-line merge rule (round 5)

Why: in the blind v0 against v1 comparison (D058, `reports/ranking_v0_vs_v1.md`), v1's one wrong answer was Gadchiroli C-section. It recommended "Government Medical College And Hospital Chandrapur", a merged record whose caesarean quote ("Cesarean delivery: the newborn is placed at the edge of the table and UCM is performed by a second team member...") is from a cord-milking trial report, and whose other facts describe IGGMC Nagpur and Sir J.J. Hospital, Mumbai. Its merge flag was only minor, so it passed the gate.

The rule reads each graded line's `evidence_items` in `gold/capability_grades_v03.parquet` and the facts next to each one in `silver/facts.parquet`. On a record with a record-level merge flag, the line gets its own `possible_merge` row (major, `service_line` set), so the ranker blocks that line and no other. The reason is a plain sentence naming the other place, and `quote` is the supporting fact verbatim:

- "The record's evidence for burn care names another facility, Indira Gandhi Government Medical College, so it may describe that place and not this one."
- "The record's evidence for C-section and high-risk delivery sits among facts about Mumbai, not this facility, and the record already mixes in other places' facts, so it may come from that place."

The Chandrapur caesarean quote names no place itself. The rule catches it because the fact two places after it is "METs were measured ... in a study at Sir J.J. Group of Hospitals, Mumbai". It does not trace the trial to Nagpur. That is how `evidence_beside_other` works in general: facts keep their source page's order inside a field, so a fact with no names of its own is read with its neighbours.

How it was narrowed. I read 30 newly blocked lines graded 2+ and 20 graded 2+ lines on merge-flagged records that the rule left alone, three times, each time on lines not read before:

| Round | Rule | Newly blocked: right / wrong / unsure | What went wrong, and the change |
| --- | --- | --- | --- |
| 5a | any record; institution or city in the fact, or any institution or city in the neighbours | 18 / 11 / 1 (60%) | Department and listing names read as other institutions ("Cancer & Chemotherapy Clinic", "Top Neurosurgery Hospitals", "Radiation Oncology Hospital", "Telemedicine Clinic"); the record's own name with a place inside it ("Shivaleela Foundation's Hubli Super-Speciality Hospital"); a spelling of its own name ("Eve" for Eva); a doctor's fellowship city named as the source. Also, though marked right because the quoted fact really is Panipat's, one Park Hospital Panipat fact blocked a Karnal line whose other supporting fact names Park Hospital Karnal itself. Changes: listings and specialty words do not count, the record's own name is checked before place words are removed, past posts and training are skipped, a line keeps its grade when another supporting fact names the record, and neighbours count only when they name a city |
| 5b | same, narrowed | 22 / 6 / 2 (73%) | On records without a merge flag, the fact-level test was right only 1 time in 7 (a paper title, "between Chennai and Kolkata", a college's own attached hospital, two department names). On merge-flagged records it was 21 of 23. Change: the rule runs only on records that already carry a merge flag, which is the brief's own condition ("a possible merge must be major when...") |
| 5c | final | 26 / 1 / 3 (87%) | The one wrong line: Life Care Hospital, whose city text is Urmar Tanda in Hoshiarpur district, but whose PIN cannot be located and whose map point is about 100 km away; its "trauma center in Hoshiarpur" fact is its own, and the problem is the point. Unsure: two facts sitting between a fact about the record's own town and one about another, and one directory line ("top urology hospital in Navi Mumbai") on a Pune record |

Rounds 5a and 5b were used to design the rule, so only 5c is a fresh measure. 26 of 30 is above the 80% bar; its 95% Wilson interval is 70% to 95%, so the true rate could be below 80%.

Lines the rule leaves alone (round 5b sample, valid for the final rule because the last change did not touch merge-flagged records): 17 of the 20 show no sign of borrowed evidence. 3 are misses: two supporting facts sit beside facts that name another hospital, not a city (Saifee Hospital Mumbai on "Sai Hospital" Nashik, "Pawade Hospital ... Arvi" on Padale Hospital), and one sits beside "Navjivan Hospital operates in Gandhidham", a town the gazetteer lacks. In round 5a's unblocked sample, the final rule now blocks the two clear misses (Chirayu Hospital, Osmanabad facts on a Ratnagiri record; Shubham Hospital, Khandwa facts on a Bhopal record).

Facility-lines graded 2+ (recipe v0.3) that the rule blocks, per service. "Newly blocked" means no other major flag already blocked the line. "Not applied" is what the fact-level test would add on records without a merge flag (computed, not used):

| Service | Blocked | Fact names another place | Fact sits beside one | Newly blocked | Not applied |
| --- | --- | --- | --- | --- | --- |
| emoc | 7 | 1 | 6 | 4 | 2 |
| nicu | 12 | 1 | 11 | 8 | 0 |
| dialysis | 12 | 8 | 4 | 7 | 3 |
| icu | 47 | 3 | 44 | 29 | 5 |
| cardiac_surgery | 6 | 1 | 5 | 3 | 2 |
| cath_lab | 7 | 1 | 6 | 4 | 0 |
| mri | 11 | 2 | 9 | 8 | 3 |
| ct | 16 | 5 | 11 | 11 | 6 |
| trauma | 33 | 6 | 27 | 21 | 6 |
| burns | 3 | 1 | 2 | 3 | 0 |
| neurosurgery | 13 | 3 | 10 | 6 | 6 |
| oncology | 3 | 2 | 1 | 3 | 1 |
| all | 170 | 34 | 136 | 107 | 34 |

ICU and trauma carry most of the new blocks because their supporting facts are often short ("ICU facility", "24/7 emergency care") and sit in lists copied from directory pages about a same-name hospital elsewhere.

## Effect on results

Facility-line pairs graded 2 or 3 under recipe v0.3 that each check touches (record-level flags touch every line of the record):

| Check | Facilities | Facility-lines graded 2+ |
| --- | --- | --- |
| `location_conflict` major (blocking; 282 already have no location) | 199 | 511 |
| `location_conflict` minor (warning; no evidence allowance) | 431 | 1,268 |
| `possible_merge` major (blocking; record-level, or that line for the per-line rule) | 164 | 332 |
| `possible_merge` minor (warning; no evidence allowance) | 456 | 1,227 |
| `implausible_claim` (blocking, that line) | 205 | 255 |
| duplicates: the record not kept | 39 | 126 |

`possible_merge` major was 51 facilities and 181 facility-lines before the per-line rule. All checks together block 1,010 facility-lines graded 2+ (520 facilities), up from 903 (441).

Scenario suite (`data/labels/scenarios.csv`, the 24 recipe scenarios, resolver `vf.locate`, the v1 ranker on v0.3 grades), each check alone against no flags at all. These differ from the `wp11_v2` report, which ran the v0 ranker on v0.2 grades:

| Check | Results replaced | Scenarios changed | Status changed |
| --- | --- | --- | --- |
| `location_conflict` | 3 | 4 | 0 |
| `possible_merge` | 3 | 4 | 1 |
| `implausible_claim` | 1 | 1 | 0 |
| duplicates | 0 | 0 | 0 |
| all | 6 | 7 | 1 |

The facilities that leave a top 3 with all checks on, and why:

- C-section near Gadchiroli: Government Medical College And Hospital Chandrapur (per-line merge rule: its caesarean fact sits among Sir J.J. Mumbai facts). Nothing else is recommendable inside the 2-hour window, so the answer is now an abstention.
- NICU near Pune: Dhanwantari Hospital (per-line merge rule: "NICU and PICU units" sits among facts about Saravali, Thane, on a record whose PIN is in Jalgaon). KEM, Inamdar and Deenanath Mangeshkar are shown.
- C-section near Lucknow: Nova Hospital (point moved to its PIN, and "Nova Hospital is located in Rampur"; both minor, so it loses the evidence allowance).
- Cath lab near Indore: Citi Hospital (unresolved location, and "City Hospital is located in Jabalpur").
- Brain surgery near Chandigarh: Alchemist Hospital (city text Gurgaon, PIN and point near Chandigarh; minor, so it loses the evidence allowance).
- ICU near Lucknow: Osteon Bone & Spine Clinic (a clinic record; implausible claim for ICU).
- Heart surgery near Bhubaneswar: the same three facilities, in a different order.

Against the `wp11_v2` flags, only the first two change: the per-line rule changes no other scenario. The changed scenarios carry WP5 hand checks whose fingerprints may no longer match, so `scripts/wp5_scenarios.py` may show them as stale.

## Thin evidence (WP3's `evidence_strength`, not a flag)

Grade 2+ rows by strength, per line, under recipe v0.3:

| Line | thin | moderate | strong |
| --- | --- | --- | --- |
| emoc | 254 | 140 | 38 |
| nicu | 363 | 276 | 55 |
| dialysis | 402 | 395 | 239 |
| icu | 887 | 1,071 | 315 |
| cardiac_surgery | 202 | 233 | 119 |
| cath_lab | 203 | 309 | 179 |
| mri | 306 | 261 | 367 |
| ct | 451 | 375 | 596 |
| trauma | 387 | 282 | 463 |
| burns | 57 | 15 | 73 |
| neurosurgery | 388 | 417 | 397 |
| oncology | 149 | 161 | 132 |
| all | 4,049 | 3,935 | 2,973 |

37% of grade-2+ rows are thin; for `emoc` it is 59% (254 of 432), so most C-section evidence rests on a single fact or a single-source record.

Hand read, 10 per level (on the v0.2 grades, before WP4's rebuild): thin 10 of 10 and moderate 10 of 10 matched the facts shown. Strong 8 of 10: one was a merged record ("The Liver Unit and Monarch Liver Lab, ... Ernakulam Medical Centre") whose strong trauma evidence belongs to the parent hospital, and one was "Heart Transplant" at a Panipat hospital with a 6-bed ICU, where strong overstates a likely scraped claim. One thin MRI grade rests on a photo caption ("resembles a medical imaging scanner (MRI/CT)"); thin is right, but the grade itself should not be 2.

## Known limits and follow-ups

- The grade-2+ samples for `type_cannot_offer` and evidence strength were read on the grades before WP4's rebuild. The flag rules did not change, and 1,027 of the 1,039 implausible-claim rows are the same rows as before, but those samples were not redrawn.
- The review is by an agent reading the record text, not by checking the facilities in the world. "Correct" means the record shows the problem, not that the facility does.
- `type_cannot_offer` trusts `facility_type`; a real hospital labelled `clinic` with no "hospital" in its name or description is still blocked (3 of 20 on fresh grade-2+ rows).
- Major merge flags block chain branches whose own record mixes in another branch (Sankara Eye Bangalore, Rainbow Vijayawada). The record is mixed, which is what the flag says, but the hospital likely offers the service. If that is too strict for the product, `possible_merge` major could become a minor flag with a larger penalty; that is a WP5 call.
- For WP1: the PIN directory places PIN 799001 (Agartala) in Telangana, so all 6 Agartala records with that PIN are `excluded_unresolved`. And `vf.geo.CITY_ALIASES` maps TUTICORIN to THOOTHUKUDI but not back, so Government Medical College Thoothukudi is excluded although its city names its PIN's place.
- A website owned by another organisation (St. John's with `msrmh.com`) is not detected.
- Per-line merge rule: it runs only on records that already have a merge flag, so a record whose only sign of mixing is a borrowed supporting fact is not caught (the same test finds 34 such grade-2+ lines; on a fresh read it was right 1 time in 7, so it is not applied). Neighbours count only when they name a city, so a fact beside "Saifee Hospital Mumbai" without the city, or beside a town missing from the gazetteer (district names, head post offices and `geo.CITY_ALIASES`; Gandhidham and Arvi are absent), is missed: 3 of 20 unblocked merge-flagged lines. It blocks the whole line when one supporting fact is borrowed, even if the others are the record's own, unless one of them names the record. It also blocks for a borrowed fact that only supports a minor requirement (Lokmanya Hospital Nigdi's "Open 24 hours" next to a Dhule fact), which can hide a large real hospital for that line. Where a record's map point is far from its own city (Life Care Hospital, Urmar Tanda), the rule reads its own city as another place.
- The per-line rows add `possible_merge` with a `service_line` to `gold/facility_flags.parquet`. `docs/07_data_contracts.md` says `service_line` is set for `implausible_claim`; it needs a line saying it is also set for per-line `possible_merge` rows (not edited here). The ranker already treats any row with a `service_line` as that line only. The app bundle (`scripts/wp12_bundle.py`) has to be rebuilt to pick the new rows up.

## Rerun

```bash
python scripts/wp11_trust_checks.py                  # flags, duplicates, review precision, scenario impact
python scripts/wp11_trust_checks.py --no-scenarios   # skip the scenario run
python scripts/wp11_trust_checks.py --round4         # append unread near_same_name pairs scoring under 100
python scripts/wp11_trust_checks.py --stats out.json # also write the numbers behind this report
pytest -q tests/test_trust.py
```

`--sample` redraws the round-1 sample, `--round2` appends a fresh round and `--round4` the fix-round census; existing marks are kept by `review_id`. The script never marks a row itself.
