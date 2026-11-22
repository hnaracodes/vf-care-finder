# Ranking v0 against v1: blind side-by-side on 26 scenarios, two rounds

Round 2 (below) judges v1 after the D058 fix round. Round 1, further down, judged v1 before it and is kept unchanged.

# Round 2: v1 after the D058 fix round

Generated 2026-11-22 from the blind judgments in `data/labels/v0v1_blind_r2/judgments.csv`, unblinded with `data/labels/v0v1_blind_r2/key.json` (seed 26112202). Answers: v0 from `reports/ranking_scenarios_raw.json` (git d597c74, score v0 D039, recipe v0.2; the same file and the same answers as round 1), v1 from `reports/ranking_scenarios_v1_raw.json` (created 06:34 UTC at git ab8d226, code committed, score spec v1-final D048 with the D058 additions, recipe v0.3, flags wp11_v3). The blinded cards were checked against that v1 file: all 26 top-3 facility lists match.

## Findings

- **v1 is still not shown to be better than v0, and not shown worse.** The judges preferred v1 in 10 scenarios, v0 in 4, and called 12 a tie. The sign test on the 14 untied gives p = 0.18 (two-sided; one-sided, v1 better: p = 0.09). The direction favours v1 more than in round 1 (8 to 5), but the difference is within chance.
- **v1 now has no wrong answer; v0 has one.** Marks: v1 12 right, 6 defensible, 0 wrong, 7 correct abstentions; v0 12, 5, 1, 7 (one scenario unmarked for both). The fix round removed v1's round 1 error: Gadchiroli now abstains, because the Chandrapur record's caesarean evidence carries a new major merge flag. v0's wrong answer is Pune, where this round's judge found that Dhanwantari Hospital is very likely not in Pune.
- **All four of v0's wins are about v1's third card, and three come from one D058 rule.** "The nearest recommendable facility is always shown" (D058 item 1) put a grade 2 record with thin or out-of-scope evidence on card 3 in Jaipur (a children's hospital, for dialysis), PIN 302001 (the same origin point and the same card) and Patna (a fertility centre whose only ICU fact is "Has ICU beds", for ventilator ICU care). Without that rule the ranker's own third card is Monilek (Jaipur, both queries) and Ford (Patna), the cards the judges named as better. Those counterfactual answers were not judged. The fourth loss, Lucknow C-section, is nearest first placing a minor-flagged merged record (Fatima) at its own drive time.
- **Four of v1's ten wins are not the ranking rule.** Nashik is won by the resolver (city head post office, not district centre). Trauma, burns and oncology show the same three facilities in both versions and were won on order by distance and drive times. Without those four the split is 6 to 4 (p = 0.75). Counting the two Jaipur queries once, the full split is 10 to 3 (p = 0.09).
- **The judges disagree with themselves about as often as the versions differ.** v0's answers are identical in both rounds, yet 4 of its 23 comparable marks changed (Pune right to wrong, Bhubaneswar right to defensible, Chandigarh wrong to defensible, Guwahati defensible to right). In the 14 scenarios where neither answer changed, 2 preferences changed (Indore v0 to tie, Ahmedabad tie to v1). That noise is the size of the margin between the versions.

Caveats: 26 scenarios, 12 ties, and two scenarios (Jaipur by name and by PIN) share one origin and one answer. The judges are agents in this workflow, not clinicians. The blind still shows drive times for v1 only, and judges cite them as reasons in at least 6 preferences, so they could tell the versions apart in kind. The blind leaves out each card's "why here" line, so judges did not see that card 3 in the six reserved-nearest answers is there because it is the nearest; the app shows that line. `ambiguous_bilaspur` has a preference (tie) but no marks: the judge reports filling the marks before reviewing it. It is left unmarked in the CSV, not imputed. This round's judges marked the two non-answers (`ambiguous_aurangabad`, `nonsense_place`) "right" where round 1 used "abstention_right"; the counts below keep the judges' marks. As in round 1, this compares the whole v1 stack (ranker, recipe v0.3, flags wp11_v3, resolver, card display), not the ordering rule alone.

## Method

- `data/labels/v0v1_blind_r2/build_blind_r2.py` copies round 1's logic with a new seed and one change: both versions are rendered to the same neutral plain text (status line, card name and place, straight-line km, "position approximate" by the same test for both, drive time when given, evidence with repeated quotes shown once, warnings). Left out: score, parts, grade, confidence, requirement text, why-here, headline. Phrases in the scenario notes that named v0's radii or recipe versions were cut. v1 was X in 15 of 26.
- Three blind judging passes, one per third (`blind_0/1/2.json`), each marking every answer and stating a preference with a reason, as in round 1.
- Unblinding joins each mark and preference to its version through `key.json`. Sign test: exact binomial on untied preferences, p = 0.5.
- Label bias check: X was preferred 8 times and Y 6 times (two-sided p = 0.79), so no sign of a position effect.
- Explanations below come from the stored v1 cards and from rerunning `vf.rank.rank` (version v1, k = 6 or 10) on each scenario's stored resolution, with grades from `gold/capability_grades_v03.parquet` and flags from `gold/facility_flags.parquet` (wp11_v3).

## Marks per version

| Mark | v0 | v1 |
| --- | --- | --- |
| right | 12 | 12 |
| defensible | 5 | 6 |
| wrong | 1 | 0 |
| abstention_right | 7 | 7 |
| abstention_wrong | 0 | 0 |
| not marked (ambiguous_bilaspur) | 1 | 1 |
| Total | 26 | 26 |

"right" includes the two non-answers (location choice in Aurangabad, place not found) for both versions.

## Preference

| Preferred | Scenarios |
| --- | --- |
| v1 | 10 |
| v0 | 4 |
| tie | 12 |

Sign test on the 14 untied: 10 against 4, two-sided p = 0.18 (one-sided, v1 better: p = 0.09).

## Per scenario

| Scenario | v0 mark | v1 mark | Preferred | v1 answer vs round 1 |
| --- | --- | --- | --- | --- |
| brief_dialysis_jaipur | right | defensible | **v0** | card 3 changed |
| brief_csection_lucknow | right | defensible | **v0** | cards 2 and 3 changed |
| brief_dialysis_rural_bihar | abstention_right | abstention_right | tie | same |
| brief_highrisk_maharashtra | defensible | right | v1 | same |
| brief_trauma_highway | right | right | v1 | same |
| line_nicu_pune | wrong | right | v1 | changed |
| line_icu_patna | defensible | defensible | **v0** | card 3 changed |
| line_cardiac_bhubaneswar | defensible | right | v1 | same cards, new order |
| line_cathlab_indore | right | right | tie | same |
| line_mri_coimbatore | right | right | v1 | card 3 changed |
| line_ct_guwahati | right | right | v1 | card 3 changed |
| line_burns_nagpur | right | right | v1 | same cards, new order |
| line_neuro_chandigarh | defensible | defensible | v1 | changed |
| line_oncology_ahmedabad | right | right | v1 | same |
| remote_dialysis_leh | abstention_right | abstention_right | tie | same |
| remote_emoc_tawang | abstention_right | abstention_right | tie | same |
| remote_cardiac_portblair | abstention_right | abstention_right | tie | same |
| remote_emoc_gadchiroli | abstention_right | abstention_right | tie | now abstains |
| fallback_cataract_hyderabad | right | right | tie | same |
| fallback_antivenom_raipur | abstention_right | abstention_right | tie | same |
| ambiguous_aurangabad | right | right | tie | same |
| ambiguous_bilaspur | not marked | not marked | tie | same |
| disambiguated_aurangabad_bihar | abstention_right | abstention_right | tie | same |
| pin_dialysis_302001 | right | defensible | **v0** | card 3 changed |
| coords_icu_lucknow | defensible | defensible | v1 | card 3 changed |
| nonsense_place | right | right | tie | same |

## Where v1 is judged worse

v1 is marked wrong nowhere in round 2. It is judged worse in four scenarios, and in each the problem is the third card.

### brief_dialysis_jaipur and pin_dialysis_302001: v1 defensible, v0 right, v0 preferred

- What v1 did: Soni (5.5 min, grade 3), NAV Imperial (5.9 min, grade 3), Garg Child Hospital (2.8 min, grade 2, the nearest). Both queries resolve to the same point (PIN 302001 head post office), so this is one answer judged twice.
- Why: Garg Child is the nearest recommendable record and passes grade 2 on "Advanced Kidney Dialysis" and "Haemodialysis - Peritoneal Dialysis". The allowance lists Soni and NAV Imperial (corroborated, about 3 minutes further) ahead of it, which pushes it past card 3, so the D058 rule reserves card 3 for it. Rerun with k = 6, the ranker's order is Soni, NAV Imperial, Monilek (8.5 min, grade 3, 12 dialysis machines), Garg Child: the first three are v0's cards. In round 1, before the rule, v1 showed exactly those three, and the judges called it a tie and a v1 win.
- What it shows: the reserved card is the nearest record with any grade 2 evidence, not the nearest suitable one. The recipes have no adult or paediatric scope, so a children's hospital qualifies for dialysis. The judge also saw a 1 km card listed third without the "always shown" explanation, which the blind leaves out.

### line_icu_patna: both defensible, v0 preferred

- What v1 did: Shrinivas (5.1 min, grade 3), Nectar (5.1 min, grade 3), Diwya Vatsalya Mamta Fertility Centre (1.0 min, grade 2, the nearest).
- Why: the same rule. Diwya passes grade 2 for ICU on one fact, "Has ICU beds" (strength thin). Rerun with k = 10, the ranker's order has Ford (5.7 min, grade 3, ICU with ventilators) third and Diwya ninth. Round 1's v1 answer was Shrinivas, Nectar, Ford, and the judge preferred it to v0.
- What it shows: one listing-style fact at a fertility clinic is enough to take a card from the strongest ICU in the list.

### brief_csection_lucknow: v1 defensible, v0 right, v0 preferred

- What v1 did: BNK (6.7 min, grade 3), Fatima (6.4 min, grade 2, the nearest, `location_conflict` and `possible_merge`, both minor), Pawar's Clinic (9.0 min, grade 2).
- Why: the D058 flag denial removed the allowance from Nova and St. Joseph (round 1's cards 2 and 3, both minor-flagged), which fall to 6th and 8th. That fixed the round 1 complaint. But Fatima is the nearest record, so it needs no allowance and keeps card 2 at its own drive time. Its record has a major merge flag only for cardiac surgery (evidence among Gorakhpur facts); for emoc its flags are minor, so it passes the gate. Elite (10.1 min) and Krishna (10.2 min), two of v0's cards, are 4th and 5th.
- What it shows: denying the allowance moves flagged records down only when they are not already nearest. A minor merge flag still lets borrowed evidence reach a card.

### v1 weaknesses inside answers it won

- line_neuro_chandigarh (both defensible, v1 preferred): card 3 is Fortis Mohali, whose five neurosurgery facts all name "Fortis Hospital Mundian Khurd", which the judge places in Ludhiana. Its only flag is a minor `location_conflict` (map point 80 km from its PIN area). The wp11_v3 check that fired on Chandrapur and Dhanwantari ("evidence sits among facts about another place") did not fire on this record.
- coords_icu_lucknow (both defensible, v1 preferred): card 3 is Nishat Hospital (1.2 min), the reserved nearest, a record the judge found mixed with Isha Hospital, Jaunpur (map point 212 km from its PIN area, minor flag). Without the rule, card 3 is RML Institute (9.7 min, grade 3).
- line_ct_guwahati (both right, v1 preferred): card 3 is Aruna Memorial, the reserved nearest, resting on one "CT scanner" equipment fact. The judge called it thin but plausible.
- In the six answers where the rule reserved card 3 (Jaipur twice, Patna, Coimbatore, Guwahati, Lucknow coordinates), the judges preferred v0 in 3 and v1 in 3. The reserved card was a reason for the preference in v1's favour once (Coimbatore: KMCH, 2 km, 3T MRI) and against v1 three times.

## How round 2 differs from round 1

- **What changed in v1:** 12 of 26 v1 answers differ from round 1 (10 in their cards, 2 in order only); v0's answers are identical. The changes trace to the D058 additions and the wp11_v3 flags: the reserved nearest (6 answers), the allowance denied to flagged records (Lucknow C-section, Bhubaneswar, and Chandigarh, where Alchemist's minor location flag now costs it the allowance and Max Mohali, the nearest, comes first), and major "evidence beside another place" flags that gate Dhanwantari (Pune) and Chandrapur (Gadchiroli).
- **Preferences on the 12 changed answers:** round 1 v1 6, v0 4, tie 2; round 2 v1 7, v0 4, tie 1. On the 14 unchanged: round 1 v1 2, v0 1, tie 11; round 2 v1 3, v0 0, tie 11.
- **Errors fixed:** both of round 1's v1 losses on doubtful records are gone. Gadchiroli abstains (Chandrapur's quote is now a major flag), and Pune no longer leads with Dhanwantari (gated by the same check). Lucknow C-section lost Nova and St. Joseph.
- **Errors introduced:** the reserved-nearest rule turned three round 1 non-losses (Jaipur tie, PIN 302001 and Patna v1 wins) into v0 wins, and put two more thin or merged records on cards (Guwahati, Lucknow coordinates).
- **Judging noise:** the same v0 answers got different marks in 4 of 23 comparable scenarios, including Pune (right in round 1, wrong in round 2: this round's judge checked Dhanwantari's PIN and description) and Chandigarh (wrong, then defensible). Bhubaneswar's v0 card 3 (Jaiprakash, Rourkela, 242 km) was called filler in both rounds but marked right in round 1 and defensible in round 2.
- **Blinding:** round 2 rendered both versions to the same text and showed each quote once (round 1 judges noticed v0's repeated quotes). Drive times still appear for v1 only.

## What this means for the story

Say that v1 is about as good as v0 on 26 blind scenarios, with no wrong answer to v0's one, and that its case rests on being measured and cited, not on beating v0. Do not say v1 is more accurate: 10 to 4 is not significant (p = 0.18), four of the ten wins are the resolver or the display, and the judges' own round-to-round disagreement is as large as the margin.

The round points to one change worth making: the nearest-always-shown card should not take the place of a ranked card when its only evidence is thin, or it should be shown apart from the three cards, labelled "nearest with any evidence". As shipped it caused three of v0's four wins. Two smaller gaps remain: the recipes have no adult or paediatric scope (a children's hospital passes for dialysis, a fertility clinic for ICU), and the evidence-beside-another-place check misses quotes that name another branch (Fortis Mundian Khurd). None of these is applied here; any of them would need a rerun of this comparison.

