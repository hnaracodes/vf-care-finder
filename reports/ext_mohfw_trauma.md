# MoHFW trauma care facilities: parsed, located, linked

Built by `scripts/wp13_ext_mohfw_trauma.py` on 2026-11-22T05:47:46Z. Source: MoHFW list of 196 trauma care facilities and funds allocated (MoHFW, file dated 8 Feb 2023), https://main.mohfw.gov.in/sites/default/files/List%20of%20Trauma%20Care.pdf, fetched from the Internet Archive capture of 15 Dec 2023 (https://web.archive.org/web/20231215043518id_/https://main.mohfw.gov.in/sites/default/files/List%20of%20Trauma%20Care.pdf) on 2026-11-22T05:23:39Z, sha256 `f29d9f7a6ddb9bcd4909fe55c1deb81034ae3ec7dd873be719e35f9c46ae12ac`. Terms and provenance: `data/external/mohfw_trauma/SOURCE.md`. Reader verdicts are applied below: a band is accepted only if its one-sided 95% lower bound of precision is at least 0.90, and only reader-marked same pairs in accepted bands are links.

## Findings

- **List:** 196 trauma care facilities in 32 states and union territories (S. No. 1 to 196, contiguous: True). Every row is a public hospital named with its town. The list gives no address, PIN, coordinates, level (I, II or III), date per facility or operating status, so every row has status `undated`.
- **Located:** 196 to a district centre, 0 not located. No row can be placed more finely than its district.
- **Candidates:** 178 pairs for 87 of 196 located list rows (auto 14, middle 21, low 143). 13 list rows have a provisional auto pair; 32 have an auto or middle pair.
- **Check sheet:** 61 pairs for readers (auto 14, middle 21, low probe 26); no sampling needed. Low probe: the 15 highest-scoring low pairs, plus every other low pair whose record's operator is public.
- **Acceptance arithmetic** (exact binomial, one-sided 95%): with all read pairs correct the lower bound reaches 0.90 at 29 pairs; at 46 pairs one wrong pair is allowed. The auto band alone (14 pairs) cannot pass even if every pair is correct. Auto and middle pooled (35 pairs) passes only with at most 0 wrong. See the reader verdicts below.
- Our data is 88% private, and the list is all public hospitals, so few list rows can have a match at all. A list row with no candidate is not evidence that the hospital is missing from our data (recall is not measured), nor that it offers trauma care today.

## Method

1. Download: the official PDF URL on main.mohfw.gov.in no longer resolves (DNS) and the new site returns 404 at the same path, so the Internet Archive's capture of that exact URL was used; its recorded length (421,544 bytes) and Last-Modified (8 Feb 2023) are those of the original. The new site's robots.txt allows all paths. No form, login or search was used.
2. Parse (pdfplumber tables): one row per S. No.; the state cell is merged over its rows and carried forward, also across pages. Citation: page and S. No.
3. Locate (`vf.locate.resolve`, within the listed state): the town or district in the unit name, comma parts last first, then single words; a few names read by hand (`HAND_PLACE`). Always reported as the district centre with that district's precision radius.
4. Our pool: 7993 of 9989 records; 1996 AYUSH, dental or veterinary records rejected by type or name. Operator type is not used to filter (it is missing or wrong too often), so readers see private namesakes too.
5. Blocking: same PIN-directory district (our record's PIN through `silver/pin_crosswalk`) plus a rare shared name token: a token in at most 10 of our names, at least 3 letters, not a unit-type word (soft match at rapidfuzz ratio 85 for tokens of 4 letters or more). **Deviation from the NCG linker:** the district's own word may be the rare token, because this list names units by their town ("District Hospital, Srikakulam"); 126 pairs were blocked only on such a place word. Same-PIN and 2 km blocks are not possible: the list has neither.
6. Score: IDF-weighted soft Jaccard on name tokens (abbreviations expanded as in `vf.public_list`, plus `Hos.`, `Taluq`, `SD`), bounded to [0, 1], after dropping the pair's district, state and same-place city words; IDF over 10185 names (ours plus the list's). So the score mostly compares unit types ("district hospital" against "district headquarters hospital") and any other distinctive words.
7. Bands (provisional, for readers): auto at score 0.7 or more and the one-to-one winner; middle at 0.3 or more, or a high score that lost the one-to-one contest; low below. Records in one WP11 duplicate cluster may share a list row.

## Score distribution

| band | pairs | list rows | min score | max score |
|---|---|---|---|---|
| auto | 14 | 13 | 0.84 | 1.00 |
| middle | 21 | 21 | 0.32 | 0.90 |
| low | 143 | 66 | 0.00 | 0.30 |

## Fifteen highest-scoring pairs (for orientation, not decisions)

