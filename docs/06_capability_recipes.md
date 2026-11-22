# Capability recipes (v0.1)

How facts become evidence tags, and how tags become a facility grade. This is a starting spec. WP3 owns it: change it when the data says so, bump the version, and log the change in `08_decisions_log.md`.

Recipe version: `v0.1` (Nov 21, 2026). v0.1 changes four things in the v0 tagger, listed under "Changes in v0.1" below. The grade rules are unchanged.

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
