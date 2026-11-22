# Sealed evaluation protocol

Written on 22 Nov 2026, 04:15 UTC (23:15 ET on 21 Nov), before any tool or judge has run on the sampled points. Harness: `scripts/eval_sealed.py` (eval_sealed_v1). Decision: D044 item 4.

The seal at the end of this file is the sha256 of every byte above it. The harness refuses the sealed half if this file, the point list or the judge prompts no longer match their hashes. Wording rule for anything public: "sealed and hashed", never "pre-registered"; the judges are "AI models", never "blind judges" alone.

## The question

For a random place in India and a service a patient needs, does each tool return a facility whose own record clearly shows it offers that service, and how often does each tool stay silent when the record of a facility inside the same radius shows it?

The headline is the cost of our caution: how often our ranker abstains, or passes over a nearer facility, when the AI judges find a facility in the radius whose record clearly shows the service.

## The sample

- Population: geocoded post offices (`scripts/wp2_places.py load_offices`, the same flags the bundle uses) that are not `coord_suspect`, not on a shared placeholder point (`coord_placeholder`), and have a known state. 137,460 offices in 36 states.
- Draw: 120 offices, seed `20261122` (`numpy.random.default_rng`). Stratified by PIN-directory state so no state dominates: each state's share is proportional to the square root of its eligible offices, largest remainder, at most 8 per state; then a uniform draw within each state.
- Per state: Uttar Pradesh 8, Maharashtra 7, Tamil Nadu 7, Andhra Pradesh 6, Gujarat 6, Karnataka 6, Madhya Pradesh 6, Odisha 6, Rajasthan 6, West Bengal 6, Bihar 5, Telangana 5, Assam 4, Chhattisgarh 4, Jharkhand 4, Kerala 4, Punjab 4, Haryana 3, Himachal Pradesh 3, Uttarakhand 3, Arunachal Pradesh 2, Delhi 2, Jammu And Kashmir 2, Manipur 2, Meghalaya 2, Goa 1, Ladakh 1, Mizoram 1, Nagaland 1, Puducherry 1, Sikkim 1, Tripura 1.
- Split: sorted by state then a random order within state, alternating dev and sealed, so each half has 60 points and each state is split within one point.
- Services: `emoc` and `dialysis` at every point; one more service line at a random 20 points of each half, drawn uniformly from the other 10 lines (dev: burns 2, cardiac_surgery 2, cath_lab 1, ct 3, mri 2, neurosurgery 4, nicu 1, oncology 2, trauma 3; sealed: cath_lab 4, ct 5, icu 4, mri 1, neurosurgery 1, nicu 3, oncology 1, trauma 1). 140 queries per half, 280 in all.
- Files: `data/labels/eval_sealed/points.csv` and `queries.csv`.
- Point list sha256: `e6101ca9a54f029058aa83e2daa35655b11b56f8347af5caccc332db8d786501` (canonical form: `points_hash` in the harness; sorted by `po_id`, columns `po_id,pin,state,district,lat,lon,split,third_service`, coordinates to 6 decimals).

## The tools

