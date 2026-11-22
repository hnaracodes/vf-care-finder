# The care finder, explained end to end

Written for Hruday, from the questions asked during the night of Nov 21 to 22, 2026. As of Nov 22, about 00:30 ET; some work is still running (the UI overhaul with the agent simulator, the v1-final ranker integration, tagger training round 2, the outside-list augmentation). Numbers come from the repo's reports and the decisions log (`docs/08_decisions_log.md`, D023 to D055). Where a result is pending, this doc says so.

Each section answers one of your questions at three depths:

- **In one sentence.**
- **Plainly, with an example.**
- **Technically:** formulas, files, decision ids.

---

## Contents

1. What went wrong, and what changed
2. What had already been done
3. What we are building, simply
4. How it works under the hood
5. How the score is calculated, and why the first one was a placeholder
6. What the models are, and why a "student"
7. What happens when nothing reliable is nearby
8. Outside data: what we use, what we do not, and why
9. The simulation: what it was, what it is becoming
10. The map
11. How we stand out
12. How we know it works
13. Databricks
14. Judging criteria and deadline
15. What we will not claim
16. What Hruday needs to do
17. Glossary
18. Timeline of the night

---

## 1. What went wrong, and what changed

**In one sentence.** The project was first planned against the wrong brief (a national "care desert" map). At about 20:15 ET on Nov 21 it was re-scoped to the official challenge: a tool that takes a service and a place and returns the best facilities, citing evidence and stating uncertainty.

**Plainly.** The first plan asked "where in India is care missing?" and drew a map of districts. The official challenge ("Your challenge this weekend: find the right care") asks a different question. A health worker needs dialysis near Jaipur: where should the patient go, and why trust that answer?

The official brief's five musts are:

- run on Databricks Free Edition;
- use the provided facility dataset;
- support a clear non-technical workflow;
- cite the underlying facility text for any important claim;
- communicate uncertainty instead of presenting weak evidence as fact.

**Technically.** Decision D023 records the re-scope.

| Category | Work packages |
| --- | --- |
| Kept | WP1 (clean facilities), the PIN lookup from WP2, WP3 and WP4 (tags and grades with quoted facts), the distance code from WP5 |
| Dropped | WP6 (district quadrants), WP7 (simulator), the NFHS survey work, the India-wide map |
| Added | 12 service lines plus a search fallback; trust checks (merged records, location conflicts, thin evidence); a location resolver; a transparent score; a Databricks App with three cards and a small map; "no reliable option nearby" |

The pre-pivot work was committed as a snapshot (`7609dbb`) on branch `rescope`, so nothing was lost.

---

## 2. What had already been done

**In one sentence.** More than the status board said: most of the old pipeline had run but was never committed.

**Plainly.** Before the pivot, agents had already cleaned the facility table, cleaned every post office, built a PIN lookup, written regex taggers with a hand-labeled test set, computed distances, and built an India-wide map with a patient swarm. The status board still said "todo" everywhere.

**Technically.**

| WP | State at the pivot | Kept? |
| --- | --- | --- |
| WP1 silver facilities | Done: 9,989 facilities, about 460,000 facts, a coordinate check against each facility's own PIN (`coord_check`) | Yes |
| WP2 geography | Done: 165,627 post offices, about 151,600 geocoded, PIN crosswalk; NFHS district table | PIN parts yes, NFHS retired |
| WP2b outside data | Done: OSM health points, LaQshya certified maternity list | As a cross-check |
| WP3 regex and gold | 13 tags, macro F1 0.90 on 429 agent-labeled facts | Yes, widened |
| WP5 access | Distances per post office | Code kept, outputs retired |
| WP6 quadrants | Done (rule version) | Dropped |
| WP8 app | India map with GPU patient swarm | Swarm code reused |

---

## 3. What we are building, simply

**In one sentence.** A care finder that only trusts what a hospital's own record says, quotes it, and admits what it does not know.

**Plainly.** Picture a very careful friend who has read every hospital web page in the dataset. Ask: "Where can my patient get dialysis near Jaipur?" A careless friend names whichever place calls itself a kidney hospital. The careful friend says:

> "Go to Soni Hospital. Its own record says 'Dialysis center performing over 400 dialysis cases per month'. Call ahead and ask whether a slot is free today. The places closer to you only list kidney care; none of them says it does dialysis."