# Round 1: v1 before the D058 fix round

Generated 2026-11-22 from the blind judgments in `data/labels/v0v1_blind/judgments.csv`, unblinded with `data/labels/v0v1_blind/key.json` (seed 20261122). Answers: v0 from `reports/ranking_scenarios_raw.json` (git d597c74, score v0 D039, recipe v0.2), v1 from `reports/ranking_scenarios_v1_raw.json` (git 958608c with uncommitted code, score spec v1-final D048, recipe v0.3).

### Findings

- **v1 is not shown to be better than v0. The two are about even.** On marks, v1 has 12 right, 4 defensible, 1 wrong and 9 correct abstentions; v0 has 11, 4, 1 and 10. On preference, the judges picked v1 in 8 scenarios, v0 in 5, and called 13 a tie. The sign test on the 13 untied scenarios gives p = 0.58 (two-sided), so the 8 to 5 split is well within chance.
- **v1 makes one new serious error, in Gadchiroli.** v0 abstained correctly. v1's 2-hour EmOC window reaches 77 km to "GMC Chandrapur" and recommends it. That record is a merge: its C-section quote comes from IGGMC Nagpur and its blood bank and bed count from Sir JJ Hospital, Mumbai. The merge flag is only "minor", so the record passes the gate and the evidence allowance moves it up.
- **v1 fixes v0's one serious error, in Chandigarh.** v0 sent a brain-surgery patient 27 to 37 km out of the tricity because its score rewarded corroborated records over distance. v1's window and capped 15-minute allowance keep the first pick 11 km away.
- **Two causes explain most of v1's losses.** (1) The evidence allowance treats a corroborated record with a minor merge or location flag the same as a clean one, so flagged records move ahead of clean ones (Lucknow, Gadchiroli). v0's 0.2 trust weight held them back. (2) Recipe v0.3 is stricter, so several of v0's strongest picks (Sahyadri for NICU, SAIMS and V One for cath lab) drop from grade 3 to grade 2. Among grade 2 records v1 orders by minutes alone, so a thin or merged record that is a few minutes closer beats a large, well-documented one (Pune, Indore, Bhubaneswar).
- **Some of v1's wins are not the ranker.** v1 also changed the location resolver (a named district now resolves to its town's head post office, not the district centre), removed duplicated quotes and added drive times. Nashik is won purely by the resolver, and the Jaipur PIN query mostly by display. Without the two presentation-driven wins (Jaipur PIN, trauma highway) the split is 6 to 5.

