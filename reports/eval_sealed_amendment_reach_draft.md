# DRAFT amendment to the sealed evaluation: drive-time windows instead of km radii

**Status: DRAFT, NOT SEALED.** Written on 22 Nov 2026 around 04:50 UTC by the dev-rubric agent for decision D048. The orchestrator seals it, by appending the text below the last seal of `reports/eval_sealed_protocol.md` and running `python scripts/eval_sealed.py seal`, only after the final ranker is frozen and the harness implements it. Nothing in this file changes the evaluation until then. The sealed half has not been run, and nothing here was informed by it.

The text between the two rules below is the proposed amendment, ready to paste. The amendment number is a placeholder: use the next free number at sealing time.

---

## Amendment N: the same drive-time window for every tool

Written on <date, UTC>, before any tool or judge has run on the sealed half. Decision D048 replaces the ranker's km radii with estimated drive-time windows. The protocol's fairness rule ("the same radius per service" and "the same right to abstain" for every tool) is kept by applying one window rule to all three tools, to the judge walk and to every metric. Nothing else changes: the sample, the services, the judge models, the judge prompts (prompt sha256 as last sealed), the walk cap and the verdict rule stay as they are.

1. Drive time. For a query at post office `p` and a facility at straight-line distance `km` from `p`'s own coordinates:

   `minutes = f(state of p) x 2.30 x km^0.842`

   `f` is the multiplier in `reference/terrain_multiplier.csv` for the post office's PIN-directory state, matched in upper case (for example "Jammu And Kashmir" to "JAMMU AND KASHMIR"). The origin's state is used for every facility, never the facility's state, so for one query minutes rise strictly with km. The point estimate is used; the displayed range (minutes x e^(plus or minus 0.52)) plays no part in any gate. Minutes are compared unrounded. No sampled state lacks a factor (the two states with a blank factor, Andaman and Nicobar Islands and Lakshadweep, are not in the sample); if one did, the harness refuses to run rather than guess.

2. Windows (minutes), per service, the same for every tool:

   | Service | Window (min) | Basis (D048) | Old radius (km) | km at factor 0.80 | km at 1.00 | km at 3.08 |
   | --- | --- | --- | --- | --- | --- | --- |
   | `emoc` | 120 | cited (Lancet Commission 2-hour indicator) | 50 | 143 | 110 | 29 |
   | `trauma` | 120 | cited (same) | 50 | 143 | 110 | 29 |
   | `cath_lab` | 90 | design choice informed by ESC 2023 | 100 | 101 | 78 | 20 |
   | `neurosurgery` | 150 | informed by Seelig 1981 | 150 | 186 | 143 | 38 |
   | `dialysis` | 60 | informed by DOPPS | 50 | 63 | 48 | 13 |
   | `icu` | 120 | design choice | 75 | 143 | 110 | 29 |
   | `nicu` | 120 | design choice | 75 | 143 | 110 | 29 |
   | `ct` | 120 | design choice | 75 | 143 | 110 | 29 |
   | `mri` | 180 | design choice | 100 | 231 | 177 | 47 |
   | `burns` | 240 | design choice | 200 | 325 | 250 | 66 |
   | `cardiac_surgery` | 240 | design choice | 250 | 325 | 250 | 66 |
   | `oncology` | 240 | design choice | 250 | 325 | 250 | 66 |

   The km columns are the window converted back to straight-line km, `(window / (2.30 x f))^(1/0.842)`, for the lowest factor in the file (0.80), a factor of 1, and the highest (3.08, Arunachal Pradesh). They are for reading only; the gate is the minutes. All windows are planning thresholds, not clinical deadlines. If the frozen ranker's windows or formula differ from this table, this amendment is wrong and must be rewritten before it is sealed; it is not sealed with a mismatch.

3. Inside the window. A facility is inside the window for a query when its minutes from that query's post office are at most the service's window. This replaces "inside the radius" everywhere in the protocol: in each tool's right to abstain, in the judge walk, and in metrics 3 and 4. Because minutes rise strictly with km for one origin, "inside the window" is the same as "within the query's window km", `(window / (2.30 x f))^(1/0.842)`, and "nearest by minutes" is the same as "nearest by km".