The user is a non-technical health worker (an ASHA worker in rural Bihar, an NGO coordinator, a district planner).

**Technically.**

- **Input:** a service (12 named lines, or free text for the fallback) and a place (PIN, city, district, village or post office, coordinates).
- **Output:** up to 3 cards, or an abstention.
- **Card contents:** a quoted fact for every requirement it meets; what is missing; flags; estimated drive time; "Before you go, ask" questions; and any dated outside-list line.
- **The 12 lines:** `emoc` (emergency C-section and high-risk delivery), `nicu`, `dialysis`, `icu`, `cardiac_surgery`, `cath_lab` (heart attack care), `mri`, `ct`, `trauma`, `burns`, `neurosurgery`, `oncology`.

---

## 4. How it works under the hood

**In one sentence.** Precompute what each facility's text proves, check each record for trouble, then at query time find the place, filter, order and explain.

**Plainly, following one record.**

The record:

```
Government Medical College, Thiruvananthapuram · PIN 695009 · public hospital · 7 web domains
facts:  "Provides dialysis services in a dedicated Dialysis Unit"
        "Dialysis Unit"
        "Specialised units and facilities include Blood Bank, Burns Unit, Dialysis Unit, ..."
```

The steps:

1. The tagger turns each fact into tags: `dialysis`, `dialysis_unit`.
2. The dialysis recipe says "dialysis plus a named unit" means grade 3, corroborated. The grade remembers exactly which sentence satisfied which requirement. Those sentences are the citations.
3. Trust checks look for problems in the record: a location far from its PIN, signs of two hospitals merged into one record, a clinic claiming heart surgery.
4. At query time the resolver turns "Jaipur" or "226001" into a point with a stated precision.
5. The ranker filters and orders, and the card is assembled from templates and quotes. No language model writes card text.

**Technically, layer by layer.**

### 4.1 Data

- 9,989 facilities, 88% private (467 public). About 460,000 facts, of which 275,095 are distinct texts.
- 165,627 post offices, of which 151,589 are geocoded.
- Both come from the Marketplace listing "Virtue Foundation Dataset (DAIS 2026)". In the workspace the catalog is `databricks_virtue_foundation_dataset_dais_2026`, schema `virtue_foundation_dataset`.
- Row counts there match the local export exactly (D042). The `facilities` table has 10,088 rows: the 10,000 real rows plus 88 junk rows that `vf.io` drops.

### 4.2 Tags and grades

- **Tags.** 24 evidence tags (for example `csection`, `blood`, `dialysis_unit`, `mri`, `trauma`). A negation filter applies to every tag, so "No MRI facility" gets no tag.
- **Grades.** Per facility and service line:

| Grade | Name | Meaning |
| --- | --- | --- |
| 0 | none | no claim, no evidence |
| 1 | claimed | a specialty label only |
| 2 | supported | a fact names the service |
| 3 | corroborated | the service and its enabling pieces are named |

- **Recipe v0.2** (`src/vf/recipes.py`) added 11 tags and 9 lines.
- **Recipe v0.3** (D052) is a rule-only regrade of the frozen tags into `gold/capability_grades_v03.parquet`, aligned to published standards: WHO 2009 Table 4 (emergency obstetric signal functions), FRU 2025, FBNC 2025 (newborn care), PM-JAY empanelment criteria, and PMNDP (the national dialysis programme).

v0.3 changes against v0.2:

| Line | Change | Grade 2+ | Grade 3 |
| --- | --- | --- | --- |
| NICU | An SNCU-only record drops to claimed. FBNC: "Simply adding ventilators to SNCU does not qualify the unit as NICU" | 706 to 694 | 178 to 112 |
| Cath lab | Needs an adult ICU with a ventilator (PM-JAY item 3) | unchanged | 481 to 205 |
| Dialysis | A nephrologist alone does not count | unchanged | 480 to 456 |
| Emergency C-section | Caesarean plus *maternal* blood; fetal transfusion excluded, because WHO signal function 9 is maternal transfusion | unchanged | 41 to 54 |

232 changed records were read by hand: none were wrong under the final rules, and 13 were debatable. v0.2 reproduces from the frozen tags with 0 differing cells.

