# Outside lists: public_list v2 (D055)

Built 2026-11-22 around 06:00 UTC from the silver tables of six sources; revised the same morning after review (section "Review fixes, 22 Nov" below). Code: `src/vf/public_list.py` (v2 section at the end), tests: `tests/test_public_list.py` (40 pass; full suite 1,104 passed, 9 skipped, 1 xfailed).

## Finding

What a deployed app would show, under the D057 terms rule as now enforced in code: 196 cited card facts (LaQshya 63, AERB 79, National Cancer Grid 54), and a lane of 1,204 listed facilities not linked to any record in this dataset (LaQshya 779, AERB 425). Built in full, the tables hold 253 card facts on 215 records and 1,592 lane rows. The gap is withheld for terms: 57 NHP blood bank facts (read from an unofficial copy), and 177 NCG, 150 MoHFW and 61 NABI lane rows ("all rights reserved" or permission required). MoHFW trauma and NABI burns give no card facts, because no band of their links reached the 0.90 rule. Caveat: most lane positions are PIN-area or district-centre points, never the site. All LaQshya rows are at district centres (median precision about 40 km), so their drive times are shown as wide ranges.

## What the server gets

`vf.public_list.annotate_answer(answer, service_line, lat, lon, state)` takes `Answer.to_dict()` and returns the same dict with two additions. Nothing existing is renamed, changed or removed. A test checks this.

- Every card dict in `results`, `beyond_radius`, `unverified_nearby` and `further_away` gets `outside_lines`, a list that may be empty. Each line is `{source_id, source_name, publisher, list_date, what, status, citation, text}`. `status` is `current`, `lapsed` or `undated`, computed when the request is served.
- The answer gets `outside_nearby`: up to 3 `{source_id, source_name, name, district, state, pin, lat, lon, position_precision_km, position_note, minutes_label, km, what, citation, text}`.
- Every `text` ends with "Outside data, not from this record." `LANE_HEADING` holds the lane title: "Also on government or registry lists nearby (not in this dataset)". That wording is Hruday's (D055) and fixed by the contract; see "Decisions for Hruday" for a proposed change.
- `annotate_answer` drops any lane entry whose name looks like one of the answer's own cards (within 30 km), so one place never shows as both a card and a lane entry.

The server calls it once per `/api/answer`, after the ranker: `d = pl.annotate_answer(ans.to_dict(), line, res.lat, res.lon, res.state)`. The tables load once and are cached (`default_outside()`). Lookup order: `$VF_BUNDLE_DIR`, then the app's `bundle/` folder next to `vf/`, then a build from `silver/`. A build from silver takes about 1.1 s. Loading from the bundle takes 7 ms, and one annotation takes 0.2 to 1.4 ms. `app/src/fixtures/make_fixtures_v2.py` already calls this signature.

`public_line()` and `PublicList` (v1) still work. One wording change: a lapsed LaQshya certificate now reads "certificate on the 8 Apr 2026 list, valid to <date>, not renewed on that list", as D051 requires. It used to read "(expired by <date>)". This affects v1 `public_line` text too.

### Which source goes where

| source | card facts on | lane for | shown by default (D057) |
|---|---|---|---|
| LaQshya (NHSRC, 8 Apr 2026) | emoc | emoc (units with a maternity operating theatre row only) | cards and lane |
| AERB radiotherapy licences (as on Apr 2025) | oncology | oncology | cards and lane |
| National Cancer Grid members (as served 22 Nov 2026) | oncology | oncology | cards only |
| MoHFW trauma care facilities (8 Feb 2023) | trauma (none accepted) | trauma | nothing (no cards accepted, lane withheld) |
| NABI burn-care providers (5 Feb 2026) | burns (none accepted) | burns | nothing (no cards accepted, lane withheld) |
| NHP blood bank directory (published 10 Aug 2017) | emoc, cardiac_surgery, trauma | never (D055) | nothing (unofficial copy) |

The gates are `OutsideSource.lane_public_ok` and `card_public_ok`. `lines_for`, `nearby` and `write_bundle` all apply them, so a bundle never carries a withheld row. `$VF_OUTSIDE_LANE_SOURCES` and `$VF_OUTSIDE_CARD_SOURCES` (comma-separated source ids, or `all`) switch sources on. The bundle's `outside_meta.json` records what was withheld under `withheld_terms`. Terms behind the defaults:

- LaQshya: NHSRC copyright policy (nhsrcindia.org/copyright-policy, read 2026-11-22T06:15Z; robots.txt allows it). Material may be reproduced free of charge without specific permission, accurately and not in a misleading context, and "the source must be prominently acknowledged". This allows the lane.
- AERB: "may be freely used elsewhere provided acknowledgement of the source is made".
- NCG and NABI: "All rights reserved". MoHFW: "may not be reproduced partially or fully, without due permission". D057 allows card facts on our own records only.
- NHP: GODL allows reuse, but the bytes come from a third-party GitHub copy (data.gov.in was unreachable), and D057 says unofficial re-hosts are not used.

A search with no service line shows every card fact except blood banks, and no lane. Lines with no list (dialysis, NICU, ICU, cath lab, CT, MRI, neurosurgery) get empty `outside_lines` and no lane.

## Per source

Link precision is same / (same + different) among pairs the agent readers read. The rule measures the matcher. Only reader-marked "same" pairs become card facts, so the accuracy of what cards show rests on one agent reader per pair, and no second reader has checked it. The reviewer read 10 accepted links each for AERB, NCG, NHP and LaQshya, and all 40 looked like the same institution. That is a spot check, not an agreement estimate. Join hazard for anyone recomputing: the AERB check file has 8 duplicate item_ids, so join reader verdicts on (item_id, our_facility_id), never item_id alone. Bounds are Clopper-Pearson. A band is accepted only if its one-sided 95% lower bound is at least 0.90. Only reader-marked "same" pairs inside an accepted band become card facts. Counts come from each source's accept stage (`reports/ext_<source>.md`). The lane column is after the v2 rules below.

| source | list rows | band: same / different (unsure) | precision, one-sided 95% lower, two-sided 95% | accepted links (records) | not in dataset | lane rows |
|---|---|---|---|---|---|---|
| AERB | 727 | auto 79 / 2 (3) | 0.975, 0.924, 0.914 to 0.997: accepted | 79 (79) | 611 | 425 |
| | | middle 38 / 23 (5) | 0.623, 0.510, 0.490 to 0.744 | | | |
| NCG | 376 Indian | auto 54 / 0 | 1.000, 0.946, 0.934 to 1.000: accepted | 54 (54) | 261 | 177 (withheld) |
| | | middle 55 / 25 (4) | 0.688, 0.592, 0.574 to 0.787 | | | |
| | | low probe 2 / 7 (3) | 0.222, 0.041, 0.028 to 0.600 | | | |
| NHP blood banks | 2,823 | auto 57 / 1 (2) | 0.983, 0.921, 0.908 to 1.000: accepted | 57 (57), withheld | 2,707 | 0 (card fact only) |
| | | middle 60 / 28 (2) | 0.682, 0.591, 0.574 to 0.777 | | | |
| MoHFW trauma | 196 | auto 14 / 0 | 1.000, 0.807, 0.768 to 1.000 | 0 | 165 | 150 (withheld) |
| | | middle 13 / 7 (1) | 0.650, 0.442, 0.408 to 0.846 | | | |
| | | low probe 5 / 20 (1) | 0.200, 0.082, 0.068 to 0.407 | | | |
| NABI burns | 127 | auto 36 / 1 | 0.973, 0.878, 0.858 to 0.999 | 0 | 80 | 61 (withheld) |
| | | middle 10 / 6 | 0.625, 0.391, 0.354 to 0.848 | | | |
| LaQshya | 2,173 rows, 1,261 units | reviewed pair by pair: 63 correct / 52 wrong (6 unsure) | matcher alone 0.548, 0.467, 0.452 to 0.641 | 63 (62) | 1,195 units with no correct or unsure link | 779 units |

Notes on the table:

- LaQshya is not band-accepted. Its matcher scores only 55%. Each of its 63 lines comes from a pair one agent reviewer read and marked correct, which is what D051 adopted. Those 63 are not a sampled band.
- Two auto bands pass only because unsure verdicts are left out, as the rule says. Counted as wrong, the lower bounds fall to 0.879 for AERB and 0.876 for NHP, and both bands would fail.
- MoHFW's auto band is clean (14 of 14), but 14 pairs are too few. A perfect band needs 29 pairs to reach a lower bound of 0.90.
- The rule drops 14 MoHFW and 36 NABI pairs that a reader marked "same". Showing them would not lower the precision of what cards show (each was read), but it would break the agreed rule, so they stay out.
- Readers were agents who saw the band and score. The bounds cover sampling error only, not reader error.

