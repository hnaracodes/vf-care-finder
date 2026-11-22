# Recipe v0.3: what changed, record by record

WP3, Nov 21, 23:30 ET, revised after review the same night (see "Fix round"). Recipe v0.3 (D048) is a rule-level regrade of the frozen tags. It changes 489 grades on four lines. I read every changed record except the cath lab demotions, where I read a random 30 of 271 and all 15 that the fix round added.

## Bottom line

- **Gate.** The grade 2+ gate (D048) moves on one line only. NICU falls from 706 to 694: the 12 records that name only an SNCU. On emoc, dialysis and cath lab, every record keeps its side of the gate.
- **Corroborated.** Grade 3 is the label behind the 15-minute evidence allowance, and it moves a lot:
  - cath lab 481 to 205
  - NICU 178 to 112
  - dialysis 480 to 456
  - emoc 41 to 54
- **Read results.** I found no wrong change under the final rules. 15 are debatable, all listed below (3 of them from the fix round). 8 are correct under the rule but sit on records whose underlying facts look wrong (eye hospitals with a NICU, and a diabetes clinic, IVF centre, cancer centre and liver lab with a level III NICU). Those are record problems that v0.3 neither creates nor fixes.
- **One fix to the rule as specified.** The rule as given ("caesarean and blood") promoted 3 records on "Fetal Blood Transfusion" or "Intrauterine blood transfusion". Those are fetal procedures, not WHO signal function 9. v0.3 masks fetal, intrauterine, neonatal and exchange transfusion wording for emoc (`blood_maternal`). This removed those 3 promotions and demoted 2 v0.2 grade 3 records that rested on the same wording. Emoc grade 3 is therefore 54, not the prototype's 59.

## Inputs and reproducibility

- Tags: `gold/fact_tags.parquet`, sha256 `6fa461a5b77a57c262ec27957c55f2215d232c9078a9e3ae46e13f1af0a6b3bf`, method student (`student:s1-1b[ob_delivery,h24,icu,ventilator]+regex:v0.2[rest]`). Not rerun, not changed.
- Regrading those tags under v0.2 reproduces `gold/capability_grades.parquet` (sha256 `07c52631...`) on all 119,868 rows and all 13 columns, with 0 differing cells. Every difference below therefore comes from the rule change alone.
- Output: `gold/capability_grades_v03.parquet` (+ `.meta.json`, which records the input hashes and the full v0.3 rule text and qualifier regexes). The v0.2 file was not written.
- Rerun: `.venv/bin/python scripts/wp3_regrade_v03.py` (add `--changes-csv <path>` to dump the changed records). Tests: `tests/test_recipes_v03.py` (43 pass; full suite 972 pass, 4 skipped, 1 xfailed).
- Rules and citations, with the standards' own words: `docs/06_capability_recipes.md`, section "v0.3". All five documents were fetched again tonight and every quote was checked against the fetched text: WHO 2009 Table 4, FRU 2025 s2.2, FBNC 2025, PM-JAY empanelment criteria, PMNDP.

## Counts, v0.2 to v0.3 (facilities)

| Line | Grade | v0.2 | v0.3 | Change |
|---|---|---|---|---|
| emoc | 0 none | 5,325 | 5,325 | 0 |
| emoc | 1 claimed | 4,232 | 4,232 | 0 |
| emoc | 2 supported | 391 | 378 | -13 |
| emoc | 3 corroborated | 41 | 54 | +13 (15 up, 2 down) |
| nicu | 0 none | 8,513 | 8,513 | 0 |
| nicu | 1 claimed | 770 | 782 | +12 |
| nicu | 2 supported | 528 | 582 | +54 |
| nicu | 3 corroborated | 178 | 112 | -66 (104 down, 38 up) |
| dialysis | 0 none | 7,631 | 7,631 | 0 |
| dialysis | 1 claimed | 1,322 | 1,322 | 0 |
| dialysis | 2 supported | 556 | 580 | +24 |
| dialysis | 3 corroborated | 480 | 456 | -24 |
| cath_lab | 0 none | 6,834 | 6,834 | 0 |
| cath_lab | 1 claimed | 2,464 | 2,464 | 0 |
| cath_lab | 2 supported | 210 | 486 | +276 |
| cath_lab | 3 corroborated | 481 | 205 | -276 (285 down, 9 up) |

The other eight lines are identical to v0.2, cell for cell. 14 emoc grade 3 records carry the FRU-level note (anaesthesia and a 24-hour service also named).

Against the research prototype (`scripts/research/score_v1/f2_v03.log`), most counts match exactly:

- NICU grade 2+ 694, SNCU-only 12, emoc FRU note 14, dialysis grade 3 456, dialysis demotions 24.

Three counts differ:

- **NICU grade 3: 112, not 113.** Before the fix round it was 118: 5 extra records say "Level-3" with a hyphen (3), which the prototype regex missed, or "tertiary NICU" (2), and FBNC places the NICU at "Level III", tertiary care. The fix round then removed 6 (CPAP alone, and ventilators in multi-unit lists).
- **Cath lab grade 3: 205, not 220.** The fix round requires the ventilator to belong to an adult ICU (PM-JAY item 3) and removed 15.
- **Emoc grade 3: 54, not 59.** This is the fetal transfusion fix above.

## Verdicts by change

