# WP10: Stretch

Start only after WP9 has a complete first cut on real data. Each item is independent. Pick by what would most improve the video, and log the choice.

## A. Too few and too many C-sections (best "surprise" segment)

NFHS gives the C-section rate separately for births in private and in public facilities. Test whether the density of private obstetric facilities (or access to them) tracks the private C-section rate across districts, and flag districts with an overall rate under about 5% as lacking surgical access. One chart, one sentence in the video.

Care needed: correlation across districts is confounded by wealth. Control for the development proxies, and describe the result as an association.

## B. Dentists as an oral cancer screening network

4,192 facilities list dentistry. Find districts with high tobacco use (`w15_plus_who_use_any_kind_of_tobacco_pct`, `m15_plus_who_use_any_kind_of_tobacco_pct`), very low oral cancer exams (`women_age_30_49_years_ever_undergone_an_oral_cancer_exam_pct`), and good access to dental clinics. Output a ranked list.

## C. Claim plausibility model (a third model)

Predict whether a facility has a given evidence tag from features that do not contain that tag: type, operator, beds, doctors, other tags, number of source domains. Facilities whose claim has a low predicted probability are flagged as implausible, for example a one-doctor clinic that lists an ICU. Adds a trust score to the gold table.

## D. Who pays?

Cross the private share of nearby facilities with `hh_member_covered_health_insurance_pct` and `average_out_of_pocket_expenditure_per_delivery_in_a_public_fac`, plus facilities tagged `scheme`. Explains the "good access, poor outcome" quadrant.

## E. Dialysis and ICU brought up to the obstetrics standard

Full treatment for the second and third service lines in the app and the lists.

## F. Repair the 30 truncated facility rows

Re-attach the junk fragments to the rows they came from. Only worth it if it is clean.

## G. Gold table handoff

Package `silver/facilities.parquet` joined with tags and grades as a single table with a one-page data card, as something Virtue Foundation could load. Do not publish the raw data publicly without checking the listing's terms.