## Lane rules (v2, revised after review)

A list row enters the lane only if all of these hold:

1. The source's terms allow it (D057; see the table above). Rows of withheld sources are built, so the checks below can use them, but are never shown or bundled.
2. The source's own rule says it is not in our dataset.
   - AERB and NABI: `lane_eligible`. AERB's rule also drops rows with an unread auto or middle candidate, a near-miss low candidate, a reader-unsure candidate or a nearby look-alike. NABI's drops repeat rows and unnamed rows.
   - NCG and MoHFW: `link_status == "not_in_dataset"`.
   - Every source with a `best_unread_score` column (NCG, MoHFW, NABI): no unread candidate scoring 0.20 or more (the AERB and NHP near-miss rule). This drops 31 NCG, 0 MoHFW and 3 NABI rows. NABI used to be exempt by accident.
   - LaQshya: the unit has a maternity operating theatre row, and every candidate link (if any) was reviewed wrong.
3. It has a position.
4. No record of ours looks like it. Names are compared on their distinctive words (generic, legal and service words removed). Initials are joined ("S. M. S." to "sms"). A small alias map adds "sskm" for IPGME&R, SSKM and our "PG Hospital". A name that says "X, a unit of Y" is also tried as X and as Y. Dental and pharmacy records are ignored. A row is dropped by the first rule that fits:
   - `nearby`: within max(10 km, the row's position precision), capped at 40 km, the distinctive words match at token set ratio 85 or more. A place-only core ("DH Gwalior") uses token sort ratio, so "Paridhi Hospital, Gwalior" does not count. Initials also count when they spell a run of the other name's words and nothing distinctive is left over ("LD Hospital" and "Lal Ded Hospital", "S.K. Medical College" and "Sri Krishna Medical College", "Kasturba Medical College" and "KMC Hospital"). Kind abbreviations (CH, RH, DH) never count as initials, and "SN Medical College" does not match "Shakya Nursing Home".
   - `generic_twin`: within the same radius, one name has no distinctive word ("Government Medical College", our "District Hospital"). The kind words must agree, with spelling slips allowed ("Referral" and "Referal", but not "Area" and "Care"), and the place must match. Our record must be a hospital.
   - `same_name_far`: the full names match at token sort ratio 95 or more, the core is distinctive (two words, or one word carried by 3 or fewer of our records), and the two are in the same district, or the same state within 60 km. This catches an off map point ("C R Gardi Hospital", our point 53 km away).
   - `linked_elsewhere`: the record is already linked by another list (an accepted link, or a LaQshya link marked correct), within 30 km or in the same district, and the names share two distinctive non-place words, match strictly, or match in full ("Delhi State Cancer Institute", AERB-linked, 22 km).
   - Drops: 138 rows (nearby 127, linked_elsewhere 5, generic_twin 3, same_name_far 3), against 105 under the first rule. By source: LaQshya 32, AERB 24 (plus 45 by AERB's own rule), NCG 53, MoHFW 15, NABI 14.
   - Every drop beyond the first rule was read by hand. All but one look right. The exception: NABI "srivenkateswara medical college" was dropped against our "Sri Venkateswara Institute of Medical Sciences", 21 km away in Tirupati, and the two are probably different colleges. NABI is withheld anyway.
5. At request time, the row lies within the service window.
   - Window: the estimated drive time from `vf.travel.travel_minutes` at the origin's state factor is at most `vf.services.window(line).minutes`, measured to the row's point.
   - Islands, where sea travel is not modelled, use the km radius in `vf.services.RADII_KM` instead, and the label says so.
   - Order: nearest first. Ties go to a current certificate first, then the more precise position.
6. One place, one entry.
   - At build time, rows of different sources within 3 km get one group key when their non-place words match (token sort too if either sits on a district centre). Two all-generic names match when the kinds and places agree ("DH Gumla" and "District Hospital, Gumla", but not "DWH Gumla"). Two better-placed rows within 1 km also match when they share a word that 3 or fewer of our records carry ("Dr. Balabhai Nanavati Hospital" and "Nanavati Max Institute"). A group never holds two rows of one source.
   - This re-keyed 92 rows into 90 groups. A first version also merged on shared district names and chained groups. It joined "DH Ranchi" to "Paras HEC Hospital" and two LaQshya units to each other, so it was replaced.
   - At request time, the lane shows one entry per group key, and none whose name looks like a card on the same answer.

Positions with a precision above 5 km are shown as a drive-time range. The range spans km minus to km plus the precision, plus the 95% band of the drive-time fit. Every lane text says "position approximate (PIN area)" or "(district centre)".

Where the lane rows are placed:

| source | lane rows | PIN area | district centre | median precision | within 5 km precision |
|---|---|---|---|---|---|
| AERB | 425 | 420 | 5 | 2.9 km | 263 |
| NCG (withheld) | 177 | 150 | 27 | 4.4 km | 90 |
| NABI (withheld) | 61 | 34 | 27 | 10.3 km | 27 |
| MoHFW (withheld) | 150 | 0 | 150 | 39.0 km | 0 |
| LaQshya | 779 | 0 | 779 | 39.9 km | 6 |

Of the 779 LaQshya lane units, 173 hold a current maternity OT certificate on 22 Nov 2026 and 606 are lapsed. Lapsed ones use the D051 wording.

### Audit of the revised lane

- Method: `rank.rank` plus `annotate_answer` with every source switched on, run at each lane row's own point and at the seven places the review named. That gave 1,599 answers and 4,601 lane entries. Every lane entry was compared with every card on its answer by a looser test than the code uses: full names at token set ratio 90 or more, or a shared word that 3 or fewer of our records carry.
- The review's cases are all fixed.
  - Silchar oncology: Cachar Cancer Hospital shows only as a card.
  - 190008 Srinagar emoc: "LD Hospital" is out of the lane, and Lal Ded Hospital is a card.
  - Muzaffarpur trauma: "S.K. Medical College" is gone.
  - 302004 Jaipur: "S. M. S." is gone.
  - 400053 Mumbai: one Nanavati entry, not two.
  - 600010 Chennai: no VS Hospitals entry beside the VS Hospitals card.
  - Kolkata: no SSKM rows.
  - Also gone from the lane: K D, S. S. G., K.R., C R Gardi, Curie-Abdur Razzaque Ansari, Kokilaben (Thane row), Delhi State Cancer Institute, both NABI SSKM rows, NABI Government Medical College Thiruvananthapuram, LaQshya IPGMER & SSKM and DH Raisen.
- 29 lane and card pairs pass the looser test. Read one by one:
  - Probably the same place: NABI "Sian Hospital" against our "Sion Hospital", 18.9 km (the spelling differs too much to match). Arguably the same: NCG "Jawaharlal Nehru Medical College (KLE ...)" against "KLE's Dr Prabhakar Kore Hospital", its teaching hospital, 1.6 km. Both sources are withheld by default.
  - Uncertain: LaQshya "DH Vidisha" and "General Hospital Adoor" against our all-generic "District Hospital" and "General Hospital", 15 to 16 km away, whose city does not name the place.
  - The other 25 are different places that share a town name, a chain name far away, or a word like "Medical College".
- Not caught: LaQshya "Referral Hospital, Barbigha" against our "Referal Hospital" at PIN 811101 (city "Nalanda"). It is 14.6 km away, beyond the row's 10.4 km radius, and the names share no distinctive word. AERB "Government Medical College", Patiala, and our "Rajindra Hospital" (the college's hospital) share no word either.
- Residual error: AERB's own 40-row audit of its lane found about 5 to 8% of rows that are really in our data, with a one-sided 95% upper bound of 18%. That audit came before these fixes. No other source's lane has been audited by sampling. The lane text says "Not linked to any record in this dataset", but the heading still says "not in this dataset" (see Decisions).

## Review fixes, 22 Nov

| review finding | response |
|---|---|
| High: lane lists hospitals that are in our data (empty cores, generic names, map points off, abbreviations, no check across sources) | Fixed. Lane rules 4 and 6 above: initials, aliases, "unit of" halves, place-backed cores, the `generic_twin`, `same_name_far` and `linked_elsewhere` rules, and a request-time check against the answer's own cards. 138 rows are now dropped, against 105. Every case the review named is out of the lane, and the live audit is above. Tests: `test_lookalike_filter_rules`, `test_name_forms_initials_and_aliases`, `test_annotate_answer_never_shows_a_card_in_the_lane`, `test_built_review_cases_not_in_the_lane`, `test_built_answers_never_show_a_card_in_the_lane`. |
| Medium: heading "(not in this dataset)" claims absence | Agreed, not changed. The wording is Hruday's (D055), it is in the binding contract, and the app hard-codes it. Proposed wording is under Decisions. |
| Medium: cross-source dedupe misses (Nanavati, VS) | Fixed by a build-time group key (lane rule 6). The first version of that merge was wrong for district-centre rows, so it was rewritten. Test: `test_cross_source_merge_and_one_place_per_lane`. |
| Medium: no gate keeps MoHFW, NABI and NCG out of a deployed bundle | Fixed, and extended. D057 is binding. NHP card facts are off too, because D057 bars unofficial re-hosts. Tests: `test_terms_gates_default_and_switch`, `test_built_publishable_follows_terms`. |
| Low: deployed app shows no outside data, silently | Partly fixed: `load_outside` logs a warning. The health check and `write_bundle` call belong to `dbapp/` and WP12. |
| Low: the 0.90 rule measures the matcher, not what cards show | Agreed, and stated in "Per source". No second reader was run in this round. |
| Low: NHP provenance | Citation fixed. `reports/ext_nhp_bloodbank.md` corrected. The `SOURCES.md` sentence is flagged for its owner. |
| Low: NABI skipped the near-miss rule | Fixed. It drops 3 NABI rows, none of them our records (the review checked all 3). |
| Low: dashes and " ," in display names | Fixed in `nice_name`. A scan of 3,193 generated texts finds no dash, " ,", "..", "cannot", "there is no" or "does not have", and every text ends with the outside-data tail. |
| Low: the end-to-end explainer is missing | Not done here; see the last item under Decisions. |

## The brief's places

Run on 22 Nov 2026 against the current ranker in `src/vf`, which another workflow is still changing, so card names may shift. The card lines below come from `annotate_answer`. Where the answer's own cards carry no line, the nearest records with a line for that service are listed instead, labeled as such.

### Lucknow 226001, emoc

- On the answer's cards (BNK Hospital, Fatima Hospital, Pawar's Clinic, plus 3 unverified nearby): no outside lines. None of them is linked to a list.
- Nearest records with an emoc line (not on this answer):
  1. Dr Ram Manohar Lohia Institute of Medical Sciences, 6.0 km: "Government directory (National Health Portal, data.gov.in, published 10 Aug 2017, row Sr No 2575): blood bank (no licence number on the list); no usable licence renewal date on the list. Outside data, not from this record."
  2. Guru Shri Gorakshnath Chikitsalaya, 240 km: "Government directory (National Health Portal, data.gov.in, published 10 Aug 2017, row Sr No 2516): licensed blood bank, licence renewal date on the list 26 Jun 2015, not renewed on that list. Outside data, not from this record."
  3. Shyam Shah Medical College, Rewa, 260 km: "Government list (LaQshya, NHSRC, 8 Apr 2026, page 34 row 1071): maternity operating theatre certified; certificate on the 8 Apr 2026 list, valid to 5 Jul 2025, not renewed on that list. Outside data, not from this record."