| Change | Records | Read | Right | Debatable or weak | Wrong |
|---|---|---|---|---|---|
| dialysis 3 to 2 (nephrologist alone) | 24 | 24 | 24 | 0 | 0 |
| nicu 2 to 1 (SNCU only) | 12 | 12 | 12 | 0 | 0 |
| nicu 3 to 2 (generic ventilator) | 98 | 98 | 88 | 10 | 0 |
| nicu 3 to 2 (fix round: CPAP alone, multi-unit list) | 6 | 6 | 6 | 0 | 0 |
| nicu 2 to 3 (level III mention) | 38 | 38 | 38 | 0 | 0 |
| emoc 2 to 3 (caesarean and blood) | 15 | 15 | 15 | 0 | 0 |
| emoc 3 to 2 (fetal transfusion only) | 2 | 2 | 2 | 0 | 0 |
| cath_lab 2 to 3 | 9 | 9 | 9 | 0 | 0 |
| cath_lab 3 to 2 (no CCU, no ventilator) | 271 | 30 (sample) | 28 | 2 | 0 |
| cath_lab 3 to 2 (fix round: ventilator not an adult ICU's) | 14 | 14 | 11 | 3 | 0 |
| emoc promotions the spec rule would have made, removed by `blood_maternal` | 3 | 3 | n/a | n/a | 3 (prevented) |

What the reading showed, line by line:

- **Dialysis.** All 24 demoted records rest on a nephrologist alone ("Nephrologists available", staff bios). That is staffing; PMNDP has the nephrologist "visit the dialysis facility fortnightly/monthly".
  - 14 of the 24 also have a dialysis-only fact naming haemodialysis ("Hemodialysis", "Offers Hemodialysis"). v0.3 does not count those, because haemodialysis alone is the procedure, not an enabling piece (v0.1 change 3). Counting them would add 135 grade 3 records across the line.
  - One demoted record, Patiala Heart Institute, says "State-of-the-art dialysis care unit". That is a named unit the frozen `dialysis_unit` regex misses, because of the word "care". This is a tag recall gap, not a rule error; see open issues.
- **NICU, SNCU only.** All 12 are public hospitals whose only newborn-unit fact names an SNCU, which FBNC 2025 defines as level II. One, Dibrugarh Assam Medical College, names an SNCU at a different hospital (Civil Hospital, Sivasagar) that it mentors. Its grade 1 claim is generous, and its v0.2 grade 2 was wrong.
- **NICU, generic ventilator.** 88 of 98 are adult, ICU, PICU, OT or ambulance ventilators ("ICU with ventilators", "Ambulance service with ventilator support"). FBNC 2025: "Simply adding ventilators to SNCU does not qualify the unit as NICU", and a generic ventilator says less than that.
  - 10 are debatable. They are children's hospitals with a named NICU whose ventilator fact sounds neonatal-capable but does not say newborn: HFOV, SLE 6000, a Sensormedics oscillator, a "pediatric ventilator", or ventilators listed with incubators or phototherapy. The rule demotes them by design; a human reader might keep some.
  - 4 demoted records are eye hospitals (Aravind, Kannika, Anandrishiji Netralaya, Maruthi) that still hold NICU grade 2 from a NICU fact. That fact is probably about another organisation (WP11).
- **NICU, level III.** All 38 promotions rest on a fact naming a "Level III" or "tertiary" NICU. 4 of them sit on records that look like a different organisation: a diabetes clinic, an IVF centre, a cancer centre and a liver lab.
- **Emoc.** All 15 promotions pair a caesarean fact with a blood bank, blood storage or blood transfusion fact. Anaesthesia and OT are no longer required, which matches WHO 2009 Table 4 (signal functions 8 and 9). Both demotions rest on "Fetal Blood Transfusion" alone.
- **Cath lab.** The 271 demotions are records with a cath lab and a generic ICU, but no CCU and no ventilator. Several v0.2 corroborations rested on weak "ICU" wording: "NICU and PICU facilities", "Stroke ICU", department lists.
  - 2 of the 30 sampled are debatable: "ICC U", probably ICCU split by a space; and "Performs primary angioplasty 24x7 with door-to-balloon time under 60 minutes", strong evidence of a working STEMI service, though no CCU is named.
  - All 9 promotions pair a cath lab with a CCU or ICCU, or with an adult ICU and an ICU ventilator. Before the fix round there was a tenth, Techno India Dama, resting on "Ventilators visible in operating room", a photo caption. The fix round removed it.

## Fix round (review, Nov 21 late)

A review found that the cath lab ICU route let the ICU and the ventilator come from any fact, and that I had read only the changed records, so the grade 3 records kept from v0.2 were never examined. It also found two NICU corroborations that contradict FBNC. I agreed with all of it and changed the rules (decisions 3, 6, 7 and 9 below). Then I reran with `--no-write`, read every record that moved against the first v0.3 output, and wrote the file.

- **Cath lab.** PM-JAY Cardiology/CTVS item 3 reads "ICU Facility with cardiac monitoring and ventilator support": the ventilator belongs to the ICU. The ICU route is now an adult ICU (`icu_adult`) plus a ventilator that is not a newborn, paediatric, ambulance, transport, theatre, anaesthesia or Covid ventilator (`ventilator_icu`), or the ICU and the ventilator named in one fact (`icu_with_ventilator`). Negated ICU wording ("excluding ICU and Emergency") no longer counts. Cath lab grade 3: 220 to 205 (CCU route 147, ICU route 73 to 58).
- **NICU.** Breathing support corroborates only when it names mechanical ventilation, not CPAP alone. FBNC 2025 has a section "CPAP facilities at SNCU", so CPAP is level II care. A ventilator fact that lists adult units with the NICU ("Has ICU, CCU and NICU with ventilators") no longer corroborates. NICU grade 3: 118 to 112.
- Several records the review named stay at grade 3, now on better evidence. For example, 83361eb2 now quotes "Has intensive care unit (ICU)" and "Ventilators" in place of "excluding ICU" and an anaesthesia machine. f37e3587 now quotes "Invasive ventilation with pulmonary graphic display" in place of "CPAP ventilation in neonates". 3477b64c, 3d2af64f, 60d478ce and 1d0b223b also keep grade 3 on a separate adult ICU fact and a generic "Ventilators" fact. Those two facts need not be the same fact, and a generic "Ventilators" fact is accepted.
- Grade 2+ is unchanged on every line by the fix round.

#### Every record the fix round moved (21 of 21 read; all were v0.3 grade 3, now grade 2)

| # | Line | Facility | facility_id | Evidence that no longer counts | Verdict |
|---|---|---|---|---|---|
| 1 | cath_lab | Wadia Children Hospital Parel | f73e1e8e | "Has 81 critical care beds" + "Level 4 NICU with high-frequency ventilators" | right: every ventilator fact is neonatal |
| 2 | cath_lab | United CIIGMA Hospital | 231f141f | "Intensive obstetric care (obstetric ICU)" + "Mechanical ventilators" | right: the only ICU is obstetric |
| 3 | cath_lab | Ankur Hospital | 37e39295 | "pediatric intensive care consultants" + "ventilatory support for 18 hours" | right: the only ICU is paediatric |
| 4 | cath_lab | Voluntary Health Services | 86239f9a | "Intensive Care Unit" + "Neonatal ambulance fully equipped with neonatal ventilator" | right |
| 5 | cath_lab | Charak Hospital | 2b5d4240 | "Department of Critical Care & Anaesthesia" + "Ambulance with ventilator support" | right |
| 6 | cath_lab | Anu Hospitals | 2e854012 | "cath lab and intensive care units" + "Neonatal ventilation" | right |
| 7 | cath_lab | Godrej Memorial Hospital | 930faaa7 | "NICU and PICU" + "Fetal Heart Monitor Coddle Graph Ventilator" | right: an "Adult ICU" fact exists, but no ventilator fact is an ICU's |
| 8 | cath_lab | Divine Heart & Multispeciality Hospital | e03a7d10 | "ICU available" + "Operating theatres equipped with anesthesia machines, ventilators" | debatable: a heart hospital with theatre ventilators probably has ICU ones, but the text does not say so |
| 9 | cath_lab | Christian Medical College Ludhiana | 017a80f7 | "Shared Institutional ICU" + "NICU provides invasive and noninvasive ventilation" | debatable: a large teaching hospital, likely a data gap; the only ventilator facts are NICU |
| 10 | cath_lab | Parmeshwari Medical Centre | 28c7548d | "24x7 ICU care" + "Ambulance Service equipped with ventilators" | right |
| 11 | cath_lab | Aster RameshOngole | 45c3f249 | "Pulmonology and Critical Care" + "Neonatal ventilation equipment" | right |
| 12 | cath_lab | Manipal Goa Cancer & General Hospital | 5a1403dd | "Contains ICU and NICU units" + "Neonatal ventilator" | right |
| 13 | cath_lab | Sri Sathya Sai Institute of Higher Medical Sciences | c826aa92 | "2020 ... COVID hospital with five ICU beds and ventilators" | debatable: a real ICU with ventilators, but a Covid-era facility |
| 14 | cath_lab | Shri Mahavir Hospital Ambikapur | 36556485 | "24 Hours ICU Care" + "NICU equipment including incubators, warmers, ventilators" | right |
| 15 | cath_lab | Techno India Dama Healthcare | 9f25a38c | "Intensive care unit infrastructure" + "Ventilators visible in operating room" | right (v0.2 grade 2; the promotion is removed) |
| 16 | nicu | Raj Eye Hospital, Gorakhpur | 3d9d4079 | "Fully modular ICUs including CCU, NICU and HDU with ventilators" | right: multi-unit list (and an eye hospital, a record problem) |
| 17 | nicu | Kovai Medical Centre and Hospital | 3ef783e8 | "Bubble CPAP" | right: CPAP is level II (FBNC) |
| 18 | nicu | Life Line Hospital Kanpur | 2bd17078 | "Has ICU, CCU and NICU with ventilators" | right: multi-unit list |
| 19 | nicu | B. K. Memorial Hospital, Tezpur | 2f95dcea | "Advanced ICU and NICU with ventilator support" | right: multi-unit list |
| 20 | nicu | Sachin Mamta Hospital | 466d16b1 | "ICU and NICU with advanced monitoring systems and ventilators" | right: multi-unit list |
| 21 | nicu | Vatsalya Mother & Child Care Hospital | cdccce56 | "Bubble CPAP" | right: CPAP is level II; it also has a "Pediatric ventilator in PICU", like the 10 debatable children's hospitals |

Not changed, and disclosed:

- **Self-declared level III.** 42 of the 112 NICU grade 3 records rest only on a level III mention (`nicu_level3`). In 25 of them, every NICU fact is the level III fact itself, for example a4b9a04d, whose one fact is "Level III NICU". There "corroborated" means a self-declared level, not two pieces of evidence. The card now says "a self-declared level III NICU".
- **Emoc blood.** WHO 2009 says transfusions "can be administered in other contexts" and that "the signal functions should have been performed in an obstetric context". Any blood bank counts for grade 3, for example e84b0206 on "Provides Path Lab, X-ray, Blood Bank, Pharmacy services". The card now says "blood bank or transfusion, not necessarily for obstetric patients".

## Decisions to log (for the orchestrator; I did not edit the decisions log)

1. v0.3 is a rule-only version: `RULE_VERSION_V03 = "v0.3"`, with `RECIPE_VERSION` left at `v0.2` so other packages' defaults do not move.
   - Requirements that depend on a tagged fact's wording are qualifiers. They are not tags, and they never appear in `tags_present` or in evidence item tags.
   - The output keeps the capability_grades schema and adds `level_note` and `level_note_items`.
2. `blood_maternal`: for emoc, fetal, intrauterine, neonatal and exchange transfusion wording does not count as blood (WHO signal function 9). This is not in the D048 text. It was added after reading the promotions, and it moves 5 records.
3. `neonatal_resp_ventilation` (was `neonatal_resp_not_sncu`): a newborn breathing-support fact corroborates a NICU only when it names mechanical ventilation (`ventilat|\bhfov\b|oscillat|intubat|surfactant`), not CPAP alone, because FBNC 2025 has "CPAP facilities at SNCU". It still drops SNCU-only facts and multi-unit lists. Fix round, moves 2 records (Bubble CPAP alone).
6. Cath lab ICU route (fix round): `icu_adult` and (`ventilator_icu` or `icu_with_ventilator`), per PM-JAY item 3 "ICU Facility with cardiac monitoring and ventilator support". Neonatal, paediatric, obstetric, Covid, mobile and negated ICU wording does not count as the ICU; newborn, paediatric (unless adult), ambulance, transport, theatre, anaesthesia and Covid ventilators do not count as ICU ventilator support. Moves 15 records (14 v0.3 grade 3 to 2, and 1 v0.2 grade 2 promotion removed).
7. `ventilator_newborn` and `neonatal_resp_ventilation` (fix round): a ventilator fact that also names an adult ICU, CCU or HDU ("Has ICU, CCU and NICU with ventilators") is a multi-unit list and does not corroborate a NICU. Moves 4 records.
8. Declared design choices for cath lab, not PM-JAY text: item 4 ("round the clock cardiologist services") is not required, so `h24` and `emergency` are no longer required; and the CCU route needs no ventilator, although item 3 asks for one.
9. Card wording (fix round): emoc blood reads "blood bank or transfusion, not necessarily for obstetric patients" (WHO 2009: transfusion "can be administered in other contexts"); NICU level III reads "a self-declared level III NICU".
4. Dialysis "haemodialysis" counts only inside a fact already tagged `dialysis_unit`, as in the prototype. A haemodialysis-only fact is still the procedure. The branch moves 0 records.
5. Emoc FRU note: our `h24` tag is any round-the-clock wording, so the note says so, and it never changes a grade.

## Open issues

- **Tag recall gap.** 21 records name a dialysis unit, beds, stations or a dialysis department in a fact the frozen `dialysis_unit` regex did not tag: "has 12 dialysis beds", "14 haemodialysis stations", "dialysis care unit". Some of these are department lists, and one is "under construction". A tag-level fix needs a new tag version, not v0.3.
- **NICU generic-ventilator rule.** It is strict by design. The 10 debatable demotions are listed below if Hruday wants a looser "paediatric ventilator or HFOV at a facility with a named NICU" route.
- **Record problems.** 8 records are correct under the rule but probably describe another organisation (listed below). They belong to WP11's flags.
- **Not in my files, for the orchestrator.**
  - `docs/07_data_contracts.md` has no entry for `gold/capability_grades_v03.parquet`. Proposed entry: the 13 `capability_grades` columns plus `level_note` (string, null except emoc grade 3 with anaesthesia and h24 facts; never changes a grade) and `level_note_items` (list of evidence items, empty when `level_note` is null), 15 columns, one row per facility per line (119,868), `recipe_version` `v0.3`, `.meta.json` with the input sha256s and the full rule and qualifier text.
  - `docs/08_decisions_log.md` needs the decisions above (1 to 9). Decision 2 (`blood_maternal`) and decisions 3, 6 and 7 go beyond the D048 text.
  - `dbapp/vf/recipes.py` is a stale partial copy of v0.3, taken at 23:23 before my final edits. It still uses the raw `neonatal_resp` tag and has none of the fix-round qualifiers, so two v0.3 definitions now share one label. Its grades now differ from `src/vf` (cath lab 205 here, 220 there). I wrote earlier that the dbapp copy "does not have v0.3". That was wrong. `dbapp/vf` is generated by `scripts/wp12_bundle.py` (WP12), and `wp12_bundle.py --check` exits 1 on this drift. Rerun the bundle script once v0.3 is final.
- **Downstream.** Consumers of grade 3 (the evidence allowance, WP5 and WP8 copy) must read `capability_grades_v03.parquet`. Until they do, the app still shows v0.2 grade 3 labels.

## Every record read

Facility ids are the first 8 characters. Quotes are truncated.

#### dialysis, 3 to 2 (24 of 24 read)

| # | Facility | facility_id | Fact that decided it | Verdict |
|---|---|---|---|---|
| 1 | Sadar Hospital | b94c3b56 | Nephrologists available | right: nephrologist only; a dialysis-only fact names haemodialysis |
| 2 | Vijaya Hospital | dd1f9efd | Dr. P. Soundararajan is a nephrologist affiliated with Vijaya Hospital; Qualifications: MBBS... | right: nephrologist only |
| 3 | Billroth Hospitals | 8cbb1ecd | Billroth Hospital provides nephrology services via Dr. Dhanasegaran S E, a nephrologist | right: nephrologist only |
| 4 | NU Hospitals | ea493e8e | NU Hospitals employs Dr. Prasad M Raj, a pediatric nephrologist | right: nephrologist only; a dialysis-only fact names haemodialysis |
| 5 | Bhagwan Das Hospital | 61b45c1a | Has specialist doctors including a laparoscopic surgeon, an orthopedic surgeon, a neurosurge... | right: nephrologist only |
| 6 | Shravan Hospital And Kidney Institute | 99f9bfec | Has Dr. Prakash Khetan, Senior Nephrologist, on staff | right: nephrologist only; a dialysis-only fact names haemodialysis |
| 7 | Tura Christian Hospital | ddb9be27 | In-house nephrologist | right: nephrologist only; a dialysis-only fact names haemodialysis |
| 8 | Imperial Multispeciality Hospital | 1bdc33d0 | Director and consultant nephrologist at Imperial Multispeciality Hospital, Chikali (Dr. Sneh... | right: nephrologist only; a dialysis-only fact names haemodialysis |
| 9 | Sanjivi Hospital | 7f96ac85 | Staff includes consultants Dr. Y. Haritha (Nephrologist) and Dr. A. Srujan Kumar (General Ph... | right: nephrologist only |
| 10 | Tanishtha's Diabetes Endocrine & Kidney Centre | 98a9007a | Staffed by two consultant specialists (endocrinologist and nephrologist) | right: nephrologist only |
| 11 | Dr. Sprint Medical Clinic / Allahabad / Mahewa Naini | ac05d243 | Offers consultations with Dermatologist, General Physician, Psychiatrist, Gynecologist, Neph... | right: nephrologist only |
| 12 | Alfa Kidney Care | cc9826e2 | Staff includes nephrologist Dr. Ravi Bhadania | right: nephrologist only; a dialysis-only fact names haemodialysis |
| 13 | Global Speciality Hospital | 5c1c445c | Dr. Shivam Yadav is a consultant nephrologist with 8 years of overall experience | right: nephrologist only; a dialysis-only fact names haemodialysis |
| 14 | Al Mahmood General Hospital Jambusar | 725a87fb | Dr. Harshal Joshi is Nephrologist And Transplant Physician at Al-mahmood General Hospital | right: nephrologist only |
| 15 | Techno India Dama Healthcare & Medical Research Centre | 9f25a38c | Affiliated nephrologists include Dr. Lalit Agarwal and Dr. Pinaki Mukherjee at Techno India... | right: nephrologist only; a dialysis-only fact names haemodialysis |
| 16 | Civil Hospital Palwal | a948a2d7 | Nephrology services offered by a nephrologist (Dr Rajesh Goel) at the center | right: nephrologist only |
| 17 | Apollo Hospital | d6d7b774 | Apollo Hospitals Bilaspur employs a Nephrology department with a Senior Consultant Nephrolog... | right: nephrologist only |
| 18 | Dr. Rama Super Speciality Hospital - Kidnecare | f463dd33 | Dr. Dinesh Dhanda is a nephrologist on staff with MBBS, MD in General Medicine, and DM in Ne... | right: nephrologist only; a dialysis-only fact names haemodialysis |
| 19 | Chhabra Hospital | 31533901 | Staff includes senior urologist, transplant surgeon and nephrologists | right: nephrologist only; a dialysis-only fact names haemodialysis |
| 20 | Regency Multi Super Speciality Hospital | 413c1fbc | Has nephrology/renal specialty on staff (Dr. Deepak Dewan, Nephrologist/Renal Specialist) at... | right: nephrologist only; a dialysis-only fact names haemodialysis |
| 21 | Veda Hospital & Kidney Centre | 0201050b | Led by Dr. MV Viswanath, a renowned nephrologist in Vijayawada | right: nephrologist only; a dialysis-only fact names haemodialysis |
| 22 | Reni Kidney Care | 16c6b758 | Provides nephrology services at Reni Kidney Care with nephrologist Dr. Suresh | right: nephrologist only |
| 23 | Patiala Heart Institute | 2c690bc5 | Staff includes cardiologists, cardiac surgeons, cardiac intensivist, nephrologists, and neur... | right: nephrologist only; a dialysis-only fact names haemodialysis |
| 24 | Manipal Hospitals | 44f168e0 | Manipal Hospital Ghaziabad employs Dr. Tirthankar Mohanty as Consultant Nephrologist | right: nephrologist only; a dialysis-only fact names haemodialysis |

#### nicu, 2 to 1, SNCU only (12 of 12 read)

| # | Facility | facility_id | Fact that decided it | Verdict |
|---|---|---|---|---|
| 1 | District Hospital | 02b8c9bf | District Hospital Dhar has a Special Neonatal Care Unit (SNCU). | right: SNCU only |
| 2 | Malkangiri District Headquarters Hospital | 43d4dca8 | Has 92 sanctioned beds (excluding SNCU) and 12 SNCU beds; total functional bed strength 163;... | right: SNCU only |
| 3 | Dibrugarh Assam Medical College | 2ee91ec2 | Serves as the tertiary care linked mentoring centre for the Special Newborn Care Unit at Civ... | right: SNCU only; the SNCU named is at another hospital |
| 4 | District Hospital Hapur | 6cd599ab | Houses a Special Newborn Care Unit (SNCU) for sick and small newborns | right: SNCU only |
| 5 | Sanjay Gandhi Memorial Hospital | 989ef605 | Has 300-bed capacity with department-specific bed allocations: Medicine 47 beds, Surgery 20... | right: SNCU only |
| 6 | Diamond Harbour Government Medical College and Hospital | 3603f591 | Has Critical Care Unit (CCU), Neonatal Care Unit (SNCU), and Dialysis Unit | right: SNCU only |
| 7 | Civil Hospital | b9a43164 | Has Sick Newborn Care Unit (SNCU) | right: SNCU only |
| 8 | M.R. Bangur Hospital | b471650e | Special Neonatal Care Unit (SNCU) | right: SNCU only |
| 9 | District Hospital, Nashik | 6720cccc | Special Newborn Care Unit (SNCU) available | right: SNCU only |
| 10 | Maharaja Jitendra Narayan Medical College and Hospital | 45134e3e | Specialized units include SNCU (Special Neonatal Care Unit) and PICU (Pediatric Intensive Ca... | right: SNCU only |
| 11 | Women & Children Hospital | 61000c87 | State-of-the-art facilities including MOT-PICU-SNCU | right: SNCU only |
| 12 | Blood Bank, District Hospital, Satna | 1ee06ace | Pediatrics department with SNCU, PICU, Paediatric ward, NRC, and Paediatric OPD | right: SNCU only |

#### nicu, 3 to 2, generic ventilator (98 of 98 read)

| # | Facility | facility_id | Fact that decided it | Verdict |
|---|---|---|---|---|
| 1 | Aravind Eye Hospital | b8a5401f | 22-bed Level II Intensive Care Unit (ICU) with 11 ventilator beds | right: generic ventilator; a NICU on an eye-hospital record is itself doubtful |
| 2 | Alchemist Hospital | aa07dc77 | Ventilators | right: generic ventilator |
| 3 | Dr DY Patil Medical College and Hospital, Pune, India | fa445a1d | Mechanical ventilation is provided to patients in the Department of Critical Care Medicine | right: generic ventilator |
| 4 | B P PODDAR HOSPITAL | 02331520 | Ambulance fleet includes Advanced Life Support (ALS) and Basic Life Support (BLS) units equi... | right: generic ventilator |
| 5 | CARE Hospitals - Nagpur | 2cb66ffa | Mechanical ventilation provided in the ICU | right: generic ventilator |
| 6 | Kamakshi Memorial Hospital | 54946f79 | Provides neuro-critical care with ventilator support following surgery | right: generic ventilator |
| 7 | Phoenix Hospital | 669a2138 | Fully equipped modern ICU with ventilators | right: generic ventilator |
| 8 | Sanjeevani Hospital | 6f8a8416 | Ventilators | right: generic ventilator |
| 9 | Sahyadri Hospitals | 7b71adc1 | Critical Care Unit equipped with multiparameter monitors and ventilators | right: generic ventilator |
| 10 | Dhanwantari Hospital | 8364c81e | PICU with 2 ventilators | right: generic ventilator |
| 11 | Niramaya Hospital | 895db286 | Ventilatory care | right: generic ventilator |
| 12 | Government ESI Medical College, Coimbatore | b9386c11 | Provides ventilator-supported critical care for patients | right: generic ventilator |
| 13 | Dr Ram Manohar Lohia Institute of Medical Sciences | d4066f5a | PICU has ventilators | right: generic ventilator |
| 14 | Lourdes Hospital | d9cca233 | Has a 6-bed intensive care unit with a ventilator. | right: generic ventilator |
| 15 | Marble City Hospital & Reseach Centre | fa1c244c | CT-ICU furnished with monitors and ventilators | right: generic ventilator |
| 16 | Dr. Mehta's Hospitals | 0ed06201 | Multidisciplinary Intensive Care Unit with infectious disease isolation rooms, dialysis unit... | right: generic ventilator |
| 17 | Sanjivani Multi Speciality Hospital | 6f7bd0c4 | 12 ICU beds with ventilators | right: generic ventilator |
| 18 | CSI Jayaraj Annapackiam Mission Hospital Palayamkottai | 712fef10 | ICU with ventilator, cardiac monitors and defibrillator | right: generic ventilator |
| 19 | Godrej Memorial Hospital | 930faaa7 | Fetal Heart Monitor Coddle Graph Ventilator | right: generic ventilator |
| 20 | Siddhivinayak Hospital | bae1a68b | Anesthesia machine with ventilator | right: generic ventilator |
| 21 | Shubham Hospital | c99f8d45 | Ventilators | right: generic ventilator |
| 22 | Ford Hospital And Research Center | e8396ece | Intensive Care Unit with ventilators and patient monitoring systems | right: generic ventilator |
| 23 | St. Philomena's Hospital, Bengaluru | f9689810 | Ventilators available for adult and pediatric patients | right: generic ventilator |
| 24 | Neha Prakash Hospital | 0f78f083 | Ambulance service with ventilator support | right: generic ventilator |
| 25 | Green City Hospital | 1d24507f | Monitors and ventilators from Philips USA | right: generic ventilator |
| 26 | Nirmal Hospital | 30cb08a6 | Ventilator facility | right: generic ventilator |
| 27 | Unity Hospital | 358c7acc | Ventilator | right: generic ventilator |
| 28 | Kasturi Medical Centre (A Nabh Multi Speciality Hospital) | 76d3602d | High-frequency ventilators for critical care | right: generic ventilator |
| 29 | Bharati Hospital | 90bab675 | Ventilators (4) | right: generic ventilator |
| 30 | C. R. Gardi Hospital | 980d56d9 | Has ventilators | right: generic ventilator |
| 31 | PGR Hospital | d71e18cb | Ventilator | right: generic ventilator |
| 32 | Garg Child Hospital | e7ecf080 | Ventilator | debatable: bare 'Ventilator' at a child hospital |
| 33 | SNS.Pahwa (C) Hospital Ludhiana | 0f50a26e | ICU with six beds and advanced critical care resources including BiPAP and ventilator. | right: generic ventilator |
| 34 | Hi-Tech Emergency Hospital – Saguna More, Bailey Road, Pa... | 1866f9cf | Fully equipped operation theatres with CSSD and ventilators | right: generic ventilator |
| 35 | Priti Hospital | 668afa66 | Ventilator-equipped high dependency unit | right: generic ventilator |
| 36 | Yash Hospital | adae175f | Ventilators | right: generic ventilator |
| 37 | Smt. Parvati Devi Hospital | c30390ca | Hi-End Ventilators | right: generic ventilator |
| 38 | Rajindra Hospital Patiala | dd0000de | Ventilator support with continuous monitoring in ICU | right: generic ventilator |
| 39 | Amrita Hospital, Kochi | f14cec09 | Non-invasive ventilation | right: generic ventilator |
| 40 | Phoenix Hospital | 14510729 | Mechanical ventilators for respiratory support via endotracheal or tracheostomy tubes | right: generic ventilator |
| 41 | Healing Touch Hospital | 26f9b0c4 | Ventilators and phototherapy unit available | debatable: ventilators listed with phototherapy, a newborn treatment |
| 42 | Geeta Hospital | 357d904e | Ventilator-equipped Intensive Care Unit (ICU) | right: generic ventilator |
| 43 | Ulwe Superspeciality Hospital | 49304b90 | Ambulances equipped with oxygen, ventilators, and monitors | right: generic ventilator |
| 44 | Government Dharmapuri Medical College, Nethaji Byebass Ro... | 52e78d9c | Ventilators with cardiac monitors including defibrillators and pulse oximeters | right: generic ventilator |
| 45 | Upadhyay Hospital | 5b4a8ebb | Ventilators, defibrillators, incubators, patient monitoring systems | debatable: ventilators listed with incubators |
| 46 | New B K Hospital | 846902e8 | Ventilator facility available | right: generic ventilator |
| 47 | Government Sivagangai Medical College Hospital, Sivagangai | 864e6f25 | Casualty block equipped with ventilators, defibrillators and patient monitoring systems | right: generic ventilator |
| 48 | Maharaja Agrasen Medical College, Agroha | 9de3817b | An Intensive Care Ambulance with ventilator, Defibrillator and other gadgets is available fo... | right: generic ventilator |
| 49 | Kannika Hospital - Eye Hospital in Harsh Nagar | b178384f | Ventilator (Savina) | right: generic ventilator; a NICU on an eye-hospital record is itself doubtful |
| 50 | Dr. Vikhe Patil Memorial Hospital | b719d232 | Ventilators in Medical & Surgical ICU | right: generic ventilator |
| 51 | Mateshwari Chikitsa Kendra | b8209a4f | Ventilator availability on site | right: generic ventilator |
| 52 | Star Children Hospital (Pediatrician) | ca2d0d2c | High Frequency Oscillatory Ventilator | debatable: HFOV at a children's hospital with an advanced NICU |
| 53 | Gopi Krishna Hospital, Sadar Bazar | d6da16ff | Ventilators | right: generic ventilator |
| 54 | Sushma Hospital | d815ec89 | 24x7 intensive care unit with ventilators | right: generic ventilator |
| 55 | Virk Hospital | 211259cc | AC Intensive Care Unit with ventilators and defibrillators | right: generic ventilator |
| 56 | Rama Healthcare Multispeciality Hospital | 43f9ec34 | Ventilator facility | right: generic ventilator |
| 57 | Mangal Prabhu Hospital | 4592713c | High-Frequency Oscillatory Ventilator (HFOV) for pediatric patients | debatable: HFOV for paediatric patients |
| 58 | JPS Children Hospital | 90293936 | Ventilators and CPAP machines for pediatric respiratory support | debatable: ventilators and CPAP for paediatric support |
| 59 | Tapan Sinha Memorial Hospital | c3e51c9e | Ventilators: 7 | right: generic ventilator |
| 60 | Satija Health Care | c6336369 | ICU for serious children, adults, and trauma patients with ventilator support | right: generic ventilator |
| 61 | Brain And Spine Care Hospital | 07df2b25 | Ventilator | right: generic ventilator |
| 62 | SPS Hospital | 2b9f9d46 | Mechanical ventilation | right: generic ventilator |
| 63 | Annai Arul Hospital | 3c2c5f60 | Ventilators, cardiac monitors, defibrillators, and infusion pumps in critical care | right: generic ventilator |
| 64 | PK Das Institute of Medical Science | 3de58404 | Intensive Care Unit with mechanical ventilator | right: generic ventilator |
| 65 | RJ Super Speciality Hospital | 45532e92 | Five ventilators | right: generic ventilator |
| 66 | Jeevandan Health | 52bb8240 | Portable ventilators | right: generic ventilator |
| 67 | Satyarthi Hospital | 5790feca | Ventilator and other critical care equipment available | right: generic ventilator |
| 68 | Siddh Hospital | 7123782c | Mechanical Ventilation Support | right: generic ventilator |
| 69 | Chirayu Hospital | 72535fdd | Intensive Care Unit (ICU) with ventilators | right: generic ventilator |
| 70 | Baluni Hospital | 79174c00 | ICU with ventilator systems | right: generic ventilator |
| 71 | Surya Hospitals Jaipur | a618961b | Advanced life-support modalities including mechanical ventilators, hemodynamic monitors and... | right: generic ventilator |
| 72 | Shraddha Heart Hospital | fe23dd4d | Ventilator and BiPAP facilities | right: generic ventilator |
| 73 | Jj Plus Hospitals Aurangabad | 049b4f30 | Maquet ventilators | right: generic ventilator |
| 74 | Mar Baselios Medical Mission Heart Institute | 0cbe2f73 | Ventilators and multi-parameter monitors | right: generic ventilator |
| 75 | Orchid Hospital | 1baadb96 | Three-bedded ICU/ICCU fully equipped with multi-parameter monitors, defibrillators, BiPAP ma... | right: generic ventilator |
| 76 | Vimala Hospital | 378fc494 | Ventilator | right: generic ventilator |
| 77 | P.K.Saha Nursing Home | 3e141f19 | Pediatric ventilator available | debatable: paediatric ventilator, 25 NICU beds |
| 78 | SPARSH Hospital, Hennur Road | 4b8e2383 | 24/7 ICU with advanced ventilators and life support | right: generic ventilator |
| 79 | Swastik Hospital | 5f8e6446 | ICU with patient monitoring systems and ventilator | right: generic ventilator |
| 80 | Morning Star Medical Centre, Adimali | 9d3f76a9 | Ventilator | right: generic ventilator |
| 81 | Techno India Dama Healthcare & Medical Research Centre | 9f25a38c | Ventilators visible in operating room | right: generic ventilator |
| 82 | Pushp Hospital | 11c43b0f | Ventilator | right: generic ventilator |
| 83 | Dr. Yashlok Hospital & Research Centre | 7e64f404 | Ambulance and ventilator services | right: generic ventilator |
| 84 | The County Hospital and Research Centre | b5baf4ac | Ventilators | right: generic ventilator |
| 85 | Priya Hospital | b79b6f78 | Has 75 ventilator beds/ICU beds | right: generic ventilator |
| 86 | Chandigarh Child Care Centre Kotkapura | cce39a61 | High-flow ventilator | debatable: high-flow ventilator at a child care centre |
| 87 | Siddh Multispecialty Hospital | cd944f16 | Over 85 ICU beds with ventilators and advanced monitoring | right: generic ventilator |
| 88 | Anandrishiji Netralaya | da424446 | Operates 24/7 Intensive Care Unit (ICU) with continuous monitoring and advanced life support... | right: generic ventilator; a NICU on an eye-hospital record is itself doubtful |
| 89 | Indore Multi Speciality Hospital | 05506c2e | Ventilators | right: generic ventilator |
| 90 | Kisholoy Children's Hospital | 0faa3635 | Provides non-invasive, conventional, and high-frequency ventilation for pediatric patients | debatable: SLE 6000, a neonatal and paediatric ventilator |
| 91 | Maruthi Eye Hospital | 1ddb0dbe | 8-bedded intensive care unit with cardiac monitors and ventilators | right: generic ventilator; a NICU on an eye-hospital record is itself doubtful |
| 92 | Vardaan Hospital | 35098b2b | Has Poisoning Care Unit With Ventilator | right: generic ventilator |
| 93 | Pulse Hospital | 49516db0 | PICU has ventilation facilities | right: generic ventilator |
| 94 | Pulse Hospital | 58447b52 | Ventilators available | right: generic ventilator |
| 95 | Gaba Hospital | 5a8a6f87 | ICU with ventilators. | right: generic ventilator |
| 96 | Seven Colors Children Hospital | 62fbabb0 | Maquet Servo Conventional Mechanical Ventilator | debatable: Sensormedics HFOV with an 11-bed NICU |
| 97 | Star Medicity Superspeciality Hospital & Trauma Centre | 696a8ec0 | ICU with ventilators | right: generic ventilator |
| 98 | Chhaya Super Speciality Hospital | 6a38792b | ICU with ventilator | right: generic ventilator |

#### nicu, 2 to 3, level III mention (38 of 38 read)

| # | Facility | facility_id | Fact that decided it | Verdict |
|---|---|---|---|---|
| 1 | Welcare Hospital | 8e687a2c | Level 3 NICU | right: level III NICU |
| 2 | Cloudnine Golf Course Road - Gurgaon | c710d0a6 | Level 3 Neonatal Intensive Care Unit (NICU) | right: level III NICU |
| 3 | Andhra Hospitals | 253a699b | Neonatal Intensive Care Unit (NICU) Level 3 accredited | right: level III NICU |
| 4 | Aayush Hospital | 882dfdeb | NICU Level-3 | right: level III NICU |
| 5 | Mayom Hospital | aee90e94 | Level III NICU with dedicated pediatric ICU | right: level III NICU |
| 6 | Apollo Cradle & Childrens Hospital | 20b34829 | Level III Neonatal Intensive Care Unit (NICU) | right: level III NICU |
| 7 | Triton Hospital | 333d6b82 | 12-bed Level III NICU | right: level III NICU |
| 8 | Mata Roop Rani Maggo Hospital | 8e374f3f | Has Level 3 NICU | right: level III NICU |
| 9 | Doctor A Kapoor's Diabetes Control Clinic at Lifeline Med... | af99e7c6 | Gyneac & Pediatric Centre of Excellence with high-risk pregnancies and tertiary NICU care | right: tertiary NICU; the record looks like a different organisation |
| 10 | Cheema Medical Complex | bc3bb6d6 | Level III NICU | right: level III NICU |
| 11 | Kinder Women’s Hospital and Fertility Centre | 39c27df7 | Has Level III NICU facility | right: level III NICU |
| 12 | Lakecity Hospital | b36e6a48 | Neonatal Intensive Care Unit (Level 3) under Mother Child Care Unit | right: level III NICU |
| 13 | Nova IVF Fertility Center - Raipur | b5ca9bc8 | Level III NICU | right: level III NICU; the record looks like a different organisation |
| 14 | Mar Sleeva Cancer Care & Research Centre | 09ae3413 | Level 3 NICU | right: level III NICU; the record looks like a different organisation |
| 15 | New Ramkrishna Seva Sadan | 1c04a5d8 | Level III NICU | right: level III NICU |
| 16 | Cocoon Hospital | b573b0e9 | Offers obstetrics & gynecology, neonatology (Level III NICU), pediatric care, gynecological... | right: level III NICU |
| 17 | Aastha Multispeciality Hospital | b7161769 | Level III NICU | right: level III NICU |
| 18 | Nirnoy Hospital | ea5b37f7 | Neonatal intensive care unit with 6 beds (Level III NICU) | right: level III NICU |
| 19 | Medicover Woman & Child Hospitals | 257efb93 | Has over 40 NICU beds designed for Level II and Level III neonatal care | right: level III NICU |
| 20 | Coimbatore Child Trust Hospital | 5327fb19 | Level 3 NICU | right: level III NICU |
| 21 | The Liver Unit and Monarch Liver Lab, Cochin Gastroentero... | 6a9c97fc | Level III neonatal intensive care unit (NICU) | right: level III NICU; the record looks like a different organisation |
| 22 | Neonest Hospital | 9c3cb801 | Has Level 3 NICU | right: level III NICU |
| 23 | Vasundhara Hospital | a4b9a04d | Level III NICU | right: level III NICU |
| 24 | Pioneer Hospital | a7459d8a | NICU Level 3 for sick newborns | right: level III NICU |
| 25 | Motherhood Hospital Chennai | 084e4ccf | Level III NICU | right: level III NICU |
| 26 | Deshmukh Hospital & Research Centre | a27ba028 | Level III NICU on site | right: level III NICU |
| 27 | Guwahati Metro Hospital Khanapara – Hospital in Guwahati | ac47f0a9 | Level 3 NICU care referenced for premature babies treated at Guwahati Metro Hospital | right: level III NICU |
| 28 | Aagman Women’s Hospital | e76ffec7 | Level-3 NICU | right: level III NICU |
| 29 | Ankura Hospital, Gachibowli | efbfd5c6 | Level III NICU and Level III PICU | right: level III NICU |
| 30 | Holy Basil Hospital | fa93aa0d | Level III NICU | right: level III NICU |
| 31 | Kinder Women’s Hospital | 624aaf86 | NICU – Level 3 | right: level III NICU |
| 32 | Ajay Hospital | a48ad99c | Neonatal Intensive Care Unit (NICU) Level III | right: level III NICU |
| 33 | Prasad Hospital | f8467b61 | Level III NICU | right: level III NICU |
| 34 | Sanjivani Multi Speciality Hospital | fac0cc70 | NICU Level III | right: level III NICU |
| 35 | Orange Neonatal And Pediatric Intensive Care Unit | 601cee71 | Operates a Level 3 neonatal and pediatric intensive care unit (NICU and PICU) available 24/7... | right: level III NICU |
| 36 | Sparsh Super-Specialty Hospital | 668cdea7 | Neonatal critical care centre with Level 3 NICU | right: level III NICU |
| 37 | Akkineni Women's Hospital | 6e49ccdd | Has Level 3 NICU | right: level III NICU |
| 38 | Krishna Maternity Home & Paediatrics Centre | 7e6e4ee0 | Level III Neonatal Intensive Care Unit | right: level III NICU |

#### emoc, 2 to 3 and 3 to 2 (17 of 17 read)

| # | Facility | facility_id | Fact that decided it | Verdict |
|---|---|---|---|---|
| 1 | Navjeevan Hospital | 61ed89ad | Has 24/7 blood bank services | right: caesarean and blood bank or transfusion |
| 2 | City Hospital | a2a3c136 | In-house Blood Bank is available | right: caesarean and blood bank or transfusion |
| 3 | Preet Hospital | a82c0050 | Operates an on-site blood bank | right: caesarean and blood bank or transfusion |
| 4 | Ganga Sewa Sadan Hospital (3 to 2) | 52a696ab | Fetal Blood Transfusion | right: the only blood fact is a fetal transfusion |
| 5 | Sub Divisional Hospital Pusa | 61731339 | Blood transfusion | right: caesarean and blood bank or transfusion |
| 6 | Nalbari Maternity Hospital | 0ea44f72 | Nalbari Maternity Hospital is listed as a blood bank in Nalbari district, Assam. | right: caesarean and blood bank or transfusion |
| 7 | Bhaktivedanta Hospital & Research Institute | 2ca2244c | Offers 24/7 Pathology, Radiology, Ambulance, Pharmacy, and Blood Storage Centre services | right: caesarean and blood bank or transfusion |
| 8 | Bhagyoday Tirth Chikitsalay | 806c465b | Has a Blood Bank | right: caesarean and blood bank or transfusion |
| 9 | Ganga Sahai Multispeciality Hospital | 0f0f3145 | On-site Path Lab and Blood Bank | right: caesarean and blood bank or transfusion |
| 10 | Surya Hospitals Jaipur | a618961b | Blood Storage Service | right: caesarean and blood bank or transfusion |
| 11 | Krishnaya Institute of Cardioc & Fetal Science (3 to 2) | d911e733 | Fetal Blood Transfusion | right: the only blood fact is a fetal transfusion |
| 12 | Miracare Hospital | 398d9f9c | Provides 24x7 blood bank services | right: caesarean and blood bank or transfusion |
| 13 | Medansh Multispeciality Hospital | 863a9801 | On-site Blood Bank | right: caesarean and blood bank or transfusion |
| 14 | Uma Super Speciality Hospital | d40967c4 | Has Blood Banks on site | right: caesarean and blood bank or transfusion |
| 15 | Care Multispeciality Hospital | e84b0206 | Provides Path Lab, X-ray, Blood Bank, Pharmacy services | right: caesarean and blood bank or transfusion |
| 16 | Kalyani Hospital | 1a7c035e | Offers 24/7 blood bank services | right: caesarean and blood bank or transfusion |
| 17 | Lbs Hospital | 24e5f7f9 | Blood transfusion | right: caesarean and blood bank or transfusion |

#### cath_lab, 2 to 3 (10 of 10 read)

| # | Facility | facility_id | Fact that decided it | Verdict |
|---|---|---|---|---|
| 1 | HOSMAT Hospital | 7cfbe1c0 | Has 21 CCU beds | right |
| 2 | Dr Ram Manohar Lohia Institute of Medical Sciences | d4066f5a | ICU/CCU/Respiratory care units are equipped with life-support equipment | right |
| 3 | Holy Family Hospital, Thodupuzha | 681c3935 | ICCU on site | right |
| 4 | Safe Gastro & Surgery Center | bf896279 | Open ICU with family-access compatible monitoring + Ventilators, infusion pumps, defibrillat... | right |
| 5 | Midas Multispeciality Hospital Pvt. Ltd, Nagpur, Maharash... | fc3b3e51 | Has a dedicated Critical Care Unit (ICU). + Prone ventilation | right |
| 6 | Life Hospital | 4ab0c4b7 | Coronary Care Unit | right |
| 7 | Ramakrishna Mission Seva Pratishthan | 871eab15 | Contains CCU (Coronary Care Unit) and HDU (High Dependency Unit) | right |
| 8 | Techno India Dama Healthcare & Medical Research Centre | 9f25a38c | Intensive care unit infrastructure with monitoring equipment + Ventilators visible in operat... | weak: the ventilator fact is a photo caption; removed in the fix round, stays grade 2 |
| 9 | Care Multispeciality Hospital | e84b0206 | ICCU (Intensive Coronary Care Unit) | right |
| 10 | Gaba Hospital | 5a8a6f87 | ICU with ventilators. + ICU with ventilators. | right |

#### cath_lab, 3 to 2, random sample (30 of 271 read; pandas sample, random_state=20261121)

| # | Facility | facility_id | Fact that decided it | Verdict |
|---|---|---|---|---|
| 1 | Shri Narayana Hospital | 1a5e766a | Critical care unit (ICU/ICC U) on site | debatable: 'ICC U' is probably ICCU split by a space |
| 2 | Life Line Hospital | 3523c38d | Fully equipped intensive care units (ICUs) | right: no CCU and no ventilator |
| 3 | The Calcutta Medical Research Institute, Kolkata | d29c85e9 | Neuro ICU | right: no CCU and no ventilator |
| 4 | Pulse Diagnostics | 86dc887d | ICU available | right: no CCU and no ventilator |
| 5 | Nehru Memorial Techno Global Hospital | e2ea28ba | Has IPD, Emergency, ICU, NICU and HDU facilities | right: no CCU and no ventilator |
| 6 | Metro Hospital | 68658330 | Facilities include ICUs and 24/7 emergency services | right: no CCU and no ventilator |
| 7 | Sum Hospital | c57fbffb | 24/7 emergency care with round-the-clock trauma, ICU, and critical care services. | right: no CCU and no ventilator |
| 8 | Sudha Hospital & Medical Research Centre | c815b7c7 | Departments include Gastro Sciences, Internal Medicine, Dental, Emergency Medicine, Anesthes... | right: no CCU and no ventilator |
| 9 | Santevita Hospital | cc6ef0bb | 24×7 Critical Care Unit (ICU) | right: no CCU and no ventilator |
| 10 | Swastha Hospital | 28f00c52 | Modern Intensive Care Units (ICUs) | right: no CCU and no ventilator |
| 11 | Cheema Medical Complex | bc3bb6d6 | Has an intensive care unit (ICU) | right: no CCU and no ventilator |
| 12 | Germanten Hospital | 6871f1d1 | Highly equipped intensive care unit | right: no CCU and no ventilator |
| 13 | Suguna Hospital | 23feae12 | Provides Critical Care | right: no CCU and no ventilator |
| 14 | General Hospital Ernakulam | bbf46f87 | Stroke ICU with modern facilities to treat brain haemorrhage | right: no CCU and no ventilator |
| 15 | Raju Hospital | d64d2823 | Provides 24/7 emergency care with round-the-clock doctor coverage in ICU | right: no CCU and no ventilator |
| 16 | Ujala Cygnus Central Hospital | f0c57e76 | Critical Care department | right: no CCU and no ventilator |
| 17 | Care Hospitals | ecd07024 | NICU and PICU facilities are available at Care Hospitals | right: no CCU and no ventilator |
| 18 | Aditya Ashwini Hospital | a43bf3f6 | Specialties include Cardiology, Neurology, Orthopedics, Nephrology, Pulmonology, Gynecology... | right: no CCU and no ventilator |
| 19 | CHOPDA MEDICARE & RESEARCH CENTRE | d40daa97 | Critical Care department | right: no CCU and no ventilator |
| 20 | Shree Mahavir Jain Hospital | 5ec20085 | Provides neurosciences, orthopedics, digestive care, maternity care, emergency care and crit... | right: no CCU and no ventilator |
| 21 | Dr. Vasantrao Pawar Medical College Hospital & Research C... | e17d7b20 | Has ICU | right: no CCU and no ventilator |
| 22 | Paras Global Darbhanga | b668392a | Pediatric NICU and PICU units | right: no CCU and no ventilator |
| 23 | Citizens Speciality Hospitals | 3c737b5e | Specialties include Cardiology, Cardiothoracic and Vascular Surgery, Neurology, Neurosurgery... | right: no CCU and no ventilator |
| 24 | Aster Ramesh Hospital, Guntur | af612410 | Has ICU beds | debatable: a 24x7 primary angioplasty service is strong evidence, but no CCU or ventilator is named |
| 25 | Baby Memorial Hospital | 7ec8248d | Emergency Medicine and Critical Care as clinical specialties. | right: no CCU and no ventilator |
| 26 | Premier Hospital | a8817326 | Multispecialty hospital with Trauma, Emergency & Critical Care | right: no CCU and no ventilator |
| 27 | Acharya Shri Chander College of Medical Sciences and Hosp... | c7185521 | Emergency and Critical Care Areas with dedicated ICU facilities | right: no CCU and no ventilator |
| 28 | Unicare Hospital | c7eb6761 | ICU is equipped for critical care | right: no CCU and no ventilator |
| 29 | Continental Hospitals | cb71f5b5 | Pediatric Intensive Care Unit | right: no CCU and no ventilator |
| 30 | Trustwell Hospitals | 9264b21d | Center of Excellence: Anesthesia and Critical Care | right: no CCU and no ventilator |
