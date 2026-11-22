# Capability recipes (v0.1)

How facts become evidence tags, and how tags become a facility grade. This is a starting spec. WP3 owns it: change it when the data says so, bump the version, and log the change in `08_decisions_log.md`.

Recipe version: `v0.1` (Nov 21, 2026), with v0.2 specified below for the re-scope (D025). v0.1 changes four things in the v0 tagger, listed under "Changes in v0.1" below. The grade rules are unchanged.

## Two layers

1. Fact level: each fact string gets zero or more evidence tags. This is what Model A learns.
2. Facility level: a deterministic rule over a facility's tags and specialties gives a grade per service line. No model here, so every grade can be explained by quoting facts.

## Tag taxonomy

Evidence tags:

| Tag | Meaning | Starter regex (lowercased text) |
| --- | --- | --- |
| `ob_delivery` | Conducts deliveries or has a maternity or labour ward | `maternity\|labou?r (room\|ward)\|normal delivery\|childbirth\|obstetric` |
| `csection` | Performs caesarean sections | `\bc[- ]?section\|caesarean\|cesarean\|\blscs\b` |
| `blood` | Blood bank, blood storage, or transfusion | `blood bank\|blood storage\|blood transfusion` |
| `anesthesia` | Anesthesia service or anesthetist | `an(a)?esthe` |
| `ot` | Operating theatre | `operation theat\|operating theat\|operating room` |
| `h24` | Round-the-clock service | `24/7\|24x7\|24 ?hours\|24 ?hrs\|24-hour\|round the clock` |
| `nicu` | Neonatal intensive or special care | `\bnicu\b\|\bsncu\b\|neonatal intensive` |
| `dialysis` | Provides dialysis | `dialysis` |
| `dialysis_unit` | Named unit, machines, or nephrologist | `dialysis (unit\|machine\|centre\|center)\|nephrologist` |
| `icu` | Intensive care unit | `\bicu\b\|intensive care\|critical care unit` |
| `ventilator` | Mechanical ventilation | `ventilator` |
| `emergency` | Emergency or trauma care | `emergency\|trauma\|casualty` |
| `scheme` | Accepts a public insurance scheme | `ayushman\|pmjay\|cghs` |

In the table above, `\|` is an escaped pipe so the table renders. In code, write a plain `|`.

Noise tags (so the model learns what to ignore, and for the gold table):

| Tag | Meaning | Example from the data |
| --- | --- | --- |
| `reputation` | Ratings, reviews, years of experience | "Rated 4.3 on JustDial with 72 ratings" |
| `location` | Address or place statement | "Located in Rampura, Kota, Rajasthan" |
| `staff_name` | Names a person | "Led by Dr. Arun Kumar Dhanuka, MBBS, MD, DM (Neurology)" |
| `accessibility` | Parking, wheelchair access | "Wheelchair-accessible parking available" |
| `photo_artifact` | Looks like a photo caption, not an equipment list | "Hospital bed with rails" |
| `web_presence` | About the web page itself | "Maintains an unofficial Facebook page" |
| `other_clinical` | A real clinical fact outside the three service lines | "Digital EEG/Video EEG testing" |

The regexes are a baseline and a way to pick which facts to send to the teacher. They are known to be crude: `ot` and `delivery` style patterns match unrelated text, and bare "obstetric" fires `ob_delivery`. Measuring how crude is part of WP3.

### Changes in v0.1

Each of these changed facility grades in v0, so the WP5 distances and WP6 lists were built on wrong grades. Found in WP3 part 1 and confirmed in the pipeline audit of Nov 21.

