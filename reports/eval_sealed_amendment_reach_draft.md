# DRAFT amendment to the sealed evaluation: drive-time windows instead of km radii

**Status: DRAFT, NOT SEALED. Implemented in the harness, inactive until sealed.** Written on 22 Nov 2026 around 04:50 UTC by the dev-rubric agent for decision D048; items 4 and 10 tightened on 22 Nov when `scripts/eval_sealed.py` implemented it. The orchestrator seals it, by appending the text below the last seal of `reports/eval_sealed_protocol.md` and running `python scripts/eval_sealed.py seal`, only after the final ranker is frozen. Nothing in this file changes the evaluation until then. The sealed half has not been run, and nothing here was informed by it.

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
   - (c) `ranker`: `vf.rank.rank(index, service, resolution, k=3)` with the point as a coordinate resolution (precision 2 km), as before, which now also carries the post office's PIN-directory state, so the ranker takes the same origin factor as item 1. Its pick is its first result card whose minutes, recomputed by the harness with item 1 from the post office's coordinates and the facility's mapped location, are at most the window. If no result card is inside the window, or the answer is `no_reliable_option`, it abstains. The ranker may apply its declared evidence allowance (a better-evidenced facility may come first if at most 15 minutes further, 30 for MRI, cardiac surgery and oncology) inside the window; the baselines have no allowance. The harness also records the minutes and window the ranker itself reports on each result card. The sealed picks refuse to start unless the dev picks were computed under this sealed amendment with the same code (the harness, this file, `src/vf` and both reference files in item 10), and on those dev picks every ranker result card, at least one, reports minutes within 0.5 minutes of the harness's and the window in the table above.

5. Judge walk. The walk stops at the farthest pick when every tool answered (unchanged), else at the query's window km (item 3) in place of the radius. Pool membership (any signal for the service), nearest-first order, the walk cap of 10 and the stored pool size are unchanged.

6. Metrics.
   - Metric 3 ("abstained when a supported option existed"): the judge-supported facility must be inside the window.
   - Metric 4 ("skipped a nearer supported option"): unchanged in form: a judge-supported facility at least 1 km nearer than the pick, not in the pick's WP11 duplicate group. For one origin, 1 km nearer is also fewer minutes. A new split is added for the ranker: skips where the pick is at most the evidence allowance further in minutes than the skipped facility (by design: the ranker chose a better-evidenced record within its declared allowance) and skips beyond it. Both counts are reported; neither is dropped.
   - Each pick and each nearest judge-supported option is stored with its km and its minutes.
   - Coverage (metric 1) is computed over every query under the window rule.

7. Judges. Q1 is unchanged and place-free, so cached Q1 verdicts on the dev half are reused where the facility and service repeat. Q2 still shows the straight-line km (the Q2 prompt and its user message are unchanged, so the prompt sha256 is unchanged); its sentence "every facility shown is inside the same search radius for this service" remains true of the window, which is one search distance per query applied to every tool.

8. Sea-pixel post offices. D048 item 7 drops the 1,032 sea-pixel post offices (`reference/sea_pixel_post_offices.csv`) from the product's coverage numbers and origins. The evaluation sample was drawn and sealed before that list existed in the evaluation, and rule 3 forbids dropping a query after the fact. So sampled points on the list stay in the sample, are run and judged like any other, and are counted in the headline numbers. The report also gives each headline figure without them, as a sensitivity line, and names how many there are per half. (On the dev half: 2 of 60 points, po_id 142781 and 13304.)

9. Re-running the dev half. The dev picks are recomputed with the window rule (`picks --split dev`), then judged (`judge --split dev`), scored and estimated, before the sealed run. The sealed picks are computed once, after this amendment is sealed and the harness that implements it is committed (Amendment 1 item 2).

10. Harness. The windows table, the formula's constants (2.30, 0.842), the evidence allowance and the sha256 of the two reference files are pinned in `scripts/eval_sealed.py` and recorded in the picks file: `reference/terrain_multiplier.csv` sha256 `a3695f7bd3466921a9b97e0e98a29efb4b9d41c72f6df8a627b1f7ed0964a344`, `reference/sea_pixel_post_offices.csv` sha256 `5278e3d7b6cfd25ff5194aa8d974b1439f6d5c7bedee689f9a1f83b12e0cd557`. Both files join the guarded paths for the sealed half. The harness activates the window rule only when this amendment is sealed and its windows table, formula ("2.30 x km^0.842") and allowance sentence match what it pins. It reads the windows from the same code the ranker uses (`vf.services`) and refuses the sealed half if they differ from the pinned table.

