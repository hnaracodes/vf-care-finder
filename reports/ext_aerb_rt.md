# AERB radiotherapy licence list: parse, positions and candidate links

Built by `scripts/wp13_ext_aerb_rt.py` on 2026-11-22T05:46:06Z. Source: Atomic Energy Regulatory Board (AERB), Government of India, "Radiotherapy centres licenced by Atomic Energy Regulatory Board (As on April, 2025)", https://aerb.gov.in/images/PDF/Radiotheraphy/RSD3.pdf, read from the Internet Archive capture of 4 Jan 2026 because aerb.gov.in refused HTTPS from this machine. Retrieved 2026-11-22T05:23:24Z. SHA-256 `9524824f9d427478...`. Terms and provenance: `data/external/aerb_rt/SOURCE.md`.

## Findings

- **79 of 727 list entries are linked to 79 of our records** (79 accepted pairs). Accepted bands: auto; only pairs a reader marked same count. These links give card facts.
- **611 list entries are not in our dataset** by the working rule (no accepted link and no candidate a reader marked same); 449 of them pass the stricter test for the nearby lane. 37 more were marked same by a reader in a band that was not accepted, so they are in our dataset but give no card fact.
- **The list has 727 licensed radiotherapy centre entries** in 32 states and union territories, as of April 2025. It gives name, city, state and PIN only: no address line, no coordinates, no licence dates and no equipment.
- **632 of 727 entries have at least one candidate record in our data** under the strict blocking rules; 205 have a candidate in the auto or middle band, and 124 have an auto-band candidate. Candidates are proposals; links are the accepted pairs.
- Candidate pairs: 4505 (low 4281, auto 125, middle 99). The reader file has 150 rows (sampled, see below).
- Positions: 712 entries placed at their PIN area (India Post offices of the PIN), 15 at a district centre, 0 not located. Median position precision 2.6 km. These are approximate positions, not the centre's site.
- Status is `undated` for every row: the list is dated, but it does not say when each licence was issued or expires. A row means the centre was on AERB's published list as of April 2025.

## Acceptance (reader verdicts)

Readers' file `data/labels/ext_link_read_aerb_rt.csv`: 150 rows, every one matching a row of the check file (0 unmatched, 0 check rows unread). Precision per band = same / (same + different); unsure verdicts are left out of both counts. Lower bound: one-sided 95% Clopper-Pearson. A band is accepted only if its lower bound is at least 0.90; inside an accepted band only pairs the reader marked same become links.

| band | pairs | read | same | different | unsure | precision | 95% lower bound | accepted |
|---|---|---|---|---|---|---|---|---|
| auto | 125 | 84 | 79 | 2 | 3 | 0.975 | 0.924 | yes |
| middle | 99 | 66 | 38 | 23 | 5 | 0.623 | 0.510 | no |
| low | 4281 | 0 | 0 | 0 | 0 | n/a | n/a | no |

Sensitivity: counting unsure verdicts as wrong, the auto band's precision is 0.940 with lower bound 0.879, which would fail the 0.90 rule. The rule as set (D055, research note) leaves unsure out; the unsure pairs themselves are never links.

Accepted bands: **auto**. Accepted links: 79 pairs, 79 list rows, 79 of our records. 41 pairs in accepted bands were not in the reader sample, so they are not links: the rule keeps only pairs a reader marked same.

