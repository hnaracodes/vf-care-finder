# Standing out: final plan for the last 13 hours

**Pitch for judges to repeat:** Nearest evidence, not nearest claim. The obvious search answers the brief's own Lucknow C-section question with a fertility clinic whose record never mentions one. This tool quotes a hospital whose record names the operation, and tells you what to ask before you go.

## Short version

1. **Four things will make this stand out, and each shows on screen:**
   - a fair foil that fails the brief's own query;
   - 'Why not the nearer ones?', with the reasons;
   - uncertainty turned into specific phone questions;
   - a small, fair evaluation that leads with the cost of our own caution.

   A fifth, smaller piece puts the government's certified list beside public hospitals our records cannot verify.
2. **Two things are requirements, not differentiators:** deploying on Databricks (nothing is deployed yet) and filming one unbroken live take.
3. **The plan is smaller than the first draft:** 6 core items, about 15.5 agent-hours, plus 2 stretch items. The first draft had 9 items and 25 hours.
4. **The Nashik beat changed.** District Hospital, Nashik is in the dataset, so the first draft's 'not in this dataset' card would have been false. The true story is stronger: we can see the hospital but cannot verify it from its record, and the government list can.
5. **One decision is yours:** keep a 15-second trip-view beat (your D032 and D038), or let the evaluation replace the simulation as the headline. The default is to keep it.

## What changed after the red team

| Red-team problem | Severity | What this version does |
| --- | --- | --- |
| Nashik 'not in this dataset' card is false | high | Verified that District Hospital, Nashik is in the data. The beat is now 'we see it but cannot verify it; the government list can', citing LaQshya page 35, row 1109. 'Not in this dataset' is banned without a link check by name, district and PIN. |
| Benchmark tilted toward us | high | Baselines get the same 50 km radius and 'nothing found' option. Coverage and precision are reported as a pair. The table leads with the cost of caution, each figure called a lower bound. The question is published before the run, the caption names AI judges and 40 human labels, and it says 'sealed and hashed', not 'pre-registered'. |
| '1 of 36' unreliable | high | Removed from the pitch, the differentiators and the voiceover. Replaced by an agent-checked name-and-district link pass: 1 of 13 current certificates, on screen only after you spot-check the 13. Otherwise Nashik appears alone, with no ratio. |
| Package too big | high | 6 core items (15.5 agent-hours) plus 2 stretch items. The government lane is one line. Volume, databricks.yml, lineage and MLflow are optional. New content arrives as typed lines, and new logic goes in new files. |
| Overrides your D038 and D032 | high | An explicit choice below. The default keeps a 15-second trip beat. |
| LaQshya positions are name-token matches | medium | No OSM-placed unit on screen. The line attaches to linked dataset records, and the median-distance figures stay off screen. |
| Cold-open point moves with the resolver | medium | Film on PIN 226001 (precise to about 2 km), with the resolver's exact point in the SQL. Re-verified tonight: 0.95 km and 3.5 km. |
| SQL foil could break live | medium | try_cast, a bounding box and the specialty filter, replicated on all 10,088 rows. No junk row passes. Run it once on the warehouse before filming. |
| Typicality caption mixes definitions | medium | One definition with a distinct-record count: 381 of 436 points, 41 records. |
| Blood statistic partly circular | medium | Now 373 of the 432 records that name a C-section (86%). |
| Competitor description inaccurate | medium | Rewritten from the fetched README. The platform is parity. The Hindi claim and the CHECK-constraint beat are dropped; the Databricks beat is now a SQL quote check against the Marketplace rows. |
| Item 1 fragile; quota risk | medium | Deploy first. One notebook run tonight and one after the freeze, with an honest footer. New problem found: the notebook is regex-only, so add the WP4 step or say 'built locally'. |
| Benchmark schedule fragile | medium | An API judge script with a cache and a budget check, 120 sealed queries, and kappa as a footnote if it is late. |
| Metamorphic tests fail by design | medium | Now a stretch item. MR2 is restated, the live MR3 demo is dropped as a known gap, and MR5 can run now that 87 duplicate pairs exist. |
| Low items | low | Tagger figure is now 0.91 (the shipped WP4 mix). The cost line reuses the scheme tag. Government lines say 'list of 8 April 2026' with page and row. A demo mode masks phone digits. Repeated quotes are deduplicated. The clinic's name is blurred. The judges are labeled as simulated personas, and CAN USE is noted as your option. |

