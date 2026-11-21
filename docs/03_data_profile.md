# Data profile

What the data looks like, measured on Nov 21, 2026. Numbers marked (local) were computed from the files in `data/raw`. Numbers marked (Databricks) came from SQL against the shared tables and include the 30 truncated rows; the queries are in `sql/profile_queries.sql`.

Read the "Traps" section even if you skip the rest.

## Facilities at a glance (local, 9,970 intact rows)

| Measure | Value |
| --- | --- |
| Real facility rows | 10,000 (9,970 intact, 30 truncated) |
| Junk rows to drop | 88 |
| Facility type | hospital 5,621, clinic 3,770, dentist 488, doctor 21, farmacy 10, pharmacy 2, nursing_home 1, null 57 |
| Operator | private 8,812, public 469, government 2, null 687 |
| Coordinates inside India | 9,964 |
| 6-digit PIN | 9,772 of 10,000, across 3,195 distinct PINs |
| Distinct city strings | 1,628 |
| Distinct state strings | 234 (dirty) |
| Doctor count present | 3,630 |
| Bed count present | 2,516 |
| `acceptsVolunteers` true | 21 |
| Duplicate ids | 11 `unique_id` values appear twice; 11 `cluster_id` values have 2 rows |

The dataset is a sample of web-visible, mostly private facilities. Public facilities are 4.7% of rows. The NFHS column `institutional_birth_in_public_facility_5y_pct` shows how much of real care the public system delivers, so compute the national figure in WP2 and put the two numbers side by side.

## Where the facilities are

Top state strings (Databricks, raw `address_stateOrRegion`): Maharashtra 1,575, Gujarat 981, Uttar Pradesh 919, Tamil Nadu 780, Karnataka 529, Kerala 483, West Bengal 477, Punjab 469, Haryana 462, Telangana 421, Rajasthan 407, Delhi 337, Andhra Pradesh 329, Madhya Pradesh 302, Bihar 258, Jharkhand 156, Chhattisgarh 140, Uttarakhand 124, Assam 117, Odisha 111.

The state field also contains cities (Thane, Navi Mumbai, Pune) and variants (Tamilnadu, Up, U.p., Orissa). Do not group by it.

District coverage through an exact PIN join (Databricks; each PIN assigned to its most common district):

| Measure | Value |
| --- | --- |
| Facilities matched to a PIN | 9,568 |
| Districts in the PIN directory | 755 |
| Districts with at least one facility | 553 |
| Districts with zero facilities | 202 |
| Median facilities per covered district | 5 |
| Covered districts with 2 or fewer | 182 |
| Covered districts with 5 or fewer | 299 |
| Districts with 50 or more | 48 |
| Facilities in the top 20 districts | 3,623 (38% of matched) |
| Covered districts with no hospital-type facility | 45 |
| Covered districts with no public-tagged facility | 300 |

Top districts: Pune 325, Ahmadabad 320, Bengaluru Urban 291, Mumbai Suburban 273, Chennai 272, Thane 220, Hyderabad 205, Jaipur 187, Kolkata 184, Surat 161, Lucknow 147, Patna 144.

## Specialties (local, deduped per facility)

Number of facilities listing each specialty: internalMedicine 6,847, familyMedicine 5,178, gynecologyAndObstetrics 4,497, dentistry 4,192, orthopedicSurgery 3,526, pediatrics 3,438, generalSurgery 3,198, cardiology 3,011, radiology 2,999, ophthalmology 2,828, otolaryngology 2,702, urology 2,628, gastroenterology 2,422, dermatology 2,330, pathology 2,251, nephrology 2,156, emergencyMedicine 2,085, neurology 1,961, reproductiveEndocrinologyAndInfertility 1,848, endocrinologyAndDiabetesAndMetabolism 1,840, endodontics 1,800, neurosurgery 1,693, orthodontics 1,679, criticalCareMedicine 1,677, oralAndMaxillofacialSurgery 1,670.

Others relevant to the plan: anesthesia 1,323, neonatologyPerinatalMedicine 1,255, maternalFetalMedicineOrPerinatology 740, obstetricsAndMaternityCare 358.

Raw mention counts are much higher (internalMedicine appears 68,026 times) because labels repeat inside a row. Never report mention counts as facility counts.

