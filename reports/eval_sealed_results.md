# Sealed evaluation: results

Run on 22 Nov 2026, 10:20 to 10:25 UTC, once, on the frozen build. Protocol: `reports/eval_sealed_protocol.md` (sealed and hashed; Amendment 3 puts every tool on the same drive-time window). Harness: `scripts/eval_sealed.py`. Full machine output: `data/labels/eval_sealed/results_sealed.md` and `results_sealed.json`. The judges are AI models (Claude Haiku 5.5 and Sonnet 5.5, Opus 5.5 on disagreements), blind to which tool produced each pick.

## The cost of our caution

**When our ranker stays silent, the AI judges found nothing it missed. When it answers, it passes over a nearer facility whose record clearly shows the service in about one answer in six, mostly on purpose.**

- Abstentions: the ranker said "no reliable option" on 39 of 140 sealed queries (28%). In 0 of those 39 (0%; 95% CI 0% to 9%) did the judges find a facility inside the same window whose record clearly shows the service. Caveat: this is a lower bound, and a thin one. In 31 of the 39 there was no facility with any signal for the service inside the window at all, so there was nothing to miss in this dataset; in the other 8 the judges read 1 to 9 facilities each (32 in all) and none clearly showed it. The judges read only this dataset's text.
- Skipped a nearer option: of the ranker's 101 answers, 16 (16%; 95% CI 10% to 24%) put a facility first while a different record at least 1 km nearer clearly shows the service to the judges.
  - Inside the declared evidence allowance (the pick at most 15 minutes further, 30 for MRI, cardiac surgery and oncology): 13 of 101 (13%; 95% CI 8% to 21%). In 12 of these the nearer facility is on the ranker's own answer, as card 2 or 3: the allowance put a better-evidenced record first, as designed.
  - Beyond the allowance: 3 of 101 (3%; 95% CI 1% to 8%), all emergency C-section queries, with the pick 29 to 43 minutes further than the nearer option.
  - The real misses: in 4 of the 16 (the 3 beyond the allowance and 1 NICU query inside it), the nearer facility is graded 1 ("claimed") by recipe v0.3, so it never passed the ranker's gate, while the judges read its record as clearly showing the service. These are grading misses, not ordering choices.
  - None of the 16 nearer options carries a WP11 major flag, and in none does the judges' yes rest only on the specialties listing.
- For comparison, on the same queries: the any-fact baseline skipped a nearer supported option in 4 of 101 answers (4%), the specialty filter in 1 of 115 (1%). Nearest-first tools skip less by construction.

## Coverage and precision per tool (sealed half, 140 queries)

Same frozen bundle, same drive-time window per service, same right to abstain for every tool. Intervals are 95% Wilson.

| | Specialty filter | Any fact names it | Our ranker |
| --- | --- | --- | --- |
| Places answered (coverage) | 115 of 140 (82%; 75% to 88%) | 101 of 140 (72%; 64% to 79%) | 101 of 140 (72%; 64% to 79%) |
| Top pick: record clearly shows the service (precision) | 23 of 115 (20%; 14% to 28%) | 85 of 101 (84%; 76% to 90%) | 90 of 101 (89%; 82% to 94%) |
| Precision without listing-only yes votes (strict and loose) | 23 of 115 (20%) | 85 of 101 (84%) | 90 of 101 (89%) |
| Top pick also judged sensible for the place | 19 of 115 (17%; 11% to 24%) | 55 of 101 (54%; 45% to 64%) | 63 of 101 (62%; 53% to 71%) |
| Abstained when a judge-supported option existed (of abstentions) | 1 of 25 (4%; 1% to 20%) | 0 of 39 (0%; 0% to 9%) | 0 of 39 (0%; 0% to 9%) |
| Skipped a nearer judge-supported option (of answers) | 1 of 115 (1%; 0% to 5%) | 4 of 101 (4%; 2% to 10%) | 16 of 101 (16%; 10% to 24%) |
| Wrong top pick, as a share of all queries | 92 of 140 (66%; 58% to 73%) | 16 of 140 (11%; 7% to 18%) | 11 of 140 (8%; 4% to 14%) |

What this says:

- **The ranker beats the obvious specialty query by a wide margin**: 89% against 20% precision. The specialty filter answers more places (82% against 72%) but its top pick's record clearly shows the service only one time in five; for emergency C-section, 4 of 58.
- **The ranker is not shown to beat the simpler any-fact baseline.** They answer exactly the same 101 queries (both need a grade 2 or 3 record inside the window; on this half the ranker's flag checks never removed the last such option) and give the same top pick in 76. On the 101 shared answers, the ranker's pick is supported and the baseline's is not in 6 queries, the reverse in 1 (exact sign test p = 0.125). On the dev half the same comparison was 9 against 1 (p = 0.02). The direction holds on both halves; the sealed half alone does not establish it.
- **The ranker's 11 wrong picks**: 4 ICU (the record shows an ICU but not ventilator care, which the question asks for), 5 dialysis (the dialysis text is about another hospital or an associated centre, a children's unit only, or a generic procedure list), 2 emergency C-section (a merged multi-city record; caesarean deliveries without emergency care stated).
- **"Sensible for the place" is the weakest number for every tool.** 34 of the ranker's 101 picks were judged not sensible. A keyword read of the judges' reasons (crude, categories overlap): 33 of 34 mention a record that merges branches or several facilities, 20 a listed city inconsistent with the distance. Merged records stay recommendable in the ranker (they lose the evidence allowance, D058, but are only blocked when the supporting fact names another institution or far city, D061). The judges also agree least on this question (below), so read it as a direction, not a rate.