## What I checked tonight

These checks were read-only, run between 22:05 and 22:50 ET on the current gold files: WP4 tags and grades (written 22:06) and WP11 flags (21:54). Regenerate every number from the frozen bundle before it goes on screen.

**Deployment**
- The CLI shows no deployed app and no vf_datathon schema (22:15).
- dbapp/bundle/keyword_index/matrix.npz is 20,042,292 bytes, and app files over 10 MB fail deployment.
- The bundle in dbapp/bundle dates from 21:03, with stub flags and pre-WP4 tags.
- The notebook runs regex-only tagging, so a Databricks run would not reproduce the shipped WP4 mix.

**Cold open, from PIN 226001**
- The SQL logic, replicated on all 10,088 rows, returns three rows:
  - Indira IVF Kanpur, 0.95 km;
  - Balrampur Hospital, 1.97 km;
  - Kamla Devi Hospital & Lucknow Test Tube Baby Centre, 2.30 km.
- Row 1's record says city Kanpur, PIN 208001.
  - It has 141 facts, and none mentions a caesarean.
  - Its facts include 'Located in Noida Sector 18' and 'Located in Bareilly, Uttar Pradesh, India'.
  - Another fact reads 'Part of Indira IVF Centers network including Lucknow center', so its map point is probably the real Lucknow branch. The trap is the merged chain record, not the location.
  - WP11 now gives it two major flags.
- Our card 1 is BNK Hospital: 3.5 km, High confidence, quoting LSCS, an on-site blood bank and a modular operating theatre.
- The funnel reads 9,989, 9,671, 118, 14, 9, 3.

**Maharashtra, the brief's high-risk delivery**
- District Hospital, Nashik is a public record at PIN 422001, graded 'claimed'.
  - Its facts include 'Blood bank service located at District (civil) Hospital Nashik', 'Offers maternity, prenatal, postnatal and maternity care' and 'Special Newborn Care Unit (SNCU) available'.
  - None of its facts names a caesarean.
- The LaQshya list of 8 April 2026 (page 35, row 1109) certifies its Maternity OT to 31 Dec 2026. Its labour room's conditional certificate ran to 1 Jul 2026.
- All four PIN 422001 post offices sit on one shared district-town point. 'Nashik' and '422001' are therefore only district-precise (about 69 km), and three records moved to that point show '0.0 km'.
- PIN 422002 is precise to about 2 km. Its three cards are Bagul Nursing Home (12.1 km, Medium), Hrudayasparsh (4.2 km, Medium) and Prapti Women's Hospital (6.4 km, Low).
  - None of the three has a blood fact.
  - District Hospital, Nashik is among the nearer claim-only records.
- The district-centre query still shows Currae Hospital as card 3 at 135.0 km, with High confidence.

**The public system, measured with care**
- A score-research agent linked LaQshya units to public records by name within the PIN's district, then checked the links by hand. It found 78 candidates: 40 correct, 31 wrong.
- Of the 40 correct links:
  - 13 have a maternity-theatre certificate that is still current;
  - only 1 of those 13 has a record that names a C-section (Murshidabad Medical College);
  - District Hospital, Nashik is one of the 13.
- 715 of 717 OSM placements came from place-name tokens, and some are wrong. For example, Aland TH Gulbarga sits on Gulbarga District Hospital.

**Other numbers**
- 373 of the 432 records that name a C-section (86%) have no fact about blood.
- Around PIN 226001, 381 of 436 deduplicated post-office points within 50 km have a nearest obstetrics listing that never names a C-section. They span 41 different records.
- Nationally, 80.9% of post-office points are within 50 km of an obstetrics listing, and 40.1% are within 50 km of a record naming a C-section. Both numbers describe records, not care.
- The shipped tagger scores macro F1 0.91 (0.90 to 0.93) on 777 agent-labeled gold facts, against 0.89 for regex v0.2. The teacher ties the regex.
- A real miss for the honesty beat: Government Medical College Chandrapur is Gadchiroli's nearest reliable option, 72.6 km away. It is graded corroborated partly on a dental operating-theatre sentence.
- Resolver precision on the brief's queries: Jaipur 71.8 km, Nashik 68.7 km, Kotputli 163.4 km.
- 9,503 records have an official phone. 114 numbers appear on 2 or more records, and one appears on 87.

