# LaQshya links: the government list beside our records

Built by `scripts/wp13_laqshya_links.py` on 2026-11-22T04:07:00Z. Certificates are called current when valid on or after 2026-11-21. Source: LaQshya master list of 8 Apr 2026 (NHSRC), retrieved 2026-11-21, https://qps.nhsrcindia.org/sites/default/files/2026-04/LaQshya_Master_data-08-04-2026.pdf.

## Findings

- **Link precision:** 63 of 115 decided candidates are correct (55%); 6 more are unsure and 0 unreviewed, out of 121 candidates. Every candidate was reviewed by an agent (Claude), not by Hruday. Only the correct ones produce a line.
- **Records with a correct link:** 62 of our records (61 LaQshya units).
- **Current maternity OT certificate:** 16 of those 62 records have a maternity operating theatre certificate valid on 2026-11-21.
- **Of those 16, 1 has a record with a fact that names a caesarean** (Murshidabad Medical College & Hospital; the WP4 `csection` tag in `gold/capability_grades.parquet`, emoc line). For the other 15, the government list certifies the maternity operating theatre while the record never names a caesarean: a gap in the record, not evidence about the hospital.
- These counts describe how our records line up with one government list. They are not a measure of how many public hospitals are missing from the data, and none is computed here.

## Method