1. `csection` needs a word boundary before `c section` (`\bc[- ]?section`). v0 matched it inside other words: "paediatric section", "economic sections of society", "IPC sections 312, 316". Five facilities had EmOC grade 2 from such text alone. A sixth had it from "caesarean section facility not available on-site" (change 4).
2. VBAC wording is removed before `csection` runs: `vaginal birth after (a )?(c(a)?esarean|c[- ]?section)( section)?|\bvbac\b`. A vaginal birth after a past caesarean is not a caesarean.
3. `dialysis_unit` no longer has the `h(a)?emodialysis` branch. "Haemodialysis" alone is dialysis, not a named unit (the gold labeling rule), and in v0 one such fact gave grade 3 dialysis. "Haemodialysis unit" and "hemodialysis machines" still match through `dialysis (unit|machine|...)`.
4. A fact with a negation or absence cue gets no evidence tag at all. The cue pattern is narrow on purpose:

   `\bnot (be |been |yet )?(available|offered|provided|present|performed|done|operational|functional)\b`, `\bnot (explicitly |specifically |clearly )?(specified|mentioned|listed|stated|detailed|confirmed)\b`, `\b(does|do|did) not\b`, `\b(doesn't|don't|didn't)\b`, `\bunavailable\b`, `\bnon[- ]?(functional|operational|availability)\b`, `^no\b.*\b(listed|mentioned|specified|available|provided|offered)\b`, `\bno (specific|explicit)\b`, `\bthere (is|are) no (?!other\b)`, `\b(lack|lacks|lacking|absence) of\b`

   Bare "no" and "non" are not cues: "non-invasive ventilation in ICU", "ICU no. 1" and "dialysis at no cost" keep their tags. Referral wording is not a cue either ("initiates referral to a senior doctor"). The pattern was written by reading every v0-tagged fact text in `silver/facts.parquet` that it or a broader draft matched, not by looking at gold-set scores. It removes the tags of 34 of the 23,768 distinct v0-tagged texts; one of them ("alleged absence of fire-fighting equipment ... during the icu fire") is a known false negation.

v0 stays reproducible as `vf.recipes.tag_fact(text, version="v0")`. The gold-set design used v0 to draw its strata.

## Facility grades

Grades are per service line.

| Grade | Label | Meaning |
| --- | --- | --- |
| 0 | none | No claim and no evidence |
| 1 | claimed | A specialty tag or a generic statement only |
| 2 | supported | The defining procedure or unit is named in the facts |
| 3 | corroborated | Supported, and the enabling pieces are named too |

Rules (unchanged in v0.1):

### Emergency obstetric care (`emoc`)

- claimed: specialties include `gynecologyAndObstetrics`, `obstetricsAndMaternityCare`, or `maternalFetalMedicineOrPerinatology`; or tag `ob_delivery`
- supported: tag `csection`
- corroborated: `csection` and `blood` and (`anesthesia` or `ot`)
- Note `h24` and `nicu` separately as bonuses; do not require them in v0

Basis: the WHO signal functions for comprehensive emergency obstetric care add caesarean section and blood transfusion to the basic functions. Verify the exact wording before quoting it in the video.

### Dialysis (`dialysis`)

- claimed: specialties include `nephrology`
- supported: tag `dialysis`
- corroborated: `dialysis` and `dialysis_unit`

### Intensive care with ventilation (`icu`)

- claimed: specialties include `criticalCareMedicine`
- supported: tag `icu`
- corroborated: `icu` and `ventilator`

Baseline counts from regex alone, to sanity-check an implementation: 4,585 facilities with an obstetric specialty, 443 with a C-section mention, 2,156 with nephrology, 1,046 with a dialysis mention, 1,677 with critical care, 2,048 with an ICU mention, 380 with ICU and ventilator.

## v0.2: twelve service lines (draft spec, Nov 21, D025)

Status: spec written at the re-scope; WP3 implements it, settles the regexes by reading facts (not gold labels), and bumps `RECIPE_VERSION` to `v0.2`. The v0.1 tags and the `emoc`, `dialysis`, `icu` rules above are unchanged. Specialty names below were checked against `silver/facilities.parquet` (Nov 21).

### New evidence tags

Starter patterns are loose on purpose: read 50 matches of each before tightening. Negation and absence cues (v0.1, change 4) apply to every new tag. Referral wording ("refer", "tie-up with", "outsourced to") and stated needs ("need for", "proposed", "planned") must not tag.