**Docs and the closest competitor (fetched)**
- Databricks Apps cannot be made public. CAN USE can be granted to users in the Databricks account, even ones without workspace access, subject to the authentication policy.
- Free Edition limits:
  - exceeding fair usage shuts compute down for the rest of the day (in extreme cases, the month), with no stated reset time;
  - 5 concurrent job tasks;
  - one 2X-Small warehouse;
  - 3 apps, each stopping 24 hours after it starts.
- The khsaydullaeva/databricks-hackathon repo:
  - notebooks that build gold tables in workspace.findcare from the Marketplace tables;
  - a Streamlit app reading them through a SQL warehouse;
  - an optional Genie space over the gold tables;
  - each result shows the supporting sentence, a score breakdown, cautions including 'call ahead', and badges.

  It reports no precision figures, and its README says 'Not yet verified' against the real Marketplace tables.

## Typical entry versus ours

| | Typical entry | Ours, and how it shows |
| --- | --- | --- |
| Opening | Own UI on the brief's example queries | The obvious SQL on the sponsor's table fails the brief's Lucknow query at a fixed PIN, then a cut to our answer |
| Why this one | A ranked list | 'Why not the nearer ones?' with the record's reasons, and a clickable funnel |
| Uncertainty | A badge plus 'call ahead' (the closest CMU repo has both) | The questions this record could not answer, with a Call button. Blood is asked on 86% of C-section records |
| Public system | One limitations line | District Hospital, Nashik: seen, not verifiable from its record, and certified by the government list, shown side by side |
| Proof | Hand-picked queries; no measured accuracy | 120 sealed random places, the same rules for every tool, blind AI judges anchored by human labels, and the cost of caution first |
| Explanations | An LLM or RAG paragraph | Templates and verbatim quotes, checked against the Marketplace rows in SQL |
| Honesty | Successes only | A miss with the sentence that fooled us, the cost of caution, and a test against unsourced numbers |
| Platform | Pipeline plus a Databricks App (the closest CMU repo has both) | Parity: job-built data, an honest footer, one unbroken live take |

## The package, ranked by value

| Rank | Item | Ideas | Agent-hours | Owners | Never cut |
| --- | --- | --- | --- | --- | --- |
| 1 | Deploy tonight, then serve job-built data | I02 | 2.5 | WP12, WP8 footer | yes |
| 2 | Cold open, 'Why not the nearer ones?', gate funnel | I01, I12 | 2.5 | WP5, WP8, WP9 | yes |
| 3 | Before you go, ask: Call, the record's gaps as questions, cost, the 108 strip | I06, I07, I17 | 2.5 | new 'ask' agent, WP12, WP8 | |
| 4 | Small, fair evaluation led by the cost of caution | I03, I27 | 4, plus 0.5 of yours | eval lead | |
| 5 | Government list beside the records it certifies; the Nashik beat | I09, I05, I11 | 1.5 | WP13, WP5, WP8 | |
| 6 | Film it: one source of truth for numbers, one miss, one live take | I34, I14 | 2.5 | WP9 | yes |
| 7 (stretch) | Cited WhatsApp, SMS and print slip | I08 | 2 | WP8 | |
| 8 (stretch) | Never-rules as tests, failures named | I15 | 1.5 | proof agent | |

**Cut order** if behind at the 03:30 check:
1. the stretch items;
2. the 120-pair grade sample in item 4 (keep the sealed queries);
3. the cost line and chips in item 3 (keep the questions and the Call button);
4. the government-line UI in item 5 (film the record and the list row side by side instead).

**Never cut** items 1, 2 or 6.

## Decision for you: the trip view and D038