4. The three tools under the window.
   - (a) `specialty`: among facilities with a usable location (the frozen bundle, `excluded_unresolved` dropped, as before) that list a related specialty (`vf.recipes` claim specialties for the line, as committed for the sealed run), the one with the fewest minutes from the post office, if its minutes are at most the window; otherwise it abstains. Ties: lower index position, as before. No trust checks, no duplicate handling, no evidence allowance.
   - (b) `any_fact`: among facilities with a usable location and grade 2 or 3 for the line by our tags (the frozen bundle's grades, the same recipe version the ranker gates on), the one with the fewest minutes, if inside the window; otherwise it abstains. No trust checks, no duplicate handling, no evidence allowance.
   - (c) `ranker`: `vf.rank.rank(index, service, resolution, k=3)` with the point as a coordinate resolution (precision 2 km), as before. Its pick is its first result card whose minutes, recomputed by the harness with item 1 from the post office's coordinates and the facility's mapped location, are at most the window. If no result card is inside the window, or the answer is `no_reliable_option`, it abstains. The ranker may apply its declared evidence allowance (a better-evidenced facility may come first if at most 15 minutes further, 30 for MRI, cardiac surgery and oncology) inside the window; the baselines have no allowance. The harness also records the minutes and window the ranker itself reports on each card, and the sealed run refuses to start if, on the dev half, any ranker card's minutes differ from the harness's by more than 0.5 minutes or any card's window differs from the table above.

5. Judge walk. The walk stops at the farthest pick when every tool answered (unchanged), else at the query's window km (item 3) in place of the radius. Pool membership (any signal for the service), nearest-first order, the walk cap of 10 and the stored pool size are unchanged.

6. Metrics.
   - Metric 3 ("abstained when a supported option existed"): the judge-supported facility must be inside the window.
   - Metric 4 ("skipped a nearer supported option"): unchanged in form: a judge-supported facility at least 1 km nearer than the pick, not in the pick's WP11 duplicate group. For one origin, 1 km nearer is also fewer minutes. A new split is added for the ranker: skips where the pick is at most the evidence allowance further in minutes than the skipped facility (by design: the ranker chose a better-evidenced record within its declared allowance) and skips beyond it. Both counts are reported; neither is dropped.
   - Each pick and each nearest judge-supported option is stored with its km and its minutes.
   - Coverage (metric 1) is computed over every query under the window rule.

7. Judges. Q1 is unchanged and place-free, so cached Q1 verdicts on the dev half are reused where the facility and service repeat. Q2 still shows the straight-line km (the Q2 prompt and its user message are unchanged, so the prompt sha256 is unchanged); its sentence "every facility shown is inside the same search radius for this service" remains true of the window, which is one search distance per query applied to every tool.

8. Sea-pixel post offices. D048 item 7 drops the 1,032 sea-pixel post offices (`reference/sea_pixel_post_offices.csv`) from the product's coverage numbers and origins. The evaluation sample was drawn and sealed before that list existed in the evaluation, and rule 3 forbids dropping a query after the fact. So sampled points on the list stay in the sample, are run and judged like any other, and are counted in the headline numbers. The report also gives each headline figure without them, as a sensitivity line, and names how many there are per half. (On the dev half: 2 of 60 points, po_id 142781 and 13304.)

9. Re-running the dev half. The dev picks are recomputed with the window rule (`picks --split dev`), then judged (`judge --split dev`), scored and estimated, before the sealed run. The sealed picks are computed once, after this amendment is sealed and the harness that implements it is committed (Amendment 1 item 2).

10. Harness. The windows table, the formula's constants (2.30, 0.842) and the sha256 of `reference/terrain_multiplier.csv` are pinned in `scripts/eval_sealed.py` and recorded in the picks file; `reference/terrain_multiplier.csv` joins the guarded paths for the sealed half. The harness reads the windows from the same code the ranker uses (`vf.services` or wherever the frozen ranker keeps them) and refuses the sealed half if they differ from the pinned table.

---

## Notes for the orchestrator (not part of the amendment)

- What the harness needs before this is sealed: a `drive_minutes(km, state)` helper (shared with the ranker if the ranker has one), a `WINDOW_MIN` table pinned as above, `nearest_in` changed to take a per-query km limit computed from the window, `tool_ranker` changed to test minutes, `run_tools` using the window km for `d_stop`, minutes stored on picks and pool entries, the skip split in `outcome_rows` and `summarize`, `reference/terrain_multiplier.csv` added to `GUARDED_PATHS`, the dev check in item 4, the new seal added to `EXPECTED_SEALS`, and tests.
- Windows are larger in km than the old radii for most services at a factor near 1 (emoc 110 km against 50), so pools and walks grow. Expect the dev re-run to cost more than the first dev run's Q2 and walk calls; Q1 calls repeat only for facilities not judged before. Run `estimate --split dev` after the new dev picks.
- In hill states the windows are much shorter in km (emoc 29 km at factor 3.08), so more abstentions are expected there for every tool alike. That is the rule working, not a bug.
- If the frozen ranker gates on a different rule (for example a facility-side factor, or the range rather than the point estimate), rewrite items 1, 3 and 4 to that rule before sealing, so all three tools use the same one.