### 4.3 Trust checks (WP11, `src/vf/trust.py`)

- 2,471 of 9,989 records flagged; 905 facility-lines at grade 2 or higher blocked; 75 duplicate pairs (`gold/facility_flags.parquet`, `gold/facility_duplicates.parquet`).

| Check | Signal | Effect |
| --- | --- | --- |
| Location conflict | Builds on WP1's `coord_check`: `excluded_unresolved` (282) is dropped, `kept_unresolved` blocks, `moved_to_pin` (785) is a minor flag with PIN-level precision | The serious kinds were 15 of 15 correct on hand review |
| Possible merged record | Facts naming other cities or branches; shared websites across districts | Penalty or warning |
| Implausible claim | E.g. a clinic, dentist or pharmacy claiming cardiac surgery | Blocking for that line |
| Duplicates | Near-same names within 1 km, or the same site and PIN | Shown once, "also listed as" |

### 4.4 Location resolver (WP2 Part B, `src/vf/locate.py`)

- A gazetteer `silver/places.parquet` of 182,755 places (PINs, districts, post office names, cities), each with a precision radius.
- Order of resolution: coordinates, PIN, district, city, post office. Exact match comes before fuzzy match, and fuzzy matching works within the state first.
- **Ambiguous names return candidates, never a guess.** Aurangabad is in both Bihar and Maharashtra.
- 54 of 55 frozen test cases correct. The miss, "Hydrabad", is returned as ambiguous on purpose.
- Median 0.4 ms per query. The "inside India" check uses the Natural Earth outline.
- Known issue being fixed (D046): a city name can resolve to its district centre. Jaipur's precision was about 72 km.

### 4.5 The bundle and the app

- `scripts/wp12_bundle.py` writes `dbapp/bundle/`: facilities, grades, flags, places, post offices, and the keyword index for free-text search. The app loads it once at startup and answers in 2 to 4 ms per query, with no database call per request (D033).
- The app is a FastAPI server (`dbapp/server.py`) plus a prebuilt web page from `app/` (Vite, TypeScript, deck.gl).

---

## 5. How the score is calculated, and why the first one was a placeholder

**In one sentence.** There is no single magic score anymore: three yes or no checks, then nearest first by estimated drive time, with one small declared bonus for stronger evidence.

**Plainly.** The first score was:

```
0.5 × evidence + 0.3 × distance + 0.2 × trust − penalties
```

Every number in it was chosen by hand and none was tested. Two problems surfaced:

- The trust part was squeezed into a narrow band, so it barely moved scores. (A later measurement showed that removing it still changed the top pick in 17% to 38% of queries, D049.)
- The additive form let distance and evidence trade off in a way the data never decided.

The plan to replace it with a learned probability failed a feasibility check (see below). So the score became a rule where every number is either measured, cited to a standard, or a declared choice shown to the user.

The new rule, for emergency C-section near Lucknow (PIN 226001):

1. **Check 1, location:** is the facility's location usable?
2. **Check 2, evidence:** does a fact name a caesarean (grade 2 or higher under v0.3), with no blocking flag?
3. **Check 3, reach:** is the estimated drive within about 2 hours?
4. **Order:** survivors go nearest first by estimated minutes. A facility with stronger evidence (for example caesarean plus maternal blood) may come first only if it is at most 15 minutes further.

**Technically (score spec v1-final, D048).**

The drive-time model:

```
minutes ≈ state_factor × 2.30 × km^0.842        (km = straight-line distance)
```

- **Fitted on** 934 real road-routed trips from OSRM (OpenStreetMap) out of 30 anchors.
- **Accuracy:** holding out one anchor at a time, the log error SD was 0.26, and the 95% band `minutes × exp(±0.52)` covered 94.6% of trips.
- **State factors** come from the Malaria Atlas Project travel-friction map, in `reference/terrain_multiplier.csv` (for example Arunachal 3.08, Meghalaya 1.40). Islands get "sea travel not modelled".
- **Shown to users as** "estimated drive time from straight-line distance, free-flow; real trips take longer".

Windows, all planning thresholds, not clinical deadlines:

