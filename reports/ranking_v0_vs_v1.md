# Ranking v0 against v1: blind side-by-side on 26 scenarios

Generated 2026-11-22 from the blind judgments in `data/labels/v0v1_blind/judgments.csv`, unblinded with `data/labels/v0v1_blind/key.json` (seed 20261122). Answers: v0 from `reports/ranking_scenarios_raw.json` (git d597c74, score v0 D039, recipe v0.2), v1 from `reports/ranking_scenarios_v1_raw.json` (git 958608c with uncommitted code, score spec v1-final D048, recipe v0.3).

## Findings

- **v1 is not shown to be better than v0. The two are about even.** On marks, v1 has 12 right, 4 defensible, 1 wrong and 9 correct abstentions; v0 has 11, 4, 1 and 10. On preference, the judges picked v1 in 8 scenarios, v0 in 5, and called 13 a tie. The sign test on the 13 untied scenarios gives p = 0.58 (two-sided), so the 8 to 5 split is well within chance.
- **v1 makes one new serious error, in Gadchiroli.** v0 abstained correctly. v1's 2-hour EmOC window reaches 77 km to "GMC Chandrapur" and recommends it. That record is a merge: its C-section quote comes from IGGMC Nagpur and its blood bank and bed count from Sir JJ Hospital, Mumbai. The merge flag is only "minor", so the record passes the gate and the evidence allowance moves it up.
- **v1 fixes v0's one serious error, in Chandigarh.** v0 sent a brain-surgery patient 27 to 37 km out of the tricity because its score rewarded corroborated records over distance. v1's window and capped 15-minute allowance keep the first pick 11 km away.
- **Two causes explain most of v1's losses.** (1) The evidence allowance treats a corroborated record with a minor merge or location flag the same as a clean one, so flagged records move ahead of clean ones (Lucknow, Gadchiroli). v0's 0.2 trust weight held them back. (2) Recipe v0.3 is stricter, so several of v0's strongest picks (Sahyadri for NICU, SAIMS and V One for cath lab) drop from grade 3 to grade 2. Among grade 2 records v1 orders by minutes alone, so a thin or merged record that is a few minutes closer beats a large, well-documented one (Pune, Indore, Bhubaneswar).
- **Some of v1's wins are not the ranker.** v1 also changed the location resolver (a named district now resolves to its town's head post office, not the district centre), removed duplicated quotes and added drive times. Nashik is won purely by the resolver, and the Jaipur PIN query mostly by display. Without the two presentation-driven wins (Jaipur PIN, trauma highway) the split is 6 to 5.

Caveats: 26 scenarios is a small set and 13 are ties, so this cannot separate two rankers that differ in a handful of cases. The judges were blind to the version name, but v1 is the only version that shows drive times, so a judge could tell the two answers apart in kind, though not which was the newer. One preference (`nonsense_place`) was not returned by its judge. Both answers there are the same "location not found" abstention, so it is recorded as a tie; ties do not enter the sign test. The comparison is of the whole v1 stack (ranker, recipe v0.3, resolver, card display), not the ordering rule alone.

## Method

- `scripts/v0v1_blind.py` built a blinded side-by-side of both answers per scenario: status, top 3 cards with name, straight-line km, drive minutes when given, quoted evidence and flags. No version, score parts or requirement text. Each scenario got its own coin flip for which version is X (v1 was X in 16 of 26).
- Three independent blind judging passes, one per third of the scenarios (`blind_0/1/2.json`), marked each answer `right`, `defensible`, `wrong`, `abstention_right` or `abstention_wrong`, checked the claims against the facility records, and stated a preference (X, Y or tie) with a reason. The judges are agents in this workflow, not clinicians.
- Unblinding joins each mark and preference to its version through `key.json`. Sign test: exact binomial on untied preferences, p = 0.5.
- Label bias check: X was preferred 7 times and Y 6 times, so no sign of a position effect.

## Marks per version

| Mark | v0 | v1 |
| --- | --- | --- |
| right | 11 | 12 |
| defensible | 4 | 4 |
| wrong | 1 | 1 |
| abstention_right | 10 | 9 |
| abstention_wrong | 0 | 0 |
| Total | 26 | 26 |

v1 has one fewer correct abstention because it answered Gadchiroli, where v0 abstained; that answer is the one marked wrong.

## Preference

| Preferred | Scenarios |
| --- | --- |
| v1 | 8 |
| v0 | 5 |
| tie | 13 (one imputed, see caveats) |

Sign test on the 13 untied: 8 against 5, two-sided p = 0.58 (one-sided, v1 better: p = 0.29).

