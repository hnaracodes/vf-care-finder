# Demo script: the 3-minute video and the 90-second cut

Written Nov 22 for the frozen build (commit 17bf7e6, deployment 01f1e65c, recipe v0.3, ranker v1, flags wp11_v3). Sources: `docs/09_presentation.md`, `reports/standout_brainstorm.md` (first 20 seconds, Option A), D045, D051, D056, D062, and the overnight retest (`reports/overnight_test_and_fix.md`). Every on-screen answer below was read from the deployed app with `demo=1` on Nov 22 (`/api/answer` and `/api/sim`), not from a local copy.

Every number in this file comes from `reports/findings.json`, with its key in backticks after it. `python scripts/wp9_findings.py --check reports/demo_script.md` must pass before recording. Voiceover is paced at about 140 words a minute; each beat's voiceover was counted against its slot and runs close to that pace (spoken numbers included). Time it against the real app in rehearsal and trim words, never numbers' keys, if a beat runs long.

## Before any take

1. Restart the app shortly before recording (D034), then open it in a clean browser window at full laptop size: `https://vf-care-finder-7474645226493940.aws.databricksapps.com/?demo=1`.
2. Check that the "Demo" chip shows in the header. If it is missing, names and phone digits are not masked: stop (overnight H1).
3. Browser zoom so the answer card and the small map are both in frame; large type, because it will be watched at half size.
4. Close the simulator panel (x) before any "Show all of India" view (overnight M12). The funnel tiles in "How we decided" are not clickable: show them, do not click them.
5. The saved warehouse result for the cold open is the "Saved warehouse result" block in `reports/cold_open.md` (query 1 and query 2). Do not run a new warehouse query (D051). Blur the row 1 name in the edit.
6. Never say "does not have", "there is no", "pre-registered", "likely", "validated", "calibrated" or "lower bound". Say "no evidence of", "its record never mentions", and "judged by AI models".
7. Before filming beat 5 (and cut beat 3), check the Nashik District Hospital LaQshya link by hand: open the LaQshya list of 8 Apr 2026, page 35 row 1109, and confirm it names this hospital and a certified maternity operating theatre valid to 31 Dec 2026. An AI agent made that link; no person has checked it yet (`reports/laqshya_links.md`). If it does not hold, cut the government line from beat 5 and cut beat 3.
8. Card names (BNK Hospital, Fatima Hospital, Pawar's Clinic, Hrudayasparsh, Raiganj) stay visible in demo mode; only the why-not and not-verified rows are masked. The facility table's reuse terms are unchecked. Decide before recording whether naming a few records on camera is acceptable; if not, blur the card names in the edit as row 1 of the cold open is blurred.

## The 3-minute video (Option A, D045)

### Beat 1. Cold open (0:00 to 0:20)

**0:00 to 0:04. On screen:** black card, white text: "Which hospital near Lucknow can handle an emergency C-section?" Small line under it: "The challenge brief's own question."
**Clicks:** none.
> Which hospital near Lucknow can handle an emergency C-section?

**0:04 to 0:11. On screen:** the Databricks SQL editor with the saved result of `sql/cold_open_lucknow.sql` on the Marketplace `facilities` table: specialty contains obstetrics, nearest to PIN 226001, top three. Row 1 highlighted, name blurred, city "Kanpur", PIN 208001, 0.95 km `cold_open.foil.row1.distance_km`. Rows 2 and 3 left plain, no caption (one is a public hospital: never strike it through).
**Clicks:** none (saved result, slow zoom to row 1).
> The obvious query: obstetrics listed, nearest first. Row one is about a kilometre away.

**0:11 to 0:15. On screen:** row 1's record (query 2 of the saved result), name blurred. Two fact lines visible: "Located in Noida Sector 18" and "Located in Bareilly, Uttar Pradesh, India". Counter overlay: "141 facts, 0 mention a caesarean" (141 `cold_open.foil.row1.facts`, 0 `cold_open.foil.row1.facts_mentioning_caesarean`).
> 141 facts `cold_open.foil.row1.facts`. Not one mentions a caesarean.

**0:15 to 0:20. On screen:** hard cut to our app on PIN 226001 (the first frame of the live take in beat 2): "Go to BNK Hospital ... Its record says it performs C-sections", 3.5 km straight-line `query.cold_open_226001.card1.distance_km`, the quote "Performs Lower Segment Caesarean Section (LSCS)" from its list of procedures, and "Also in its record: On-site Blood Bank". Caption: "Same data. Same question. Nearest evidence, not nearest claim."
> Same data. We say BNK Hospital, 3.5 kilometres `query.cold_open_226001.card1.distance_km`, because its record names the operation and a blood bank.

### Beat 2. The answer card, live (0:20 to 0:41)

**On screen:** the live take on PIN 226001 (recorded once, unbroken, from the landing page; the cold open's last shot is cut from its first answer frame). Card 1 is BNK Hospital; cards 2 and 3 are Fatima Hospital and Pawar's Clinic.
**Clicks:**
1. Landing page. "Care" select: "Emergency C-section (high-risk birth)". "Near": type `226001`. Click "Find care".
2. Point the cursor at the quote and its field line ("from its list of procedures"), then at "Strong record evidence."
3. Point at "Before you go, ask:" and its two questions ("Can you do an emergency C-section right now?" and "Do you accept Ayushman Bharat, and what will the patient have to pay?").
4. Click "Show number to call": the digits stay hidden in demo mode.
5. Scroll down and open "Why not the nearer ones?". Hold on the first row, a public hospital, whose reason ends "this silence is not evidence either way".

> Every claim is a quote, with the list it came from, and the strength in words. Before you go, ask: can you do a C-section right now, and what will the patient pay? Why not the nearer ones? Each has a reason. A public hospital's silence is not evidence either way.

### Beat 3. How we decided (0:41 to 0:55)

**On screen:** the "How we decided" fold, open. Funnel tiles for this query: 9,989 records `query.cold_open_226001.gate.usable_location.of`, 9,671 with a usable position `query.cold_open_226001.gate.usable_location.n`, 468 that list or claim it within reach `query.cold_open_226001.gate.within_reach.n`, 47 recommendable `query.cold_open_226001.gate.recommendable.n`, 13 within the window `query.cold_open_226001.gate.within_radius.n`, 3 shown `query.cold_open_226001.results`. Below: the window line citing the Lancet Commission's 2-hour indicator (120 min `method.window.emoc`) and the order rule with the 15-minute allowance (15 min `method.allowance.urgent`).
Lower-third caption (edit): "Fact tagger: hand-written rules on 20 tags, small models on 4. Macro F1 0.916 `tagger_wp4b.mix.macro_f1` against 0.890 `tagger_wp4b.regex.macro_f1` for rules alone, on 777 `tagger.gold_rows` agent-labeled facts."
**Clicks:** scroll to "How we decided", click it open. Do not click the tiles.
> How we decided: 47 `query.cold_open_226001.gate.recommendable.n` records name the operation with no blocking warning; 13 `query.cold_open_226001.gate.within_radius.n` are within two hours' drive. Nearest first, unless better evidence is at most 15 `method.allowance.urgent` minutes further.

### Beat 4. Trust claims alone: the trip view (0:55 to 1:13)

**On screen:** the expanded map with the simulator, "Where would nearby villages go?", in "Trust listed specialties alone" mode. Dots leave their villages; diamonds are places that list obstetrics with no evidence in the record. The takeaway line under the map gives the app's own count of villages that go first to a place with no evidence. The run takes about 18 seconds on Lucknow: speed it up in the edit and caption "Sped up".
Lower-third caption (edit): "Across India: 81.5% `emoc.coverage_50km_specialty_label` of post-office points within fifty km straight-line of an obstetrics listing; 40.2% `emoc.coverage_50km_grade_2plus` within fifty km of a record that names a C-section. Facts about records, not about care."
**Clicks:** scroll to "For planners", click "Play it on the map", click "Trust listed specialties alone", click "Play".
> Not one bad row. Trust listed specialties alone, and most villages go first to a place whose record never names a C-section. Across India, about four in five post-office locations are near an obstetrics listing; only two in five are near a record that names one.

Wording rule (D045): the counterfactual is "if that place cannot help", never a probability. The voiceover gives no count from the simulator, because its counters are not in `findings.json`.

### Beat 5. Maharashtra: ask about blood, and the public hospital (1:13 to 1:38)

**On screen:** the brief's high-risk delivery query in Nashik. Origin line: Nashik city, known to about 12 km `query.brief_highrisk_maharashtra.location_precision_km`. Card 1: Hrudayasparsh Multispeciality Hospital, about 3 km `query.brief_highrisk_maharashtra.card1.distance_km`, "Some record evidence.", and the line "Not found in its record: a blood bank or blood transfusion." Its "Before you go, ask" includes "Do you have a blood bank?" with "Why ask: No fact in the record mentions a blood bank or transfusion. A C-section can need blood."
Then "Why not the nearer ones?", row D: a public hospital in Nashik (name hidden in demo mode), "position approximate", whose record names maternity service but no caesarean, with the "Outside this dataset" box: "Government list (LaQshya, NHSRC, 8 Apr 2026, page 35 row 1109): maternity operating theatre certified, valid to 31 Dec 2026. Outside data, not from this record."
**Clicks:** click "Close map", click "Change", type `Nashik` in "Near" (Care stays on emergency C-section), click "Find care". Point at the blood question. Scroll to "Why not the nearer ones?" and hold on row D and its government line.
> Nashik. The top record names C-sections but not blood, so the card says: ask. That gap is common: 373 `emoc.csection_records_without_blood` of 432 `emoc.records_naming_csection` C-section records have no fact about blood. The district hospital's own record never names a caesarean. The government's LaQshya list certifies its maternity theatre. We show both, apart.

Default taken: demo mode hides the public hospital's name on screen (D051, overnight open item), so the voiceover says "the district hospital", which the screen's own row and government line identify. No LaQshya ratio is spoken (brainstorm rule).

### Beat 6. Rural Bihar dialysis, and a what-if (1:38 to 1:58)

**On screen:** headline "No clear evidence of dialysis within 1 hour of Jokihat in this dataset." and "This is not proof that no care exists nearby." The nearest place whose record names dialysis: Raiganj Government Medical College And Hospital, about 76 km `query.brief_dialysis_rural_bihar.beyond_radius.distance_km` straight-line, "Limited record evidence." Then the simulator with a what-if dialysis unit added at Jokihat: the purple "WHAT-IF VIEW, not real data" bar, grey rings clearing, and the takeaway counter changing (read it from the screen; it is not voiced).
**Clicks:** click "Change", "Care": "Dialysis", "Near": type `Jokihat, Bihar`, click "Find care". Hold on the headline and the Raiganj card. Scroll to "For planners", click "Try a what-if: add, close or check a place", click "Add a facility", click "Add here" next to "Jokihat (the searched place)".
> Dialysis, rural Bihar. Nothing here shows dialysis within an hour, and we say so, then point to the nearest record that does, about 76 `query.brief_dialysis_rural_bihar.beyond_radius.distance_km` kilometres away, with limited evidence. For planners: add one imagined unit, and watch most villages come inside the hour.

Rehearsal check: the overnight retest saw most villages come inside the hour after this exact click (`reports/figures/overnight/retest_laptop_06_jokihat_whatif_add.png`). If the live counter does not show that, change the last sentence to "and watch the grey rings clear".

### Beat 7. Proof: the sealed evaluation (1:58 to 2:19)

**On screen:** a static table card, held 8 seconds, then the voiceover continues over it.

| Sealed half, 140 queries at 60 random places | Specialty filter | Any fact names it | Our ranker |
| --- | --- | --- | --- |
| Queries answered | 82% `sealed_eval.sealed.specialty.coverage.rate` | 72% `sealed_eval.sealed.any_fact.coverage.rate` | 72% `sealed_eval.sealed.ranker.coverage.rate` |
| Top pick's record clearly shows the service | 20% `sealed_eval.sealed.specialty.precision.rate` | 84% `sealed_eval.sealed.any_fact.precision.rate` | 89% `sealed_eval.sealed.ranker.precision.rate` |

Footer line on the card: "Sealed and hashed before the run. Judged by AI models (Claude), blind to which tool picked. Same drive-time window and the same right to abstain for every tool. Ranker vs any-fact: not shown better (p = 0.125 `sealed_eval.sealed.ranker_vs_any_fact.sign_test_p`)."
**Clicks:** none (edit insert).
> On 140 `sealed_eval.sealed.queries` searches at 60 `sealed_eval.sealed.points` random places, sealed before the run, AI models judged each top pick blind. The specialty query's pick clearly shows the service 20% `sealed_eval.sealed.specialty.precision.rate` of the time. Ours, 89% `sealed_eval.sealed.ranker.precision.rate`. A simpler any-fact rule gets 84% `sealed_eval.sealed.any_fact.precision.rate`: we are not proven better than that.

### Beat 8. Honesty: the cost of caution (2:19 to 2:33)

**On screen:** a second card: "The cost of our caution". Line 1: "When we abstained, AI judges found a supported option in 0 of 39 `sealed_eval.sealed.ranker.abstained_with_support`; in 31 `sealed_eval.sealed.ranker.abstentions.no_signal_in_window` of them nothing nearby had any signal." Line 2: "When we answered: we put a nearer, judge-supported record below another in 16% `sealed_eval.sealed.ranker.skipped_nearer.rate` of answers, mostly by our 15-minute evidence allowance `method.allowance.urgent`."
**Clicks:** none (edit insert).
> Our caution has a cost. When we stay silent, mostly nothing nearby even claims the service, and the judges found nothing we missed. When we answer, 16% `sealed_eval.sealed.ranker.skipped_nearer.rate` of the time we pass a nearer record they accepted, mostly by design.

### Beat 9. Honesty: one miss, shown (2:33 to 2:49)

**On screen:** a pre-recorded clip of emergency C-section at Gadchiroli: "Also nearby, not verified", the hospital in Chandrapur (name hidden), "Not recommended: The record's evidence for C-section and high-risk delivery sits among facts about Mumbai, not this facility". Then a text insert quoting that record's fact, with its field: "Operation Theater is equipped with modern surgical instruments for major dental and oral surgeries." (procedure). Caption: "Recipe v0.2 quoted this as the operating theatre for a C-section grade." Second caption: "v1 ranker against v0, AI judges (drive times visible for v1 only): v1 preferred 10 `v0v1.r2.prefer.v1`, v0 4 `v0v1.r2.prefer.v0`, ties 12 `v0v1.r2.prefer.tie`; p = 0.18 `v0v1.r2.prefer.sign_test_p`. Not proven better."
**Clicks (for the clip):** "Change", "Care": "Emergency C-section (high-risk birth)", "Near": `Gadchiroli`, "Find care", scroll to the not-verified row.
> One real miss: our first recipe counted a dental operating theatre toward this hospital's C-section grade. A merged-record check now keeps it off the cards. And our new ranker is not proven better than our first.

What is true about this miss (D051 item 3; checked on the frozen data): under recipe v0.2 the record's C-section grade quoted the dental sentence as its "anaesthesia or operating theatre" evidence. Recipe v0.3 no longer needs a theatre for that grade, but the tagger still tags the dental sentence as a theatre, and the record still grades "corroborated" on a caesarean sentence from a research trial at another hospital. What keeps it off the cards is the wp11_v3 merge check, not a better reading. Do not say the miss is fixed.

### Beat 10. Runs on Databricks, limits, end card (2:49 to 3:00)

**On screen:** 2:49 to 2:54: the Databricks workspace "Apps" page with `vf-care-finder` shown as Running, then the app URL in the address bar (`databricksapps.com`). 2:54 to 2:57: the "Limits" list in "How we decided". 2:57 to 3:00: end card: "Nearest evidence, not nearest claim." plus one small line, "We propose: referral lists that require cited evidence; blood recorded with every C-section claim." (README, "What we propose"), and the repo link (raw data excluded).
**Clicks:** none in the app (cuts).
> It runs as a Databricks App on Free Edition. Drive times are estimates; web pages go stale. Nearest evidence, not nearest claim.

Do not say "built by a Databricks job": no job was run; the bundle was built from the Marketplace export and deployed with the app.

## The 90-second cut

Six beats, as the brainstorm lists them, plus the honesty lines.

### Cut beat 1. Cold open (0:00 to 0:20)

Same as beat 1 of the 3-minute video, same voiceover.

### Cut beat 2. The card and "Before you go, ask" (0:20 to 0:36)

**On screen and clicks:** beat 2's live take on PIN 226001, trimmed to the quote, the strength line and "Before you go, ask:". No why-not strip.
> Every claim is a quote from the record, with the strength in words. And before you go, the questions the record could not answer: can you do a C-section right now, and what will the patient pay?

### Cut beat 3. Nashik: blood, and the government line (0:36 to 0:55)

**On screen and clicks:** beat 5's take: card 1's blood question, then row D of "Why not the nearer ones?" with its LaQshya line (page 35 row 1109, valid to 31 Dec 2026).
> 373 `emoc.csection_records_without_blood` of 432 `emoc.records_naming_csection` C-section records say nothing about blood, so the card asks. In Nashik, the district hospital's record never names a caesarean, but the government's LaQshya list certifies its maternity theatre. We show both, apart.

### Cut beat 4. Proof and honesty (0:55 to 1:20)

**On screen:** beat 7's table card (6 seconds), then beat 8's cost-of-caution card, then beat 9's dental-theatre insert. The v0 against v1 caption from beat 9 stays on screen under the insert.
> On 140 `sealed_eval.sealed.queries` sealed searches at random places, AI judges found the specialty query's pick clearly shows the service 20% `sealed_eval.sealed.specialty.precision.rate` of the time. Ours, 89% `sealed_eval.sealed.ranker.precision.rate`, not proven better than a simpler rule at 84% `sealed_eval.sealed.any_fact.precision.rate`. Our caution skips a nearer accepted record in 16% `sealed_eval.sealed.ranker.skipped_nearer.rate` of answers. One miss: a dental theatre counted toward a C-section grade.

### Cut beat 5. Databricks and end card (1:20 to 1:30)

**On screen:** the Apps page with `vf-care-finder` Running, then the end card with the repo link.
> It runs on Databricks Free Edition. Nearest evidence, not nearest claim.

## Numbers used, with their keys

| Number | Key in `reports/findings.json` | Where |
| --- | --- | --- |
| 0.95 km | `cold_open.foil.row1.distance_km` | beat 1 (screen only; voiced as "about a kilometre") |
| 141 | `cold_open.foil.row1.facts` | beat 1 |
| 0 | `cold_open.foil.row1.facts_mentioning_caesarean` | beat 1 (screen counter) |
| 3.5 km | `query.cold_open_226001.card1.distance_km` | beat 1 |
| 9,989; 9,671; 468; 47; 13; 3 | `query.cold_open_226001.gate.usable_location.of`, `.gate.usable_location.n`, `.gate.within_reach.n`, `.gate.recommendable.n`, `.gate.within_radius.n`, `.results` | beat 3 |
| 120 min | `method.window.emoc` | beat 3 |
| 15 min | `method.allowance.urgent` | beats 3 and 8 |
| 0.916; 0.890; 777 | `tagger_wp4b.mix.macro_f1`, `tagger_wp4b.regex.macro_f1`, `tagger.gold_rows` | beat 3 caption |
| 81.5%; 40.2% | `emoc.coverage_50km_specialty_label`, `emoc.coverage_50km_grade_2plus` (sea-pixel points dropped, D048) | beat 4 caption (voiced as "four in five" and "two in five") |
| about 12 km; about 3 km | `query.brief_highrisk_maharashtra.location_precision_km`, `query.brief_highrisk_maharashtra.card1.distance_km` | beat 5 (screen) |
| 373 of 432 | `emoc.csection_records_without_blood`, `emoc.records_naming_csection` | beat 5, cut beat 3 |
| about 76 km | `query.brief_dialysis_rural_bihar.beyond_radius.distance_km` | beat 6 |
| 140; 60 | `sealed_eval.sealed.queries`, `sealed_eval.sealed.points` | beat 7, cut beat 4 |
| 82%; 72%; 72% | `sealed_eval.sealed.specialty.coverage.rate`, `sealed_eval.sealed.any_fact.coverage.rate`, `sealed_eval.sealed.ranker.coverage.rate` | beat 7 card |
| 20%; 84%; 89% | `sealed_eval.sealed.specialty.precision.rate`, `sealed_eval.sealed.any_fact.precision.rate`, `sealed_eval.sealed.ranker.precision.rate` | beat 7, cut beat 4 |
| p = 0.125 | `sealed_eval.sealed.ranker_vs_any_fact.sign_test_p` | beat 7 card |
| 0 of 39; 31 | `sealed_eval.sealed.ranker.abstained_with_support.k`, `.n`; `sealed_eval.sealed.ranker.abstentions.no_signal_in_window` | beat 8 card |
| 16% | `sealed_eval.sealed.ranker.skipped_nearer.rate` | beat 8, cut beat 4 |
| 10; 4; 12; p = 0.18 | `v0v1.r2.prefer.v1`, `v0v1.r2.prefer.v0`, `v0v1.r2.prefer.tie`, `v0v1.r2.prefer.sign_test_p` | beat 9 caption |

## What is left out, and why

- **Simulator counts** (villages going first to a place with no evidence on Lucknow, villages within the hour before and after the Jokihat what-if) are computed by the app but are not in `findings.json`. The app shows them on screen; the voiceover does not speak them. To voice them, add the `/api/sim` summaries to findings with `--extra` and rerun the check.
- **Minutes on cards** ("about 7 min" and similar) are on screen but not in `findings.json`; the voiceover uses straight-line km from findings instead.
- **Lucknow typicality** (`cold_open.typicality.*`) is not voiced: it measures nearly the same thing as the simulator's on-screen count, by a different definition, and two different counts in one shot would confuse a viewer. The national coverage figures are used instead.
- **Human check of the judges:** the 40 items `sealed_eval.sealed.human.items` are not labeled yet and the agreement finding is null, so no human-agreement claim appears. If Hruday labels them and findings is rebuilt, add one line to beat 7's footer.
- **No LaQshya ratio, no probability wording, no OSM-placed units** (brainstorm "What not to do").
- **"Something else" free-text search** (docs/09 beat 8) is cut for time.
