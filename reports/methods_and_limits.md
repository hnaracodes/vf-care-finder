# Methods and limits

How the care finder turns a messy table of Indian health facilities into a cited answer, what we measured, and what we do not claim. Every number here is in `reports/findings.json` (built and checked by `scripts/wp9_findings.py`). D-numbers point to `docs/08_decisions_log.md`.

## The answer in one paragraph

A user picks a service (one of twelve lines such as emergency C-section, dialysis, NICU or trauma, or free text) and a place (PIN, town, district or coordinates). The tool keeps facilities whose own record has a sentence naming the service, drops records with a serious problem, keeps those inside the service's drive-time window, and lists them nearest first. Every card quotes the facility's own text. When nothing passes, it says no facility in this dataset shows clear evidence of the service within the window, and shows the nearest place that has evidence. No language model writes card text (D030).

## Why: a specialty label is not evidence

The dataset has 9,989 facility records, 88% private, compiled from web pages. 4,489 records list obstetrics as a specialty; only 418 of them have a sentence naming a caesarean section (432 records name one in all), and 373 of those 432 (86%) mention no blood supply. Of 113,653 post-office points on land (549 sea-pixel points dropped, D048), 81.5% are within fifty kilometres (straight line) of a record with the obstetrics label; only 40.2% are that close to one whose text names the procedure. A specialty filter answers more places and is usually wrong: in the sealed evaluation below, its top pick's record clearly showed the service in 23 of 115 answers (20%).

## How

**Data.** The Databricks Marketplace listing "Virtue Foundation Dataset (DAIS 2026)": facilities and the India Post PIN directory (NFHS-5 was dropped at the re-scope, D023). The loader drops junk rows and NUL bytes, reads the string `null` as missing, and dedupes repeated specialty labels. 9,671 records have a usable map position.

**Tags (Model A).** Each fact sentence gets any of 24 evidence tags (for example `csection`, `blood`, `dialysis_unit`, `ventilator`), with a negation filter. Hand-written patterns score macro F1 0.89 (0.87 to 0.91) on 777 gold facts labeled by AI agents. An LLM teacher labeled the texts and small students learned from it (TF-IDF plus logistic regression; a fine-tuned bge-small encoder). Two ship rules were used, both needing the paired bootstrap gain on gold to sit above zero. Round 1 (WP4) shipped `h24`, `icu` and `ventilator` when at least half of 25 texts the student added, read unblinded by the WP4 agent, were right: `h24` 23 of 25, `ventilator` 25 of 25, `icu` only 15 of 25. The `icu` student raised 306 facilities to grade 2 or higher and dropped 85 below it, and some of the new grade 2 ICU facilities are wrong in the way the read found (critical care as a department name). Round 2 (D050) needed at least 20 verdicts in the candidate's favour from a blinded read of 25 texts by an agent that did not build it: the encoder won 23 of 25 on `ob_delivery` (gold F1 0.75 to 0.82) and shipped; on `burns` it passed on gold but won only 15 of 25, so it did not. The shipped mix, patterns on 20 tags and students on 4, scores 0.916 (0.898 to 0.931); gold also decided which tags ship, so that score is slightly optimistic. Honest effect: the second tagger round moved 0 facility-service lines across the grade 2 gate, so it changed no recommendation.

**Recipes and standards.** Tags become a grade per facility and service: 0 none, 1 claimed (a label only), 2 supported (a sentence names the service), 3 corroborated (the service and what it needs are named). Recipe v0.3 follows published standards: WHO 2009 emergency obstetric signal functions (maternal transfusion, not fetal), FBNC 2025 (an SNCU is not a NICU), PM-JAY empanelment (a cath lab needs an adult ICU with a ventilator), and the national dialysis programme. Against v0.2, NICU grade 2 or higher fell from 706 to 694 and cath lab grade 3 from 481 to 205. A grade keeps the exact sentences that earned it; those are the card quotes.