| Tag | Meaning | Starter pattern | Watch for |
| --- | --- | --- | --- |
| `neonatal_resp` | Neonatal breathing support | `\bcpap\b\|bubble cpap\|neonatal ventilat\|infant ventilat` | adult CPAP for sleep apnoea |
| `cardiac_surgery` | Heart surgery performed | `\bcabg\b\|bypass surgery\|open heart\|valve (replacement\|repair)\|cardiac surg\|cardiothoracic surg\|\bctvs\b` | gastric bypass (bariatric) |
| `cath_lab` | Cardiac catheterisation | `cath ?lab\|angioplast\|coronary angiograph\|\bptca\b\|\bpci\b\|(coronary\|cardiac) stent` | "angiography" of other organs; peripheral stents |
| `ccu` | Cardiac intensive care | `\bccu\b\|\biccu\b\|cardiac (intensive\|critical) care\|coronary care unit` | |
| `mri` | MRI on site | `\bmri\b\|magnetic resonance` | "MRI reports reviewed", referral to a scan centre |
| `ct` | CT on site | `\bct ?scan\|computed tomograph\|\d+[- ]?slice\|\bmdct\b\|\bhrct\b` | "CT" as an abbreviation for something else; CT-guided referral |
| `trauma` | Trauma care | `trauma (cent(re\|er)\|care\|unit\|ward\|team\|surgery\|services)\|polytrauma\|accident (and\|&) emergency` | "trauma counselling", "birth trauma", "dental trauma" |
| `neurosurgery` | Brain or spine surgery performed | `neurosurg\|craniotom\|brain tumou?r (surgery\|removal)\|(spine\|spinal) surgery\|head injur(y\|ies) (management\|surgery\|care)` | neurology consults only |
| `burns` | Burn care | `\bburns? (unit\|ward\|icu\|care\|centre\|center\|management\|treatment)\|burn injur` | "heartburn", "razor burns" |
| `chemo` | Chemotherapy given | `chemotherap\|\bchemo\b\|day care chemo` | |
| `radiotherapy` | Radiotherapy on site | `radiotherap\|radiation (therapy\|oncology)\|\blinac\b\|linear accelerator\|brachytherap\|\bcobalt[- ]?60\b` | radiology (imaging) |

Bonus tags from v0.1 that the new rules reuse: `icu`, `ventilator`, `blood`, `ot`, `anesthesia`, `h24`, `emergency`.

### New grade rules

`claimed` is any listed specialty or any v0.2 tag of a weaker kind; `supported` needs the defining tag; `corroborated` needs the enabling pieces too.

| Line | Claimed (specialties, or tag) | Supported | Corroborated |
| --- | --- | --- | --- |
| `nicu` | `neonatologyPerinatalMedicine` | `nicu` | `nicu` and (`neonatal_resp` or `ventilator`) |
| `cardiac_surgery` | `cardiacSurgery`, `cardiothoracicSurgery` | `cardiac_surgery` | `cardiac_surgery` and (`icu` or `ccu`) and `blood` |
| `cath_lab` | `cardiology`, `interventionalRadiology` | `cath_lab` | `cath_lab` and (`ccu` or `icu`) and (`h24` or `emergency`) |
| `mri` | `radiology`, `diagnosticRadiology` | `mri` | `mri` in at least 2 distinct facts, or in 2 different fields |
| `ct` | `radiology`, `diagnosticRadiology` | `ct` | `ct` in at least 2 distinct facts, or in 2 different fields |
| `trauma` | `emergencyMedicine`, `orthopedicSurgery`; or tag `emergency` | `trauma` | `trauma` and `ot` and (`blood` or `icu`) and `h24` |
| `burns` | `burnAndTraumaPlasticSurgery`, `plasticSurgery`, `reconstructivePlasticSurgery` | `burns` | `burns` and `icu` and `ot` |
| `neurosurgery` | `neurosurgery`, `spineNeurosurgery`, `skullBaseNeurosurgery` | `neurosurgery` | `neurosurgery` and `icu` and `ct` |
| `oncology` | `medicalOncology`, `surgicalOncology`, `radiationOncology`, `gynecologicalOncology` | `chemo` or `radiotherapy` | `chemo` and `radiotherapy` |

Notes:

- `mri` and `ct` corroboration counts facts, not tags, so the rule engine needs a `min_distinct_facts` or distinct-field condition (WP3 step 2). Imaging has no natural "enabling piece" in this data; two independent mentions are the next best thing.
- `cath_lab` claimed from `cardiology` is weak on purpose: cardiology is listed by 3,003 facilities. Claimed never makes a facility recommendable (D027), so breadth here only affects the "also nearby, not verified" list.
- Each group needs a plain-language `requirement` name for the cards ("caesarean section", "blood bank or transfusion", "intensive care unit"), kept next to the rule in `vf.recipes`.
- These rules are product choices informed by what a service needs, not clinical standards. Before the video quotes one, check its basis (as for the WHO signal functions under `emoc`).

