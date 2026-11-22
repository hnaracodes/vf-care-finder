# Model A results: fact tagger

Model A gives each fact text zero or more evidence tags (recipe v0.2, `docs/06_capability_recipes.md`). This page scores each tagger against the hand-labeled gold set (`data/labels/gold_facts.csv`).

**The gold set is never used for training, prompt tuning, regex writing, or choosing thresholds.** It only produces the numbers on this page. Teacher prompts and student models must be built without looking at it.

## Summary

The recipe v0.2 regex reaches a macro F1 of 0.89 (95% interval 0.87 to 0.91) over all 24 evidence tags on 777 gold facts. 18 of 24 tags score 0.85 or more. The three weakest are `ob_delivery` (0.53), `neurosurgery` (0.58), `ventilator` (0.79), with `burns` (0.79) and `icu` (0.79) close behind. The weak tags fail for plain reasons (below). Some of it is regex wording, and some is gold labels that disagree with the written rules. The 11 new tags score a macro F1 of 0.90; the 13 v0.1 tags score 0.88 on the full set. On the original 429 rows the v0.1 tags score 0.90, exactly as recipe v0.1 reported, because v0.2 does not change their patterns. They score lower on the full set because the added rows over-draw department lists and cardiac care units and neonatal ventilation, which those patterns handle badly. The gold set over-draws regex matches and near misses on purpose, so these are rates on hard cases, not on a typical fact.

| Method | What it is | Tags | Gold rows | Macro precision | Macro recall | Macro F1 (95% CI) | Micro F1 | Exact match | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| regex v0.2 | recipe v0.2 regexes (src/vf/recipes.py) | 24 | 777 | 0.93 | 0.87 | 0.89 (0.87 to 0.91) | 0.88 | 0.80 | done |
| regex v0.2, v0.1 tags | the same run, the 13 v0.1 tags only | 13 | 777 | 0.91 | 0.87 | 0.88 (0.87 to 0.90) | 0.87 | 0.84 | done |
| regex v0.2, new tags | the same run, the 11 new tags only | 11 | 777 | 0.96 | 0.87 | 0.90 (0.86 to 0.93) | 0.91 | 0.95 | done |
| v0.2 starter patterns | loose patterns from the recipes doc, before reading facts (new tags only) | 11 | 777 | 0.85 | 0.90 | 0.87 (0.84 to 0.90) | 0.87 | 0.91 | comparison |
| regex v0.1 | history: recipe v0.1, on the v0.1 gold rows | 13 | 429 | 0.92 | 0.89 | 0.90 (0.88 to 0.92) | 0.89 | 0.81 | superseded |
| regex v0 | history: original patterns, before the v0.1 fixes | 13 | 429 | 0.87 | 0.90 | 0.88 (0.86 to 0.90) | 0.88 | 0.77 | superseded |
| teacher | LLM teacher labels (WP3 part 3), claude-haiku-5-5 | 24 | 777 | 0.89 | 0.90 | 0.89 (0.87 to 0.91) | 0.88 | 0.81 | scored |
| student 1a | TF-IDF (word 1-2, char 2-5) + logistic regression per tag | 24 | 777 | 0.89 | 0.87 | 0.87 (0.85 to 0.89) | 0.87 | 0.80 | scored |
| student 1b | student 1a features + the 24 regex v0.2 tags as inputs, ship candidate (best validation macro F1) | 24 | 777 | 0.91 | 0.91 | 0.91 (0.89 to 0.92) | 0.90 | 0.83 | scored |
| shipped mix | student 1b on 4 tags (h24, icu, ob_delivery, ventilator), regex v0.2 on the rest | 24 | 777 | 0.94 | 0.90 | 0.91 (0.90 to 0.93) | 0.92 | 0.86 | shipped |

Macro averages weight each evidence tag equally. Micro F1 pools every (fact, tag) decision. Exact match is the share of gold facts whose full evidence-tag set (over the tags scored) is predicted exactly. Intervals come from a stratified bootstrap (2,000 resamples within each sampling stratum). The v0.1 and v0 rows are history: the same numbers the v0.1 page reported, recomputed here on the 429 rows that existed then.

![Regex precision and recall per tag](figures/wp3_regex_gold_scores.png)

## Per tag