| Line | Window (min) | Basis |
| --- | --- | --- |
| emoc, trauma | 120 | Lancet Commission 2-hour access indicator (cited) |
| cath_lab | 90 | Design choice informed by ESC 2023 (120 min diagnosis to PCI, minus about 30 min) |
| neurosurgery | 150 | Design choice informed by Seelig 1981 |
| dialysis | 60 | Design choice informed by DOPPS travel bands |
| icu, nicu, ct | 120 | Design choice |
| mri | 180 | Design choice |
| burns | 240 | Design choice |

- **Evidence allowance:** 15 minutes (30 for planned care). This caps any detour by construction.
- **Why not a learned probability?** A trustworthy "chance this facility really offers the service" needs about 100 (ICU) to 2,900 (emoc, corroborated) linked hospitals per grade cell. The best independent source tonight had 32. The likely private-sector list (PM-JAY specialty empanelment) records business choices and allows outsourcing, so its absences are not negatives.
- **Full research:** `reports/score_methodology_research.md` (9 research lenses, each fact-checked, then critiqued). Superseded decisions: D027 and D039.
- **Status:** the v1-final ranker is being integrated now (workflow `integrate-v1-final`). The deployed app still runs the old v0 score.

---

## 6. What the models are, and why a "student"

**In one sentence.** The main model is a fact tagger: Claude labels a sample (the teacher), a small fast classifier learns from those labels (the student), and the student is used only on tags where it provably beats the hand-written patterns.

**Plainly.** There are 275,095 distinct sentences to tag. Three ways to tag them:

| Way | What it is | Gold macro F1 (777 facts) |
| --- | --- | --- |
| Regex v0.2 | Hand-written word patterns | 0.890 (0.872 to 0.907) |
| Teacher | Claude Haiku labeled 29,250 sentences for $0.53 | 0.888 (0.870 to 0.904), a tie |
| Student 1b | One logistic regression per tag, on word features plus the regex tags | 0.906 (0.889 to 0.920) |
| **Shipped mix** | Student on 4 tags, regex on the other 20 | **0.913 (0.896 to 0.928)** |

**Is the student a lightweight classifier?** Yes. For each tag it is one logistic regression over TF-IDF word features plus the regex's own tags. It trains in 24 seconds and tags all 275,095 texts in 22 seconds.

**Why have a student at all?** Honestly, it was not strictly needed: Claude could have tagged everything for about $5. It earns its place for three reasons:

1. It runs free, offline and fast, inside a Databricks job with no API key or outbound network. Virtue Foundation could rerun it on every country.
2. It is deterministic: same input, same output.
3. It combines two signals. Neither Claude alone nor regex alone was best; the student, using both, won on 4 tags.

The gain is real but small: a paired +0.017 to +0.029.

**The burns trap.** The student's `burns` tag also passed on the gold set, but a hand read of 25 new burns tags on real data found 1 right. It had learned "plastic surgery" from the teacher. Burns stays on regex. Gold scores alone would have missed this.

**Technically.**

- **WP4 ship rule:** the paired 95% bootstrap interval of (student minus regex) gold F1 must lie above 0, *and* a hand read of the population must agree.
- **Shipped on** `ob_delivery` (0.53 to 0.75), `h24` (0.88 to 0.96), `icu` (0.79 to 0.91) and `ventilator` (0.79 to 0.93). Hand reads of new tags: ob_delivery 17/25, h24 23/25, icu 15/25, ventilator 25/25.
- **Model version:** `student:s1-1b[ob_delivery,h24,icu,ventilator]+regex:v0.2[rest]`.
- **Round 2 (WP4b, D050, running now):**
  - **Track A, stronger teacher.** Sonnet 5.5 against Opus 5.5 on a pilot of about 600 non-gold hard texts. Disagreements are settled by two independent readers who are blind to which model said what. The chosen teacher relabels about 12,000 weak-tag texts within a $12 cap, and the student is retrained.
  - **Track B, encoder students.** Frozen bge-small embeddings plus logistic regression (2a), and a fine-tuned small encoder on the Mac's GPU (2b), in a separate venv `.venv-ml` so the shared environment is untouched.
  - **The ship rule, declared before any result:** per tag, a candidate replaces the current method only if (1) the paired 95% gold interval is above 0 *and* (2) independent blinded readers agree with it on at least 20 of 25 random disagreements. Ties are broken on validation F1, not gold.
  - **Outputs:** candidate files only (`gold/fact_tags_candidate_wp4b.parquet`). Nothing overwrites the frozen tags. Results pending.
