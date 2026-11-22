# NHP blood bank directory: parsed, located, candidate links

Built by `scripts/wp13_ext_nhp_bloodbank.py` on 2026-11-22T05:46:35Z. Source: National Health Portal blood bank directory, data.gov.in catalog "Blood Bank Directory (updated till last month)", https://data.gov.in/resources/blood-bank-directory-updated-till-last-month (published on the portal 10 Aug 2017; licence Government Open Data License - India (GODL), under NDSAP). Copy used: https://raw.githubusercontent.com/snap24/caresync/b9fb624a5fb749ee1160d2e4cf08fec0bd3036a6/data/raw/bloodbanks.csv (sha256 `7c315aecf56e45f114b61d4649be49afbb1399103b655fdff3b6d098fee40264`), retrieved 2026-11-22T05:22:24Z. See `data/external/nhp_bloodbank/SOURCE.md`.

Use under D055: a linked blood bank listing is a card fact only, for emoc, cardiac surgery and trauma. It never appears in the "nearby, not in this dataset" lane. Links are final only where `accepted` is true: readers labelled `data/labels/ext_link_check_nhp_bloodbank.csv`, and a band is accepted only if the one-sided 95% lower bound of its precision is at least 0.90 (see Acceptance).

## Findings

- **The list:** 2823 blood banks (1055 Government, 999 Private, 690 Charity, 79 not stated). 2217 carry a licence number.
- **Dates:** the copy is old, a list of about 2017 to 2018. Licence dates by year from 2014: {'2014': 114, '2015': 106, '2016': 12, '2017': 3, '2018': 1, '2021': 1} (latest 2021-12-31); the latest renewal date is 2022-12-30. Against 2026-11-22: 0 current, 1798 lapsed (renewal date passed, no later renewal on this copy), 1025 undated. "Lapsed" describes this copy of the list, not the blood bank today.
- **Positions:** 2268 rows keep their listed point (within tolerance of their PIN area or district centre), 413 use the PIN area, 141 the district centre, and 1 could not be located.
- **Candidates:** 19726 pairs for 2095 list rows. Bands: {'low': 19125, 'middle': 361, 'auto': 240}. Auto band: 238 list rows, 240 of our records.
- **Check sheet:** 150 pairs (60 auto of 240, 90 middle of 361, 0 best low as a boundary probe); sampled: True.

## Acceptance (reader verdicts)

Readers' file `data/labels/ext_link_read_nhp_bloodbank.csv`: 150 rows (0 not in the check sheet, 0 check rows unread). Precision per band = same / (same + different); unsure verdicts are left out of both counts. Lower bound: one-sided 95% Clopper-Pearson. A band is accepted only if its lower bound is at least 0.90; inside an accepted band only pairs the reader marked same become links.

| band | pairs | read | same | different | unsure | precision | 95% lower bound | lower bound if unsure counted wrong | accepted |
|---|---|---|---|---|---|---|---|---|---|
| auto | 240 | 60 | 57 | 1 | 2 | 0.983 | 0.921 | 0.876 | yes |
| middle | 361 | 90 | 60 | 28 | 2 | 0.682 | 0.591 | 0.576 | no |
| low | 19125 | 0 | 0 | 0 | 0 | n/a | n/a | n/a | no |

Accepted bands: **auto**. Accepted links: 57 pairs, 57 list rows, 57 of our records (list status of the linked rows: {'lapsed': 41, 'undated': 16}). 180 pairs in accepted bands were not in the reader sample, so they are not links: the rule keeps only pairs a reader marked same.

The auto band passes narrowly. With 57 same and 1 different its lower bound is 0.921; had the 2 unsure pairs been wrong it would be 0.876 and fail. The rule leaves unsure out, so the band is accepted, and the unsure pairs themselves are not links.