| Tag | New in v0.2 | Gold positives | Regex precision | Regex recall | Regex F1 (95% CI) | Regex v0.1 F1 (429 rows) | Starter F1 | Teacher F1 | Student F1 (student 1b) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `ob_delivery` |  | 46 | 0.47 (28/59) | 0.61 (28/46) | 0.53 (0.43 to 0.62) | 0.61 |  | 0.67 | 0.75 (ships) |
| `csection` |  | 26 | 1.00 (26/26) | 1.00 (26/26) | 1.00 (1.00 to 1.00) | 1.00 |  | 1.00 | 1.00 |
| `blood` |  | 30 | 0.97 (28/29) | 0.93 (28/30) | 0.95 (0.89 to 1.00) | 0.97 |  | 0.97 | 0.95 |
| `anesthesia` |  | 33 | 0.85 (33/39) | 1.00 (33/33) | 0.92 (0.84 to 0.97) | 0.90 |  | 0.79 | 0.80 |
| `ot` |  | 29 | 1.00 (29/29) | 1.00 (29/29) | 1.00 (1.00 to 1.00) | 1.00 |  | 0.98 | 0.98 |
| `h24` |  | 37 | 0.97 (30/31) | 0.81 (30/37) | 0.88 (0.81 to 0.95) | 0.91 |  | 0.93 | 0.96 (ships) |
| `nicu` |  | 42 | 0.94 (34/36) | 0.81 (34/42) | 0.87 (0.79 to 0.94) | 0.91 |  | 0.88 | 0.90 |
| `dialysis` |  | 47 | 0.96 (47/49) | 1.00 (47/47) | 0.98 (0.95 to 1.00) | 0.98 |  | 0.93 | 0.97 |
| `dialysis_unit` |  | 28 | 1.00 (24/24) | 0.86 (24/28) | 0.92 (0.86 to 0.98) | 0.92 |  | 0.86 | 0.94 |
| `icu` |  | 64 | 0.94 (44/47) | 0.69 (44/64) | 0.79 (0.71 to 0.86) | 0.85 |  | 0.87 | 0.91 (ships) |
| `ventilator` |  | 41 | 0.93 (28/30) | 0.68 (28/41) | 0.79 (0.70 to 0.87) | 0.84 |  | 0.93 | 0.93 (ships) |
| `emergency` |  | 74 | 0.97 (68/70) | 0.92 (68/74) | 0.94 (0.90 to 0.98) | 0.89 |  | 0.81 | 0.86 |
| `scheme` |  | 28 | 0.87 (27/31) | 0.96 (27/28) | 0.92 (0.84 to 0.98) | 0.93 |  | 0.95 | 0.92 |
| `neonatal_resp` | yes | 20 | 1.00 (20/20) | 1.00 (20/20) | 1.00 (1.00 to 1.00) |  | 0.82 | 0.93 | 1.00 |
| `cardiac_surgery` | yes | 21 | 1.00 (20/20) | 0.95 (20/21) | 0.98 (0.91 to 1.00) |  | 0.86 | 0.88 | 0.92 |
| `cath_lab` | yes | 27 | 1.00 (23/23) | 0.85 (23/27) | 0.92 (0.85 to 0.98) |  | 0.87 | 0.91 | 0.96 |
| `ccu` | yes | 27 | 1.00 (27/27) | 1.00 (27/27) | 1.00 (1.00 to 1.00) |  | 1.00 | 0.88 | 0.98 |
| `mri` | yes | 26 | 1.00 (26/26) | 1.00 (26/26) | 1.00 (1.00 to 1.00) |  | 1.00 | 1.00 | 0.98 |
| `ct` | yes | 34 | 0.97 (34/35) | 1.00 (34/34) | 0.99 (0.95 to 1.00) |  | 0.91 | 0.97 | 0.97 |
| `trauma` | yes | 41 | 0.97 (32/33) | 0.78 (32/41) | 0.86 (0.77 to 0.94) |  | 0.86 | 0.90 | 0.84 |
| `neurosurgery` | yes | 22 | 1.00 (9/9) | 0.41 (9/22) | 0.58 (0.36 to 0.76) |  | 0.61 | 0.62 | 0.65 |
| `burns` | yes | 25 | 0.94 (17/18) | 0.68 (17/25) | 0.79 (0.65 to 0.90) |  | 0.92 | 1.00 | 0.94 |
| `chemo` | yes | 20 | 0.91 (20/22) | 1.00 (20/20) | 0.95 (0.87 to 1.00) |  | 0.95 | 0.91 | 0.89 |
| `radiotherapy` | yes | 16 | 0.74 (14/19) | 0.88 (14/16) | 0.80 (0.62 to 0.92) |  | 0.76 | 0.77 | 0.75 |

Precision and recall show their counts in brackets. Most F1 intervals overlap, so the order of the middle tags is not reliable. No tag's F1 interval lies entirely below all the others. `csection`, `ot`, `neonatal_resp`, `ccu`, `mri` made no errors on the gold set, so their intervals have zero width. That does not make them perfect; it means the sample found no error. `neonatal_resp` (20), `cardiac_surgery` (21), `neurosurgery` (22), `chemo` (20), `radiotherapy` (16) have fewer than 25 gold positives, so their intervals are wide.

## What recipe v0.2 changed

Recipe v0.2 adds 11 evidence tags for the nine new service lines and leaves the 13 v0.1 patterns alone: on all 777 gold rows the v0.2 tagger gives exactly the v0.1 output on the v0.1 tags. Each new tag started from a loose pattern in the recipes doc, and its final regex was written by reading matches and near misses in `silver/facts.parquet`, not gold labels. Against the starter patterns, the new tags' macro F1 moves from 0.87 to 0.90; F1 rises on `neonatal_resp` (0.82 to 1.00), `cardiac_surgery` (0.86 to 0.98), `cath_lab` (0.87 to 0.92), `ct` (0.91 to 0.99), `radiotherapy` (0.76 to 0.80), and falls on `neurosurgery` (0.61 to 0.58), `burns` (0.92 to 0.79). Read this comparison with care: the added gold rows were drawn from starter-pattern hits and near misses, so the sample is built around the starter's decisions, and the starter has no negation, list or referral handling at all. The drops are where the list and staff rules suppress names the labelers counted (next section).

## Where the regex fails: the three worst tags

### `ob_delivery`: F1 0.53, precision 0.47, recall 0.61

