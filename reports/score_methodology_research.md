# Making the score real: what we actually need (final)

Lead methodologist, Saturday Nov 21, 23:00 ET. This replaces the first synthesis and fixes every high and medium point from both critiques.

- **Build.** Counts were recomputed tonight on the current build (grades sha256 07c52631, model student:s1-1b with regex v0.2, checks wp11_v2) and on the 22:36 app bundle.
- **Scripts and logs:** /private/tmp/claude-501/-Users-hrudaynara-Projects-CMU-Datathon/dda738e7-6b95-4922-b02b-c70ed6c6d96e/scratchpad/score_research/lead_final/
- **Sources.** Links marked (re-fetched) were fetched by this agent tonight. The rest were fetched and checked by the research and check agents in this session.

## Bottom line

A real probability is not possible tonight, so the score should stop pretending to be one. We can make it real in a narrower, honest sense: every number that filters or orders results is measured, cited to a published standard, or a declared design choice shown in How we decided with a sensitivity check. That takes about 22 agent-hours across 10 agents and lands by the 05:00 ET cut line, which leaves the video its time.

| Part | Tonight | Basis |
|---|---|---|
| Evidence tags behind the gate | Measured | Shipped tagger: macro F1 0.91 (0.90 to 0.93) on 777 agent-labelled gold facts; caesarean 26 of 26; weakest is neurosurgery at 0.58 |
| Grade rules | Cited | WHO 2009, FBNC 2025, PM-JAY criteria, PMNDP (recipe v0.3) |
| Travel minutes | Measured | 934 OSRM free-flow trips from 30 anchors; out-of-fold log error SD 0.26; the 95% band covered 94.6% |
| Windows | 2 cited, 3 informed by evidence, 7 design choices | Holmer 2019, ESC 2023, Seelig 1981, Moist 2008 |
| Order | One design choice | Nearest first; a corroborated record can move ahead if it is at most 15 minutes further (30 for planned care) |
| Outside lists | Links measured; shown as dated facts | Agent-checked precision, one-sided 95% lower bound of at least 0.90 |
| Probability, frequency, p gate | Not tonight | No source gives true negatives |

## What changed after the critique

**The order now favours time.** The first synthesis's S = W x reach was flat inside a 120-minute window, so an unmeasured grade premium chose the card. On 1,500 random post offices it put 11% of emoc and 21% of ICU top picks 30 or more minutes past the nearest facility with evidence, at worst 100 minutes. Nearest-first with a declared allowance caps the detour by construction. The allowance changes the top pick in 13% (cath lab) to 31% (neurosurgery) of answered queries at 15 minutes, and in 23% to 38% of the planned-care queries at 30 minutes.