- **Other models:** the drive-time model (section 5). The calibrated probability model was dropped (D048).

---

## 7. What happens when nothing reliable is nearby

**In one sentence.** The tool says plainly that this dataset shows no clear evidence within reach, then points to the nearest real option and what else exists.

**Plainly.** Dialysis from Jokihat, a village in Bihar. The tool does not invent an answer and does not say "there is no dialysis". It says:

> **No facility in this dataset shows clear evidence of dialysis within about 1 hour's estimated drive.**
> **Nearest one that does:** Raiganj Government Medical College And Hospital, about 76 km.
> **Also nearby, not verified:** places that list kidney care but never say they do dialysis, each with the reason.
> **On government lists nearby:** if a listed public unit is near (coming, D055), it appears as a dated outside fact.
> *This dataset mostly covers private facilities with a web presence. A public hospital nearby may not appear here.*

**Technically.**

- Answer `status = "no_reliable_option"`, with `beyond_radius` (the nearest recommendable facility beyond the window) and `unverified_nearby` (claim-only or blocked facilities inside it, each with a reason).
- In the 26-scenario judged run, all 10 no-card answers were judged correct ("abstention_right"). All 4 scenarios written to expect abstention did abstain.
- The abstention rate in that run was 7 of 23 resolved places (30.4%, v0 radii). Under v1-final windows the score research expects about 20% of post offices to abstain for emoc.
- Wording rule: "no facility in this dataset shows clear evidence of", never "there is no" (D028, D048).

---

## 8. Outside data: what we use, what we do not, and why

**In one sentence.** Outside data shapes the method (roads, terrain, medical standards) and appears as dated facts on cards, but it never decides which hospital is recommended.

**Plainly.**

- Road trips and a terrain map tell us how long a drive takes.
- WHO and Indian standards tell us what "corroborated" should mean.
- A government list (LaQshya, certified maternity units) can appear on a card as "certified maternity theatre, valid to …, outside data".
- A hospital that appears only on a government list cannot be recommended, because the brief requires citing the facility's own text and weak name matching would put wrong hospitals on cards.

**Technically.**

| Outside source | Use | Affects ranking? |
| --- | --- | --- |
| OSRM / OpenStreetMap roads, 934 trips | Fits the drive-time formula | Yes (reach) |
| Malaria Atlas Project friction surface (CC BY 4.0) | State terrain factors | Yes (reach) |
| WHO 2009, FRU 2025, FBNC 2025, PM-JAY, PMNDP | Recipe v0.3 rules | Yes (evidence) |
| Natural Earth outline | Outside-India check; India shape on the new map | No |
| LaQshya list of 8 Apr 2026 | Dated card lines, current and lapsed (D051) | No |

- **Why the facility's own website is not an independent check:** for 4,734 of the 8,385 facilities with a website, that website is already one of the record's scraped sources (D040).
- **LaQshya linkage:** all 121 candidate links were read by hand; 63 are correct, 52 wrong and 6 unsure. The matcher alone is 55% precise (90% on strict name matches). Only the 63 correct links produce lines; they cover 62 records. 16 of those records hold a current maternity-theatre certificate, and 1 of the 16 has a record that names a caesarean (exploratory; Hruday spot-checks before it goes on screen).
- **Research:** a workflow verified 51 outside sources (`reports/` and the workflow output), with pilot matches against our records.
- **Augmentation (D055, running before the 04:00 freeze).** Up to 5 lists: AERB radiotherapy licences, the MoHFW list of trauma care facilities, the NHP blood bank directory, NABI burn-care providers, and the National Cancer Grid. They are used two ways:
  1. dated facts on matching cards;
  2. a separate labeled lane, "Also on government or registry lists nearby (not in this dataset)", that can show listed public facilities our private-heavy dataset misses.

  Only links with an agent-checked precision lower bound of at least 0.90 show. The contract is in `docs/integration_notes_standout.md`.

---

## 9. The simulation: what it was, what it is becoming

**In one sentence.** Today it replays fixed paths; it is being rebuilt as an agent swarm where you can verify, add or close a hospital and watch the effect.