The pattern is the v0.1 one, unchanged in v0.2, and it scores lower than in v0.1 (0.61 on the 429 v0.1 rows) because the 348 new rows hold many department and specialty lists. Precision: the bare word 'obstetric' fires on lists, department names and doctors' qualifications that never say the facility delivers babies. 30 of 31 false positives have 'obstetric' as the only trigger, and 16 of the 31 come from the new rows, 14 of those department or specialty lists. Recall: the gold set counts a caesarean as a delivery, and this pattern has no caesarean branch, so 15 of 18 misses are caesarean texts. The caesarean misses change no EmOC grade, because a caesarean fact already gives grade 2. The false positives can lift a facility to grade 1 (claimed) with no delivery statement.

- False positive (regex tags it, gold does not): "multispecialty hospital with departments including ophthalmology, orthopedics, neurosurgery, obstetrics and gynecology, ent, general medicine, and pulmonology"
- False positive (regex tags it, gold does not): "gynecology and obstetrics services provided at shri hari hospital"
- Missed (gold tags it, regex does not): "performs caesarean delivery"

### `neurosurgery`: F1 0.58, precision 1.00, recall 0.41

Recall only: the regex made no false positive and missed 13 of 22. It treats 'neurosurgery' and 'spine surgery' as specialty names, which tag only outside a list of departments and outside a staff biography. 8 misses are lists, 2 are facts the staff cue blocks (it includes 'specializes in', which also catches a facility's own 'specializes in neurosurgical care'), and 3 use wording no pattern covers ('spinal fracture fixation'). Part of this is the gold set, not the regex. The written rule (TAGGING_RULES in vf.recipes) says a specialty word in a list is a claim, not a service, yet of the 26 gold texts where neurosurgery appears only inside a list, 8 are labeled neurosurgery. The four labelers split on this: labeler 1 marked 2 of 7, labeler 2 marked 0 of 8, labeler 3 marked 0 of 4, labeler 4 marked 6 of 7. So this F1 mixes regex misses with label disagreement, and the true recall on texts that follow the rule is higher.

- Missed (gold tags it, regex does not): "specializes in neurosurgical care"
- Missed (gold tags it, regex does not): "specializes in minimally invasive spine surgery"
- Missed (gold tags it, regex does not): "spinal fracture fixation"

### `ventilator`: F1 0.79, precision 0.93, recall 0.68

Recall: the pattern needs the word 'ventilator' (or 'ventilatory'), so 'mechanical ventilation', 'non-invasive ventilation' and 'neonatal ventilation' are missed (11 of 13 misses). The new rows add 6 misses, 5 of them 'neonatal ventilation', which is why the tag falls from 0.84 on the v0.1 rows. The regex tags 5 of those 5 `neonatal_resp`, which the `nicu` line accepts in place of `ventilator`, so they cost no NICU grade. The other misses can cost the `icu` line its corroboration, which needs `icu` and `ventilator`. One gold row labels bare 'bipap' as ventilator, which the v0.1 labeling rule excludes (CPAP, BiPAP or high-flow oxygen alone is not). Precision: it fires when ventilators are a stated need rather than equipment on hand, and on an ambiguous photo caption (2 false positives).

- Missed (gold tags it, regex does not): "provides neonatal ventilation support"
- Missed (gold tags it, regex does not): "non-invasive mechanical ventilation"
- False positive (regex tags it, gold does not): "need for monitors, ventilators, infusion pumps, and defibrillators to augment icu facilities"

### Close behind

`burns` (F1 0.79, precision 0.94, recall 0.68): 8 misses: 6 are lists where 'burns management' or 'burn care' is a list item, and 1 is a fact the staff cue blocks through 'specializes in'; 1 ('dedicated burns operation theatre') matches no pattern. The regex treats that wording as a specialty name; the labelers read it as a service, and the written rule does not settle it. This is why `burns` scores below its starter pattern (0.92), which had no list rule.
Missed (gold tags it, regex does not): "specializes in burns management".

`icu` (F1 0.79, precision 0.94, recall 0.69): 15 of 20 misses are coronary or cardiac care units (CCU, ICCU), which the gold set counts as intensive care. The v0.1 `icu` pattern has no CCU branch; v0.2 tags them `ccu` (F1 1.00) but not `icu`, so a CCU-only facility gets no `icu` grade.
Missed (gold tags it, regex does not): "coronary care unit (ccu)".

Other recurring errors (every error is listed in `data/labels/regex_scores.json`):

| Tag | Error | Kind | Example from the gold set |
| --- | --- | --- | --- |
| `radiotherapy` | Planned equipment worded as 'will include' | false positive | "phase ii will include truebeam radiation therapy system" |
| `radiotherapy` | Techniques listed in a doctor's profile | false positive | "radiotherapy techniques listed in dr babita singh's profile include imrt, igrt, rapic arc, sbrt, srs, and image-guided brachytherapy." |
| `chemo` | A general medical statement, not a service | false positive | "chemotherapy can boost the effect of radiation therapy" |
| `cath_lab` | Coronary angiography in another word order | missed | "angiography of coronary arteries" |
| `cath_lab` | Transcatheter valve procedures and 'interventional cardiology' | missed | "interventional cardiology services including tavi and cabg" |
| `trauma` | Casualty handling trauma, with no trauma-unit word | missed | "has a 24/7 casualty block to handle trauma" |
| `burns` | A research award, not a burn service | false positive | "awarded the young investigator award at the international society of burn injuries (isbi)." |
| `dialysis_unit` | Unit words outside the list ('dialysis ward', 'dialysis clinic') | missed | "dialysis ward" |
| `h24` | The v0.1 negation cue drops a hedged statement the gold set counts | missed | "24x7 helpline (not listed? will not mention)" |
| `emergency` | Trauma rehabilitation is not emergency care | false positive | "major trauma rehabilitation" |
| `anesthesia` | Local or topical anaesthesia for a minor procedure | false positive | "cataract surgery performed under topical anesthesia with a 2.2 mm corneal incision" |
| `scheme` | 'Ayushman' as a hospital name | false positive | "ayushman hospital offers ivf and surrogacy services" |

## History: recipe v0.1

Recipe v0.1 was scored on 429 gold rows and 13 tags. Those numbers are unchanged and recomputed here: macro F1 0.90 (0.88 to 0.92), micro F1 0.89. Its three weakest tags were `ob_delivery` (0.61), `ventilator` (0.84), `icu` (0.85).

- `ob_delivery` (F1 0.61, precision 0.63, recall 0.59): Precision: the bare word 'obstetric' fires on specialty lists, department names and doctors' qualifications that never say the facility delivers babies. 14 of 15 false positives are texts where 'obstetric' is the only trigger. Recall: no caesarean branch, so 16 of 18 misses are caesarean texts. The plural 'normal deliveries' also slips past 'normal delivery'. Example: missed "facilitates normal deliveries".
- `ventilator` (F1 0.84, precision 0.92, recall 0.77): Recall: 'mechanical ventilation' and 'non-invasive ventilation' have no 'ventilator' word (6 of 7 misses). Precision: stated needs and an ambiguous photo caption. Example: missed "non-invasive mechanical ventilation".
- `icu` (F1 0.85, precision 0.91, recall 0.80): Recall: ICU types outside the pattern (ICCU, ITU, PICU, 'critical care' stated as a service) are missed, 8 of 8 misses. Precision: 'intensive care' fires on a neonatal intensive care unit, and 'ICU' on an ICU ambulance and a research statistic (3 false positives). Example: missed "has 14 itu beds"; false positive "neonatal intensive care unit (nicu) level iii with 16 beds".

### What recipe v0.1 changed

Recipe v0.1 fixes four failure modes of v0 that changed facility grades (`docs/06_capability_recipes.md`, "Changes in v0.1"): a word boundary before "c section", VBAC wording ignored for `csection`, no `dialysis_unit` from "haemodialysis" alone, and no evidence tag on a fact with a negation or absence cue. On the 429 v0.1 gold rows macro F1 moves from 0.88 to 0.90, and the tags whose F1 changed are `csection` (0.90 to 1.00), `blood` (0.95 to 0.97), `h24` (0.95 to 0.91), `dialysis_unit` (0.80 to 0.92), `icu` (0.84 to 0.85), `emergency` (0.87 to 0.89). Read this gain as optimistic: the failure modes were found in WP3 part 1 before the gold set was drawn, and the patterns were written from the full fact table, not tuned on gold scores, but the gold set over-samples exactly these failure modes, so it rewards their fix more than a random sample would. The negation cue costs `h24` recall: hedged statements such as "24x7 helpline (not listed? will not mention)" lose their tag. `h24` is a bonus tag and never changes a grade.

Errors of v0 that v0.1 fixes, with a gold example of each:

| Tag | Error fixed | Kind | Example from the gold set |
| --- | --- | --- | --- |
| `csection` | Word-internal 'c section' | false positive | "dedicated paediatric section" |
| `csection` | VBAC is a vaginal birth after a past caesarean | false positive | "provides vaginal birth after cesarean (vbac) services as a supported option" |
| `dialysis_unit` | 'Haemodialysis' alone counted as a named unit | false positive | "provides hemodialysis treatment" |
| `emergency` | Negation was not handled | false positive | "does not operate an emergency department" |

### Known v0.1 failure modes, measured

Five strata of the v0.1 gold rows were built from failure modes found in WP3 part 1. Inside each one, this is how often the targeted regex tag is right, for v0.2 (the same as v0.1 on these tags) and for v0. A stratum where the regex no longer fires shows no precision.

| Stratum | Distinct texts in data | Sampled | Tag checked | Regex fired | Right | Precision | v0 fired | v0 right | v0 precision |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `fm_csection_wordpart` | 5 | 5 | csection | 0 | 0 |  | 5 | 0 | 0.00 |
| `fm_negation` | 77 | 14 | any evidence tag | 9 | 6 | 0.67 | 15 | 8 | 0.53 |
| `fm_haemodialysis_only` | 245 | 10 | dialysis_unit | 0 | 0 |  | 10 | 1 | 0.10 |
| `fm_neonatal_icu` | 644 | 10 | icu | 10 | 9 | 0.90 | 10 | 9 | 0.90 |
| `fm_obstetric_only` | 3,895 | 16 | ob_delivery | 16 | 2 | 0.12 | 16 | 2 | 0.12 |

- `fm_csection_wordpart`: regex tags csection but no real caesarean term (word-internal 'c section', e.g. 'paediatric section')
- `fm_negation`: regex tags something and a negation cue (\b(no|not|without|unavailable|lacks?|lacking|absence of|absent|neither|nor|never|non-availability|nil)\b) occurs within 40 characters of a regex match
- `fm_haemodialysis_only`: regex tags dialysis_unit only through h(a)emodialysis (no unit, machine, centre or nephrologist)
- `fm_neonatal_icu`: regex tags icu and the text has neonatal or paediatric intensive care words
- `fm_obstetric_only`: regex tags ob_delivery only through the word 'obstetric' (no maternity, labour room or ward, normal delivery, childbirth)

### Population estimates (v0.1 tags, v0.1 rows)

Only the v0.1 gold rows have design weights, so population estimates exist only for the 13 v0.1 tags. The 348 rows added for v0.2 were drawn from overlapping strata (starter hits, near misses, a random draw) with no inclusion probabilities, so there are no population estimates for the new tags. Since v0.2 leaves the v0.1 patterns unchanged, these hold for v0.2 too.

Over all fact texts, the `ob_delivery` regex is mostly wrong: weighted precision is 0.33, so about 67% of the distinct texts it tags have no delivery statement, and 98% of those are texts where 'obstetric' (a specialty, department or doctor's title) is the only trigger. The ventilator regex finds an estimated 59% of ventilation texts. Weighting each gold fact by its stratum's population over sample size estimates the rates over all 275,095 distinct fact texts. Weighted macro F1 is 0.88 (95% interval 0.86 to 0.91). Treat these as rough. The random stratum has 30 texts standing for 208,276 texts with no regex tag and no near-miss word. It held 0 evidence positives, which bounds misses there only loosely. So weighted recall is an optimistic estimate, and the bootstrap cannot show that.

| Tag | Weighted precision | Weighted recall | Weighted F1 | Est. texts with the tag | Est. texts the regex tags |
| --- | --- | --- | --- | --- | --- |
| `ob_delivery` | 0.33 | 0.73 | 0.46 | 2,395 | 5,254 |
| `csection` | 1.00 | 1.00 | 1.00 | 537 | 537 |
| `blood` | 0.97 | 0.95 | 0.96 | 740 | 725 |
| `anesthesia` | 0.75 | 1.00 | 0.85 | 1,248 | 1,672 |
| `ot` | 1.00 | 1.00 | 1.00 | 2,853 | 2,853 |
| `h24` | 0.94 | 0.95 | 0.95 | 4,002 | 4,058 |
| `nicu` | 0.95 | 0.94 | 0.94 | 977 | 967 |
| `dialysis` | 0.95 | 1.00 | 0.98 | 1,633 | 1,717 |
| `dialysis_unit` | 1.00 | 0.82 | 0.90 | 838 | 690 |
| `icu` | 0.93 | 0.83 | 0.88 | 3,524 | 3,159 |
| `ventilator` | 0.92 | 0.59 | 0.72 | 787 | 501 |
| `emergency` | 0.90 | 0.86 | 0.88 | 6,877 | 6,577 |
| `scheme` | 0.91 | 1.00 | 0.95 | 879 | 962 |

## Gold set

- 777 distinct `text_norm` values from `silver/facts.parquet`, in two parts.
- v0.1 part, 429 texts, sampled by `scripts/wp3_gold_sample.py` (seed 20261121). There are 30 strata: five known failure modes, texts the regex tags (grouped by their rarest tag), regex-untagged near misses ("kidney", "critical", "round-the-clock", ...), and a random draw from everything else. Phase 2: after phase 1 (396 texts) was labeled, six tags had fewer than 25 positives, and 33 more texts were drawn at random from those tags' strata (seed 20261122). These rows were later relabeled for the 11 new tags only; their v0.1 tags are unchanged, and 30 of them gained a new tag.
- v0.2 part, 348 texts, sampled by `scripts/wp3_gold_v02_sample.py` (seed 20261123) from texts not already in the gold set, in 23 strata: for each new tag, 20 texts its starter pattern matches and 8 near misses (a related word, no starter match), plus 40 at random. Merged by `scripts/wp3_gold_v02_merge.py`.
- Labeled by agents (`labeled_by = agent`), from blind worksheets that showed no regex output. The v0.2 labels came from four agent labelers, one per chunk of rows. An agent-labeled gold set is weaker evidence than a human one, and the four labelers do not always agree (see `neurosurgery` above). Hruday should spot-check it before the numbers go in the video, and the methods note must say who labeled it.
- Positives per evidence tag: `ob_delivery` 46, `csection` 26, `blood` 30, `anesthesia` 33, `ot` 29, `h24` 37, `nicu` 42, `dialysis` 47, `dialysis_unit` 28, `icu` 64, `ventilator` 41, `emergency` 74, `scheme` 28, `neonatal_resp` 20, `cardiac_surgery` 21, `cath_lab` 27, `ccu` 27, `mri` 26, `ct` 34, `trauma` 41, `neurosurgery` 22, `burns` 25, `chemo` 20, `radiotherapy` 16.
- Under the target of 25 positives: `neonatal_resp` (20), `cardiac_surgery` (21), `neurosurgery` (22), `chemo` (20), `radiotherapy` (16). The merge sidecar lists them under `evidence_tags_under_25`.
- Noise tags are labeled too, for scoring the teacher and student later: `reputation` 20, `location` 10, `staff_name` 24, `accessibility` 0, `photo_artifact` 7, `web_presence` 6, `other_clinical` 165. 36 texts have no tag at all.

Labeling rules for the v0.1 tags:

- Each text is labeled by its meaning, from a blind worksheet that showed only the text (no stratum, no regex output).
- Negated, absent, referred-out, planned, historical, hypothetical and 'not specified in the content' statements get no evidence tag.
- A specialty word is not a service: 'Nephrology department' is not dialysis, 'Obstetrics and gynaecology' is not ob_delivery, and 'critical care' in a specialty or department list is not icu. The exception is anesthesiology, where the specialty is the service.
- ob_delivery needs a delivery, childbirth, labour or maternity word. A caesarean is a delivery, so csection texts also get ob_delivery.
- dialysis_unit needs a named dialysis unit, centre, ward or clinic, dialysis machines or equipment, or a nephrologist. 'Haemodialysis' alone is dialysis only.
- icu covers ICU, MICU, SICU, CCU, ICCU, PICU, RICU, ITU, intensive care, and critical care beds or services stated as provided. A NICU alone is nicu, not icu. An HDU is neither.
- ventilator covers ventilators and mechanical ventilation, invasive or non-invasive, when named as ventilation. CPAP, BiPAP or high-flow oxygen alone is not.
- anesthesia excludes local or topical anaesthesia as part of a minor, dental or eye procedure, dental sedation, and epidural steroid injections for pain.
- emergency covers emergency, casualty, trauma and ambulance services, of any kind. scheme covers public schemes only (Ayushman Bharat/PM-JAY, CGHS, ECHS, ESI, state schemes), not private insurers, TPAs, or 'Ayushman' as a hospital name.
- A name counts as a claim ('Sai Clinic & Maternity' gets ob_delivery). Photo captions get photo_artifact, plus an evidence tag only when the unit is unambiguous.
- Noise tags (reputation, location, staff_name, accessibility, photo_artifact, web_presence, other_clinical) are labeled too, but the regex has none, so they are not scored here.

Rules for every evidence tag in v0.2 (`TAGGING_RULES` in `vf.recipes`; tag definitions are `TAG_DEFINITIONS` there):

- No evidence is not evidence of absence, and a stated absence is not evidence: a fact that says a service is not available, not provided, or not specified gets no evidence tag.
- Referral is not service: a fact that refers patients elsewhere, outsources the service, or names a tie-up gets no tag for that service.
- Plans and needs are not service: proposed, planned, under construction, coming soon, or a stated need for equipment get no tag.
- A specialty word in a list of departments or specialties is a claim, not a service: "departments include cardiology, neurosurgery, oncology" is not neurosurgery. A named unit or procedure in a list still counts ("ICU, CCU and burns unit").
- A staff biography ("Dr X, neurosurgeon with 15 years of experience") is not evidence that the facility offers the service.
- A fact may carry several tags ("24x7 ICU with ventilator" is h24, icu, ventilator).

<!-- wp4:start -->
## WP4: student tagger

The student ships on 4 of 24 evidence tags (`ob_delivery`, `h24`, `icu`, `ventilator`); regex v0.2 stays on the other 20. Over all 24 tags the shipped mix scores macro F1 0.91 (0.90 to 0.93) against regex 0.89 (0.87 to 0.91); paired difference +0.017 to +0.029. The ship candidate alone (student 1b) scores 0.91, the teacher 0.89: the student learned the teacher, mistakes included, and the regex remains better on the tags where the teacher is worse.

Training: 23,441 teacher-labeled texts, validation 5,859 (random split by `text_norm`, seed 20261121). The gold set shares no text with the teacher set; it was not used for training, for the per-tag C and thresholds, or for choosing between students (the ship candidate is the one with the higher validation macro F1 on teacher labels, student 1a 0.908, student 1b 0.923). The gold set decides only which tags ship, by the rule below. Because that choice is made on gold, the shipped mix's gold score is slightly optimistic.

Ship rule: a tag uses the student only when the paired bootstrap 95% interval of student F1 minus regex F1 on gold (2,000 stratified resamples, same rows) lies entirely above 0. The stricter column asks the student's own F1 interval to sit entirely above the regex's. A second gate, added after the dry run showed the student tagging plastic surgery as burns, can only remove tags: the gold set is built around regex hits and near misses and cannot see what the student adds elsewhere, so for each tag that passes the gold rule 25 random texts the student tags and the regex does not (outside the teacher set) were read by hand, and the tag ships only if at least half are right (`data/models/wp4_student/population_check.json`).

| Tag | Gold positives | Regex F1 (95% CI) | Teacher F1 | Student 1a F1 | Student 1b F1 (95% CI) | student 1b minus regex (95% CI) | Strict rule | Additions right (hand read) | Ships |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `ob_delivery` | 46 | 0.53 (0.43 to 0.62) | 0.67 | 0.70 | 0.75 (0.66 to 0.83) | +0.13 to +0.31 | pass | 17/25 of 763 added | student |
| `csection` | 26 | 1.00 (1.00 to 1.00) | 1.00 | 0.98 | 1.00 (1.00 to 1.00) | +0.00 to +0.00 |  |  | regex |
| `blood` | 30 | 0.95 (0.89 to 1.00) | 0.97 | 0.92 | 0.95 (0.89 to 1.00) | -0.03 to +0.05 |  |  | regex |
| `anesthesia` | 33 | 0.92 (0.84 to 0.97) | 0.79 | 0.74 | 0.80 (0.69 to 0.90) | -0.21 to -0.05 |  |  | regex |
| `ot` | 29 | 1.00 (1.00 to 1.00) | 0.98 | 0.96 | 0.98 (0.94 to 1.00) | -0.06 to +0.00 |  |  | regex |
| `h24` | 37 | 0.88 (0.81 to 0.95) | 0.93 | 0.97 | 0.96 (0.91 to 1.00) | +0.02 to +0.14 |  | 23/25 of 371 added | student |
| `nicu` | 42 | 0.87 (0.79 to 0.94) | 0.88 | 0.88 | 0.90 (0.83 to 0.96) | -0.02 to +0.09 |  |  | regex |
| `dialysis` | 47 | 0.98 (0.95 to 1.00) | 0.93 | 0.90 | 0.97 (0.93 to 1.00) | -0.04 to +0.00 |  |  | regex |
| `dialysis_unit` | 28 | 0.92 (0.86 to 0.98) | 0.86 | 0.86 | 0.94 (0.87 to 1.00) | -0.07 to +0.10 |  |  | regex |
| `icu` | 64 | 0.79 (0.71 to 0.86) | 0.87 | 0.87 | 0.91 (0.87 to 0.95) | +0.04 to +0.20 | pass | 15/25 of 998 added | student |
| `ventilator` | 41 | 0.79 (0.70 to 0.87) | 0.93 | 0.93 | 0.93 (0.86 to 0.98) | +0.06 to +0.21 |  | 25/25 of 91 added | student |
| `emergency` | 74 | 0.94 (0.90 to 0.98) | 0.81 | 0.83 | 0.86 (0.79 to 0.91) | -0.15 to -0.04 |  |  | regex |
| `scheme` | 28 | 0.92 (0.84 to 0.98) | 0.95 | 0.87 | 0.92 (0.85 to 0.98) | -0.05 to +0.06 |  |  | regex |
| `neonatal_resp` | 20 | 1.00 (1.00 to 1.00) | 0.93 | 0.69 | 1.00 (1.00 to 1.00) | +0.00 to +0.00 |  |  | regex |
| `cardiac_surgery` | 21 | 0.98 (0.91 to 1.00) | 0.88 | 0.88 | 0.92 (0.82 to 1.00) | -0.14 to +0.00 |  |  | regex |
| `cath_lab` | 27 | 0.92 (0.85 to 0.98) | 0.91 | 0.90 | 0.96 (0.91 to 1.00) | +0.00 to +0.09 |  |  | regex |
| `ccu` | 27 | 1.00 (1.00 to 1.00) | 0.88 | 0.96 | 0.98 (0.94 to 1.00) | -0.06 to +0.00 |  |  | regex |
| `mri` | 26 | 1.00 (1.00 to 1.00) | 1.00 | 0.98 | 0.98 (0.94 to 1.00) | -0.06 to +0.00 |  |  | regex |
| `ct` | 34 | 0.99 (0.95 to 1.00) | 0.97 | 0.94 | 0.97 (0.93 to 1.00) | -0.05 to +0.00 |  |  | regex |
| `trauma` | 41 | 0.86 (0.77 to 0.94) | 0.90 | 0.82 | 0.84 (0.74 to 0.91) | -0.10 to +0.05 |  |  | regex |
| `neurosurgery` | 22 | 0.58 (0.36 to 0.76) | 0.62 | 0.70 | 0.65 (0.47 to 0.79) | -0.11 to +0.25 |  |  | regex |
| `burns` | 25 | 0.79 (0.65 to 0.90) | 1.00 | 0.98 | 0.94 (0.86 to 1.00) | +0.05 to +0.28 |  | 1/25 of 320 added | regex |
| `chemo` | 20 | 0.95 (0.87 to 1.00) | 0.91 | 0.87 | 0.89 (0.78 to 0.98) | -0.15 to +0.02 |  |  | regex |
| `radiotherapy` | 16 | 0.80 (0.62 to 0.92) | 0.77 | 0.76 | 0.75 (0.58 to 0.88) | -0.16 to +0.05 |  |  | regex |

Grade counts per line (none / claimed / supported / corroborated), regex v0.2 against the shipped mix:

| Line | Regex v0.2 | Shipped mix | Grade 2+ regex | Grade 2+ mix | Facilities changed | Shift over 10% |
| --- | --- | --- | --- | --- | --- | --- |
| `emoc` | 5,311 / 4,246 / 391 / 41 | 5,325 / 4,232 / 391 / 41 | 432 | 432 | 42 |  |
| `nicu` | 8,513 / 770 / 532 / 174 | 8,513 / 770 / 528 / 178 | 706 | 706 | 4 |  |
| `dialysis` | 7,631 / 1,322 / 556 / 480 | 7,631 / 1,322 / 556 / 480 | 1,036 | 1,036 | 0 |  |
| `icu` | 7,252 / 685 / 1,677 / 375 | 7,173 / 543 / 1,850 / 423 | 2,052 | 2,273 | 418 | claimed, supported, corroborated |
| `cardiac_surgery` | 9,075 / 360 / 442 / 112 | 9,075 / 360 / 435 / 119 | 554 | 554 | 9 |  |
| `cath_lab` | 6,834 / 2,464 / 235 / 456 | 6,834 / 2,464 / 210 / 481 | 691 | 691 | 41 | supported |
| `mri` | 6,663 / 2,392 / 304 / 630 | 6,663 / 2,392 / 304 / 630 | 934 | 934 | 0 |  |
| `ct` | 6,537 / 2,030 / 446 / 976 | 6,537 / 2,030 / 446 / 976 | 1,422 | 1,422 | 0 |  |
| `trauma` | 4,952 / 3,905 / 698 / 434 | 4,952 / 3,905 / 669 / 463 | 1,132 | 1,132 | 41 |  |
| `burns` | 8,261 / 1,583 / 75 / 70 | 8,261 / 1,583 / 71 / 74 | 145 | 145 | 6 |  |
| `neurosurgery` | 7,681 / 1,106 / 848 / 354 | 7,681 / 1,106 / 805 / 397 | 1,202 | 1,202 | 51 | corroborated |
| `oncology` | 7,982 / 1,565 / 277 / 165 | 7,982 / 1,565 / 277 / 165 | 442 | 442 | 0 |  |

- `icu` (418 facilities change; claimed -21%, supported +10%, corroborated +13%): the student adds `icu` to 998 distinct texts the regex misses, mostly critical care stated as a service, critical care beds, ICCU, ITU, MICU, PICU and the plural 'icus', and removes it from 270, mostly NICU-only texts (a NICU alone is `nicu`, not `icu`) and plans. 306 facilities rise to grade 2 or 3 and 85 fall below 2. Of 12 sampled 2 -> 0 moves, all 12 rested only on a NICU text. Of 12 sampled 0 -> 2 moves, about 9 or 10 are right; the rest rest on 'critical care' as a department name or a directory listing. The hand read of additions found 15 of 25 right, so some new grade 2 ICU facilities are wrong in the same way. 27 more move 2 -> 3 because the student finds ventilation the regex misses ('non-invasive ventilation', 'mechanical ventilation'; 25 of 25 additions right).
- `cath_lab` (supported -11%, corroborated +6%): 33 facilities move 2 -> 3 and 8 move 3 -> 2. Corroboration needs a cath lab fact plus (`ccu` or `icu`) plus (`h24` or `emergency`); 31 of the 33 gain it through a new `icu` tag and 2 through a new `h24` tag. The supported count is small (235), so 25 net moves exceed 10%.
- `neurosurgery` (corroborated +12%): 47 facilities move 2 -> 3, all through a new `icu` tag (the rule needs neurosurgery, `icu` and `ct`); 4 move 3 -> 2 where the student removed an `icu` tag.

Cost and throughput: the teacher labeled 29,250 texts in about 6 minutes (81 texts per second with 8 parallel requests) for $0.53, $0.0181 per 1,000 texts; labeling all 275,095 distinct fact texts that way would cost about $4.98 and 57 minutes. The ship candidate trained in 24 seconds and tagged all 275,095 texts in 22 seconds (12,567 texts per second) on a laptop CPU, at no API cost. The regex takes 11 seconds for the same texts.

- The gold set is agent-labeled (four labelers who split on specialty names in lists), and each tag has 16 to 74 positives. A difference of a few points means nothing; only the paired intervals decide.
- The student learns the teacher's reading of each tag. Where the teacher is clearly worse than the regex on gold (`anesthesia`, `emergency`, `ccu` in the teacher report), the student is too, and those tags keep the regex.
- The teacher labeled a selection that over-draws regex hits and near misses (21,874 hits, 3,426 near misses, 2,000 common-tag hits, 2,000 random). The student is applied to all 275,095 distinct texts, most of which look like the random stratum; its precision there is not measured by the gold set beyond the gold set's own random rows.
- Student 2 (a fine-tuned encoder) was not run: torch is not installed in the project environment, the linear student already reproduces the teacher labels at validation macro F1 0.92, and an encoder would learn the same teacher, which does not beat the regex on most tags. It remains the next step for `neurosurgery`, `trauma` and `radiotherapy`, where no tagger is good.
- Facility examples for every line whose grades moved are in gold/model_a_report.json under grade_change_examples.

Reproduce: `.venv/bin/python scripts/wp4_student.py` (regex v0.2 grades: `.venv/bin/python scripts/wp3_regex_baseline.py --out-dir <folder>`). Generated 2026-11-22T03:06:11Z.
<!-- wp4:end -->

## Caveats

- These are fact-level scores on distinct texts. They are not facility-level grade accuracy: one wrong fact can change a grade, and many grade-relevant errors cancel out across a facility's other facts.
- The gold set cannot see contamination (a fact about a different organization filed under this facility). That is a facility-linkage problem, not a text-tagging one.
- Several labels are judgment calls; each has a note in `gold_facts.csv`. Where labelers split on a rule (specialty names in lists, 'burns management' as a list item, BiPAP as ventilation), the score mixes regex error with label disagreement. Adjudicating those rows would change `neurosurgery` and `burns` most.
- 5 tags have fewer than 25 gold positives; their scores rest on 16 to 22 positives each.

## Reproduce

```bash
.venv/bin/python scripts/wp3_gold_sample.py sample   # v0.1 design; labels come from the hand-labeled TSV
.venv/bin/python scripts/wp3_gold_v02_merge.py       # merge the v0.2 labeled chunks
.venv/bin/python scripts/wp3_score.py
```

Generated by `scripts/wp3_score.py` on 2026-11-22 02:34 UTC.
