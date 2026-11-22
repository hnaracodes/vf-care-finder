# How the v1-final order and abstentions respond to their design choices

Generated 2026-11-22 06:07 UTC by `scripts/wp5_scenarios.py --sensitivity` (git f5a7427, code uncommitted: True). Grades: recipe v0.3 (rules aligned to WHO 2009, FBNC 2025, PM-JAY criteria, PMNDP; D048, D052), from gold/capability_grades_v03.parquet. Ranker: rank_v1 (score spec v1-final, D048).

## Findings

- **The window size drives abstention; the allowance drives which facility comes first.** Averaged over the 12 lines, 12.8% of origins get no recommendable facility inside the window. Halving every window raises that to 44.3%; doubling it lowers it to 1.4%.
- **Turning the allowance off changes the top pick in 27.1% of answered origins** (mean over lines; range 13.1% to 36.5%). A 30-minute allowance on every line changes it in 5.5%.
- **The terrain factor changes the top pick in 1.3% of answered origins** and moves abstention from 12.8% to 11.2% when it is turned off. Inside one origin the factor scales every facility's minutes alike, so it changes the order only through the allowance.
- **The allowance has a cost in minutes, capped by design.** Under the shipped settings the top pick is not the nearest recommendable facility in 27.2% of answered origins (mean over lines); the extra estimated drive is at most the allowance (30.0 minutes at worst across all lines).
- **The nearest recommendable facility is always on the cards.** When the allowance pushes it past the third card it takes the last place; that happens in 9.4% of answered origins (mean over lines; range 0.2% to 18.1%).
- **Denying the allowance to records with a possible-merge or location-conflict warning** changes the top pick in 7.3% of answered origins and the set of three cards in 13.9% (mean over lines), measured by turning the denial off.
- Largest mean top-pick change: allowance 0 (27.1%). Largest mean abstention change: windows x0.5 (12.8% to 44.3%).
- The candidate cap (50 nearest recommendable records) hid a corroborated record that the allowance would have put first in 16 of 18,000 line-origin queries.

Caveats: origins are unweighted post offices, not people; minutes are free-flow estimates from straight-line distance (log error SD 0.26), so these are statements about the rule, not about real trips. Windows and the allowance are planning thresholds and declared design choices, not clinical deadlines. Nothing here is a probability.

## Method