## Per scenario

| Scenario | v0 mark | v1 mark | Preferred |
| --- | --- | --- | --- |
| brief_dialysis_jaipur | right | right | tie |
| brief_csection_lucknow | right | defensible | **v0** |
| brief_dialysis_rural_bihar | abstention_right | abstention_right | tie |
| brief_highrisk_maharashtra | defensible | right | v1 |
| brief_trauma_highway | right | right | v1 |
| line_nicu_pune | right | defensible | **v0** |
| line_icu_patna | defensible | defensible | v1 |
| line_cardiac_bhubaneswar | right | defensible | **v0** |
| line_cathlab_indore | right | right | **v0** |
| line_mri_coimbatore | right | right | tie |
| line_ct_guwahati | defensible | right | v1 |
| line_burns_nagpur | right | right | v1 |
| line_neuro_chandigarh | wrong | right | v1 |
| line_oncology_ahmedabad | right | right | tie |
| remote_dialysis_leh | abstention_right | abstention_right | tie |
| remote_emoc_tawang | abstention_right | abstention_right | tie |
| remote_cardiac_portblair | abstention_right | abstention_right | tie |
| remote_emoc_gadchiroli | abstention_right | **wrong** | **v0** |
| fallback_cataract_hyderabad | right | right | tie |
| fallback_antivenom_raipur | abstention_right | abstention_right | tie |
| ambiguous_aurangabad | abstention_right | abstention_right | tie |
| ambiguous_bilaspur | abstention_right | abstention_right | tie |
| disambiguated_aurangabad_bihar | abstention_right | abstention_right | tie |
| pin_dialysis_302001 | right | right | v1 |
| coords_icu_lucknow | defensible | right | v1 |
| nonsense_place | abstention_right | abstention_right | tie (imputed) |

## Where v1 is marked wrong or judged worse

Each explanation below comes from rerunning the shipped v1 ranker (`vf.rank.rank`, k = 10) on the same resolved location and comparing grades in `gold/capability_grades.parquet` (v0.2) and `gold/capability_grades_v03.parquet` (v0.3).

### remote_emoc_gadchiroli: v1 wrong, v0 abstained correctly

- What v1 did: recommended "Government Medical College And Hospital Chandrapur", 77 km, about 85 minutes, grade 3, flag `possible_merge` (minor).
- Why: two changes combined. The v1 EmOC window is the cited 2-hour indicator, which from Gadchiroli town reaches about 115 km straight-line; v0's radius was 50 km, so v0 found nothing. Inside the window, the only recommendable record is this one, and a minor flag does not block it. The judge found that its C-section quote is a CORDMILK trial protocol from IGGMC Nagpur and that its blood bank, 2,844 beds and website belong to Sir JJ Hospital, Mumbai.
- What it shows: the window is doing what it should. The weak point is that a merge flag graded "minor" lets borrowed evidence through. The judge's concern that 85 minutes may be past the reach limit does not hold under the spec: it is inside the 120-minute window.

### brief_csection_lucknow: v1 defensible, v0 right, v0 preferred

- What v1 did: BNK (6.7 min), Nova (10.8 min, `location_conflict` and `possible_merge`, both minor, confidence low), St. Joseph Lucknow (12.6 min, `possible_merge` minor).
- Why: all three are grade 3, so each gets the 15-minute allowance (order minutes -8.3, -4.3, -2.4). The clean grade 2 records v0 showed, Elite (10.1 min) and Krishna Medical Centre (10.2 min), drop to 6th and 7th, behind even Fatima and Pawar's Clinic. Nova and St. Joseph were grade 3 under v0 too, but v0's trust weight and confidence pushed them below Elite and Krishna.
- What it shows: the allowance applies to a corroborated record however doubtful its provenance. The judge found Nova's record placed in Rampur and St. Joseph's mixed with Ghaziabad and dental-clinic facts.

### line_nicu_pune: v1 defensible, v0 right, v0 preferred

- What v1 did: Dhanwantari (3.2 min, `possible_merge` minor, confidence low), KEM Pune (3.7 min), Inamdar (4.9 min).
- Why: recipe v0.3 requires newborn ventilation or a self-declared level III NICU (FBNC 2025) for grade 3. Sahyadri, Dhanwantari and DY Patil, v0's three cards, all drop from grade 3 to grade 2, and none of the ten nearest recommendable records is grade 3. With every candidate at grade 2, v1 orders by minutes alone, so Dhanwantari, a 12-bed merged record with a Chalisgaon PIN, comes first and Sahyadri (9.7 min, 900 beds, "NICU on-site") falls to 5th.
- What it shows: nearest-first among equal grades does not use record quality. The stricter recipe is defensible clinically, but it removed the one signal that put Sahyadri first.