Caveats: 26 scenarios is a small set and 13 are ties, so this cannot separate two rankers that differ in a handful of cases. The judges were blind to the version name, but v1 is the only version that shows drive times, so a judge could tell the two answers apart in kind, though not which was the newer. One preference (`nonsense_place`) was not returned by its judge. Both answers there are the same "location not found" abstention, so it is recorded as a tie; ties do not enter the sign test. The comparison is of the whole v1 stack (ranker, recipe v0.3, resolver, card display), not the ordering rule alone.

### Method

- `scripts/v0v1_blind.py` built a blinded side-by-side of both answers per scenario: status, top 3 cards with name, straight-line km, drive minutes when given, quoted evidence and flags. No version, score parts or requirement text. Each scenario got its own coin flip for which version is X (v1 was X in 16 of 26).
- Three independent blind judging passes, one per third of the scenarios (`blind_0/1/2.json`), marked each answer `right`, `defensible`, `wrong`, `abstention_right` or `abstention_wrong`, checked the claims against the facility records, and stated a preference (X, Y or tie) with a reason. The judges are agents in this workflow, not clinicians.
- Unblinding joins each mark and preference to its version through `key.json`. Sign test: exact binomial on untied preferences, p = 0.5.
- Label bias check: X was preferred 7 times and Y 6 times, so no sign of a position effect.