**Plainly.**

- **What it is today (option A, D045):** nearby villages (post office areas) animate toward the results. A "trust claims alone" toggle shows the detour. Example: for dialysis near Jaipur, 110 of 238 nearby areas would first reach a facility with no evidence of dialysis.
- **What it is becoming (D044 plus your requirements):**
  - **Agents decide as they go.** Each village's patients pick a destination by a stated rule. "Trust specialty claims" means the nearest place that lists the specialty; "trust the evidence" follows the ranker's rule. They move at a speed from the drive-time model, and re-decide when the world changes or when they reach a place whose record shows no evidence.
  - **What-if tools.** "Verify this record" (what if Virtue Foundation confirmed it), "Add a facility", and "Close a facility". Before and after counters update live.
  - **"Which record should be verified first?"** A button that tests verifying each nearby claim-only facility and shows the one that brings the most villages within reach of evidence-backed care. That is a concrete action for Virtue Foundation.

**Technically.**

- The current trip view uses `/api/trip` with precomputed nearest targets (`app/src/swarm.ts`, `swarm-layer.ts`).
- The rebuild is part D of the UI workflow: a new agent engine in `app/src/sim/`, fed by a `/api/sim` payload (post office points with state factors, candidate facilities with evidence status, flags, windows and allowance), running in the browser on the deck.gl GPU layer.
- **No probabilities or random outcomes:** what happens on arrival comes from the record's evidence status (D045).
- An optional capacity mode is allowed only as a user-set assumption, default off, labeled "assumption, not from data".
- Seeded, so a recording repeats exactly. What-if edits are labeled "what-if", with undo and reset.

---

## 10. The map

**In one sentence.** The map is being rebuilt to show the city and its neighbours with names, zoom out to the shape of India, and expand to full screen.

**Plainly.** You said the map made no sense. The new requirements:

- the searched place, nearby towns and districts with names, and borders;
- zoom out to all of India with the place marked;
- a full-screen mode with scroll-to-zoom and pan;
- a simulation that explains itself, with a legend, play control and a one-line takeaway.

**Technically.**

- No third-party tile service (D009).
- India's shape comes from about 137,000 post office points plus the Natural Earth outline and admin-1 state borders (public domain).
- Labels come from the gazetteer.
- Part B of the UI workflow owns the map (`app/src/minimap.ts`, `geo.ts`, new map files).

---

## 11. How we stand out

**In one sentence.** The obvious tool gives a confident wrong answer on the brief's own example; ours quotes the evidence, shows why it skipped nearer places, and turns uncertainty into a phone call.

**Plainly.**

- **The cold open.** On the brief's Lucknow C-section question (PIN 226001), the obvious query (filter by obstetrics, sort by distance) returns as row 1 a fertility-clinic record 0.95 km away whose 141 facts never mention a caesarean, and whose facts place it in Noida and Bareilly.
- **Ours** returns BNK Hospital about 3.5 km away, quoting "Performs Lower Segment Caesarean Section (LSCS)".

**Technically.**

- **Competitive landscape** (from the brainstorm workflow; its four "judges" were simulated personas, not real feedback):
  - The CMU brief matches Track 3 ("Referral Copilot") of the June 2026 DAIS "Apps & Agents for Good" hackathon, which used the same data.
  - Its winners already did citation guards, Hindi, and merged-record signals, so those features are table stakes.
  - Two CMU teams have public repos; one is close to our card design but was verified only on synthetic data.
- **Adopted package (D044):**
  1. the cold open (SQL on the Marketplace table, 2 warehouse queries, reproduced in pandas);
  2. "Why not the nearer ones?" plus a clickable funnel (at 226001: 9,989 → 9,671 → 118 → 14 → 9 → 3);
  3. "Before you go, ask" (373 of 432 C-section-capable records have no blood fact, so their cards ask about blood);
  4. the sealed evaluation;
  5. the public-system beat;
  6. one numbers file (`findings.json`, 475 numbers with sources, plus a `--check` mode).
- **Shown miss:** the dental operating theatre counted toward a C-section grade (D051).
- **Filming:** the saved SQL result, with names blurred.

---

## 12. How we know it works

**In one sentence.** Each layer is tested on its own, then the whole tool is judged on scenarios and a sealed, fair comparison.