### line_cardiac_bhubaneswar: v1 defensible, v0 right, v0 preferred

- What v1 did: Kalinga (12.3 min, grade 3, allowance applied), Hi-Tech (10.7 min, grade 2, Rourkela PIN in its record), Utkal (12.5 min, grade 2, a department listing).
- Why: AIIMS Bhubaneswar is grade 2 under both recipes (no blood bank fact), at 14.1 min. It comes 5th, 1.6 minutes behind Utkal and 0.5 behind IGKC. v0 put AIIMS second because its evidence score was higher. v0's third card (Jaiprakash, Rourkela, 242 km) was filler, and v1's 4-hour window correctly keeps that kind of card off the list.
- What it shows: the same pattern as Pune. A minute or two of estimated drive decides the order among grade 2 records, and an estimate whose error SD is 0.26 in log minutes cannot really tell 12.5 from 14.1 minutes apart.

### line_cathlab_indore: both right, v0 slightly preferred

- What v1 did: V One (2.3 min), Indira Memorial (2.3 min, evidence is the bare words "Cath Lab"), Medanta Indore (9.0 min, one source domain).
- Why: recipe v0.3 requires a CCU or ICCU or non-newborn ventilator support (PM-JAY criteria) for grade 3. SAIMS and V One drop from grade 3 to grade 2, so all four recommendable records inside the window are grade 2 and nobody gets the allowance. SAIMS (1,400 beds, angioplasty, open heart surgery) is 4th at 17.7 min.
- What it shows: the judge called this a slight edge. Both answers are right; v1 just leaves out the strongest heart centre.

### What v1 won, for balance

- line_neuro_chandigarh (v0 wrong): v0's score put two corroborated records 28 and 38 km away first. Under v1 the allowance is capped at 15 minutes, so Neelam (30 min) and Healing Touch (39 min) fall to 6th and 10th and Alchemist (13.7 min) comes first.
- brief_highrisk_maharashtra: v0 measured from the Nashik district centre, overstating distances by 15 to 20 km. This is the resolver change, not the ranker.
- line_ct_guwahati, line_burns_nagpur, coords_icu_lucknow, line_icu_patna: nearest first put a closer, general or better-matched facility ahead of v0's evidence-weighted pick.
- pin_dialysis_302001 and brief_trauma_highway: the same facilities, with drive times and no duplicated quotes. The judges called the trauma difference small.

## Ordering behaviour (from `reports/ordering_behaviour.md`)

These figures come from 1,500 random geocoded post offices (seed 7) across the 12 lines, not from the 26 scenarios.

- **The window size drives abstention; the allowance drives which facility comes first.** 12.8% of origins get no recommendable facility inside the window. Halving every window raises that to 44.3%; doubling it lowers it to 1.4%.
- **Turning the allowance off changes the top pick in 25.6% of answered origins** (range 14.6% to 35.5% by line). A 30-minute allowance on every line changes it in 5.4%.
- **The terrain factor matters little to the order:** turning it off changes the top pick in 1.3% of origins and moves abstention from 12.8% to 11.2%.
- **The allowance costs at most its own size.** The top pick is not the nearest recommendable facility in 25.7% of answered origins, and the extra estimated drive is at most 30 minutes (15 on most lines).
- In the judged set, the allowance moved the top pick off the nearest recommendable record in 9 of the 16 graded answers (extra drive 0.2 to 5.8 minutes). That is higher than the 25.7% average because these are mostly cities with many corroborated records.
- The candidate cap (50 nearest recommendable records) hid a record the allowance would have put first in 16 of 18,000 line-origin queries.

Caveats from that report: origins are unweighted post offices, not people; minutes are free-flow estimates from straight-line distance; windows and the allowance are planning thresholds, not clinical deadlines.

## What this means for the story

v1 should not be presented as more accurate than v0 on this evidence. What can be said honestly: v1 makes the order explainable with no weights (nearest first, a declared allowance), it fixes v0's out-of-town recommendation, and on 26 blind scenarios it scores about the same as v0. Its one wrong answer and its most common weakness both trace to minor-flagged records. Two changes would address that: deny the allowance to records with a `possible_merge` or `location_conflict` flag for the line, and treat a merge whose quoted evidence comes from another facility's source as major. Neither is applied here, and either would need a rerun of this comparison.