### Marks per version

| Mark | v0 | v1 |
| --- | --- | --- |
| right | 11 | 12 |
| defensible | 4 | 4 |
| wrong | 1 | 1 |
| abstention_right | 10 | 9 |
| abstention_wrong | 0 | 0 |
| Total | 26 | 26 |

v1 has one fewer correct abstention because it answered Gadchiroli, where v0 abstained; that answer is the one marked wrong.

### Preference

| Preferred | Scenarios |
| --- | --- |
| v1 | 8 |
| v0 | 5 |
| tie | 13 (one imputed, see caveats) |

Sign test on the 13 untied: 8 against 5, two-sided p = 0.58 (one-sided, v1 better: p = 0.29).

### Per scenario

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

### Where v1 is marked wrong or judged worse

Each explanation below comes from rerunning the shipped v1 ranker (`vf.rank.rank`, k = 10) on the same resolved location and comparing grades in `gold/capability_grades.parquet` (v0.2) and `gold/capability_grades_v03.parquet` (v0.3).

#### remote_emoc_gadchiroli: v1 wrong, v0 abstained correctly

- What v1 did: recommended "Government Medical College And Hospital Chandrapur", 77 km, about 85 minutes, grade 3, flag `possible_merge` (minor).
- Why: two changes combined. The v1 EmOC window is the cited 2-hour indicator, which from Gadchiroli town reaches about 115 km straight-line; v0's radius was 50 km, so v0 found nothing. Inside the window, the only recommendable record is this one, and a minor flag does not block it. The judge found that its C-section quote is a CORDMILK trial protocol from IGGMC Nagpur and that its blood bank, 2,844 beds and website belong to Sir JJ Hospital, Mumbai.
- What it shows: the window is doing what it should. The weak point is that a merge flag graded "minor" lets borrowed evidence through. The judge's concern that 85 minutes may be past the reach limit does not hold under the spec: it is inside the 120-minute window.