### Evidence strength (per facility and line)

Computed over the facts that satisfied the grade's required groups.

- `thin`: grade 2 or 3 resting on a single fact, or on facts from a record with one source domain and no official website.
- `strong`: grade 3, at least 3 supporting facts, and at least 2 source domains or an official website.
- `moderate`: everything else at grade 2 or 3.
- `none` at grade 0; grade 1 is `thin` by definition.

`description`-only support (no capability, procedure or equipment fact) caps strength at `thin`. Add the description as a fact source with `field = description` only if WP3 finds it adds real support; log the choice.

## v0.3: grade rules aligned to published standards (Nov 21, D048)

Status: implemented as `vf.recipes.GRADE_RULES_V03` and written by `scripts/wp3_regrade_v03.py` to `gold/capability_grades_v03.parquet`. v0.3 is a rule-level regrade only:

- It reads the frozen `gold/fact_tags.parquet` (sha256 `6fa461a5...`). No regex or student rerun, no tag definition changes.
- `gold/capability_grades.parquet` (v0.2) is not rewritten. Regrading the frozen tags under v0.2 reproduces it cell for cell.
- `RECIPE_VERSION` stays `v0.2` (the tagger version), and `tag_fact` has no v0.3.
- Four lines change: `emoc`, `nicu`, `dialysis`, `cath_lab`. The other eight keep their v0.2 rules (the same objects).
- The hand-read of every changed record is in `reports/recipe_v03_changes.md`.

### Qualifiers

Some v0.3 requirements depend on the wording of a fact the tagger already tagged. They are qualifiers: per-fact markers computed from the fact's own tags and normalized text (`vf.recipes.QUALIFIERS_V03`).

- They are never written as tags. `tags_present` and every `evidence_items[].tag` keep the real tag names.
- `facility_line_grades(..., version="v0.3")` adds them itself.
- Callers of `grade_line` or `grade_facility` with v0.3 must pass tags from `qualify_tags`.

| Qualifier | Base tag on the same fact | Holds when the text | Used by |
| --- | --- | --- | --- |
| `nicu_named` | `nicu` | matches `\bnicu\b\|neonatal intensive` (so not an SNCU-only fact) | nicu 2, 3 |
| `nicu_level3` | `nicu` | names a NICU and matches `\blevel[- ]?(iii\|3\|three)\b\|\btertiary (level \|care )?(nicu\|neonatal intensive)` | nicu 3 |
| `ventilator_newborn` | `ventilator` | matches `neonat\|new[- ]?born\|\bnicu\b\|\bnnicu\b\|preterm\|prematur\|\binfants?\b`, and is not an SNCU-only fact | nicu 3 |
| `neonatal_resp_not_sncu` | `neonatal_resp` | is not an SNCU-only fact (veto `^(?!.*(\bnicu\b\|neonatal intensive\|newborn intensive)).*\bsncu\b`) | nicu 3 |
| `blood_maternal` | `blood` | still matches the `blood` pattern after removing `(intra[- ]?uterine\|fetal\|foetal\|in[- ]?utero\|neonatal\|exchange)( \w+){0,2} (blood )?transfusions?` | emoc 3 |
| `dialysis_unit_named` | `dialysis_unit` | matches `dialysis (unit\|machine\|cent(re\|er)\|ward\|room\|department\|dept\|bed\|station)\|\bh(a)?emo[- ]?dialysis` (so not a nephrologist alone) | dialysis 3 |

### Rules