- Lane: three LaQshya units, all placed at the Lucknow district centre, 0.6 km from the origin point. Unchanged in the rerun after the review. The blood bank lines above are now off by default.
  1. "Ram Sagar Misra Hospital, Lucknow, Uttar Pradesh: maternity operating theatre certified; certificate on the 8 Apr 2026 list, valid to 5 Jul 2025, not renewed on that list (Government list, LaQshya, NHSRC, 8 Apr 2026, page 64 row 2012). Not linked to any record in this dataset. About 1 min to 50 min estimated drive, free-flow (the listed position is approximate); real trips take longer; position approximate (district centre). Outside data, not from this record."
  2. "Jhalkari Bai Mahila Hospital, Lucknow, Uttar Pradesh: maternity operating theatre certified; certificate on the 8 Apr 2026 list, valid to 31 Aug 2025, not renewed on that list (Government list, LaQshya, NHSRC, 8 Apr 2026, page 63 row 2001). ..."
  3. "CHC Sarojini Nagar, Lucknow, Uttar Pradesh: maternity operating theatre conditionally certified; certificate on the 8 Apr 2026 list, valid to 13 Mar 2024, not renewed on that list (... page 61 row 1922). ..."
- A name search of our 9,989 records found no "Jhalkari", "Ram Sagar" or "Sarojini Nagar" hospital in Lucknow.

