# National Cancer Grid member list: parsed, located, candidate links

Built by `scripts/wp13_ext_ncg.py` on 2026-11-22T05:33:51Z. Source: National Cancer Grid member list, "Cancer Centres / Units", https://www.ncgindia.org/hospitals-and-institutions/members (served by https://www.ncgindia.org/hospitalsandinstitutions/_index?type_id=D83F0EAA-2B81-4E98-92B7-67AE3DA693FD&country_id=&state_id=), retrieved 2026-11-22T05:22:22Z, sha256 `60adba9e18339af932db6376608d6d637d190656bd1acf7f8b3ba99f772a25fd`. Reader verdicts are applied below: a band is accepted only if its one-sided 95% lower bound of precision is at least 0.90, and only reader-marked same pairs in accepted bands are links.

## Findings

- **List:** 376 Indian member cancer centres or units (392 rows including 16 outside India, dropped). The list is undated and carries no validity; every row has status `undated`.
- **Located:** 331 to a PIN area, 45 to a district centre, 0 not located. The list gives no coordinates.
- **Candidates:** 2153 pairs for 298 list rows (auto 54, middle 84, low 2015). 54 list rows have a provisional auto pair.
- **Check sheet:** 150 pairs for readers (auto 54, middle 84, low probe 12); no sampling needed.
- Acceptance arithmetic (exact binomial, one-sided 95%): with all read pairs correct the lower bound reaches 0.90 at 29 pairs; at 46 pairs one wrong pair is allowed. If readers judge all 54 auto pairs, the band passes with at most 1 wrong.

## Method

1. Download: the public members page loads its table from its own GET request; that response for type "Cancer Centres / Units" was saved once to `data/external/ncg/` and parsed as text. robots.txt allows all paths. No form, login or search was used.
2. Parse: one row per table row, with the section heading (state, "National", or a foreign country) and the row number inside the section as the citation.
3. Locate (`vf.locate.resolve`): the last 6-digit PIN in the address that is in the India Post directory and in the listed state gives the PIN-area centre and its precision; otherwise an address part that resolves to a district or a district-named city gives the district centre.
4. Our pool: 8020 of 9989 records; 1969 AYUSH, dental or veterinary records rejected by type or name.
5. Blocking: same PIN, or the same PIN-directory district plus a rare shared name token (a token in at most 10 of our names, at least 4 letters, not a generic unit word, not the district's own name; soft match at rapidfuzz ratio 85). The 2 km block is not used: the list has no coordinates.
6. Score: IDF-weighted soft Jaccard on name tokens (abbreviations expanded as in `vf.public_list`), bounded to [0, 1]; IDF over 10365 names (ours plus the list's).
7. Bands (provisional, for readers): auto at score 0.7 or more and the one-to-one winner; middle at 0.3 or more, or a high score that lost the one-to-one contest; low below. One-to-one is greedy by score; records in one WP11 duplicate cluster may share a list row.

## Score distribution

| band | pairs | same PIN | min score | max score |
|---|---|---|---|---|
| auto | 54 | 49 | 0.71 | 1.00 |
| middle | 84 | 56 | 0.30 | 0.69 |
| low | 2015 | 1855 | 0.00 | 0.30 |

## Ten highest-scoring pairs (for orientation, not decisions)

| list name | our name | score | band | km apart |
|---|---|---|---|---|
| Indira Gandhi Institute of Medical Sciences, Patna | Indira Gandhi Institute of Medical Sciences | 1.00 | auto | 1.13 |
| Goa Medical College | Goa Medical College | 1.00 | auto | 0.04 |
| Manipal Hospital, Goa | Manipal Goa Hospital | 1.00 | auto | 1.55 |
| Hanumant Hospital | Hanumant Hospital | 1.00 | auto | 85.88 |
| K K Patel Super Speciality Hospital | K. K. Patel Super Speciality Hospital | 1.00 | auto | 24.85 |
| Bhanot Hospital | Bhanot Hospital | 1.00 | auto | 1.59 |
| Ranchi Cancer Hospital & Research Centre (Tata Trusts) | Ranchi Cancer Hospital & Research Centre(Tata Trusts),Ranchi,Jharkhand | 1.00 | auto | 1.99 |
| Cytecare Hospitals Pvt Ltd | Cytecare Hospitals | 1.00 | auto | 5.05 |
| Sri Shankara Cancer Hospital and Research Centre | Sri Shankara Cancer Hospital and Research Centre, Bengaluru | 1.00 | auto | 0.66 |
| Vydehi Cancer Center | Vydehi Cancer Center | 1.00 | auto | 1.21 |

## Reader verdicts and acceptance

Readers judged every pair in `data/labels/ext_link_check_ncg.csv` (`data/labels/ext_link_read_ncg.csv`). Precision is same / (same + different); unsure pairs are left out of it, and a second column counts them as wrong. A band is accepted only if the one-sided 95% Clopper-Pearson lower bound is at least 0.90. Inside an accepted band only pairs the reader marked same are kept.

| band | pairs | read | same | different | unsure | precision | lower 95% | lower 95%, unsure as wrong | accepted |
|---|---|---|---|---|---|---|---|---|---|
| auto | 54 | 54 | 54 | 0 | 0 | 1.000 | 0.946 | 0.946 | yes |
| middle | 84 | 84 | 55 | 25 | 4 | 0.688 | 0.592 | 0.560 | no |
| low | 2015 | 12 | 2 | 7 | 3 | 0.222 | 0.041 | 0.030 | no |

## What the list adds

| list rows | count | share |
|---|---|---|
| linked (accepted link, card fact) | 54 | 14.4% |
| in our data per reader, band not accepted (no card fact) | 54 | 14.4% |
| reader unsure for a candidate (excluded from the lane) | 7 | 1.9% |
| not in our dataset (nearby-lane candidates) | 261 | 69.4% |
| all Indian list rows | 376 | 100% |

Location quality of the not-in-dataset rows:

| position | rows | precision radius km (median) | max |
|---|---|---|---|
| PIN area | 226 | 2.4 | 111.0 |
| district centre | 35 | 33.6 | 70.1 |

- 54 accepted links join 54 list rows to 54 of our records; all are reader-marked same pairs from the auto band.
- Of the 261 not-in-dataset rows, 78 had no candidate at all and 183 had only unread low-band candidates (score under 0.30). The low-band probe (the 12 best low pairs) found 2 same pairs, the lowest at score 0.29; 0 not-in-dataset rows have an unread candidate at or above that score. Unread pairs below it may still hide a few matches under another name, so recall is not measured. `best_unread_score` in `ext_ncg.parquet` carries this per row.
- Middle band: readers marked many pairs same but also many different, so the band fails and its same pairs give no card fact. Those list rows are still kept out of the not-in-dataset lane.

## Limits

- Membership of the National Cancer Grid is a network membership, not an accreditation. The card fact can say the centre is on the member list; it cannot say which cancer treatments it offers.
- The list is undated. A member that left the network may still be listed, and a new member may be missing.
- Positions are PIN-area or district centres, so `km_apart` mixes our record's error with the list's precision radius.
- A list row with no candidate is not evidence that the centre is missing from our data. Recall is not measured.
