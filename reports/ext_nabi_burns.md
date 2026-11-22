# NABI burn care providers: parsed, located, linked

Built by `scripts/wp13_ext_nabi_burns.py` on 2026-11-22T05:46:49Z. Source: National Academy of Burns India (NABI), "List of Burn Care Providers in India (XLS File 128 Burn Care Centres) updated till 5th Feb 2026", linked from http://www.thenabi.org/bcp.php. Download record, terms and checksum in `data/external/nabi_burns/SOURCE.md`. Service line: burns.

## Findings

- **The sheet has 127 rows, not 128.** Serial numbers skip 17 and 73 and repeat 16. 126 rows are in India (one is a Kathmandu, Nepal centre), and 124 of those name a hospital. Some units appear more than once (King George Hospital and Andhra Medical , VISAKHAPATNAM: rows 10, 41; SSKM/ IPGME&R, Kolkata: rows 114, 115; Bembde Hospital, Aurangabad: rows 15, 72; SHER-I-KASHMIR INSTITUTE OF MEDICAL SCIE, SRINAGAR: rows 42, 43; Tata Main Hospital, Janshedpur: rows 45, 124; KLES Dr.Prabhakar Kore Hospital &MRC, Belgaum: rows 50, 120; Hamidia Hospital, Campus Bhopal, Bhopal: rows 64, 127; PRATAP Hospital, Vijayawada: rows 7, 9; Aiims Bhubaneswar, Bhubaneswar: rows 81, 83), leaving 115 distinct units by the list-side grouping (same district and the same distinctive name words; looser repeats such as row 62 against rows 64 and 127 for Hamidia Hospital are not grouped).
- **Positions are approximate.** The list has no coordinates. 69 rows are placed at their PIN area and 55 at a district centre; 3 are not placed (row 28: UCMS and GTB hospital, Delhi; row 31: PGIMER & Dr RML HOSPITAL, delhi; row 57: Nepal Cleft and Burn Center, Nepal). Two rows with no hospital name are placed at their district but cannot be linked.
- **What a row says.** Every row is a burn care unit on NABI's list; 95 rows report a burns ICU and 27 a skin bank. These are answers to NABI's form, self-reported and undated per row; the list as a whole is dated 5 Feb 2026, so every row has status `undated`.
- **No link is accepted, so NABI gives no card facts.** Readers marked 36 of 37 auto-band pairs the same unit; that is a one-sided 95% lower bound of 0.878, under the 0.90 rule. The middle band is lower. 81 named Indian rows have no accepted link and no reader-marked same pair; 1 of them likely repeats a row that has one, leaving 80 not in our dataset. 78 of those are placed and may feed the nearby lane. See the reader section.
- **Candidate links.** 603 blocked pairs for 92 list rows; bands: auto 37, middle 16, low 550. 35 list rows have an auto-band pair and 47 have an auto or middle pair. 53 pairs go to readers (all auto and middle pairs; no sampling).

## Method