1. LaQshya units: 1261 (from 2173 list rows: one row per unit and department). Each unit already has an NFHS `district_key` from WP2b.
2. Our candidate pool: 547 records (467 operator public, 71 operator private, government name, 9 operator missing, public name). Public operator type is a requirement, except that a missing or `private` operator is let in when the name itself says it is a government unit, so a mislabelled district hospital can be reviewed.
3. Our district: the record's PIN through `silver/pin_crosswalk` (every district the PIN's offices fall in) and `silver/district_crosswalk`. Record coordinates and the OSM points in `ext_laqshya` are not used.
4. Names: lowercased, abbreviations expanded (DH, SDH, CHC, MC, Govt, ...), generic words removed to get a core ("District Hospital, Nashik" to "nashik"); rapidfuzz token_set and token_sort ratios on the cores; unit type from the name (DH, SDH, CHC, MC) checked for agreement.
5. Candidates: same district and core token_set at least 70 (or, with no core, full-name match at least 90); or same state, another district, core token_sort at least 95 with a 4-letter core word and no type conflict. Up to 2 per unit.
6. Review: every candidate read by hand (names, unit type, town, our record's facts when needed) and marked correct, wrong or unsure with a reason, in `data/labels/laqshya_link_review.csv`.

## Review counts

| scope | correct | unsure | wrong |
|---|---|---|---|
| district | 55 | 4 | 48 |
| state | 8 | 2 | 4 |
| all | 63 | 6 | 52 |

Precision by matcher feature (correct / (correct + wrong); unsure left out):

| feature | value | correct | wrong | unsure | precision |
|---|---|---|---|---|---|
| unit type agrees | agrees | 43 | 26 | 4 | 62% |
| unit type agrees | conflicts | 1 | 3 | 0 | 25% |
| unit type agrees | not stated | 19 | 23 | 2 | 45% |
| strict core match (token_sort >= 90) | no | 19 | 45 | 2 | 30% |
| strict core match (token_sort >= 90) | yes | 44 | 7 | 4 | 86% |
| candidate rank in unit | 1 | 60 | 38 | 5 | 61% |
| candidate rank in unit | 2 | 3 | 14 | 1 | 18% |
| operator in our record | missing | 0 | 3 | 0 | 0% |
| operator in our record | private | 1 | 15 | 1 | 6% |
| operator in our record | public | 62 | 34 | 5 | 65% |

Why the wrong ones were wrong (first clause of the reason):

- different institution: 32
- private or non-allopathic college: 18
- different town: 2

## Records with a current maternity OT certificate

| our record | listed as | emoc grade | fact names a caesarean | line |
|---|---|---|---|---|
| Baramulla district hospital | District Hospital Baramulla | claimed | no | Government list (LaQshya, NHSRC, 8 Apr 2026, page 17 row 509): maternity operating theatre certified, valid to 16 Oct 2028. Outside data, not from this record. |
| Bhagwan Mahavir Hospital Government of NCT Delhi | Bhagwan Mahavir Hospital | claimed | no | Government list (LaQshya, NHSRC, 8 Apr 2026, page 9 row 278): maternity operating theatre certified, valid to 5 Aug 2028. Outside data, not from this record. |
| Champhai District Hospital, Champhai | Champhai District Hospital | none | no | Government list (LaQshya, NHSRC, 8 Apr 2026, page 40 row 1263): maternity operating theatre certified, valid to 14 Jun 2027. Outside data, not from this record. |
| Dhemaji Civil Hospital | Dhemaji Civil Hospital | none | no | Government list (LaQshya, NHSRC, 8 Apr 2026, page 4 row 119): maternity operating theatre certified, valid to 13 May 2028. Outside data, not from this record. |
| Diamond Harbour Government Medical College and Hospital | Diamond Harbour Government Medical College | claimed | no | Government list (LaQshya, NHSRC, 8 Apr 2026, page 66 row 2066): maternity operating theatre certified, valid to 5 Dec 2026. Outside data, not from this record. |
| District Hospital, Nashik | District Hospital nashik | claimed | no | Government list (LaQshya, NHSRC, 8 Apr 2026, page 35 row 1109): maternity operating theatre certified, valid to 31 Dec 2026. Outside data, not from this record. |
| Durgapur Sub Divisional Hospital | SDH Durgapur | claimed | no | Government list (LaQshya, NHSRC, 8 Apr 2026, page 68 row 2113): maternity operating theatre certified, valid to 3 Apr 2028. Outside data, not from this record. |
| Government District Hospital Dausa | DH Dausa | none | no | Government list (LaQshya, NHSRC, 8 Apr 2026, page 45 row 1435): maternity operating theatre certified, valid to 11 Jul 2027. Outside data, not from this record. |
| Government Hospital (Palaniappan Street Pattukkottai, Pattukottai) | SDH Pattukottai | none | no | Government list (LaQshya, NHSRC, 8 Apr 2026, page 55 row 1714): maternity operating theatre certified, valid to 6 Feb 2028. Outside data, not from this record. |
| Government Medical College and Hospital, Theni | Theni Medical College & hospital | claimed | no | Government list (LaQshya, NHSRC, 8 Apr 2026, page 56 row 1754): maternity operating theatre certified, valid to 7 Dec 2026. Outside data, not from this record. |
| M.R. Bangur Hospital | M R Bangur District Hospital | claimed | no | Government list (LaQshya, NHSRC, 8 Apr 2026, page 66 row 2081): maternity operating theatre certified, valid to 8 Jan 2027. Outside data, not from this record. |
| Malda Medical College and Hospital | Maldah Medical College | claimed | no | Government list (LaQshya, NHSRC, 8 Apr 2026, page 67 row 2085): maternity operating theatre certified, valid to 7 Jul 2027. Outside data, not from this record. |
| Murshidabad Medical College & Hospital | Murshidabad Medical College | supported | yes | Government list (LaQshya, NHSRC, 8 Apr 2026, page 67 row 2091): maternity operating theatre certified, valid to 5 Dec 2026. Outside data, not from this record. |
| Raiganj Government Medical College And Hospital | Raiganj District hospital | none | no | Government list (LaQshya, NHSRC, 8 Apr 2026, page 67 row 2099): maternity operating theatre certified, valid to 7 Jul 2027. Outside data, not from this record. |
| SSG Hospital | SSG Hospital | claimed | no | Government list (LaQshya, NHSRC, 8 Apr 2026, page 14 row 437): maternity operating theatre certified, valid to 5 Jan 2029. Outside data, not from this record. |
| Sanjay Gandhi Memorial Hospital | Sanjay Gandhi Memorial Hospital | claimed | no | Government list (LaQshya, NHSRC, 8 Apr 2026, page 9 row 289): maternity operating theatre certified, valid to 13 Feb 2027. Outside data, not from this record. |

## District Hospital, Nashik, end to end

- Our record: **District Hospital, Nashik**, `6720cccc-3f42-4bf6-9bc7-33a16492741c`, operator `public`, PIN 422001, city Nashik, coord_check `moved_to_pin`.
- Emergency obstetric care grade: **claimed** (grade 1, evidence thin); tags present: blood, ob_delivery, nicu; missing for the next grade: caesarean section.
- A fact naming a caesarean: none in the record.
- Candidate link: LaQshya unit `d9ac44677a8c` "District Hospital nashik" (DH, Maharastra|Nashik), scope district, core match 100/100, review **correct**: same hospital: name and town agree (District Hospital, Nashik, PIN 422001)
  - list row: page 35 row 1108, Labour Room, conditionally_certified, result 2026-01-01, valid to 2026-07-01 (expired on 2026-11-21)
  - list row: page 35 row 1109, Maternity OT, certified, result 2026-01-01, valid to 2026-12-31 (current on 2026-11-21)
- `public_line("6720cccc-3f42-4bf6-9bc7-33a16492741c")` returns:

  > Government list (LaQshya, NHSRC, 8 Apr 2026, page 35 row 1109): maternity operating theatre certified, valid to 31 Dec 2026. Outside data, not from this record.

  page 35, row 1109, department Maternity OT, validity 2026-12-31, retrieved 2026-11-21.

## Against the earlier pass

The stand-out brainstorm quoted a score-research pass (scratchpad, not in the repo): 78 candidates, 40 correct, 31 wrong, 13 records with a current maternity OT certificate, 1 of them naming a caesarean (Murshidabad Medical College). This pass reviews a wider candidate set with its own rules; the figures above replace those.

## Limits

- The review is one agent's reading. Hruday's spot-check of the current-certificate links is still to do before any count goes on screen.
- A unit with no candidate is not evidence that the hospital is missing: our names may differ beyond what the matcher accepts, or the record's PIN may sit in another district.
- Some correct links attach to records whose own PIN points to another district (state-pass links). The line still describes the listed unit; the record's location problem is WP11's.
- LaQshya certifies a labour room or a maternity operating theatre on a date. A current certificate is outside evidence that the unit was assessed; it is not a guarantee of service today.