#### brief_csection_lucknow: v1 defensible, v0 right, v0 preferred

- What v1 did: BNK (6.7 min), Nova (10.8 min, `location_conflict` and `possible_merge`, both minor, confidence low), St. Joseph Lucknow (12.6 min, `possible_merge` minor).
- Why: all three are grade 3, so each gets the 15-minute allowance (order minutes -8.3, -4.3, -2.4). The clean grade 2 records v0 showed, Elite (10.1 min) and Krishna Medical Centre (10.2 min), drop to 6th and 7th, behind even Fatima and Pawar's Clinic. Nova and St. Joseph were grade 3 under v0 too, but v0's trust weight and confidence pushed them below Elite and Krishna.
- What it shows: the allowance applies to a corroborated record however doubtful its provenance. The judge found Nova's record placed in Rampur and St. Joseph's mixed with Ghaziabad and dental-clinic facts.

#### line_nicu_pune: v1 defensible, v0 right, v0 preferred

- What v1 did: Dhanwantari (3.2 min, `possible_merge` minor, confidence low), KEM Pune (3.7 min), Inamdar (4.9 min).
- Why: recipe v0.3 requires newborn ventilation or a self-declared level III NICU (FBNC 2025) for grade 3. Sahyadri, Dhanwantari and DY Patil, v0's three cards, all drop from grade 3 to grade 2, and none of the ten nearest recommendable records is grade 3. With every candidate at grade 2, v1 orders by minutes alone, so Dhanwantari, a 12-bed merged record with a Chalisgaon PIN, comes first and Sahyadri (9.7 min, 900 beds, "NICU on-site") falls to 5th.
- What it shows: nearest-first among equal grades does not use record quality. The stricter recipe is defensible clinically, but it removed the one signal that put Sahyadri first.