**No probability, frequency or p gate tonight.** PM-JAY empanels a hospital "for one or more specialities". It also allows dialysis, ICU and blood bank "either In-House or with Outsourcing arrangements" (https://cdnbbsr.s3waas.gov.in/s3169779d3852b32ce8b1a1724dbf5217d/uploads/2022/04/2022072941.pdf). A missing specialty is a business choice, not a missing service, so it gives no negatives. Two more problems:

- The threshold rule needed about 87% observed confirmation (26 of 30), while the only rate measured so far is about 8%.
- Pooled cells would have put an emoc label on a number that is about 96% other services.

All three are cut.

**The build is frozen.** At 22:06 the student tagger rewrote the grades under an unchanged recipe_version:

- 418 ICU grades changed, and 391 crossed the gate (grade 2 rose from 1,677 to 1,850).
- Cath lab grade 3 rose from 456 to 481.

Recipe v0.3 therefore becomes a rule-level regrade into a new file. It cannot undo the student tags or invalidate the gold scores.

**Windows are planning thresholds, not deadlines.** Holmer et al. 2019, "Evaluating the collection, comparability and findings of six global surgery indicators" (Br J Surg), quote the 2-hour indicator and note that it "does not capture whether patients actually receive care" (https://pmc.ncbi.nlm.nih.gov/articles/PMC6790969, re-fetched). ESC 2023 sets "an absolute time of 120 min from STEMI diagnosis to PCI-mediated reperfusion" (https://academic.oup.com/eurheartj/article/44/38/3720/7243210, re-fetched). The 90-minute cath lab travel window is therefore our design choice, not ESC's.

**Execution is protected.**

- A checkpoint commit and a fallback deploy, now.
- One owner for rank.py.
- An Answer contract that keeps the old fields.
- A time-boxed acquisition step, with a go/no-go at 00:30.
- A cut line at 05:00.

**Smaller fixes.**

- Reach uses the measured travel error (SD 0.26), not a chosen sigma.
- The time range is a 95% band, worded "estimated drive time from straight-line distance".
- A state terrain factor cuts the fallback's disagreement with MAP in hill and northeast states from 12.6% to 7.4% (emoc).
- Ties break on minutes, never on flags.
- Folds for any later model no longer chain 79% of records into one group: the fixed rule's largest group is 3.1% of hospitals.

## The score (v1-final)

- **Gates.** A usable location; grade 2 or higher under recipe v0.3 with no major flag; estimated minutes inside the window.
- **Travel.** t = state factor x 2.30 x km^0.842 minutes, shown with the range t x e^(plus or minus 0.52). The MAP raster replaces it if it ships.
- **Windows.**
  - Cited: emoc and trauma, 120 minutes.
  - Informed by evidence: cath lab 90 (ESC 2023), neurosurgery 150 (Seelig 1981), dialysis 60 (DOPPS).
  - Design choices: ICU, NICU and CT 120; MRI 180; burns, cardiac surgery and oncology 240.
- **Order.** By minutes, minus 15 minutes for a corroborated record (30 for MRI, cardiac surgery and oncology). The nearest recommendable facility is always shown. Trust, recency, domain counts and flags are facts on the card, not weights.
- **On the card.**
  - Minutes, with a range and a window badge that names its basis.
  - The facility's own quotes.
  - What we could not find, phrased as questions to ask.
  - A dated outside-list line, or "Not yet checked against an outside list".
  - Never a percentage, "chance" or "confidence".
- **Abstention.** "No facility within about 2 hours' estimated drive (free-flow) of <place> has clear evidence of emergency C-section in our data. This is a gap in our data, not proof that no care exists." Then the nearest facility with evidence beyond the window, places that claim the service (to call), and listed government facilities.

## What we measured tonight

**Claims against evidence.**

- 421 of the 4,577 records that list an obstetrics specialty (9.2%) have a fact naming a caesarean. 432 records name one in all. The brief's 4,489 counts the gynecologyAndObstetrics label alone.
- For 93.6% of post offices, the nearest facility that lists obstetrics or delivery care has no such fact. The nearest one that does is a median 35 minutes further (p75 68).

**LaQshya.** We linked 32 government hospitals that hold a LaQshya maternity-theatre certificate. Only 1 has web text naming a caesarean (Wilson 95% 0.6% to 15.7%), and 10 of the 32 certificates were valid on Nov 21. This is agreement within a public, certified slice, not the tool's recall. It shows that public hospitals are nearly invisible in web text.

**Coverage.** Shares of people, on rough 2011 district weights:

- Emoc: 16.6% (fallback with terrain factor) to 20.8% (MAP) live more than 2 hours' estimated free-flow drive from an emoc-recommendable facility. Under the old 50 km radius, the figure was 50.6%.
- Dialysis: 37.4% are beyond 60 minutes.
- Cath lab: 25.6% are beyond 90 minutes.

**Leakage.** 40.1% of grade 2+ pairs sit on records that already cite an insurer network page; PM-JAY mirrors account for 3.7% and ESIC for 2.9%. A label from any of those families would leak into the features.

## Labels each line would need

The table gives the linked hospitals needed for 30 in one grade cell of one line (Wilson half-width about 0.16). It assumes linked hospitals look like our 5,401 usable hospitals; empanelled hospitals skew larger, so the real need may be lower.

| Line | Grade 2 | Grade 3 | Label path tonight |
|---|---|---|---|
| ICU | 97 | 420 | None (PM-JAY lets the ICU be outsourced) |
| Neurosurgery | 236 | 437 | PM-JAY neurosurgery, if acquired |
| Trauma | 276 | 374 | PM-JAY polytrauma, if acquired |
| NICU | 304 | 1,558 | PM-JAY neonatal supports grade 2 only; SNCU lists are level II |
| Dialysis | 309 | 389 | PM-JAY nephrology and urology; PMNDP lists for agreement only |
| Cath lab | 402 | 787 | PM-JAY cardiology/CTVS |
| Cardiac surgery | 410 | 1,385 | Needs a CTVS-specific field |
| CT | 430 | 228 | Licence list only (unverified) |
| Emoc | 542 | 2,946 | LaQshya agreement (public only) |
| MRI | 619 | 362 | None known |
| Oncology | 651 | 1,039 | PM-JAY cancer care |
| Burns | 2,614 | 2,383 | Counts only |

Tonight at most one or two lines can fill a single cell. Every other line ships with counts and dated card facts, and How we decided says "not checked against an outside list tonight".

## What a real probability needs (after tonight)

- **A source with true negatives.** Each facility record must list the services the facility provides, as a licence or a registry with a services field does, so that a missing service means "not offered". Positive-only lists cannot measure precision (Bekker and Davis, https://arxiv.org/abs/1811.04820).
- **Enough labels.** At least 385 effective labelled pairs, the Riley overall-risk criterion (https://pmc.ncbi.nlm.nih.gov/articles/PMC6519266/). At 4.9 graded lines per hospital and an intraclass correlation of 0.3, that means about 170 to 290 linked hospitals from 3 or more states.
- **Clean folds.** Group by chain domain, district or PIN, with no transitive merging.
- **Baselines beaten.** The model must beat grade-only and size-only baselines out of fold. Size alone predicts grade 2+ with an AUC of 0.74 to 0.81.

## Must-haves tonight

| # | Work | Owner | Hours | Done by |
|---|---|---|---|---|
| 1 | Checkpoint commit, build hashes, freeze gold writes, rebuild bundle, fallback deploy | Orchestrator, WP12 | 1.0 | 00:00 |
| 2 | Protocol and story frozen in the decisions log | Orchestrator | 0.5 | 23:30 |
| 3 | Recipe v0.3 rule-level regrade, hand-read of changed records | WP3 | 1.5 | 01:30 |
| 4 | Outside-list acquisition, at most 2 sources, time-boxed | WP13 | 3.0 | 00:30 |
| 5 | Strict linkage and agent checks, LaQshya first | WP13 | 5.0 | 03:30 |
| 6 | reports/outside_lists.md: LaQshya finding, per-line tables | WP13 | 1.5 | 04:30 |
| 7 | WP5 v1-final: windows, travel, order, Answer v2, tests | WP5 | 4.0 | 03:00 |
| 8 | Card, abstention and How-we-decided copy; integration files | WP8 | 2.5 | 04:30 |
| 9 | Bundle rebuild and v1-final deploy | WP12 | 1.0 | 05:30 |
| 10 | Three findings locked; video script from 02:00 | WP9, orchestrator | 1.5 | 05:00 |
| 11 | Spot check of on-screen links; restart; submit | Hruday | 0.5 | 08:00 |

## What we will not claim

- No chance, percentage or "likely offers".
- No "calibrated", and no "lower bound".
- No absence statement drawn from an empanelment list.
- No service-named number built from other services.
- No "gate recall" for LaQshya.
- No simulated trip savings.
- No free-flow minutes presented as real trips.
- No window presented as a clinical deadline.
- No agent check presented as human verification.

The full list is in do_not_claim.

## Timeline

| ET | Milestone |
|---|---|
| 23:15 | Checkpoint, freeze, launch |
| 00:30 | Go/no-go on new sources |
| 01:30 | v0.3 grades frozen; bundle rebuilt |
| 02:00 | Video script starts |
| 03:30 | Links checked |
| 05:00 | Cut line: all video numbers locked; ship v1-final, or the v0 fallback with honest copy |
| 08:00 | Hruday's spot check done |
| 09:00 | Everything ready |
| Before 12:00 | Restart the app; Hruday submits (12:30 PM is the tentative deadline on cmudsc.com) |