Pairs marked different inside an accepted band (the band's error, kept out):

- MEDICOVER HOSPITALS (A UNIT OF ABHAYANJANEYA HEALTH CARE PRIVATE LIMITED) (524003) vs Medicover Hospital (524002): Medicover Nellore vs Medicover Visakhapatnam: different branches, 509 km
- ASIAN INSTITUTE OF GASTROENTEROLOGY PVT. LTD (500032) vs Asian Institute of Gastroenterology (500082): AIG 500032 Gachibowli vs record at 500082 Somajiguda, 18.5 km: different branch (record mixes both)

## List rows: in our dataset or not

| status | list rows |
|---|---|
| linked (accepted link, gives a card fact) | 79 |
| reader marked same, band not accepted (in our dataset, no card fact) | 37 |
| not in our dataset (no accepted link, no candidate marked same) | 611 |
| of which eligible for the nearby lane | 449 |
| total | 727 |

"Not in our dataset" is the task's definition. It is not proof of absence: the linker's recall is not measured. For the nearby lane, which must show only facilities that are clearly not any of our records, a not-in-dataset row is also excluded when (first reason counted):

- a reader marked a candidate unsure: 8
- it has an auto or middle candidate no reader saw: 68
- it has a low-band mutual-best candidate scoring 0.20 or more (a possible match the score missed): 41
- one of our records within 10 km has a look-alike name (token set ratio of the distinctive words at least 85, ignoring PIN and district), which catches records the linker cannot block on, such as a missing or wrong PIN: 45
- it could not be located: 0
- eligible: 449

Residual error of the lane, one-off agent audit (22 Nov): 40 lane-eligible rows (seeded sample, seed 7) read against every record within 40 km and a keyword search of all records. 2 appear to be our records and 1 possibly: NIMHANS, Bengaluru 560029: our record is named only 'NIMHANS'; Sharda Hospital, Greater Noida: our record carries PIN 270001; possibly V.S. Hospital, Chennai 600031 vs our 'VS Hospitals', Chennai 600010. So roughly 5% to 8% of lane rows may be in our data under another name or PIN (one-sided 95% upper bound 18%). The lane wording should say "not linked to a record in this dataset" rather than claim absence. An earlier audit of 40 rows before the look-alike rule found the same kind of misses (Batra Hospital, Delhi, with no PIN on our record; Regency Hospital, Kanpur, with a Lucknow PIN), which the rule now removes.

Location quality (positions are PIN-area or district-centre points, never the site):

| rows | count | PIN area | district centre | not located | median precision km | precision <= 5 km | precision > 10 km |
|---|---|---|---|---|---|---|---|
| all list rows | 727 | 712 | 15 | 0 | 2.6 | 464 | 184 |
| linked | 79 | 77 | 2 | 0 | 2.1 | 59 | 14 |
| not in our dataset | 611 | 599 | 12 | 0 | 2.8 | 379 | 161 |
| lane eligible | 449 | 443 | 6 | 0 | 2.9 | 277 | 119 |
The look-alike rule also measures the linker's recall from below: of its 45 exclusions, many are plainly the same hospital, for example PARAS HMRI Hospital (100014) and our Paras HMRI Hospital at 4.2 km; BELGAUM CANCER HOSPITAL PRIVATE LIMITED (590010) and our Belgaum Cancer Hospital at 3.3 km; P. D. Hinduja National Hospital (400016) and our P D Hinduja Hospital at 0.5 km; ALL INDIA INSTITUTE OF MEDICAL SCIENCES BHUBANESWAR (751019) and our All India Institute of Medical Sciences at 0.5 km. The strict blocking missed them (different PIN and no rare shared token, or our record's PIN is wrong). They are candidates for a second reader pass, not links.

Verdicts come from agent readers (D055: agent-checked precision), not from Hruday, and the readers saw the band and score. The 95% bound covers sampling error in the band, not reader error.

`silver/ext_aerb_rt.parquet` carries `link_status`, `linked_facility_ids`, `lane_eligible` and `lane_exclusion` per list row; `silver/ext_links_aerb_rt.parquet` carries `reader_verdict` (same, different, unsure, unread) and `accepted` per candidate pair.

## Method

