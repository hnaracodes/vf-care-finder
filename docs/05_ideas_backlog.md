# Ideas backlog

The ten directions considered on Nov 21, strongest first. Ideas 1 and 2 are the project. The rest are side findings or stretch work; see `tasks/WP10_stretch.md` before starting any of them.

A copy of this list lives in Hruday's Claude Doc "CMU Datathon: Virtue Foundation Dataset Brainstorm" (private link, agents cannot open it): https://claude.ai/artifact/R9pRDfgPhxQH8PDRXA8AJA

## 1. Care desert or data desert? (chosen)

Cross distance-based access with NFHS outcomes such as `institutional_birth_5y_pct`, and read each district by quadrant. Full method in `04_thesis_and_plan.md`.

## 2. Claims versus evidence (chosen, underpins idea 1)

Grade each capability as claimed, supported, or corroborated, using recipes such as emergency obstetrics = C-section plus blood plus anesthesia plus round-the-clock cover. The gaps are large: 4,585 facilities claim an obstetric specialty and 443 mention a C-section.

Correction to the original brainstorm: it said per-fact provenance would give corroboration for free. It does not. `source_types` is not aligned with the facts. Use the number of distinct domains in `source_urls` instead.

## 3. Too few and too many C-sections

NFHS splits the C-section rate by private and public facility. Test whether private obstetric density tracks the private C-section rate, and flag districts under about 5% overall as lacking surgical access. A good "surprise" segment for the video.

## 4. Dentists as an oral cancer screening network

Dentistry is the fourth most common specialty by facility count (4,192 facilities). Find districts with high tobacco use, near-zero oral cancer exams, and many dental clinics. Cheap, specific, and unexpected.

Correction to the original brainstorm: it said third most common. That was by raw mentions, which repeat within a row.

## 5. Who pays?

Cross the private share of facilities with `hh_member_covered_health_insurance_pct` and out-of-pocket delivery cost, plus the roughly 800 facilities that mention Ayushman, PMJAY, or CGHS. Shows where scheme empanelment would matter most. This also explains the "good access, poor outcome" quadrant.

## 6. Diabetes to dialysis

Compare the high blood sugar burden with distance to the 1,046 facilities that mention dialysis. This is the dialysis service line in the main plan.

## 7. Referral router

Given a PIN and a need, return the nearest facilities with graded evidence and quoted facts, and abstain when the evidence is weak. The map's "patient mode" in WP8 is a lightweight version of this.

## 8. Mission host finder

Find facilities with an operating theatre, anesthesia, and beds that lack a specialty the district needs. It has to be inferred, because `acceptsVolunteers` is true for only 21 rows.

## 9. Gold table for Virtue Foundation

A cleaned table: deduped, states normalized, each fact tagged as clinical, logistics, or marketing. The fact tagger in WP4 produces most of this as a by-product. Worth one sentence in the video.

## 10. Inverse care law chart

Plot advanced-capability access against need across districts. A single supporting chart, if WP6 has time.