| Line | Claimed | Supported | Corroborated | Change from v0.2 |
| --- | --- | --- | --- | --- |
| `emoc` | unchanged | `csection` | `csection` and `blood_maternal` | anaesthesia or OT no longer required; fetal, intrauterine, neonatal and exchange transfusion do not count as blood |
| `nicu` | `neonatologyPerinatalMedicine`, or a `nicu` fact that names only an SNCU | `nicu_named` | `nicu_named` and (`neonatal_resp_not_sncu` or `ventilator_newborn` or `nicu_level3`) | SNCU-only drops to grade 1; a generic ventilator no longer corroborates; a level III mention does |
| `cath_lab` | unchanged | `cath_lab` | `cath_lab` and (`ccu`, or `icu` and `ventilator`) | `h24` or `emergency` no longer required; a generic ICU needs ventilators |
| `dialysis` | unchanged | `dialysis` | `dialysis` and `dialysis_unit_named` | a nephrologist alone no longer corroborates |

Two new output fields:

- `level_note` and `level_note_items`, for `emoc` only. When a grade 3 record also has `anesthesia` and `h24` facts, `level_note` says "FRU-level: anaesthesia and a 24-hour service are also named, two more of the FRU 2025 s2.2 criteria; the 24-hour fact may not be about obstetric care". The two facts are quoted in `level_note_items`. The note never changes a grade.
- A grade 1 `nicu` record whose only nicu evidence is an SNCU gets an evidence item with the requirement "names an SNCU (level II), not a NICU".

Requirement names on the cards cite the standard, for example "caesarean section (WHO 2009 Table 4, signal function 8)".

### Basis, with the standards' own words

All five documents were fetched again on Nov 21 (23:13 to 23:16 ET). Each quote below was checked against the fetched text. Page numbers are PDF pages.

- **emoc, WHO 2009.** *Monitoring emergency obstetric care: a handbook* (WHO, UNFPA, UNICEF, AMDD), Table 4, "Signal functions used to identify basic and comprehensive emergency obstetric care services" (p. 17). Comprehensive services are "Perform signal functions 1–7, plus: (8) Perform surgery (e.g., caesarean section) (9) Perform blood transfusion". The text adds that a facility "is classified as functioning at the comprehensive level when it offers the seven signal functions plus surgery (e.g. caesarean) and blood transfusion". Copy used: https://aa.unfpa.org/sites/default/files/pub-pdf/obstetric_monitoring.pdf (WHO record: https://www.who.int/publications-detail-redirect/9789241547734).
  - Caveat: grade 3 names signal functions 8 and 9 only. The data cannot show the seven basic functions or the 3-month performance window, so grade 3 is not "a comprehensive EmOC facility".
- **emoc note, FRU 2025.** NHM, *Revised Guidelines for Operationalizing First Referral Units*, November 2025, s2.2 "Essential Criteria for Designation and Functionality" (pp. 27 to 28):
  - "1. 24/7 Availability of Emergency Services: The facility must be equipped and staffed to handle emergencies round the clock."
  - "3. ... performing Caesarean sections and managing major obstetric complications 24/7."
  - "4. Anaesthesia Services: Anaesthesia services are available 24/7, either by a qualified Anaesthetist or an MBBS Medical Officer trained and certified in Life-Saving Anaesthesia Skills (LSAS)."
  - "7. Functional Blood Storage Unit (BSU): An operational BSU licensed as per Drugs and Cosmetics Rules, with 24/7 availability of blood and capability for safe blood transfusion."
  - Source: https://www.nhsrcindia.org/sites/default/files/2025-12/FRU%20guidenline%202%201.pdf
  - Caveat: our `h24` tag is any round-the-clock wording, not 24-hour obstetric care, so the note is a pointer and never a grade.
- **nicu, FBNC 2025.** MoHFW Child Health Division, *Facility Based Newborn Care Operational Guidelines 2025*:
  - SNCU care is "comprehensive secondary level of newborn care to small and sick neonates not requiring surgical intervention and mechanical ventilation" (p. 21).
  - "Special newborn care unit and Mother Newborn Care Unit delivers level II care to sick and small newborns" (p. 49).
  - NICU care is for newborns "who require constant nursing, complex surgical procedures, ongoing respiratory support, or other intensive monitoring" (p. 22).
  - The levels figure places "NICU(Level III)" at tertiary care, and the guidelines add: "Simply adding ventilators to SNCU does not qualify the unit as NICU" (p. 23).
  - Source: https://nhm.gov.in/images/pdf/programmes/CH-Programmes/FBNC/FBNC%20Operational%20Guidelines%202025.pdf