| list name | list district | our name | our city | score | band | km apart |
|---|---|---|---|---|---|---|
| Medical College & Hospital, Silchar | Cachar | Silchar Medical College & Hospital | Silchar | 1.00 | auto | 5.36 |
| General Hospital, Ernakulam | Ernakulam | General Hospital Ernakulam | Ernakulam | 1.00 | auto | 9.68 |
| Regional Institute of Medical Sciences, Imphal | Imphal West | Regional Institute of Medical Sciences | Imphal | 1.00 | auto | 2.52 |
| District Hospital, Churachandpur | Churachandpur | CHURACHANDPUR DISTRICT HOSPITAL | Churachandpur | 1.00 | auto | 6.09 |
| District Hospital Champhai | Champhai | Champhai District Hospital, Champhai | Champhai | 1.00 | auto | 10.08 |
| GMCH, Tirunelveli | Tirunelveli | Government Tirunelveli Medical College and Hospital | Palayamkottai | 1.00 | auto | 8.33 |
| GMCH, Kanyakumari | Kanniyakumari | Kanyakumari Government Medical College Hospital | Nagercoil | 1.00 | auto | 11.86 |
| Agartala Govt. Medical College | West Tripura | Agartala Government Medical College | Agartala | 1.00 | auto | 3.22 |
| GMCH, Kanyakumari | Kanniyakumari | Kanyakumari Government Medical College | Asaripallam | 0.93 | auto | 11.72 |
| SCB Medical College, Cuttack | Cuttack | SCB Medical College and Hospital, Cuttack | Cuttack | 0.91 | auto | 0.93 |
| Burdwan Medical College & Hospital, Burdwan | Purba Bardhaman | Burdwan Medical College Burdwan | Burdwan | 0.91 | auto | 8.99 |
| Government Medical College & Hospital, Thiruvananthapuram | Thiruvananthapuram | Government Medical College, Thiruvananthapuram | Thiruvananthapuram | 0.90 | auto | 2.61 |
| Government Medical College & Hospital, Thiruvananthapuram | Thiruvananthapuram | Government Medical College, Thiruvananthapuram | Thiruvananthapuram | 0.90 | middle | 6.81 |
| MKCG Medical College, Behrampur | Ganjam | MKCG Medical College and Hospital, Berhampur | Berhampur | 0.87 | auto | 17.02 |
| Murshidabad Medical College | Murshidabad | Murshidabad Medical College & Hospital | Berhampore | 0.84 | auto | 14.78 |

## Reader verdicts and acceptance

Readers judged every pair in `data/labels/ext_link_check_mohfw_trauma.csv` (`data/labels/ext_link_read_mohfw_trauma.csv`). Precision is same / (same + different); unsure pairs are left out of it, and a second column counts them as wrong. A band is accepted only if the one-sided 95% Clopper-Pearson lower bound is at least 0.90. Inside an accepted band only pairs the reader marked same are kept.

| band | pairs | read | same | different | unsure | precision | lower 95% | lower 95%, unsure as wrong | accepted |
|---|---|---|---|---|---|---|---|---|---|
| auto | 14 | 14 | 14 | 0 | 0 | 1.000 | 0.807 | 0.807 | no |
| middle | 21 | 21 | 13 | 7 | 1 | 0.650 | 0.442 | 0.417 | no |
| low | 143 | 26 | 5 | 20 | 1 | 0.200 | 0.082 | 0.079 | no |

**No band qualifies, so this source produces no card facts.** The auto band was 14 of 14 same, but 14 pairs is too few: even a perfect band needs 29 pairs for the lower bound to reach 0.90, and 14 perfect pairs give 0.807. The middle band is mixed (13 same, 7 different). Pooling auto and middle (a sensitivity check, not a declared rule) gives 27 same and 7 different, precision 0.794, lower bound 0.648: it fails too. No pooling with another source was declared, so none is applied.

## What the list adds

| list rows | count | share |
|---|---|---|
| linked (accepted link, card fact) | 0 | 0.0% |
| in our data per reader, band not accepted (no card fact) | 30 | 15.3% |
| reader unsure for a candidate, none same (excluded from the lane) | 1 | 0.5% |
| not in our dataset (outside-nearby lane candidates) | 165 | 84.2% |
| all list rows | 196 | 100% |

Location quality of the not-in-dataset rows:

| position | rows | precision radius km (median) | min | max |
|---|---|---|---|---|
| district centre | 165 | 36.6 | 8.6 | 132.4 |

- 0 accepted links join 0 list rows to 0 of our records. Readers marked a candidate same for 30 list rows; with no accepted band those rows give no card fact, but they are kept out of the not-in-dataset lane.
- Of the 165 not-in-dataset rows, 109 had no candidate at all and 46 still have unread low-band candidates (score under 0.30). The low-band probe read every low pair whose record is public: 5 same, 9 different, 1 unsure, so renamed or upgraded government units do hide in the low band. It also read 11 private low pairs, all 0 same. The unread low pairs are 109 private records and 8 with no operator type; 5 not-in-dataset rows have an unread candidate that is not marked private. Recall is not measured, so "not in this dataset" means no candidate was confirmed, not that a match was ruled out.
- Every not-in-dataset row is placed at its district centre only. The lane must say "position approximate (district centre)", and its distance carries the district's radius as error.

## Limits

- The list records central funds allocated to upgrade a hospital's trauma care. It does not say the unit was built, is staffed, or works today, and operating status is not checked here. The card fact can say only that the hospital is on this MoHFW list of funded trauma care facilities, dated 8 Feb 2023.
- Levels I to III are not in this file. The amount allocated tracks the level only loosely and is not used as a level.
- Positions are district centres, so `km_apart` mixes our record's error with the district's radius, and the nearby lane must say "position approximate (district centre)".
- MoHFW's copyright policy says its website content "may not be reproduced partially or fully, without due permission". Showing listed names on screen needs Hruday's decision.