### Jokihat, Bihar, dialysis

None of the six lists covers dialysis. There are no card lines, and the lane is empty. The answer stays an abstention (`no_reliable_option`), and the outside lists cannot change that.

### Kotputli, trauma

- Rerun 22 Nov after the review, default gates: no card lines and an empty lane. MoHFW is withheld, and NHP blood bank facts are off. The text below was the build before the gates, with every source on.
- On the answer's cards (Shri Shyam Hospital 1 km, Sachkhand Hospital, Rainbow Hospital Narnaul, plus 3 unverified): no outside lines. MoHFW gives no card facts, and no NHP-linked record is nearby. The nearest blood-bank-linked records are 137 to 150 km away (Jindal Super Speciality Hospital, Delhi Heart & Lungs Institute, Lal Bahadur Shastri Hospital).
- Lane (all sources on): two MoHFW rows, both at district centres.
  1. "District Hospital, Rewari, Haryana: trauma care facility funded under the MoHFW trauma care scheme for national highways; the list records scheme funding and gives no level or operating status (Government list, MoHFW, 8 Feb 2023, page 2 S. No. 60). Not linked to any record in this dataset. About 35 min to 3 h 10 min estimated drive, free-flow (the listed position is approximate); real trips take longer; position approximate (district centre). Outside data, not from this record."
  2. "Taluk Hospital, Kotputli, Jaipur, Rajasthan: ... (Government list, MoHFW, 8 Feb 2023, page 4 S. No. 141). Not linked to any record in this dataset. About 20 min to 5 h 15 min estimated drive ...; position approximate (district centre). ..."