All three read the same frozen bundle (facility locations after WP1's location checks, `excluded_unresolved` dropped), measure straight-line km from the post office's own coordinates, and use the same radius per service from `vf.services` (km: emoc 50, nicu 75, dialysis 50, icu 75, cardiac_surgery 250, cath_lab 100, mri 100, ct 75, trauma 50, burns 200, neurosurgery 150, oncology 250). Each returns one top pick inside the radius, or abstains when nothing it accepts lies inside it.

- (a) `specialty`: the nearest facility that lists a related specialty (`vf.recipes` claim specialties, for example `gynecologyAndObstetrics` for `emoc`, `nephrology` for `dialysis`). This is the obvious query on the table.
- (b) `any_fact`: the nearest facility with at least one fact naming the service (grade 2 or 3 by our tags), with no trust checks, no duplicate handling and no score.
- (c) `ranker`: `vf.rank.rank(index, service, resolution, k=3)` with the point as a coordinate resolution (precision 2 km); its first card inside the radius. `no_reliable_option` is an abstention.

For each (point, service, tool) the harness stores the pick (facility id, name, km) or the abstention, and the full record text the judges read (`picks_<split>.json`, `records_<split>.json`). For the ranker it also stores the answer status and checks every quote on every card of the answer (results, the beyond-radius card, the unverified list) against the record: exact match to the text of the cited fact, and found anywhere in the record.

## The judges

- Judge A: `claude-haiku-5-5` (`VF_LLM_MODEL`), effort `low`. Judge B: `claude-sonnet-5-5`, effort `low`, thinking off. A different model for B, so the two do not share one model's blind spots. Judge C, used only when A and B disagree or one fails: `claude-opus-5-5`, effort `low`. Structured JSON answers; no temperature setting (these models reject one).
- The judges never see which tool produced a pick, our tags, grades, flags, scores or card text. They see the record as the dataset has it: name, type, operator, listed address and PIN, specialties, stated numbers, description, and every capability, procedure and equipment fact.
- Q1, per (facility, service), with no place: does the record clearly show this facility offers the service? Answer, a quote copied from the record, and a reason.
- Q2, per pick, with the place and the straight-line km: is the pick a sensible place to send this patient, judging from the record (location consistent with the distance, not an unclear merged or multi-branch record, a kind of facility that fits)?
- Abstentions and skipped options: the facilities inside the radius with any signal for the service (a grade of 1 or more by our tags, a related specialty, or a keyword match in their text, regexes below) are judged on Q1 nearest first, until one clearly shows the service or 10 non-pick facilities have been read. The walk stops at the farthest pick when every tool answered, else at the radius.
- Verdict: A and B agree, or else the majority of A, B and C. If no two agree, the unit is unresolved and counts as "not shown".
- Every response is cached on disk and every call is logged with its tokens and cost (`spend.jsonl`). The harness stops before any call that could pass its cap (default $12 for this evaluation) or the project's `VF_LLM_BUDGET_USD` minus every other ledger.

Service needs as the judges read them:

- `emoc`: an emergency caesarean section (C-section) for a woman in labour.
- `nicu`: intensive care for a sick newborn baby (a neonatal intensive care unit or special newborn care unit).
- `dialysis`: haemodialysis for a patient with kidney failure.
- `icu`: intensive care with a ventilator for a critically ill adult.
- `cardiac_surgery`: heart surgery such as coronary bypass (CABG) or heart valve surgery.
- `cath_lab`: emergency heart attack treatment with coronary angiography and angioplasty in a cath lab.
- `mri`: an MRI scan.
- `ct`: a CT scan.
- `trauma`: emergency treatment of a seriously injured road accident victim (trauma care).
- `burns`: treatment of serious burns (a burns unit or specialised burn care).
- `neurosurgery`: brain or spine surgery, including surgery for a serious head injury.
- `oncology`: cancer treatment with chemotherapy or radiotherapy.

Judge prompt sha256: `7fba0ba0095d9c0696050f1aa45c80b002da7c8717116f9a5a7d6ea9c03de19a` (prompt version `j1`: both system prompts, both JSON schemas and the service needs above).

Q1 system prompt:

```
You are reading one health facility's record for a research study in India. The record was compiled automatically from web pages, so it can be incomplete, repetitive, out of date, or mix in text about other places.

You will be told one service a patient needs. Decide whether the record CLEARLY SHOWS that THIS facility offers that service.

- clearly_offers = true only if text in the record states or directly shows that this facility provides the service.
- clearly_offers = false if the record only makes the service seem likely, if the text that mentions the service is about a different facility, branch or organisation, if the mention is negated, or if you cannot tell.
- Judge the text only. Do not use anything you may know about the facility from elsewhere.
- quote: copy, character for character, the one line from the record that best shows the service. Use an empty string if nothing in the record comes close.
- reason: one short plain sentence.
```

Q2 system prompt:

```
You are checking, for a research study in India, whether a facility that a search tool returned is a sensible place to send a patient. You get the patient's place (a post office area), the service needed, the straight-line distance from that place to where the facility is mapped, and the facility's record. The record was compiled automatically from web pages, so it can be incomplete or mix in text about other places.

For this question, assume the facility does offer the service. Decide whether sending this patient there is sensible, judging from the record:
- sensible = false if the record places the facility somewhere clearly inconsistent with the stated distance (for example, its listed city or state is far from the patient's place while the distance is short, or the record says it is located elsewhere);
- sensible = false if the record describes several branches or appears to merge several facilities, so that it is unclear which building the patient would reach;
- sensible = false if this kind of facility does not fit the service (for example, a clinic, laboratory, pharmacy or a specialty centre of an unrelated kind);
- otherwise sensible = true. Distance alone is not a reason for false: every facility shown is inside the same search radius for this service.
- reason: one short plain sentence. Judge the text only.
```

Keyword regexes for the judge pool (case-insensitive, over fact text and description):

- `emoc`: `\b(caesarean|cesarean|caeserean|c[- ]?sections?|lscs)\b`
- `nicu`: `\b(nicu|sncu|neonatal intensive|newborn intensive|special newborn care)\b`
- `dialysis`: `\b(dialysis|haemodialysis|hemodialysis)\b`
- `icu`: `\b(icu|intensive care|critical care unit|ventilators?)\b`
- `cardiac_surgery`: `\b(cabg|bypass surgery|open[- ]heart|valve (replacement|repair|surgery)|cardiac surgery|cardiothoracic)\b`
- `cath_lab`: `\b(cath[- ]?lab|catheteri[sz]ation lab|angioplasty|coronary angiography|ptca|stents?)\b`
- `mri`: `\b(mri|magnetic resonance)\b`
- `ct`: `\b(ct[- ]?scans?|cect|computed tomography|slice ct|ct imaging)\b`
- `trauma`: `\b(trauma|polytrauma|accident (and|&) emergency)\b`
- `burns`: `\b(burns? (unit|ward|care|centre|center|icu|management|treatment)|burn injur\w*)\b`
- `neurosurgery`: `\b(neurosurg\w*|craniotomy|brain surgery|spine surgery|head injur\w*)\b`
- `oncology`: `\b(chemotherapy|radiotherapy|radiation therapy|linear accelerator|linac|oncology)\b`

## The metrics

Per tool, over the same queries, each with a 95% Wilson interval:

1. Places answered (coverage): queries with a pick, of all queries. Computed over every query, judged or not.
2. Precision of the top pick: picks whose record the judges say clearly shows the service, of picks. Also reported: picks that are also sensible for the place (Q1 and Q2).
3. Abstained when a supported option existed: abstentions where the judges found a facility inside the radius whose record clearly shows the service, of abstentions and of all queries.
4. Skipped a nearer supported option: picks where a judge-supported facility is at least 1 km nearer and is not in the pick's duplicate group (WP11), of picks.
5. Card quotes verbatim (ranker only): quotes on every card that equal the cited fact's text, and that appear anywhere in the record.

Coverage and precision are always reported as a pair. The report leads with the cost of our caution (metrics 3 and 4 for the ranker), and every "missed" figure is called a lower bound: the judges read only facilities with some signal for the service, at most 10 per query, and only text in this dataset. A "no" from the judges is about the record, never about the facility: the wording is "the record does not show", not "does not offer".

The judges do not see trust warnings, so some "skipped" options are skipped by the ranker on purpose (for example, a merged record or a location conflict). The report breaks them out rather than hiding them.

## Human anchor

`human_labels_<split>.csv`: 40 Q1 items for Hruday to label by hand, drawn round-robin (seed `20261122`) across cells (tool that picked it, judges' answer), (read only during a walk, judges' answer) and (A and B disagreed, answer), then shuffled. The file shows the service, the place and km for picks, and the record; no tool, judge answer or stratum. The key is in `human_key_<split>.csv`. `agreement` reports raw agreement and Cohen's kappa of the verdict, judge A and judge B against the hand labels, for Q1 and Q2. Kappa is a footnote if the labels come late; the human labels never change the judges' verdicts.

## Rules

1. Dev first. The dev half may be judged repeatedly; prompts, the walk cap and the judge models may change on dev only, before the sealed run, and only through a dated amendment appended below the seal and sealed in turn (`seal`). The sealed half is never used to change anything.
2. The sealed half runs once, on the frozen bundle (the 04:00 ET freeze), with `--split sealed --unseal`. The run records the bundle manifest sha256, the silver files' sha256, the protocol seal and the prompt hash. If it is interrupted, it resumes from the cache; it is not restarted with changes.
3. Every number is reported, including ones that are bad for us. No query is dropped after the fact; unresolved and walk-capped queries are counted and shown.
4. No tuning of the ranker on anything the judges said about the sealed half. The ranker's weights and radii stay as frozen.
5. If the budget stops the sealed run, the report covers the fully judged queries, in the harness's fixed seeded order, and says how many were not judged.

## Expected cost

Measured on the dev dry run and written to `cost_estimate_dev.json`; reported with the harness result.

Protocol sha256 (of every byte above this line): `6dae9ca7aff2cd749401d5661237073760d36cda428667ce4ff2b62dc0713dc6`

## Amendment 1

Written on 22 Nov 2026, 04:30 UTC (23:30 ET on 21 Nov), after the dev dry run and before any tool or judge has run on the sealed half. Nothing above this heading is changed: edits above a seal are refused. This amendment changes no part of the sample, the services, the tools, the radii, the judge prompts (prompt sha256 unchanged), the judge models, the walk cap or the verdict rule. It corrects one date, makes the rules enforceable and adds to the report.

1. Correction. The first line says "Written on 22 Nov 2026, 04:15 UTC". That time is wrong. This protocol was sealed at 04:11:42 UTC (23:11 ET on 21 Nov). The dry-run judge calls on the dev half ran from 04:11:50 UTC to 04:12:29 UTC (28 calls in `spend.jsonl`). Two model checks on a facility outside the sample ran at 04:11:33 and 04:11:35 UTC, before the seal. No record shows a tool or judge run on the sampled points before the seal; the dry run's picks file was later replaced, at 04:15:06 UTC, by a rerun with identical picks.
2. Tamper evidence. The seal chain (the original seal, then the seal of this amendment) is pinned in the harness (`EXPECTED_SEALS`). The sealed half refuses to run unless the seal lines in this file are exactly that chain. Its picks and judging also refuse to run unless this file, `scripts/eval_sealed.py` and `src/vf` are committed and unmodified (`git status` lists nothing under them), so a commit dates the protocol and the code. A later amendment needs its seal added to the harness in the same commit.
3. Code. The sealed half runs from a clean commit made after the 04:00 ET freeze. The ranker (`vf.rank`) and the specialty baseline's specialty sets (`vf.recipes` claim specialties) are the ones in that commit, not only the frozen bundle. The sha256 of the harness, this file and every `src/vf` module is recorded in the picks and judged files.
4. Rules enforced in code. The sealed picks are computed once: an existing `picks_sealed.json` is never overwritten, and an interrupted run resumes through `judge`, which reads it and the judge cache. Sealed judging refuses a walk cap other than 10, any judge other than the three named above (model, effort, and thinking off for judge B, sent as `between_tools`, the setting that turns thinking off on that model), and any change to the code or the seal between the sealed picks and the judging. Spend caps only stop a run early; rule 5 covers that case.
5. Report additions. No judged question changes.
   - Metrics 3 and 4 are split by whether the nearest judge-supported facility carries a WP11 major flag for the service in the bundle (the ranker passes such records over on purpose; the judges do not see flags), as the paragraph on trust warnings above promises.
   - Precision is also reported without "listing-only" yes votes: a yes where every judge who said yes quoted only text from the record's "Specialties listed" line. The specialty baseline selects on that field. The Q1 rubric is unchanged; if more dev judging shows such votes are common, a rubric amendment (prompt version `j2`) may follow, before the sealed run only.
   - Metric 5 is not an evaluation result. The quote on a ranker card is, by construction, the text of the fact it cites, so the figure is reported as an integrity check of the bundle against the silver facts, apart from the evaluation table.
   - Each judge run is listed with its API calls and why it stopped, with the call count and spend for the half from `spend.jsonl`.
6. Human anchor. Every item in `human_labels_<split>.csv` shows a place and a km: for a facility read only during a walk, the first query in the seeded order whose walk read it. Q2 is asked on every item; only items whose judges answered Q2 (picks) enter the Q2 agreement. Phone numbers and email addresses are masked in that file only; the judges read the record as the dataset has it.
Protocol sha256 (of every byte above this line): `51e25495d6c0a64935a524994af4a21f1e9cb66ee054fc756e21988d18f07e0f`

## Amendment 2

Written on 22 Nov 2026, 04:31 UTC, dev half only, before any tool or judge has run on the sealed half. Changes only what is reported.

1. Besides the strict "listing-only" reading in Amendment 1 (every judge who said yes quoted only the "Specialties listed" line), the report also gives a loose reading: at least one judge who said yes quoted only that line. Precision is shown without each, and the "skipped" and "abstained" counts give both. In the dev dry run the one "skipped a nearer option" case is loose but not strict: judge A quoted the specialties line, judge B a department list in the description.
Protocol sha256 (of every byte above this line): `01ef778314f40f47c93e7bfba06b98c80849ce62122e1b2f78e2739a8d589ded`

## Amendment 3: the same drive-time window for every tool

Written on 22 Nov 2026, 10:17 UTC (05:17 ET), after the final build was frozen and before any tool or judge has run on the sealed half. The frozen build is recorded in `reports/FROZEN_BUILD.json` (git commit 17bf7e6, bundle manifest sha256 `520a5159b49da8786b2de7885a6129b76d52fb19cf18a6bbe925fd72ed3158da`, the same manifest the deployed app serves); it was frozen at 10:15 UTC (05:15 ET), later than the 04:00 ET named in rule 2, and that frozen build is the one the sealed half reads. Decision D048 replaces the ranker's km radii with estimated drive-time windows. The protocol's fairness rule ("the same radius per service" and "the same right to abstain" for every tool) is kept by applying one window rule to all three tools, to the judge walk and to every metric. Nothing else changes: the sample, the services, the judge models, the judge prompts (prompt sha256 as last sealed), the walk cap and the verdict rule stay as they are.

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
Protocol sha256 (of every byte above this line): `84a7bdd667c547a48ae6621fd4d1d2d7d4996854ad2932755d97dfe3d2fcee06`