**Plainly.** Gold labels test the tagger, frozen cases test the resolver, hand reviews test the trust checks, and independent judges grade the answers. A sealed evaluation compares our tool with the obvious tools on random places, with judges who never see which tool made which pick.

**Technically.**

| Question | Evidence | Status |
| --- | --- | --- |
| Tagger | Shipped mix macro F1 0.913 on 777 agent-labeled gold facts, with intervals and population hand reads | Done |
| Grade rules | Cited standards; 232 changed records read | Done |
| Resolver | 54/55 frozen cases | Done |
| Trust checks | Hand-review precision per check | Done |
| Answers (v0) | 26 scenarios, 3 independent judges: of 48 cards, 31 right (65%), 16 defensible (33%), 1 wrong (2%); 10 of 10 abstentions right | Done; a blinded v0 against v1 rerun is pending |
| Against the obvious tools | Sealed evaluation, below | Dev half done; sealed half pending |

**Sealed evaluation design** (`reports/eval_sealed_protocol.md`):

- **Sample:** 120 random geocoded, non-suspect post offices, stratified by state, split 60 dev and 60 sealed. Services: emoc and dialysis at every point, plus a third line at a random 20 points per half.
- **Tools compared**, each with the same reach rule and the same right to abstain: (a) specialty filter, (b) nearest facility with any service fact, (c) our ranker.
- **Judges:** Haiku and Sonnet, with Opus on disagreements. The judges are blind to which tool made the pick.
- **Human check:** 40 human labels from Hruday check the judges.
- **Seals:** the protocol is hashed and sealed. The reach-rule amendment (all tools on the v1-final drive-time windows) is drafted, not yet sealed.

**Dev half, v0 radii:**

| Tool | Top pick's record clearly shows the service |
| --- | --- |
| Specialty filter | 25 of 112 (22%) |
| Any-fact baseline | 69 of 83 (83%) |
| Our ranker | 76 of 82 (93%) |

- **Cost of our caution:** the ranker skipped a nearer facility that the judges considered supported in 23 of 82 answers (28%, 19% to 39%). The report leads with this number.
- **Rubric check:** only 8 of 107 judge "yes" votes (7.5%) rested on a specialty listing alone, under the 10% threshold fixed in advance, so the rubric stayed as it was (D053).

---

## 13. Databricks

**In one sentence.** The app is deployed on Databricks Free Edition and answers through its own URL; the pipeline runs as a notebook job on serverless compute.

**Technically.**

- **Workspace:** `https://dbc-96b6f0b2-f8fd.cloud.databricks.com`, with the CLI authenticated (profile DEFAULT).
- **Marketplace catalog:** `databricks_virtue_foundation_dataset_dais_2026`. Serverless Starter Warehouse `ee1fa7958ed99cf2`.
- **App:** `vf-care-finder` at https://vf-care-finder-7474645226493940.aws.databricksapps.com (workspace login required).
- **Verified through the deployed URL:** health ok; C-section at 226001 returns 3 cited cards (D054). The deployed build is v0; it is redeployed once the v1-final ranker and the new UI are both done.
- **Free Edition limits (D034):** serverless only, about 3 apps per account, one Vector Search endpoint. **An app stops 24 hours after its last start or deploy, so restart it before judging.**
- **10 MB file limit:** the keyword-search matrix was 20 MB in one file. It is now written as row blocks under 8 MB each and stacked back on load, with a shape check (D043). The largest bundle file is 7.2 MB.
- **Notebook:** `notebooks/01_pipeline.py` reads the Marketplace tables with Spark and runs the same Python steps. It was fixed to apply the saved WP4 student and to survive the trust-check step.

---

## 14. Judging criteria and deadline

From cmudsc.com/datathon-fall2026 (D047):

| Criterion | Organizers' words | Our answer |
| --- | --- | --- |
| Exploration | "Uncover novel datasets and provide insightful analysis" | Outside lists, 934 routed trips, published standards; about 420 of 4,577 obstetric listings name a caesarean |
| Modeling | "Develop advanced models to find meaningful data relationships" | Teacher and student tagger with blinded ship rules; the drive-time model with out-of-fold error; the sealed evaluation |
| Impact | "Propose actionable and impactful policies" | A one-minute tool for health workers; phone questions instead of wasted trips; "which record to verify first" for Virtue Foundation |