- This shows the weak spot of district-centre positions. The Taluk Hospital is in Kotputli itself, but it sits at the Jaipur district centre, 96 km from the origin, and is listed second. Do not film this lane as a distance claim. Our records include no Kotputli government hospital (only Shri Shyam Hospital and a laboratory).

### A cancer query near Mumbai ("Mumbai", oncology)

Rerun 22 Nov after the review, default gates: the cards are Tata Memorial, P D Hinduja, KEM, Gleneagles, Metropolis Lab and Balaji Hospital, none with a line. The lane is AERB only: Cancare Trust, Shantilal Shanghvi Foundation and Cama & Albless Hospitals. The text below is the earlier build, with NCG on.

- On the answer's cards: Jaslok Hospital and Research Center, 3 km: "Regulator list (AERB, as on Apr 2025, page 12 row 13): licensed by AERB for radiotherapy; the list gives no licence dates. Outside data, not from this record." Tata Memorial Hospital and Saifee Hospital carry no line. Tata Memorial is surely on both lists, but its pairs were not in the readers' sample, so the strict rule gives no link.
- Other records with an oncology line nearby:
  - Sir H. N. Reliance Foundation Hospital, 2.4 km: AERB page 12 row 3.
  - Kokilaben Dhirubhai Ambani Hospital, 16.7 km: "Registry list (National Cancer Grid member list, as served 22 Nov 2026, section 'Maharashtra' row 37): member cancer centre of the National Cancer Grid; membership is not an accreditation of a service. Outside data, not from this record."
- Lane:
  1. "Asian Cancer Institute, Mumbai, Maharashtra: member cancer centre of the National Cancer Grid; membership is not an accreditation of a service (Registry list, National Cancer Grid member list, as served 22 Nov 2026, section 'Maharashtra' row 5). Not linked to any record in this dataset. About 1 min to 15 min estimated drive, free-flow (the listed position is approximate); real trips take longer; position approximate (district centre). Outside data, not from this record."
  2. "Cancare Trust, Mumbai, Maharashtra: licensed by AERB for radiotherapy; the list gives no licence dates (Regulator list, AERB, as on Apr 2025, page 12 row 11). Not linked to any record in this dataset. About 2 min estimated drive, free-flow; real trips take longer; position approximate (PIN area). Outside data, not from this record."
  3. "Punyashlok Ahilyadevi Holkar Head and Neck Institute of of India Super Specialty Hospital, Mumbai, Maharashtra: member cancer centre of the National Cancer Grid ... section 'Maharashtra' row 51 ... About 2 min estimated drive ...; position approximate (PIN area). ..." The doubled "of" is in the NCG list itself.
- None of the three names appears in our records.

## What the WP12 bundle must include