#### line_cardiac_bhubaneswar: v1 defensible, v0 right, v0 preferred

- What v1 did: Kalinga (12.3 min, grade 3, allowance applied), Hi-Tech (10.7 min, grade 2, Rourkela PIN in its record), Utkal (12.5 min, grade 2, a department listing).
- Why: AIIMS Bhubaneswar is grade 2 under both recipes (no blood bank fact), at 14.1 min. It comes 5th, 1.6 minutes behind Utkal and 0.5 behind IGKC. v0 put AIIMS second because its evidence score was higher. v0's third card (Jaiprakash, Rourkela, 242 km) was filler, and v1's 4-hour window correctly keeps that kind of card off the list.
- What it shows: the same pattern as Pune. A minute or two of estimated drive decides the order among grade 2 records, and an estimate whose error SD is 0.26 in log minutes cannot really tell 12.5 from 14.1 minutes apart.

#### line_cathlab_indore: both right, v0 slightly preferred

- What v1 did: V One (2.3 min), Indira Memorial (2.3 min, evidence is the bare words "Cath Lab"), Medanta Indore (9.0 min, one source domain).
- Why: recipe v0.3 requires a CCU or ICCU or non-newborn ventilator support (PM-JAY criteria) for grade 3. SAIMS and V One drop from grade 3 to grade 2, so all four recommendable records inside the window are grade 2 and nobody gets the allowance. SAIMS (1,400 beds, angioplasty, open heart surgery) is 4th at 17.7 min.
- What it shows: the judge called this a slight edge. Both answers are right; v1 just leaves out the strongest heart centre.

#### What v1 won, for balance

- line_neuro_chandigarh (v0 wrong): v0's score put two corroborated records 28 and 38 km away first. Under v1 the allowance is capped at 15 minutes, so Neelam (30 min) and Healing Touch (39 min) fall to 6th and 10th and Alchemist (13.7 min) comes first.
- brief_highrisk_maharashtra: v0 measured from the Nashik district centre, overstating distances by 15 to 20 km. This is the resolver change, not the ranker.
- line_ct_guwahati, line_burns_nagpur, coords_icu_lucknow, line_icu_patna: nearest first put a closer, general or better-matched facility ahead of v0's evidence-weighted pick.
- pin_dialysis_302001 and brief_trauma_highway: the same facilities, with drive times and no duplicated quotes. The judges called the trauma difference small.

### Ordering behaviour (from `reports/ordering_behaviour.md`)

These figures come from 1,500 random geocoded post offices (seed 7) across the 12 lines, not from the 26 scenarios.

- **The window size drives abstention; the allowance drives which facility comes first.** 12.8% of origins get no recommendable facility inside the window. Halving every window raises that to 44.3%; doubling it lowers it to 1.4%.
- **Turning the allowance off changes the top pick in 25.6% of answered origins** (range 14.6% to 35.5% by line). A 30-minute allowance on every line changes it in 5.4%.
- **The terrain factor matters little to the order:** turning it off changes the top pick in 1.3% of origins and moves abstention from 12.8% to 11.2%.
- **The allowance costs at most its own size.** The top pick is not the nearest recommendable facility in 25.7% of answered origins, and the extra estimated drive is at most 30 minutes (15 on most lines).
- In the judged set, the allowance moved the top pick off the nearest recommendable record in 9 of the 16 graded answers (extra drive 0.2 to 5.8 minutes). That is higher than the 25.7% average because these are mostly cities with many corroborated records.
- The candidate cap (50 nearest recommendable records) hid a record the allowance would have put first in 16 of 18,000 line-origin queries.

Caveats from that report: origins are unweighted post offices, not people; minutes are free-flow estimates from straight-line distance; windows and the allowance are planning thresholds, not clinical deadlines.

### What this means for the story

v1 should not be presented as more accurate than v0 on this evidence. What can be said honestly: v1 makes the order explainable with no weights (nearest first, a declared allowance), it fixes v0's out-of-town recommendation, and on 26 blind scenarios it scores about the same as v0. Its one wrong answer and its most common weakness both trace to minor-flagged records. Two changes would address that: deny the allowance to records with a `possible_merge` or `location_conflict` flag for the line, and treat a merge whose quoted evidence comes from another facility's source as major. Neither is applied here, and either would need a rerun of this comparison.