- **Submission:** code, analysis and a video through the organizers' form, tentatively due **Sunday Nov 22, 12:30 PM**.
- **Judging:** 1 to 4 PM.

---

## 15. What we will not claim

- No probabilities, "likely offers", "calibrated" or "validated".
- No "there is no care here", and no "cannot". Only "no facility in this dataset shows clear evidence".
- Drive times are estimates from straight-line distance at free-flow speed, not road travel times.
- Time windows are planning thresholds, not clinical deadlines.
- A hospital missing from a government list is not evidence that it lacks the service.
- The gold set, link checks and scenario judges are agent-made; Hruday's 40 labels are the human check. Use "sealed and hashed", never "pre-registered".
- "Web sources" are not independent: facebook.com is a source in 88.5% of records and justdial.com in 54%.

---

## 16. What Hruday needs to do

1. **About 15 minutes, after the sealed run:** label 40 shuffled evaluation items, with the tool hidden.
2. **About 20 minutes, before 08:00:** spot-check the LaQshya and new outside-list links that will appear on screen.
3. **Morning:** restart the app, record the video, and submit through the form by 12:30 PM.

---

## 17. Glossary

| Term | Meaning |
| --- | --- |
| Fact | One short sentence from a facility record (capability, procedure, equipment) |
| Tag | A label on a fact saying which service evidence it contains, e.g. `csection` |
| Grade | 0 none, 1 claimed, 2 supported, 3 corroborated, per facility and service |
| Corroborated | The service plus its enabling pieces are named (e.g. caesarean plus maternal blood) |
| Recipe | The rule that turns tags into a grade for one service |
| Gate | A yes or no check every recommendation must pass |
| Window | The estimated drive time within which a facility counts as reachable |
| Allowance | How much further (15 or 30 min) a better-evidenced facility may be and still come first |
| Abstention | "No reliable option nearby", plus what is known |
| Teacher | Claude labeling sentences for training |
| Student | The small logistic-regression classifier trained on the teacher's labels |
| Gold set | 777 hand-labeled facts used only for scoring, never training |
| F1 | Balance of precision (tags right when given) and recall (right tags found); 1.0 is perfect |
| Macro F1 | F1 averaged over tags, each tag counting equally |
| Wilson interval | A 95% range for a proportion that behaves well with small counts |
| Paired bootstrap | Resampling the same rows for two methods to test whether one is really better |
| Sealed | A protocol hashed and committed before results exist, so it cannot change to fit them |
| Bundle | The small set of files the app loads at startup |
| Precision (linkage) | Share of matched outside records that really are the same facility |
| Trust flag | A warning that the record may be wrong: location, merge, implausible claim |
| Resolver | The code that turns typed text into a map point with a precision |

---

## 18. Timeline of the night (ET)

| Time | Event |
| --- | --- |
| Nov 21, ~20:00 | Re-scope to the official brief (D023); pre-pivot snapshot committed |
| ~20:45 | Teacher API key verified; Databricks CLI installed (D037) |
| ~21:00 | Outside-data research and the core build launched in parallel |
| ~21:45 | Score methodology research and stand-out brainstorm launched |
| ~22:30 | Keyword index split for the 10 MB Apps limit (D043) |
| ~22:45 | Stand-out package adopted (D044); trip view option A (D045) |
| ~23:00 | Score spec v1-final adopted (D048); learned probability dropped |
| ~23:20 | Tagger round 2 launched with the ship rule declared first (D050) |
| ~23:35 | Stand-out modules and sealed protocol committed |
| ~23:45 | Recipe v0.3 committed (D052) |
| ~23:50 | App deployed and verified on Databricks (D054); dev rubric check done (D053) |
| Nov 22, ~00:05 | Core build complete: 976 tests pass, 42 of 42 quotes verbatim |
| ~00:10 | v1-final integration and UI overhaul launched |
| ~00:20 | Map, agent swarm and what-if requirements added to the UI workflow; outside-list augmentation planned (D055) |
| ~04:00 (planned) | Freeze the bundle, seal the reach amendment, run the sealed evaluation |
| 12:30 PM | Submission (tentative) |
