# How the v1-final order and abstentions respond to their design choices

Generated 2026-11-22 06:36 UTC by `scripts/wp5_scenarios.py --sensitivity` (git ab8d226, code uncommitted: False). Grades: recipe v0.3 (rules aligned to WHO 2009, FBNC 2025, PM-JAY criteria, PMNDP; D048, D052), from gold/capability_grades_v03.parquet. Ranker: rank_v1 (score spec v1-final, D048).

## Findings

- **The window size drives abstention; the allowance drives which facility comes first.** Averaged over the 12 lines, 12.9% of origins get no recommendable facility inside the window. Halving every window raises that to 44.6%; doubling it lowers it to 1.4%.
- **Turning the allowance off changes the top pick in 27.2% of answered origins** (mean over lines; range 13.1% to 36.5%). A 30-minute allowance on every line changes it in 5.4%.
- **The terrain factor changes the top pick in 1.3% of answered origins** and moves abstention from 12.9% to 11.3% when it is turned off. Inside one origin the factor scales every facility's minutes alike, so it changes the order only through the allowance.
- **The allowance has a cost in minutes, capped by design.** Under the shipped settings the top pick is not the nearest recommendable facility in 27.3% of answered origins (mean over lines); the extra estimated drive is at most the allowance (30.0 minutes at worst across all lines).
- **The nearest recommendable facility is always on the cards.** When the allowance pushes it past the third card it takes the last place; that happens in 9.4% of answered origins (mean over lines; range 0.2% to 18.1%).
- **Denying the allowance to records with a possible-merge or location-conflict warning** changes the top pick in 6.8% of answered origins and the set of three cards in 12.9% (mean over lines), measured by turning the denial off.
- Largest mean top-pick change: allowance 0 (27.2%). Largest mean abstention change: windows x0.5 (12.9% to 44.6%).
- The candidate cap (50 nearest recommendable records) hid a corroborated record that the allowance would have put first in 15 of 18,000 line-origin queries.

Caveats: origins are unweighted post offices, not people; minutes are free-flow estimates from straight-line distance (log error SD 0.26), so these are statements about the rule, not about real trips. Windows and the allowance are planning thresholds and declared design choices, not clinical deadlines. Nothing here is a probability.

## Method