- Origins: 1,500 random geocoded post offices (seed 7, the score research's seed), inside India's box, without the 1,032 sea-pixel offices in `reference/sea_pixel_post_offices.csv` (D048 item 7). Each origin uses its PIN-directory state's terrain factor.
- For every origin, line and setting, the v1 ranker's own code (`vf.rank.pick_v1`, the same gates and order as `vf.rank.rank`) gives the top pick or an abstention.
- Top-1 change: among origins answered under both the shipped settings and the alternative, the share whose first card is a different facility. Abstention: the share of all origins with no recommendable facility inside the window.
- Settings: allowance 0, 15 or 30 minutes on every line (shipped: 15, and 30 for MRI, cardiac surgery and oncology); every window halved or doubled; terrain factor off (1.00 everywhere; islands still get no minutes); flag denial off (records with a possible-merge or location-conflict warning get the allowance too).
- Top-3 change: the same, for the set of up to three cards shown (with the nearest always among them).
- Run time 14 s.

## Top pick changes against the shipped settings (share of origins answered under both)

| Line | Window (min) | Basis | Allowance | allowance 0 | allowance 15 | allowance 30 | windows x0.5 | windows x2 | terrain off | flag denial off |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| emoc | 120 | cited | 15 | 13.1% | 0.0% | 4.3% | 1.7% | 1.0% | 1.2% | 2.7% |
| nicu | 120 | design choice | 15 | 21.0% | 0.0% | 8.6% | 1.9% | 0.5% | 1.4% | 4.8% |
| dialysis | 60 | design choice | 15 | 26.4% | 0.0% | 3.9% | 5.3% | 3.5% | 3.1% | 5.4% |
| icu | 120 | design choice | 15 | 29.9% | 0.0% | 10.7% | 2.4% | 0.3% | 0.8% | 7.3% |
| cardiac_surgery | 240 | design choice | 30 | 32.6% | 6.2% | 0.0% | 1.5% | 0.0% | 1.4% | 8.1% |
| cath_lab | 90 | design choice | 15 | 26.0% | 0.0% | 6.3% | 2.3% | 1.5% | 1.9% | 5.9% |
| mri | 180 | design choice | 30 | 30.8% | 5.0% | 0.0% | 2.2% | 0.3% | 0.7% | 10.8% |
| ct | 120 | design choice | 15 | 24.6% | 0.0% | 7.3% | 0.8% | 0.4% | 0.8% | 8.3% |
| trauma | 120 | cited | 15 | 31.4% | 0.0% | 9.8% | 1.7% | 0.8% | 1.6% | 9.3% |
| burns | 240 | design choice | 15 | 21.1% | 0.0% | 6.1% | 1.3% | 0.0% | 0.6% | 7.9% |
| neurosurgery | 150 | design choice | 15 | 31.6% | 0.0% | 9.0% | 2.5% | 0.4% | 1.0% | 7.0% |
| oncology | 240 | design choice | 30 | 36.5% | 6.9% | 0.0% | 3.1% | 0.0% | 1.2% | 10.3% |

## Abstention (share of all origins with nothing recommendable inside the window)

| Line | Shipped | allowance 0 | allowance 15 | allowance 30 | windows x0.5 | windows x2 | terrain off | flag denial off |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| emoc | 23.4% | 23.4% | 23.4% | 23.4% | 64.0% | 1.1% | 20.4% | 23.4% |
| nicu | 13.1% | 13.1% | 13.1% | 13.1% | 53.3% | 0.7% | 11.8% | 13.1% |
| dialysis | 46.8% | 46.8% | 46.8% | 46.8% | 74.8% | 10.3% | 45.7% | 46.8% |
| icu | 5.3% | 5.3% | 5.3% | 5.3% | 33.7% | 0.5% | 3.9% | 5.3% |
| cardiac_surgery | 0.5% | 0.5% | 0.5% | 0.5% | 18.2% | 0.1% | 0.1% | 0.5% |
| cath_lab | 32.5% | 32.5% | 32.5% | 32.5% | 70.5% | 2.4% | 30.0% | 32.5% |
| mri | 1.9% | 1.9% | 1.9% | 1.9% | 28.1% | 0.2% | 0.7% | 1.9% |
| ct | 6.9% | 6.9% | 6.9% | 6.9% | 42.7% | 0.4% | 4.7% | 6.9% |
| trauma | 10.6% | 10.6% | 10.6% | 10.6% | 46.6% | 0.5% | 8.1% | 10.6% |
| burns | 6.6% | 6.6% | 6.6% | 6.6% | 39.7% | 0.5% | 5.9% | 6.6% |
| neurosurgery | 4.9% | 4.9% | 4.9% | 4.9% | 39.7% | 0.3% | 2.6% | 4.9% |
| oncology | 0.7% | 0.7% | 0.7% | 0.7% | 20.3% | 0.1% | 0.2% | 0.7% |

## Top-3 card set changes against the shipped settings (share of origins answered under both)

| Line | allowance 0 | allowance 15 | allowance 30 | windows x0.5 | windows x2 | terrain off | flag denial off |
| --- | --- | --- | --- | --- | --- | --- | --- |
| emoc | 15.6% | 0.0% | 7.0% | 49.3% | 31.5% | 9.4% | 5.0% |
| nicu | 30.2% | 0.0% | 9.3% | 44.7% | 25.3% | 9.3% | 5.5% |
| dialysis | 30.2% | 0.0% | 3.5% | 35.4% | 42.2% | 10.7% | 9.1% |
| icu | 41.7% | 0.0% | 16.9% | 39.8% | 9.2% | 6.8% | 14.9% |
| cardiac_surgery | 54.1% | 14.1% | 0.0% | 28.7% | 1.4% | 3.8% | 13.5% |
| cath_lab | 31.6% | 0.0% | 6.0% | 40.9% | 39.7% | 10.2% | 11.6% |
| mri | 50.8% | 9.2% | 0.0% | 33.7% | 6.1% | 4.5% | 23.5% |
| ct | 41.5% | 0.0% | 7.2% | 41.7% | 17.6% | 7.5% | 17.0% |
| trauma | 47.9% | 0.0% | 11.2% | 45.4% | 16.6% | 8.8% | 20.4% |
| burns | 26.8% | 0.0% | 8.6% | 44.6% | 11.0% | 6.6% | 16.6% |
| neurosurgery | 47.0% | 0.0% | 12.5% | 39.3% | 8.6% | 6.0% | 13.7% |
| oncology | 56.7% | 11.1% | 0.0% | 35.5% | 1.5% | 2.0% | 16.1% |

## What the allowance costs under the shipped settings

| Line | Top pick not the nearest | Nearest moved to the last card | Extra minutes, p90 | Extra minutes, max | Cap hid a promotable record |
| --- | --- | --- | --- | --- | --- |
| emoc | 13.1% | 0.2% | 2.3 | 15.0 | 0 |
| nicu | 21.0% | 3.7% | 4.8 | 15.0 | 0 |
| dialysis | 26.6% | 8.1% | 5.3 | 14.8 | 0 |
| icu | 29.9% | 9.9% | 7.0 | 15.0 | 6 |
| cardiac_surgery | 32.8% | 11.2% | 9.7 | 30.0 | 0 |
| cath_lab | 26.0% | 8.2% | 4.7 | 14.9 | 0 |
| mri | 30.6% | 16.4% | 8.2 | 29.7 | 7 |
| ct | 24.8% | 11.3% | 4.5 | 14.6 | 2 |
| trauma | 31.3% | 9.8% | 5.5 | 14.9 | 0 |
| burns | 22.1% | 2.4% | 4.3 | 14.9 | 0 |
| neurosurgery | 31.7% | 12.8% | 5.4 | 14.9 | 1 |
| oncology | 36.5% | 18.1% | 10.3 | 30.0 | 0 |