D032 (keep the trip view) and D038 (the simulation becomes the evaluation) are your decisions, and this plan does not overrule them.

- **Option A** (the default if you have not answered by 01:00): keep a 15-second trip-view beat.
  - It uses the deterministic 'if we trusted claims alone' detour, worded as a counterfactual, with no probability numbers. D038 already allows this before WP13's probabilities exist.
  - The sealed evaluation is added as a second, independent check.
- **Option B:** make the sealed evaluation the evaluation of record, and drop the trip beat from the video (it stays in the app). Log this as superseding D038's 'becomes the evaluation' clause.

I recommend A. A score-research check (evaluation_design/circularity.log in the scratchpad) shows why the simulation cannot carry a measured claim yet:
- With the ranker's own probabilities as the world, it scores itself well by construction.
- In an assumed grade-based world, our v0 ranking costs more expected km than 'nearest evidence', but needs fewer second trips.

Those numbers are illustrative and must not go on screen. They only argue for waiting for WP13.

## Schedule (ET)

| When | Work |
| --- | --- |
| 22:45 to 00:15 | **WP12:** get the index under 10 MB, rebuild the bundle, deploy, smoke-test the five brief queries; you record a 30-second insurance take. **WP5:** within-radius cards, 'position approximate', quote dedupe, dropped reasons. **Eval lead:** spec, harness, judge script, dev run. **WP2:** Kotputli. **WP13:** link table |
| 00:15 to 02:00 | **WP12:** one serverless notebook run with the WP4 step, parity table, card_quotes; redeploy from it if parity holds. **Ask agent:** questions.py and the emergency CSV. **WP8:** one UI pass for items 2, 3 and 5, plus the footer. **Eval:** dev-half judging, rubric fixes on dev only |
| 02:00 to 04:00 | Scenario recheck; findings.json and its test; **freeze at 04:00** (hash); second notebook run; redeploy |
| 04:00 to 05:30 | Sealed evaluation, run once; report; optional MLflow log |
| 05:30 to 07:00 | Scripted capture of every beat with demo mode on; demo script timed; stretch items only if everything above is done |
| 07:30 to 09:00 | You: 40 blind labels; read the questions, emergency strip and government lines; spot-check the 13 links; restart the app; record the live take |
| 09:00 to 12:00 | Voiceover, edit, submit; buffer |

## The first 20 seconds

- **0:00 to 0:04.** A black card with the organizers' own question: 'Which hospital near Lucknow can handle an emergency C-section?'
- **0:04 to 0:11.** The SQL editor on the Marketplace table: specialty contains obstetrics, nearest to PIN 226001, top 3. Row 1, name blurred: city 'Kanpur', 1.0 km.
- **0:11 to 0:15.** Row 1's record: 'Located in Noida Sector 18', 'Located in Bareilly', and the counter '141 facts, 0 mention a caesarean'.
- **0:15 to 0:20.** Cut to our app: BNK Hospital, 3.5 km straight-line, High confidence, quoting LSCS and the on-site blood bank. Caption: 'Same data. Same question. Nearest evidence, not nearest claim.'

## The 3-minute video (Option A)

| Time | Beat | Item |
| --- | --- | --- |
| 0:00 | Cold open: the SQL foil, then our card | 2 |
| 0:20 | Not one bad row: 381 of 436 around Lucknow; 81% against 40% nationally, as facts about records | 2 |
| 0:30 | Live take on 226001: quotes with fields, confidence in words, 'Why not the nearer ones?', Call | 2, 3, 6 |
| 0:55 | How we decided: click '14 recommendable' to see who fell out and why | 2 |
| 1:10 | Trip view: 'if we trusted claims alone', worded as a counterfactual | D032 |
| 1:25 | Maharashtra on 422002: 'Before you go, ask' with the blood question; District Hospital, Nashik with the government line | 3, 5 |
| 1:55 | Rural Bihar dialysis at Jokihat: an honest abstention; the nearest reliable option is 75.8 km away, Low confidence, and the card says why | 2 |
| 2:10 | Proof: the evaluation table for 8 seconds, then one miss with its sentence | 4, 6 |
| 2:35 | Runs on Databricks: the job run, the footer's run id, the quote check in SQL | 1 |
| 2:50 | Limits and end card | 6 |