By service, precision of the top pick (sealed):

| Service | Specialty filter | Any fact | Our ranker |
| --- | --- | --- | --- |
| Emergency C-section (60 queries) | 4 of 58 | 44 of 49 | 47 of 49 |
| Dialysis (60) | 15 of 40 | 28 of 35 | 30 of 35 |
| Third service, one per point (20) | 4 of 17 | 13 of 17 | 13 of 17 |

## Card quotes verbatim (integrity check, not an evaluation result)

Every quote on every ranker card (results, the beyond-window card and the unverified list) equals the text of the fact it cites: 451 of 451, and all 451 appear in the record. This holds by construction, so it confirms the bundle and the silver facts agree; it does not measure reading accuracy.

## Judge agreement

| | Units | A and B agree | Cohen's kappa (A vs B) | Sent to judge C |
| --- | --- | --- | --- | --- |
| Q1: record clearly shows the service | 522 | 500 (95.8%; 93.7% to 97.2%) | 0.87 | 22 |
| Q2: sensible for the place | 216 | 154 (71.3%; 64.9% to 76.9%) | 0.44 | 62 |

No unit was left unresolved. Q1, which carries the precision and the cost-of-caution figures, is reliable between the two models. Q2 is not: judge A (Haiku) said "sensible" on 66 of 216, judge B (Sonnet) on 114.

Human check: 40 items for Hruday are in `data/labels/eval_sealed/human_labels_sealed.csv` (instructions: `reports/HUMAN_LABELING.md`). **Not labeled yet; no human agreement figure exists.** The items are stratified across tools, outcomes and judge disagreements, so the agreement they give will be a check on hard and easy cases, not an accuracy estimate.

## Dev half, re-run under the window rule (for reference)

Same commit, judged before the sealed half. The dev half was used for design decisions (the Q1 rubric check, D053), so its numbers are not clean evidence.

| | Specialty filter | Any fact names it | Our ranker |
| --- | --- | --- | --- |
| Coverage | 114 of 140 (81%; 74% to 87%) | 93 of 140 (66%; 58% to 74%) | 93 of 140 (66%; 58% to 74%) |
| Precision | 26 of 114 (23%; 16% to 31%) | 81 of 93 (87%; 79% to 92%) | 89 of 93 (96%; 89% to 98%) |
| Also sensible | 16 of 114 (14%) | 47 of 93 (51%) | 59 of 93 (63%) |
| Abstained with a supported option | 1 of 26 | 0 of 47 | 0 of 47 (0%; 0% to 8%) |
| Skipped a nearer supported option | 3 of 114 | 4 of 93 | 16 of 93 (17%; 11% to 26%): 14 inside the allowance, 2 beyond |

The earlier dev run under the old km radii and the v0 ranker (D053) had the ranker at 76 of 82 precision and skipping a nearer option in 23 of 82 (28%). The dev half holds 2 sea-pixel post offices; without them the ranker's dev precision is 85 of 88. The sealed half holds none, so its sea-pixel sensitivity line equals the headline.

## How it was run

- Freeze: `reports/FROZEN_BUILD.json` (10:15 UTC, commit 17bf7e6, bundle manifest sha256 `520a5159...`, every bundle file and 34 silver and gold tables hashed; the deployed app serves the same manifest and the same `vf` code). `scripts/freeze_build.py --check` reported no difference before the sealed picks and after scoring. The freeze came at 05:15 ET, later than the 04:00 ET the protocol names; Amendment 3 records this.
- Seal: Amendment 3 sealed (`84a7bdd6...`) and pinned in `EXPECTED_SEALS`, committed with the harness as c09c630. Harness tests: 50 passed, 1 skipped (the test for the unsealed state); full suite 1,120 passed.
- Dev: picks under the window rule at c09c630 (ranker card check: 228 of 228 cards report minutes within 0.05 of the harness and the pinned window), judged and scored.
- Sealed: picks once at 10:20:08 UTC from the clean commit c09c630 (card check 264 of 264), judged 10:20 to 10:25 UTC with the protocol's judges and walk cap, all 140 queries complete, no stop. Scored once. Not re-run.
- Spend: dev re-run $1.30 (403 API calls), sealed half $4.61 (1,374 calls; estimated $4.22, high $5.52). This evaluation's ledger: $10.57. Project total across all ledgers: $18.25, under the $28 cap.

## Limits

- **AI judges.** The verdicts are from Claude models reading the record text. A "no" means the record does not clearly show the service, never that the facility lacks it. No one has yet checked the judges by hand on the sealed half.
- **Agent-built tools, agent-built judge harness, same model family.** The ranker, both baselines, the harness and the judges all come from Claude models. Shared blind spots between builder and judge are possible, and the two-model judge design only partly guards against them.
- **The any-fact baseline shares our tags.** It is a fair test of what the ranker's trust checks and ordering add, not an independent check of the tags. Both depend on recipe v0.3; the 4 grading misses above affect both.
- **One draw.** 60 points and 140 queries per half, stratified by state with square-root allocation, so small states are over-represented and the rates are per sampled place, not per person. Intervals are wide: the ranker's precision could be anywhere from 82% to 94%.
- **Lower bounds.** Every "missed" figure counts only facilities with some signal for the service, at most 10 per query, in this dataset.
- **Drive time is estimated** from straight-line distance (state factor x 2.30 x km^0.842, free-flow); windows are planning thresholds, not clinical deadlines.
- **Q2 is unreliable** between judges (kappa 0.44); the "sensible" row is a direction only.