- Origins: 1,500 random geocoded post offices (seed 7, the score research's seed), inside India's box, without the 1,032 sea-pixel offices in `reference/sea_pixel_post_offices.csv` (D048 item 7). Each origin uses its PIN-directory state's terrain factor.
- For every origin, line and setting, the v1 ranker's own code (`vf.rank.pick_v1`, the same gates and order as `vf.rank.rank`) gives the top pick or an abstention.
- Top-1 change: among origins answered under both the shipped settings and the alternative, the share whose first card is a different facility. Abstention: the share of all origins with no recommendable facility inside the window.
- Settings: allowance 0, 15 or 30 minutes on every line (shipped: 15, and 30 for MRI, cardiac surgery and oncology); every window halved or doubled; terrain factor off (1.00 everywhere; islands still get no minutes); flag denial off (records with a possible-merge or location-conflict warning get the allowance too).
- Top-3 change: the same, for the set of up to three cards shown (with the nearest always among them).
- Run time 15 s.

## Top pick changes against the shipped settings (share of origins answered under both)

| Line | Window (min) | Basis | Allowance | allowance 0 | allowance 15 | allowance 30 | windows x0.5 | windows x2 | terrain off | flag denial off |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| emoc | 120 | cited | 15 | 13.1% | 0.0% | 4.3% | 1.7% | 1.0% | 1.2% | 2.3% |
| nicu | 120 | design choice | 15 | 21.2% | 0.0% | 8.5% | 1.9% | 0.5% | 1.3% | 4.7% |
| dialysis | 60 | design choice | 15 | 26.5% | 0.0% | 3.9% | 5.3% | 3.3% | 3.0% | 5.4% |
| icu | 120 | design choice | 15 | 30.8% | 0.0% | 10.6% | 2.5% | 0.4% | 0.8% | 6.8% |
| cardiac_surgery | 240 | design choice | 30 | 32.8% | 6.4% | 0.0% | 1.6% | 0.0% | 1.4% | 7.4% |
| cath_lab | 90 | design choice | 15 | 25.8% | 0.0% | 6.4% | 2.3% | 1.5% | 1.9% | 5.5% |
| mri | 180 | design choice | 30 | 30.7% | 5.0% | 0.0% | 2.2% | 0.3% | 0.7% | 10.3% |
| ct | 120 | design choice | 15 | 24.3% | 0.0% | 7.3% | 0.8% | 0.4% | 0.8% | 7.9% |
| trauma | 120 | cited | 15 | 31.0% | 0.0% | 9.5% | 2.0% | 0.8% | 1.6% | 7.2% |
| burns | 240 | design choice | 15 | 21.8% | 0.0% | 5.7% | 1.3% | 0.0% | 0.6% | 8.0% |
| neurosurgery | 150 | design choice | 15 | 31.6% | 0.0% | 8.5% | 2.5% | 0.4% | 1.1% | 6.5% |
| oncology | 240 | design choice | 30 | 36.5% | 6.9% | 0.0% | 3.1% | 0.0% | 1.2% | 9.9% |

## Abstention (share of all origins with nothing recommendable inside the window)

| Line | Shipped | allowance 0 | allowance 15 | allowance 30 | windows x0.5 | windows x2 | terrain off | flag denial off |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| emoc | 23.8% | 23.8% | 23.8% | 23.8% | 64.2% | 1.1% | 20.8% | 23.8% |
| nicu | 13.3% | 13.3% | 13.3% | 13.3% | 53.5% | 0.7% | 12.1% | 13.3% |
| dialysis | 47.2% | 47.2% | 47.2% | 47.2% | 74.9% | 10.3% | 46.0% | 47.2% |
| icu | 5.3% | 5.3% | 5.3% | 5.3% | 35.2% | 0.5% | 4.0% | 5.3% |
| cardiac_surgery | 0.5% | 0.5% | 0.5% | 0.5% | 18.3% | 0.1% | 0.1% | 0.5% |
| cath_lab | 32.5% | 32.5% | 32.5% | 32.5% | 70.7% | 2.4% | 30.0% | 32.5% |
| mri | 1.9% | 1.9% | 1.9% | 1.9% | 28.1% | 0.2% | 0.7% | 1.9% |
| ct | 6.9% | 6.9% | 6.9% | 6.9% | 42.7% | 0.4% | 4.7% | 6.9% |
| trauma | 10.7% | 10.7% | 10.7% | 10.7% | 47.2% | 0.5% | 8.1% | 10.7% |
| burns | 6.6% | 6.6% | 6.6% | 6.6% | 40.3% | 0.5% | 5.9% | 6.6% |
| neurosurgery | 4.9% | 4.9% | 4.9% | 4.9% | 39.9% | 0.3% | 2.7% | 4.9% |
| oncology | 0.7% | 0.7% | 0.7% | 0.7% | 20.3% | 0.1% | 0.2% | 0.7% |

## Top-3 card set changes against the shipped settings (share of origins answered under both)

| Line | allowance 0 | allowance 15 | allowance 30 | windows x0.5 | windows x2 | terrain off | flag denial off |
| --- | --- | --- | --- | --- | --- | --- | --- |
| emoc | 15.7% | 0.0% | 7.3% | 50.5% | 31.4% | 9.5% | 4.7% |
| nicu | 29.4% | 0.0% | 9.3% | 44.4% | 25.3% | 9.4% | 5.1% |
| dialysis | 30.4% | 0.0% | 3.5% | 35.5% | 41.8% | 10.6% | 9.2% |
| icu | 41.8% | 0.0% | 16.4% | 38.9% | 9.5% | 7.1% | 13.9% |
| cardiac_surgery | 54.2% | 14.1% | 0.0% | 29.5% | 1.4% | 3.8% | 11.3% |
| cath_lab | 32.1% | 0.0% | 5.4% | 40.8% | 40.0% | 10.4% | 11.6% |
| mri | 50.4% | 9.2% | 0.0% | 33.7% | 6.1% | 4.5% | 22.6% |
| ct | 41.0% | 0.0% | 7.2% | 41.7% | 17.6% | 7.5% | 16.1% |
| trauma | 47.8% | 0.0% | 10.4% | 46.8% | 17.1% | 8.6% | 17.8% |
| burns | 27.0% | 0.0% | 9.1% | 46.4% | 11.0% | 6.5% | 14.6% |
| neurosurgery | 47.3% | 0.0% | 12.4% | 39.7% | 8.6% | 6.1% | 13.3% |
| oncology | 56.7% | 11.1% | 0.0% | 35.5% | 1.5% | 2.0% | 14.9% |

## What the allowance costs under the shipped settings

| Line | Top pick not the nearest | Nearest moved to the last card | Extra minutes, p90 | Extra minutes, max | Cap hid a promotable record |
| --- | --- | --- | --- | --- | --- |
| emoc | 13.1% | 0.2% | 2.3 | 15.0 | 0 |
| nicu | 21.2% | 3.7% | 4.9 | 15.0 | 0 |
| dialysis | 26.6% | 8.2% | 5.3 | 14.8 | 0 |
| icu | 30.8% | 9.9% | 7.3 | 15.0 | 5 |
| cardiac_surgery | 33.0% | 11.3% | 9.7 | 30.0 | 0 |
| cath_lab | 25.8% | 8.2% | 4.5 | 14.9 | 0 |
| mri | 30.5% | 16.3% | 8.2 | 29.7 | 7 |
| ct | 24.4% | 11.2% | 4.5 | 14.6 | 2 |
| trauma | 31.0% | 9.9% | 5.5 | 14.9 | 0 |
| burns | 22.8% | 2.6% | 4.7 | 14.9 | 0 |
| neurosurgery | 31.8% | 12.9% | 5.4 | 14.9 | 1 |
| oncology | 36.5% | 18.1% | 10.3 | 30.0 | 0 |