---

## Notes for the orchestrator (not part of the amendment)

### What the harness now does (22 Nov, `scripts/eval_sealed.py`, `tests/test_eval_sealed.py`)

- Implemented: `drive_minutes`, `window_km`, the pinned `WINDOW_MIN`, `ALLOWANCE_MIN`, `DRIVE_A`, `DRIVE_B`, `TERRAIN_SHA256`, `SEA_PIXEL_SHA256`; a `Reach` rule per query (origin state factor, upper case, refusing a blank or missing factor); all three tools, the judge pool and `d_stop` on the window; the ranker pick as its first result card inside the window; minutes on every pick, a parallel `pool_minutes` list (pool entries keep their `[id, km, group]` shape) and on the nearest supported option; the skip split inside and beyond the allowance in `outcome_rows`, `summarize` and the results page; the sea-pixel sensitivity section in `score`; both reference files in `GUARDED_PATHS` and `code_hashes`; the dev card check and the sealed-half refusals of items 4 and 10.
- Activation: `reach_amendment_status()` finds the heading "## Amendment <n>: the same drive-time window for every tool", checks that the first seal line after it covers it, and parses the windows table, the formula text "2.30 x km^0.842" and the allowance text "at most 15 minutes further, 30 for MRI, cardiac surgery and oncology". Keep those three exactly as written when pasting, or the harness will not activate it. Until it is active, dev runs use the radii and the sealed half refuses at every stage. `--reach window` on dev before the seal is a preview written to `picks_dev_window_preview.json`; it never replaces `picks_dev.json`.
- What the frozen ranker must expose for the item 4 check to pass: per result card, the minutes as `minutes` (or `drive_minutes`, `est_minutes`, `travel_minutes`, `minutes_est`, top level or inside a `drive`, `travel`, `drive_time`, `travel_time`, `reach` or `parts` dict) and the window as `window_min` (or `window_minutes`, or `window` as a number or `{minutes}`), on the card or on the answer; and the windows table in `vf.services` as `WINDOWS_MIN` (or `WINDOW_MIN`, `WINDOWS`, or `window_min` on every `Service`). Measured on dev previews: the committed v0 ranker fails the check (0 of 240 cards report minutes), the intended refusal; the uncommitted v1 ranker in the working tree on 22 Nov passes it (229 of 229 cards, largest difference 0.05 minutes, every window the pinned one; its `vf.services.WINDOWS` minutes and allowances equal the pinned tables).

### Sequence after the ranker is frozen

1. Check the frozen ranker's windows, formula and allowance equal item 2 and item 4; if not, rewrite items 1 to 4 first.
2. Paste the text between the rules above below the last seal of `reports/eval_sealed_protocol.md`, with N replaced by 3 and the date filled in; run `python scripts/eval_sealed.py seal`.
3. Add the printed seal to `EXPECTED_SEALS` in `scripts/eval_sealed.py`; commit the protocol, the harness, `src/vf` and `reference/` together.
4. The v0-radius dev outputs (picks, records, judged, judgments, outcomes, results, cost estimate) are archived in `data/labels/eval_sealed/archive_v0_radius/`, since the next step overwrites them. `picks --split dev` (now the window rule by default), then `estimate --split dev`, `judge --split dev --cap-usd 14`, `score --split dev`.
5. `picks --split sealed --unseal` (refuses unless step 4's dev picks pass the card check on the same commit), then `judge --split sealed --unseal --cap-usd 14`, `score --split sealed --unseal`.

### Cost (measured 22 Nov on dev previews under the window rule; no API calls were made)

- Dev re-run, with the judge cache (about 380 Q1 and 170 Q2 units already judged): with the uncommitted v1 ranker, expected $1.24 (low $0.49, high $1.70), about 364 calls; with the committed v0 ranker, expected $1.69 (high $2.53), because it abstains more and every abstention walks to the window.
- Sealed half under the window rule, no cache: expected $4.74 (low $3.01, high $5.80) with the v1 ranker, against $4.55 (high $5.50) under the radii.
- The evaluation ledger already holds $4.67. Expected total $10.65, high $12.17, which passes the default cumulative cap (`DEFAULT_CAP_USD`, $12) at the high end, so pass `--cap-usd 14`. Project budget: $30, with $7.68 in other ledgers at the time of writing (the WP4b ledger may still grow).
- In hill states the windows are much shorter in km (emoc 29 km at factor 3.08), so more abstentions are expected there for every tool alike. That is the rule working, not a bug.