1. Parse: pdfplumber table extraction, 4 columns on all 23 pages; every row has a 6-digit PIN; the row count equals the count of PINs ending a line in the page text (727). Wrapped names are joined. Citation is page and row on page.
2. Locate: `vf.locate.resolve(PIN)`; if the PIN is not in the India Post directory, the list's city as a district in its state (position note `district centre`). District is the PIN's main district in `silver/pin_crosswalk`.
3. Our records: 9989 in `silver/facilities.parquet`; 2026 rejected as AYUSH, dental, veterinary or pharmacy and never candidates.
4. Blocking: same PIN; or same district (any district of either PIN's post offices) plus a shared rare name token (in at most 25 names across our data and the list, not a generic or legal word; 6461 such tokens). The list has no coordinates, so the 2 km rule does not apply.
5. Score: IDF-weighted soft Jaccard of name tokens, bounded to [0, 1]. Tokens pair when their edit ratio is at least 85; legal words (pvt, ltd, unit of) are dropped; the list's city and district and our city are removed from both names; the best of the full name and the name before "a unit of" is used. Abbreviations are expanded as in `vf.public_list` plus AIIMS.
6. Bands: auto = score at least 0.7 and mutual best one-to-one (records in one WP11 duplicate cluster count as one); middle = score at least 0.35 otherwise; low = the rest. Bands only propose; links are decided in the acceptance stage above. A band is accepted later only if the readers' one-sided 95% lower bound of precision is at least 0.90 (with every pair correct, that needs at least 29 read pairs).

## Bands

| band | pairs | list entries | same PIN | median score |
|---|---|---|---|---|
| auto | 125 | 124 | 106 | 1.00 |
| middle | 99 | 86 | 65 | 0.47 |
| low | 4281 | 593 | 3974 | 0.00 |

## Reader file

`data/labels/ext_link_check_aerb_rt.csv`: 150 rows, columns item_id, list_name, list_address, list_pin, list_district, our_facility_id, our_name, our_address_or_city, our_pin, km_apart, score, band. Band sizes {'auto': 125, 'middle': 99} exceed the cap of 150; taken {'auto': 84, 'middle': 66} by seeded random sample within each band (seed 20261122).
Readers write `data/labels/ext_link_read_aerb_rt.csv`. `km_apart` is between our record's point and the list entry's PIN-area point, so it carries the list's position error.

## Examples (top auto pairs)

| list entry | our record | score | block |
|---|---|---|---|
| Sri Venkateswara Inst. Of Medical Sciences (517507) | Sri Venkateswara Institute of Medical Sciences, Tirupati (517507) | 1.00 | same_district_rare_token+same_pin |
| Hemalata Hospitals and Research Centre (751023) | Hemalata Hospital & Research Centre (751023) | 1.00 | same_district_rare_token+same_pin |
| Sri Ramakrishna Hospital (641044) | Sri Ramakrishna Hospital (641044) | 1.00 | same_district_rare_token+same_pin |
| Kovai Medical Center And Hospital Limited (641014) | Kovai Medical Centre and Hospital Coimbatore (641014) | 1.00 | same_district_rare_token+same_pin |
| Sankara Nethralaya (600006) | Sankara Nethralaya (600006) | 1.00 | same_district_rare_token+same_pin |
| BILLROTH HOSPITALS PRIVATE LIMITED (600030) | Billroth Hospitals (600028) | 1.00 | same_district_rare_token |
| MAX SUPER SPECIALITY HOSPITAL, MOHALI-(A UNIT OF HOMETRAIL BUILDTECH PRIVATE LIMITED) (160055) | Max Super-Speciality Hospital, Mohali (160055) | 1.00 | same_district_rare_token+same_pin |
| Fortis Hospital (160062) | Fortis Hospital (160062) | 1.00 | same_district_rare_token+same_pin |

## Limits

- The live file could not be fetched (TLS reset from aerb.gov.in). The archive copy of 4 Jan 2026 matches the June 2025 capture, and the radiotherapy page archived in June 2026 still links the same URL, but a newer live version may exist.
- The list licenses a facility for radiotherapy; it does not say what other cancer care it offers. A listed centre is outside evidence that radiotherapy was licensed, not a statement about our record. Absence from the list says nothing about a record.
- Recall of the linker is not measured. Records with a different PIN and a generic name in another district are not candidates by design (strict linkage). Just under the middle band there are 41 mutual-best pairs scoring 0.20 to 0.35; an unscored read of about 30 of them on 22 Nov found several that look like the same hospital under a brand or trust name (for example Jawaharlal Nehru Cancer Hospital, Bhopal; K. K. Patel Super Speciality Hospital, Bhuj) and several that do not (Aster Medcity and Aster Labs). They stay out of the reader file: strict precision is chosen over recall.
- The reader file shows the band and score. Readers are not blind to the matcher's proposal.