- **cath_lab, PM-JAY.** NHA *Hospital Empanelment Criteria*, Category 2 advanced criteria. Hospitals "should be fully equipped with ICCU/SICU/ NICU/ relevant Intensive Care Unit in addition to and in support of the OT facilities that they have" (p. 3). "A. Specific criteria for Cardiology/ CTVS" (p. 4) includes "3. ICU Facility with cardiac monitoring and ventilator support" and "6. Fully equipped Catheterization Laboratory Unit with qualified and trained Paramedics". Source: https://cdnbbsr.s3waas.gov.in/s3169779d3852b32ce8b1a1724dbf5217d/uploads/2022/04/2022072941.pdf
  - Caveat: "cardiac monitoring" is not checked, and the ICU and ventilator facts need not be the same fact.
- **dialysis, PM-JAY and PMNDP.** PM-JAY "H. Specific criteria for Nephrology and Urology Surgery" starts with "1. Dialysis unit" (p. 6). MoHFW *Operational Guidelines for Pradhan Mantri National Dialysis Programme*:
  - "Haemodialysis requires a dialysis machine and pure water and is typically done in a health facility (Dialysis centre)" (p. 13).
  - The nephrologist is a visiting role: "A Qualified Nephrologist ... shall visit the dialysis facility fortnightly/monthly" (p. 18).
  - Source: https://nhsrcindia.org/sites/default/files/2026-07/Operational%20Guidelines%20for%20PMNDP.pdf
  - Caveat: a fact that says only "haemodialysis" is still the procedure, not an enabling piece (v0.1 change 3). Haemodialysis counts only inside a fact the tagger already marked `dialysis_unit`. On the frozen tags this branch moves no record.

These are standards for what a service needs, applied to web text. A grade says what the web text names. It does not certify that a facility meets the standard.

### Counts (facilities, grades 0/1/2/3, v0.2 to v0.3)

| Line | v0.2 | v0.3 | Moves |
| --- | --- | --- | --- |
| `emoc` | 5,325 / 4,232 / 391 / 41 | 5,325 / 4,232 / 378 / 54 | 15 up (2 to 3), 2 down (3 to 2); FRU note on 14 |
| `nicu` | 8,513 / 770 / 528 / 178 | 8,513 / 782 / 576 / 118 | 12 down (2 to 1), 98 down (3 to 2), 38 up (2 to 3) |
| `dialysis` | 7,631 / 1,322 / 556 / 480 | 7,631 / 1,322 / 580 / 456 | 24 down (3 to 2) |
| `cath_lab` | 6,834 / 2,464 / 210 / 481 | 6,834 / 2,464 / 471 / 220 | 271 down (3 to 2), 10 up (2 to 3) |

Grade 2 or higher, the D048 gate, moves only for `nicu` (706 to 694, the 12 SNCU-only records).

## Source confidence (separate from the grade)

A grade says how complete the evidence is. Confidence says how much to trust the page it came from. Keep them as two fields.

Candidate signals, all available per facility:

- number of distinct domains in `source_urls`
- has an `officialWebsite`
- `recency_of_page_update`
- `affiliated_staff_presence`

Not usable: `source_types` (not aligned with facts) and `number_of_facts_about_the_organization` (mostly null).

Watch for contamination: some `source_urls` point at unrelated sites, so a fact may describe a different organization.

## Teacher prompt requirements (WP3)

- Give the model the tag taxonomy with one-line definitions and two examples each.
- Send facts in batches with ids; ask for JSON back: `{"id": ..., "tags": [...]}`.
- Tell it to tag negated statements ("no ICU facility") with no evidence tag.
- Tell it that a specialty word alone ("nephrology department") is not the service tag (`dialysis`).
- Temperature 0. Cache every response on disk keyed by fact text and recipe version, so reruns cost nothing.
- Spot-check 50 teacher labels by hand before labeling at scale.

## Gold set (WP3)

- About 400 distinct facts, hand-labeled with the same taxonomy, stratified so each evidence tag has at least 25 positives. Include facts the regex missed and facts the regex wrongly matched.
- Stored at `data/labels/gold_facts.csv`. Never train on it.
- If Hruday cannot label overnight, an agent labels it carefully and marks the file `labeled_by: agent`. Say so in the video's methods note; an agent-labeled gold set is weaker evidence than a human one.