Call `vf.public_list.write_bundle(bundle_dir)` from `scripts/wp12_bundle.py`. It builds from silver, keeps only rows the terms gates allow (`publishable`), and writes the three files atomically. Keep copying `laqshya_links.parquet` as now: `load_outside(bundle_dir)` reads the LaQshya card lines from it. Then rerun `copy_vf`. `dbapp/vf/public_list.py` matched the earlier v2, and it is stale again after these fixes.

As of this revision, `dbapp/bundle` holds no `outside_*.parquet`. The deployed server's `load_outside()` would fall back to a build from silver, which does not exist on Databricks, and return empty tables. `load_outside` now logs a warning ("outside lists empty: ...") when that happens or when a bundle holds no rows. A `/api/health` check on `outside meta card_facts > 0` belongs in `dbapp/server.py`, which this step does not own.

| file | bytes (this build, default gates) | content |
|---|---|---|
| `outside_card_facts.parquet` | 14,279 | 133 accepted links (AERB 79, NCG 54): facility_id, source_id, list_uid, what, citation, locator, valid_to |
| `outside_lane.parquet` | 119,070 | 1,204 lane rows (LaQshya 779, AERB 425; one per row and service line): position, precision, what, citation, locator, valid_to, group_key |
| `outside_meta.json` | 20,349 | version, build time, lane rules, per-source counts and exclusions, look-alike examples with the rule that fired, sources shown, `withheld_terms`, source names and terms |
| `laqshya_links.parquet` (already shipped) | 50,106 | the reviewed LaQshya links (63 correct) |

The total is about 204 KB, and no file comes near the 10 MB Databricks limit. The silver source tables are not needed in the bundle. No raw list file goes into it.

## Limits and decisions for Hruday

- Lane heading. "(not in this dataset)" claims absence, which breaks data trap 12, and the audit above shows the claim is not always true. Proposed: "Also on government or registry lists nearby (not linked to a record in this dataset)". The current wording is yours (D055), it is fixed in the binding contract in `docs/integration_notes_standout.md`, and `app/src/render.ts` hard-codes it. So `LANE_HEADING` is unchanged until you decide. A change needs those three places and a line in `docs/08_decisions_log.md`. Until then, do not film the lane as proof that a hospital is missing from the data.
- Terms. The code now enforces D057. The lane shows only LaQshya and AERB, and NCG gets card facts only. NHP blood bank facts are off, because the copy is unofficial. NCG, MoHFW and NABI lane rows need your permission decision (or the publishers'). NHP facts need either the official data.gov.in file or your acceptance of the GitHub copy. Switch them on with `VF_OUTSIDE_LANE_SOURCES` and `VF_OUTSIDE_CARD_SOURCES`.
- NHP provenance. `data/external/SOURCES.md` says the GitHub copies "agree row for row". They do not: one mirror differs on 714 licence dates and 390 renewal dates (`reports/ext_nhp_bloodbank.md`, Copy check). That file is outside this step's ownership, so please correct the sentence there. The NHP citation now says "read from a third-party copy".
- District-centre positions (all of LaQshya and MoHFW, part of NABI and NCG) make "nearby" coarse, as the Kotputli and Nashik cases show. In the Nashik case, three Nashik-district units sit on one point 17 km from the city. The lane gives drive-time ranges for these. A town-name geocode was tried and dropped: the PIN directory points for towns such as Manmad, Kalwan and Kotputli are themselves placeholders with 68 to 163 km precision.
- LaQshya's lane depends on the linker's candidates. Units with no candidate are treated as not in our data. Recall is not measured. A 40-unit spot check found no missed link, and the look-alike rule is the backstop.
- Recall of every linker is unmeasured. Accepted links are few: Tata Memorial, P D Hinduja and AIIMS Bhubaneswar get no line. The cause is unread auto pairs (AERB 41, NHP 180), not a failure of the rule. Reading them would add card facts.
- The lane never makes a facility recommendable and never changes a grade or the order of cards (D048, D055).
- Contract additions for `docs/07_data_contracts.md`, which this step may not edit:
  - the answer fields `outside_nearby` and per-card `outside_lines`, as in `docs/integration_notes_standout.md`;
  - bundle files `outside_card_facts.parquet`, `outside_lane.parquet` and `outside_meta.json`;
  - the lane rules above;
  - the LaQshya lapsed wording change in v1 `public_line`.
- The user's request for an end-to-end explainer of the conversation is not covered here. This step has no copy of that conversation. The session that holds it should write it.