1. Parse: the xlsx is read as XML from the zip (no spreadsheet software, nothing executed). One row per sheet row; `list_uid` is `nabi_burns:r<sheet row>` because the serial numbers are not unique.
2. Personal data dropped: unit-head names, unit and form e-mails, skin-bank phone and contact cells, and the form filler's name are not copied. Raw non-personal columns are kept as `raw_*`.
3. Hand fixes (one per affected row, each with its reason in `fix_note`): swapped state and city (row 15), a state cell reading Hyderabad on a Visakhapatnam row (row 41), Apollo DRDO Hyderabad listed under Andhra Pradesh with a Telangana address (row 11). Row 128 has an extra phone cell at column I, so its values for columns I to Z sit one column right; it is realigned.
4. Locate: a 6-digit PIN found in the address or name cell, known to India Post and in the row's state, gives the PIN area (`vf.locate.resolve`, position_note `PIN area`). Otherwise the town is resolved with the state and the row is placed at that district's centre (`district centre`). Towns the gazetteer finds in several districts use a small hand table read from the addresses (Mangalore, Bhilai, Airoli, Vijayawada, Wada); Delhi rows without a PIN are not placed, because the town spans several districts.
5. Pool: our 7,844 records, after removing 490 with facility type dentist and 1,655 more whose name says AYUSH (ayurveda, homoeopathy, unani, siddha, naturopathy, yoga), dental or veterinary.
6. Blocking: same PIN; or the same district (every district our record's PIN falls in, from `silver/pin_crosswalk`) or the same town name, plus a shared name token used by at most 30 pool records that is not a generic or department word. The 2 km rule does not apply: the list has no coordinates.
7. Score: IDF-weighted soft Jaccard of name tokens, bounded to [0, 1]. Names are normalised with `vf.public_list.normalise_name`, dotted and listed acronyms are expanded (AIIMS, KGMU, SKIMS, PMCH, KEM, RPGMC and others; the table is in the script), and generic words (`vf.public_list.GENERIC`: hospital, medical, college, government ...) are removed, falling back to all tokens when a side has none left. Close spellings match at rapidfuzz ratio 85 or more, weighted by similarity. Place-name tokens (the row's town, district and state, our record's town and state) count at 0.3 of their IDF weight, because blocking already put the pair in one place. The best score over variants of the list's name cell counts (whole cell, each institutional part, each without department words such as "Department of Plastic Surgery").
8. One-to-one: greedy by score; a list unit takes its best record and that record's WP11 duplicates; a record goes to one list unit. Repeated rows of the same unit share a group and may link together.
9. Bands: `auto` = one-to-one winner with score at least 0.6 and, when both points are known, no further apart than the row's position precision plus 25 km; `middle` = any other pair scoring at least 0.35; `low` below that. Readers get every auto and middle pair, shuffled (cap 150).
10. How the scorer was set (disclosed so readers can weigh it): the band thresholds were written before any pair was read. A first pass scored all tokens; reading its auto and middle pairs showed generic and place words carrying false matches (Nalanda Medical College against Patna Medical College, auto at 0.64) and acronyms missing true ones (AIIMS Rishikesh, 0.23). The scorer was revised once (generic words out, place words down-weighted, acronyms expanded, the distance gate, and electro-homoeopathic names caught by the AYUSH filter), and these are its results. The readers' check is independent of that tuning, so its precision is the number that decides each band.

## Blocks behind the candidate pairs

| block | pairs |
|---|---|
| same PIN | 404 |
| same district | 171 |
| same town | 28 |

## Band counts

| band | pairs | list rows |
|---|---|---|
| auto | 37 | 35 |
| middle | 16 | 16 |
| low | 550 | 72 |

## Reader verdicts and acceptance

Readers judged every pair in `data/labels/ext_link_check_nabi_burns.csv` and wrote `data/labels/ext_link_read_nabi_burns.csv`. Precision is same / (same + different); unsure pairs are left out of it, and a second column counts them as wrong. A band is accepted only if the one-sided 95% Clopper-Pearson lower bound is at least 0.90; inside an accepted band only pairs the reader marked same are links. Low-band pairs were not read.

| band | pairs | read | same | different | unsure | precision | lower 95% | lower 95%, unsure as wrong | same needed to pass | accepted |
|---|---|---|---|---|---|---|---|---|---|---|
| auto | 37 | 37 | 36 | 1 | 0 | 0.973 | 0.878 | 0.878 | 37 of 37 | no |
| middle | 16 | 16 | 10 | 6 | 0 | 0.625 | 0.391 | 0.391 | n/a | no |
| low | 550 | 0 | 0 | 0 | 0 | n/a | n/a | n/a | n/a | no |

## What the list adds

| Indian list rows | count | share |
|---|---|---|
| linked (accepted link, card fact) | 0 | 0.0% |
| in our data per reader, band not accepted (no card fact) | 43 | 34.1% |
| reader unsure for a candidate (kept out of the lane) | 0 | 0.0% |
| may repeat a row the reader found in our data: same district and a shared distinctive name word (kept out of the lane) | 1 | 0.8% |
| no hospital name in the sheet (cannot be linked; kept out of the lane) | 2 | 1.6% |
| not in our dataset (no accepted link, no reader-marked same pair) | 80 | 63.5% |
| all Indian rows | 126 | 100% |

One further row is outside India (Kathmandu) and is not counted.

Location quality of the not-in-dataset rows:

| position | rows | precision radius km (median) | max |
|---|---|---|---|
| PIN area | 44 | 2.0 | 33.4 |
| district centre | 34 | 39.2 | 111.5 |
| not located | 2 | n/a | n/a |

- **No band qualifies, so this source gives no card facts.** The auto band was 36 same of 37 read (precision 0.973), but with 37 pairs the one-sided 95% lower bound is 0.878; passing needed 37 of 37, that is no wrong pair at all. Wrong auto pairs: NEMCARE Hospital -> Nemcare SuperSpeciality Hospital: Nemcare Superspeciality / Heart Institute on GMCH Road is a separate group hospital; the list entry matches Nemcare Hospital G.S. Road (b2db2916). The middle band was 10 of 16 (lower bound 0.391). The thresholds and the acceptance rule were fixed before reading and are not moved after it.
- 43 list rows have a pair the reader marked same, so the unit is very likely in our data; it gets no card fact because its band failed, and it is kept out of the not-in-dataset lane.
- Sheet row 126 (Bai Jerbai Wadia hospital for children, MUMBAI) has no reader-marked pair of its own, but it shares the district and a distinctive name word with row 75 (BJ Wadia hospital for Children), which the reader found in our data. The list-side grouping missed it, so it is kept out of the lane rather than shown as not in our dataset.
- 80 rows are not in our dataset by this definition; 78 of them are placed and may feed the nearby lane. 32 had no candidate at all, 3 had only candidates the reader marked different, and 45 had only unread low-band candidates (score under 0.35); 0 of those have one at 0.30 or more and 3 at 0.20 or more. Low pairs were not read, so recall is not measured: a few of these rows may be in our data under another name. `best_unread_score` in `ext_nabi_burns.parquet` carries this per row, and `lane_eligible` marks the rows the lane may use.

## Rows not placed or not linkable

- sheet row 28: UCMS and GTB hospital, Delhi, DELHI: Town 'Delhi' spans several districts; not placed.
- sheet row 31: PGIMER & Dr RML HOSPITAL, delhi, DELHI: Town 'delhi' spans several districts; not placed.
- sheet row 39: (no name), SHIMLA, HIMACHAL PRADESH: no hospital name in the sheet
- sheet row 57: Nepal Cleft and Burn Center, Nepal, : Nepal Cleft and Burn Center, Kirtipur, Kathmandu: outside India
- sheet row 68: (no name), Indore, MADHYA PRADESH: no hospital name in the sheet

## Limits

- The list is compiled from NABI members' form answers. It says itself that some centres are missing. Being on it is outside evidence that a unit described itself as a burn care provider to a professional body; it is not an accreditation or a government licence, and not being on it says nothing about a hospital.
- No row has its own date. The list date (5 Feb 2026) is the only date; a row could be years old.
- Positions are PIN areas or district centres, not addresses. A district centre can be tens of kilometres from the unit; `position_precision_km` says how far.
- Terms: the page says "All contents and pictures are private properties of NABI. All rights reserved". The file is publicly linked with no login. It is used here for research and cited per row; the raw file is not committed or republished. Whether facility names and the citation may be shown in the public demo is Hruday's call.