**Trust checks.** Per record: location conflict (position disagrees with the PIN or the text's city), possible merged record (text naming other addresses or institutions), implausible claim (a dental clinic claiming a C-section), and duplicates. 2,471 records (25%) carry a flag; the checks block 1,010 facility-service lines (of the 10,957 at grade 2 or higher). Each signal was reviewed by an AI agent reading the records, not by a person: serious location conflicts were 15 of 15 correct; specialty breadth as a merge signal was 0 of 15 and was dropped. The final merged-record rule (block a line when its supporting sentence points to another institution or a distant city) was right in 26 of 30 fresh agent reads (87%; 70% to 95%).

**Location resolver.** From the PIN directory: coordinates, then PIN, district, town or post office, exact before fuzzy. An ambiguous name such as Aurangabad (Bihar and Maharashtra) returns choices, never a guess, and every answer states how precise the place is. It gets 54 of 55 frozen test cases right (98%), including 5 of 5 ambiguous names. The cases were written by the same agent that wrote the resolver.

**Ranking v1-final (D048).** A number that filters or orders results must be measured, cited, or a declared design choice shown in "How we decided".
- Gates: usable location; grade 2 or higher; no major flag; estimated drive time inside the service window.
- Drive time = state terrain factor x 2.30 x km^0.842 minutes, fitted to 934 free-flow road trips from 30 anchor post offices (leave-one-anchor-out log SD 0.26; the shown band covered 94.6% of held-out trips).
- Windows: emergency C-section and trauma 120 min (the Lancet Commission's 2-hour access indicator); dialysis 60; cath lab 90; ICU, NICU and CT 120; neurosurgery 150; MRI 180; burns, cardiac surgery and oncology 240. Planning thresholds, not clinical deadlines.
- Order: nearest first, with one declared exception: a better-evidenced record may go first if at most 15 minutes further (30 for planned care). Merged-looking records get no exception. The nearest recommendable facility is always on the cards.
- Sensitivity on 1,500 random post offices (mean over the lines): no answer at 12.9%; halving every window gives 44.6%, doubling gives 1.4%. Removing the 15-minute exception changes the top pick at 27.2% of answered origins.
- Against the first additive score (v0) on 26 scenarios, AI judges preferred v1 in 10, v0 in 4, tie in 12 (sign test p = 0.18). These are the same 26 scenarios used to find and fix v1's one earlier wrong answer (Gadchiroli, D061), so v1's 0 wrong in this round is not a fresh result; the rendering showed drive times for v1 only. v1 is not proven better.

**Outside lists (D055, D057).** Government and registry lists add dated, cited lines to cards and never make a facility recommendable. Every candidate link was read by an AI agent, not by a person. A link shows only if that agent-checked precision has a one-sided 95% lower bound of at least 0.90: LaQshya maternity certificates (63 agent-confirmed links; the automatic matcher alone was right 55% of the time), AERB radiotherapy licences (79 links, bound 0.924; 0.879 if the 3 unsure links are counted wrong, below the bar), National Cancer Grid (54 links, bound 0.946). A separate lane shows 1,204 listed facilities not matched to a record here (LaQshya 779, AERB 425), only from lists whose terms allow reuse with acknowledgement. Withheld: MoHFW trauma centres and NABI burn units (no link band passed, and terms need permission), NHP blood banks (only an unofficial copy was reachable).

## Evaluation: a sealed test at random post offices

140 sealed queries at 60 random post offices (emergency C-section and dialysis at every point, a third service at 20); three tools on the same frozen build, with the same window and the same right to abstain: a specialty filter, "any fact names it" (grade 2 or 3, nearest first), and our ranker. Two AI judges (Claude Haiku 5.5 and Sonnet 5.5, Opus 5.5 on disagreements) read each pick blind to the tool. The protocol was sealed and hashed first; the sealed half ran once (D065, D066).

| Sealed half, 140 queries | Specialty filter | Any fact names it | Our ranker |
| --- | --- | --- | --- |
| Queries answered | 115 (82%) | 101 (72%) | 101 (72%) |
| Top pick's record clearly shows the service | 23 of 115 (20%) | 85 of 101 (84%) | 90 of 101 (89%; 82% to 94%) |
| Top pick also judged sensible for the place | 19 of 115 (17%) | 55 of 101 (54%) | 63 of 101 (62%) |

- **Far better than the specialty filter.** For emergency C-section the filter's pick was supported in 4 of 58 answers, the ranker's in 47 of 49.
- **Not shown better than the any-fact baseline.** Same 101 queries answered, same top pick in 76; where they differ, only the ranker is supported in 6 and only the baseline in 1 (sign test p = 0.125).
- **The cost of caution.** The ranker gave no answer to 39 of 140 queries; the judges found a supported option it missed in 0 of 39 (95% CI 0% to 9%). That check is thin: 31 of the 39 had no facility with any signal in the window. When it answered, it put another facility ahead of a nearer supported one in 16 of 101 answers (16%); in 12 the nearer one was on its own cards, as the exception intends, and 4 were grading misses.
- **Judges** agree on "the record shows the service" (kappa 0.87) but not on "sensible for the place" (kappa 0.44), so read that row as a direction only.
- **Human check pending.** 40 items await Hruday's labels; no human agreement figure exists. The gold set, link checks and scenario marks are agent-made.
- Every ranker card quote equals the fact it cites (451 of 451, true by construction). Project LLM spend: $18.25.

## Limits, and what we do not claim

- **No evidence is not evidence of absence.** The tool says "no clear evidence in this dataset", never "no care here". Most records are private; public hospitals are thinly described online.
- **No probabilities.** We do not say "likely", "calibrated" or "validated": no linked list was large enough to calibrate one.
- **Drive times are estimates** from straight-line distance at free-flow speed; real trips take longer, and sea travel is not modelled. **Windows are planning thresholds,** not clinical deadlines.
- **Records can be wrong.** Some merge branches in several cities. The judges called 34 of the ranker's 101 sealed picks not sensible for the place; a crude keyword read of their reasons suggests most cite a merged record or a city that does not fit the distance. Merged records stay recommendable unless the supporting sentence itself points elsewhere.
- **Web sources are not independent.** Social and directory sites appear in most records, so cards say "found on N websites", not "confirmed". The data has no per-fact source, so a quote names its field, not its web page.
- **Positions are often approximate** (PIN-area centres). Cards say so.
- **The ordering exception is a design choice,** not a measured optimum; it often puts a better-documented hospital ahead of a nearer one.
- **A missing list entry proves nothing.** Lane rows are "not matched to a record in this dataset".
- **Process.** The build froze later than the protocol named (recorded in Amendment 3). The dev half informed design, so only the sealed half is clean. The Databricks notebook matched the local counts on the earlier build; the final bundle was built locally and deployed to the same workspace.
- **Call first.** The tool shows what a record says; each card lists questions to ask by phone before the trip.

The proposals that follow from these numbers (evidence-filtered referral lists, recording blood availability, choosing verification calls by reach, a shared facility identifier) are in the README under "What we propose".