Pairs marked different inside an accepted band (the band's error, kept out):

- Hi-Tech Medical college & Hospital Blood Bank (769004) vs Hi-Tech Medical College & Hospital, Bhubaneswar (769004): Hi-Tech Rourkela vs Hi-Tech Bhubaneswar campus, 240 km, different branch

Pairs marked unsure inside an accepted band (not links):

- Kasturba Hospital Blood Bank (576104) vs Kasturba Hospital (576104): facts are Kasturba Manipal and PIN 576104, but record placed in Tumkur, 251 km; duplicate of the Manipal record
- Military Hospital (250001) vs Military Hospital (250001): record conflates Military Hospitals in Jhansi, Agra and Meerut; PIN is Meerut

## List rows: in our dataset or not

| status | list rows |
|---|---|
| linked (accepted link, gives a card fact) | 57 |
| reader marked same, band not accepted (in our dataset, no card fact) | 59 |
| not in our dataset (no accepted link, no candidate marked same) | 2707 |
| of which had no candidate at all | 728 |
| of which clearly not any of our records | 2202 |
| eligible for the nearby lane | 0 |
| total | 2823 |

"Not in our dataset" is the task's definition. It is not proof of absence: the linker's recall is not measured, and only a sample of the auto and middle pairs was read. A not-in-dataset row is counted as clearly not any of our records only when none of these holds (first reason counted):

- a reader marked a candidate unsure: 3
- it has an auto or middle candidate no reader saw: 389
- it has a low-band mutual-best candidate scoring 0.20 or more (a possible match the score missed): 112
- it could not be located: 1
- clearly not ours: 2202

None of them feed the "nearby, not in this dataset" lane: the public_list v2 contract (D055) makes a blood bank listing a card fact only. `clearly_not_ours` is recorded for completeness, and `lane_eligible` is false on every row.

Location quality. A listed point is the list's own latitude and longitude, kept only when it lies near the row's PIN area or district centre; it is self-reported and has no stated precision. Precision figures below are for the PIN-area and district-centre rows only.

| rows | count | listed point | PIN area | district centre | not located | median precision km | precision <= 5 km | precision > 10 km |
|---|---|---|---|---|---|---|---|---|
| all list rows | 2823 | 2268 | 413 | 141 | 1 | 8.9 | 244 | 262 |
| linked | 57 | 52 | 3 | 2 | 0 | 3.1 | 3 | 2 |
| reader same, band not accepted | 59 | 58 | 1 | 0 | 0 | 2.0 | 1 | 0 |
| not in our dataset | 2707 | 2158 | 409 | 139 | 1 | 9.0 | 240 | 260 |
| clearly not ours | 2202 | 1704 | 364 | 134 | 0 | 9.7 | 213 | 242 |

Verdicts come from agent readers (D055: agent-checked precision), not from Hruday, and the readers saw the band and score. The 95% bound covers sampling error in the band, not reader error.

`silver/ext_nhp_bloodbank.parquet` carries `link_status`, `linked_facility_ids`, `clearly_not_ours`, `not_ours_exclusion`, `lane_eligible` and `lane_exclusion` per list row; `silver/ext_links_nhp_bloodbank.parquet` carries `reader_verdict` (same, different, unsure, unread), `reader_note`, `band_accepted` and `accepted` per candidate pair.

## Method

1. Parse: cp1252 CSV, HTML entities unescaped, whitespace collapsed. One row per `Sr No` (`list_uid` = `nhpbb-` + zero-padded Sr No). Officer names, phone numbers and emails are dropped.
2. Dates: day first (Indian usage); month-year and year-only values kept with a note. `Date of Renewal` is read as the date the licence fell due for renewal (for example obtained 29-4-2013, renewal 28-4-18). Status: `current` if that date is on or after the as-of date, `lapsed` if before, `undated` if absent or text only.
3. Position: the listed PIN (or a PIN in the address) through `vf.locate.resolve`, accepted only in the listed state (Andhra Pradesh and Telangana treated as one); else the listed district, then the city, as a district centre. The listed point is kept only if it lies within max(15 km, 2 x precision) of the PIN area, or max(30 km, 2 x precision) of the district centre; otherwise the area position is used.
4. Pool: all of `silver/facilities.parquet` except dentists and AYUSH, dental or veterinary names (2152 rejected, pool 7837).
5. Blocks: same PIN; within 2 km (only for rows whose listed point was kept, against our valid coordinates); same district plus a shared name token that at most 10 of our records carry. Pairs by block: {'same_pin': 11247, 'within_2km': 13458, 'district_rare_token_only': 336}.
6. Score: IDF-weighted soft Jaccard on name tokens in [0, 1], after removing the service words (blood bank, blood transfusion centre, blood storage, component and apheresis unit, BB) from both names. Tokens pair at rapidfuzz ratio 85+. Bands: auto at 0.7+ and the one-to-one winner, middle at 0.3+ (or a high score that lost the contest or shares only generic words such as hospital or civil: 7 pairs), else low. Runs of initials are joined into one token (B.N.K. to bnk).
7. One-to-one, greedy by score; records in one WP11 duplicate cluster may share a list row (4 pairs lost the contest).

## Copy check

Four public copies of the same CSV were found. The chosen copy has the raw, mixed date formats; the others differ from it only where a spreadsheet reformatted dates or phone numbers:

- `mirror_roshk01`: {'rows': 2823, 'differing_cells_by_column': {'Mobile': 10, 'Helpline': 4, 'Contact Nodal Officer': 1, 'Mobile Nodal Officer': 4, 'Service Time': 1, 'Date License Obtained': 714, 'Date of Renewal': 390}}
- `mirror_krishnadashtisastra`: {'rows': 2823, 'differing_cells_by_column': {'Mobile': 1, 'Helpline': 2, 'Date License Obtained': 25, 'Date of Renewal': 43}}
- `mirror_apoorva_basingada`: {'rows': 2820, 'rows_chosen': 2823}

## Suggested card wording (for the public_list v2 owner)

A linked row would read, for a lapsed row: "Listed as a licensed blood bank in the National Health Portal blood bank directory (data.gov.in, a list of about 2017 to 2018), licence renewal date on that list 28 Apr 2018, not renewed on that list. Outside data, not from this record." For an undated row, drop the renewal clause. Never "has a blood bank" or "blood available".

## Status counts

| status | rows |
|---|---|
| lapsed | 1798 |
| undated | 1025 |

## Highest-scoring auto pairs (for orientation, not decisions)

| list name | our name | block | score | km |
|---|---|---|---|---|
| GMR Varalakshmi Care Hospital Blood Bank | GMR Varalakshmi Care Hospital | same_district_rare_token:varalakshmi | same_pin | within_2km | 1.00 | 0.03 |
| Silchar Medical College and Hospital Blood Bank | Silchar Medical College & Hospital | same_pin | within_2km | 1.00 | 0.07 |
| Jorhat Medical College and Hospital Blood Bank | Jorhat Medical College and Hospital | same_pin | within_2km | 1.00 | 0.01 |
| Hayat Hospital Blood Bank | Hayat Hospital | same_district_rare_token:hayat | same_pin | within_2km | 1.00 | 0.01 |
| Karimganj Civil Hospital Blood Bank | Karimganj Civil Hospital | same_pin | within_2km | 1.00 | 0.53 |
| North Lakhimpur Civil Hospital Blood Bank | Civil Hospital, North Lakhimpur | same_pin | within_2km | 1.00 | 0.03 |
| Morigaon Civil Hospital Blood Bank | Morigaon Civil Hospital | same_pin | within_2km | 1.00 | 0.05 |
| Lal Bahadur Shastri Hospital Blood Bank | Blood Bank, Lal Bahadur Shastri Hospital | same_district_rare_token:bahadur,shastri | same_pin | within_2km | 1.00 | 0.74 |

## Limits

- The official file could not be fetched from data.gov.in tonight (HTTP 503 or no connection). The copy used is a third-party GitHub copy whose header matches the data.gov.in schema; its exact snapshot date is not known. The copies do not agree row for row: they have the same rows and names, but one mirror differs on 714 licence dates and 390 renewal dates, another on 25 and 43 (see Copy check). Renewal dates on cards therefore rest on one unofficial copy.
- D057 says unofficial third-party re-hosts are not used. So `vf.public_list` keeps NHP card facts off by default (`card_public_ok=False`); `VF_OUTSIDE_CARD_SOURCES=nhp_bloodbank` turns them on if Hruday accepts the copy. The citation now reads "National Health Portal, data.gov.in file published 10 Aug 2017, read from a third-party copy". Review addendum, 22 Nov 2026. The catalog said "updated till last month" but showed "Published on Data Portal: 10/08/2017" as late as Nov 2021.
- A licence on an old list says the blood bank was licensed then. It is not evidence that it runs, or has blood, today. Card text must say so.
- Many listed blood banks are standalone (Red Cross, Rotary, charity and private banks). They have no hospital record to link to, and missing links are expected. Recall is not measured.
- Listed points are self-reported and many are rough; the position check keeps only points near the row's PIN area or district centre.
- Dates are ambiguous where day and month are both 12 or less; day first is assumed and noted.