## Claims versus evidence (local, regex over facts and description)

This is the core finding so far. A specialty tag is a claim. A specific procedure or piece of equipment in the text is evidence.

| Service line | Claim | Facilities | Evidence | Facilities | Claim and evidence |
| --- | --- | --- | --- | --- | --- |
| Emergency obstetrics | OB-GYN, obstetrics, or maternal-fetal specialty | 4,585 | Mentions a C-section | 443 | 430 |
| Dialysis | Nephrology specialty | 2,156 | Mentions dialysis | 1,046 | 839 |
| Intensive care | Critical care specialty | 1,677 | Mentions an ICU | 2,048 | not computed |
| Intensive care, stricter | Mentions an ICU | 2,048 | Also mentions a ventilator | 380 | 380 |

Keyword counts (facilities whose text matches; patterns are in `06_capability_recipes.md`):

| Keyword | Facilities | Keyword | Facilities |
| --- | --- | --- | --- |
| ICU | 2,048 | Operating theatre | 2,043 |
| NICU | 703 | Anesthesia | 1,159 |
| Dialysis | 1,046 | Maternity or delivery | 1,578 |
| C-section | 443 | Emergency or trauma | 3,796 |
| Blood bank | 537 | Open 24 hours | 4,107 |
| Ventilator | 442 | Ultrasound | 2,025 |
| Ayushman, PMJAY, or CGHS | 797 | | |

Also measured in Databricks (10,000 rows): MRI 946, CT 1,495, cath lab or angioplasty 625, chemotherapy or radiotherapy 401, ambulance 1,184, insurance or cashless 1,663, cataract 777, X-ray 2,407, telemedicine 638, ratings or reviews 733, wheelchair 1,440.

A missing keyword is not proof of a missing capability. It means the scraped pages did not say so. Every chart and sentence must say "no evidence of", not "does not have".

## Two example records

Dhanuka Neurocare (clinic, private, Ludhiana, Punjab, PIN 141001). Clean and specific:

- capability: "Neurology clinic with a neurologist on staff", "Neurodiagnostic testing capabilities including EEG/Video EEG, NCV/NCS, EMG, Evoked Potentials, and ECG", but also "Has 28 years of experience in healthcare" and a staff name.
- equipment: "Digital EEG/Video EEG system", "Electromyography (EMG) equipment".
- specialties repeat: epilepsyNeurology twice, familyMedicine three times, neurology twice.

Satellite Government Hospital (public, Kota, Rajasthan). Thin and noisy:

- description: "Open 24 Hrs".
- capability: "Rated 4.3 on JustDial with 72 ratings", "Provides emergency medical care", "Maintains an unofficial Facebook page".
- equipment: "Wheelchair-accessible parking available", "Hospital bed with rails", "Medical monitor displaying waveforms". These look like captions of photos, not an equipment list.
- specialties: internalMedicine repeated about ten times.

Public hospitals tend to look like the second record. That is part of why the dataset under-represents public care.

## Traps

1. Counting facilities per district measures web presence, not care. 202 districts show zero facilities. Use distance from post offices to the nearest graded facility instead.
2. The literal string `null` is the missing value. `df[col].notna()` is true for every cell.
3. Drop the 88 junk rows. Flag the 30 truncated rows; they have no coordinates or facts.
4. Dedupe specialties per facility before counting.
5. `source_types` is not per-fact provenance. Use distinct domains in `source_urls` for corroboration, and expect some unrelated URLs.
6. `address_stateOrRegion` has 234 values. Derive geography from coordinates or the PIN instead.
7. A PIN join fans out: 1,478 PINs map to more than one district.
8. PIN directory coordinates include `NA`, malformed strings, and about 2,600 points outside India.
9. NFHS `*` is missing, not zero. Parenthesized values are low-sample estimates: carry a flag.
10. NFHS has 706 districts, the PIN directory 755, and names disagree. Only 565 pairs match exactly.
11. NFHS is from 2019 to 2021. The facility pages were updated as late as December 2025. Say so wherever the two are compared.
12. The export cannot distinguish SQL NULL from the string `null`. It does not matter in practice, since both mean missing.