Under Option B, the trip beat's 15 seconds go to Maharashtra.

The 90-second cut keeps six beats:
- the cold open;
- the card with 'ask';
- the District Hospital, Nashik line;
- the evaluation table;
- the Databricks beat;
- the end card.

## What not to do

- **No chatbot, RAG or Genie front door, and no LLM text on cards.**
- **Keep the demo workspace light:** no ai_query, Vector Search or model serving, and at most two notebook runs before filming.
- **'Not in this dataset' needs a check first:** never say it about a named government hospital without a link check by name, district and PIN.
- **No LaQshya ratio in the pitch,** and none in the voiceover before your spot-check. No OSM-placed units and no median-distance gains on screen.
- **Evaluation wording:** never 'pre-registered', and never 'judged blind' without 'by AI models'. Do not lead with rows that are zero for us by construction. No tuning on the sealed half.
- **No probability numbers** unless WP13 passes its held-out check.
- **Distances must be honest:** no card beyond the radius, and no '0.0 km' for a shared PIN point.
- **No number outside findings.json.**
- **No negative claims about named facilities.** Blur the cold-open clinic's name, and never strike through a silent public hospital.
- **The footer must be true:** no 'built by a Databricks job' claim unless it was.
- **No Hindi or voice tonight.**
- **No new surfaces before noon.**
- **No public mirror, no phones or raw records in the repo,** and no capture without demo-mode masking.

## What you need to do

- **Tonight, if you are up:**
  - choose A or B (one line);
  - record a 30-second insurance take when the first deploy lands.
- **Your calls, though nothing here needs either:**
  - whether to grant CAN USE to organizer emails if they ask;
  - whether to keep a second Free Edition account as a quota fallback.
- **Morning, about 75 minutes:**
  - 40 blind labels;
  - read the roughly 30 questions and the 108/102 strip;
  - spot-check the 13 government links;
  - restart the app;
  - record the live take;
  - voiceover.

## How this fits the score-reality track

The two checks answer different questions:
- **Item 4, the evaluation,** asks whether we read the record correctly. It measures the precision of 'recommendable' and how much evidence we miss, using readers who never see our tags.
- **Item 5, the government-list check,** asks whether the record itself is right. It gives the first independent number, and that number is weak: 1 of 13. That weakness is the honest case for WP13's outside data.

Three rules keep the tracks from contaminating each other:
1. WP13 uses dev-half judgments only.
2. The score stays labeled 'provisional' until WP13 passes its held-out check, with the evaluation's measured line beside it.
3. The D038 simulation shows no probability numbers until P is independent of the ranker.

## Sources fetched

- Databricks Apps development (10 MB file limit): https://docs.databricks.com/aws/en/dev-tools/databricks-apps/app-development
- Databricks Apps permissions (not public; CAN USE for account users): https://docs.databricks.com/aws/en/dev-tools/databricks-apps/permissions
- Databricks Free Edition limitations: https://docs.databricks.com/aws/en/getting-started/free-edition-limitations
- The closest public CMU entry: https://github.com/khsaydullaeva/databricks-hackathon

The judge model IDs and per-token rates come from the Claude API reference bundled with Claude Code (cached 3 Nov 2026), not from a web page. The 'typical entry' column also draws on the other agents' landscape scan, which I did not re-fetch.

My read-only check scripts are in /private/tmp/claude-501/-Users-hrudaynara-Projects-CMU-Datathon/dda738e7-6b95-4922-b02b-c70ed6c6d96e/scratchpad/final/:
- v_rank.py: the brief queries;
- v_cold.py and v_sql.py: the cold open and the SQL replication;
- v_nashik.py, v_nashik2.py and v_nashik3.py: the Nashik checks;
- v_stats.py and v_stats2.py: blood, coverage and typicality.

The link file is /private/tmp/claude-501/-Users-hrudaynara-Projects-CMU-Datathon/dda738e7-6b95-4922-b02b-c70ed6c6d96e/scratchpad/score_research/devils_advocate/laqshya_public_matches_handlabeled.csv.