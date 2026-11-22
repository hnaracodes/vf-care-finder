# The care finder, explained end to end

Written for Hruday, from the questions asked during the night of Nov 21 to 22, 2026. Updated Nov 22, about 06:00 ET: every workstream from the night has finished, the build is frozen (D065), and the sealed evaluation has run once (D066). Numbers come from `reports/findings.json` (1,763 numbers, each with its source file and how it was computed, D067) and the decisions log (`docs/08_decisions_log.md`, D023 to D067). The only open items are Hruday's: the 40 human labels, the recording and the submission (section 16).

This is internal process history, not a submission document. About 60 of its numbers (process counts, build sizes, rehearsal simulator counts, and values that `findings.json` holds as explicit nulls) are not in `findings.json`, so `wp9_findings.py --check` does not pass on it; do not copy numbers from here into the video, the README or the methods without checking them there first.

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
9. The simulation: what it was, what it became
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

**Plainly.** Before the pivot, agents had already cleaned the facility table, cleaned every post office, built a PIN lookup, written regex taggers with an agent-labeled test set, computed distances, and built an India-wide map with a patient swarm. The status board still said "todo" everywhere. (The board in `tasks/README.md` now shows the final state of every work package.)

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

- 9,989 facilities, 88% private (8,835 private, 467 public, 687 unknown). 451,105 facts, of which 275,095 are distinct texts. 9,671 facilities have a usable location.
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
- **Recipe v0.3** (D052) is a rule-only regrade of the frozen tags into `gold/capability_grades_v03.parquet`, aligned to published standards: WHO 2009 Table 4 (emergency obstetric signal functions), FRU 2025, FBNC 2025 (newborn care), PM-JAY empanelment criteria, and PMNDP (the national dialysis programme). v0.3 is what the frozen build and the deployed app gate on.

v0.3 changes against v0.2:

| Line | Change | Grade 2+ | Grade 3 |
| --- | --- | --- | --- |
| NICU | An SNCU-only record drops to claimed. FBNC: "Simply adding ventilators to SNCU does not qualify the unit as NICU" | 706 to 694 | 178 to 112 |
| Cath lab | Needs an adult ICU with a ventilator (PM-JAY item 3) | unchanged | 481 to 205 |
| Dialysis | A nephrologist alone does not count | unchanged | 480 to 456 |
| Emergency C-section | Caesarean plus *maternal* blood; fetal transfusion excluded, because WHO signal function 9 is maternal transfusion | unchanged | 41 to 54 |

232 changed records were read by an AI agent: none were wrong under the final rules. D052 logged 13 as debatable; the recipe report's final list has 15 (3 added in the fix round), 10 of them children's hospitals under the NICU ventilator rule. v0.2 reproduces from the frozen tags with 0 differing cells.

- **Final grade counts (frozen build):** 10,957 facility-lines at grade 2 or higher across the 12 lines. For emergency C-section: 432 records name a caesarean (378 supported, 54 corroborated), 409 of them recommendable after trust checks.

### 4.3 Trust checks (WP11, `src/vf/trust.py`, final version `wp11_v3`)

- 2,471 of 9,989 records flagged (25%), in 3,654 flag rows; 1,010 of the 10,957 facility-lines at grade 2 or higher blocked, on 520 facilities; 75 duplicate pairs (`gold/facility_flags.parquet`, `gold/facility_duplicates.parquet`).

| Check | Signal | Effect |
| --- | --- | --- |
| Location conflict | Builds on WP1's `coord_check`: `excluded_unresolved` (282) is dropped, `kept_unresolved` blocks, `moved_to_pin` (785) is a minor flag with PIN-level precision | The serious kinds were 15 of 15 correct on an AI agent's review |
| Possible merged record | Facts naming other cities or branches; shared websites across districts | Penalty or warning |
| Merged evidence (added in `wp11_v3`, D061) | On a record already flagged as a possible merge, the supporting fact names another institution or a city over 40 km away, or sits within 2 facts of one | Blocks that line. 26 of 30 correct on a fresh agent read (87%, Wilson 70% to 95%). Blocks 170 lines, 107 of them new. Blocks the Gadchiroli record, so v1 now abstains there; its grade is still wrong |
| Implausible claim | E.g. a clinic, dentist or pharmacy claiming cardiac surgery | Blocking for that line |
| Duplicates | Near-same names within 1 km, or the same site and PIN | Shown once, "also listed as" |

### 4.4 Location resolver (WP2 Part B, `src/vf/locate.py`)

- A gazetteer `silver/places.parquet` of 182,755 places (PINs, districts, post office names, cities), each with a precision radius. Final version `places-v3`.
- Order of resolution: coordinates, PIN, district, city, post office. Exact match comes before fuzzy match, and fuzzy matching works within the state first.
- **Ambiguous names return candidates, never a guess.** Aurangabad is in both Bihar and Maharashtra.
- 54 of 55 frozen test cases correct (cases written by the resolver's author agent). The miss, "Hydrabad", is returned as ambiguous on purpose.
- Median 0.4 ms per query. The "inside India" check uses the Natural Earth outline.
- **City points (fixed, D046 and D058):** a city name uses the city point only when that point is precise to under 15 km; otherwise the answer says "Uncertain: X spans about N km" and cards say "drive time unclear". Jaipur now resolves to about 13 km (it was about 72 km).
- **PIN points (places-v3, D061):** outlier post offices are dropped before a PIN's point and precision are computed. Kotputli PIN 303108 went from 163.4 to 11.5 km. PIN precision median 6.4 to 4.9 km, 90th percentile 46.1 to 20.8 km.
- **Overnight fixes:** integer coordinate pairs such as "30,79" are read as coordinates (precision 111 km); "Delhi" goes to the New Delhi district centre through a resolver-only alias.

### 4.5 The bundle and the app

- `scripts/wp12_bundle.py` writes `dbapp/bundle/`: facilities, v0.3 grades, `wp11_v3` flags, duplicates, places-v3, post offices, the terrain table, record info with call numbers, LaQshya links, the outside card facts and lane, and the keyword index for free-text search (D064).
- Totals: 46.5 MB, every file under 10 MB. Call numbers live only in `record_info.parquet` and are served masked unless the user taps to call with demo mode off. Every other shipped quote has digit runs of 8 or more masked as `[phone number]`. `--check` scans every file under `dbapp/` for call numbers in clear and finds none.
- The app loads the bundle once at startup and answers in 2 to 4 ms per query, with no database call per request (D033). In the v1 scenario run the median was 0.9 ms.
- The app is a FastAPI server (`dbapp/server.py`) plus a prebuilt web page from `app/` (Vite, TypeScript, deck.gl).
- The bundle is frozen: `reports/FROZEN_BUILD.json` records the manifest hash `520a5159...` and 34 silver and gold tables (D065).

---

## 5. How the score is calculated, and why the first one was a placeholder

**In one sentence.** The score is gone: three yes or no checks, then nearest first by estimated drive time, with one small declared bonus for stronger evidence.

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
| burns, cardiac_surgery, oncology | 240 | Design choice |

- **Evidence allowance:** 15 minutes (30 for planned care: MRI, cardiac surgery, oncology). This caps any detour by construction.
- **Why not a learned probability?** A trustworthy "chance this facility really offers the service" needs about 100 (ICU) to 2,900 (emoc, corroborated) linked hospitals per grade cell. The best independent source tonight had 32. The obvious private-sector list (PM-JAY specialty empanelment) records business choices and allows outsourcing, so its absences are not negatives.
- **Full research:** `reports/score_methodology_research.md` (9 research lenses, each fact-checked, then critiqued). Superseded decisions: D027 and D039.
- **Status: integrated, frozen and deployed (D058).** The ranker is `v1`; the deployed app serves it. Four declared design choices were added during review:
  1. the nearest recommendable facility is always shown, taking the last card if the allowance pushed it out (it was missing in 689 of 6,274 sweep queries before, 0 after);
  2. records with a possible-merge or location-conflict flag get no evidence allowance;
  3. when the place is wider than the window reaches, the headline says "Uncertain: X spans about N km";
  4. a city name uses the city point only when it is precise to under 15 km.
- **Sensitivity of the frozen ranker** (1,500 random geocoded post offices, averaged over the 12 lines, `ordering.*` in findings.json):

| Change | Origins with no answer | Top pick changes |
| --- | --- | --- |
| As shipped | 12.9% | |
| Windows halved | 44.6% | 2.3% |
| Windows doubled | 1.4% | 0.7% |
| Terrain factors off | 11.3% | 1.3% |
| Evidence allowance off | 12.9% | 27.2% |
| Flagged records keep the allowance | 12.9% | 6.8% |

The allowance is the biggest single ordering choice: turning it off changes the top pick at 27.2% of origins. The windows decide how often the tool abstains.

---

## 6. What the models are, and why a "student"

**In one sentence.** The main model is a fact tagger: Claude labels a sample (the teacher), a small fast classifier learns from those labels (the student), and the student is used only on tags where it beat the hand-written patterns on agent-labeled gold and passed an agent's read of the texts it adds.

**Plainly.** There are 275,095 distinct sentences to tag. Ways to tag them, scored on the same 777 gold facts:

| Way | What it is | Gold macro F1 (777 facts) |
| --- | --- | --- |
| Regex v0.2 | Hand-written word patterns | 0.890 (0.872 to 0.907) |
| Teacher | Claude Haiku labeled 29,300 sentences (23,441 train, 5,859 validation); the whole run cost $0.55 | 0.888 (0.870 to 0.904), a tie |
| Student 1b | One logistic regression per tag, on word features plus the regex tags | 0.906 (0.889 to 0.920) |
| WP4 mix | Student 1b on 4 tags, regex on the other 20 | 0.913 (0.896 to 0.928) |
| **Final mix (frozen build)** | Round 2's fine-tuned encoder on `ob_delivery`, student 1b on 3 tags, regex on the other 20 | **0.916 (0.898 to 0.931)** |

**Is the student a lightweight classifier?** Mostly yes. For 3 of its 4 tags it is one logistic regression over TF-IDF word features plus the regex's own tags. It trains in 24 seconds and tags all 275,095 texts in 22 seconds. The fourth tag, `ob_delivery`, now uses a small fine-tuned text encoder (bge-small), trained in 5 minutes on the Mac's GPU.

**Why have a student at all?** Honestly, it was not strictly needed: Claude could have tagged everything for about $5. It earns its place for three reasons:

1. It runs free, offline and fast, with no API key or outbound network, so it could run inside a Databricks job (none was run for the final build). Virtue Foundation could rerun it on every country.
2. It is deterministic: same input, same output.
3. It combines two signals. Neither Claude alone nor regex alone was best; the student, using both, won on 4 tags.

The gain is real but small: the final mix beats regex by a paired +0.019 to +0.032.

**The burns trap.** The student's `burns` tag also passed on the gold set, but an agent's read of 25 new burns tags on real data found 1 right. It had learned "plastic surgery" from the teacher. Burns stays on regex. Gold scores alone would have missed this. It happened again in round 2 (below).

**Technically.**

- **WP4 ship rule:** the paired 95% bootstrap interval of (student minus regex) gold F1 must lie above 0, *and* an agent's unblinded read of 25 texts the student adds must find at least half right.
- **WP4 shipped on** `ob_delivery` (0.53 to 0.75), `h24` (0.88 to 0.96), `icu` (0.79 to 0.91) and `ventilator` (0.79 to 0.93). Agent reads of new tags: ob_delivery 17/25, h24 23/25, icu 15/25, ventilator 25/25. icu passed at 60%, the rate at which burns was later rejected under the round-2 bar; the icu student raised 306 facilities to grade 2 or higher, some wrongly.
- **Round 2 (WP4b, D050 rule declared first; result D059; promoted D064):**
  - **Track A, stronger teacher.** On 269 blinded disagreements settled by two AI agent readers, Opus 5.5 was right on 214 (80%, 74% to 84%), Sonnet 5.5 on 178 (66%, 60% to 72%), Haiku on 76 (28%, 23% to 34%; Haiku mostly says yes alone on lists). Opus was chosen and relabeled 12,000 weak-tag texts for $6.55.
  - **Track B, encoder students.** Three candidates were retrained on 36,299 merged labels: `1b_r2` (logistic regression), `2a_r2` (frozen bge-base embeddings plus logistic regression) and `2b_r2` (bge-small fine-tuned on the Mac's GPU). As whole taggers all three scored below the WP4 mix (gold macro F1 0.899, 0.877 and 0.893; every paired interval below 0).
  - **Per tag, 2 of 72 (candidate, tag) pairs passed the gold gate,** both from `2b_r2`:
    - `ob_delivery`: gold F1 0.75 to 0.82, paired interval +0.005 to +0.144; blinded population read 23 of 25 for the candidate (92%, 75% to 98%). **Ships.** The gain is marginal: the interval is above 0 in 70 of 100 other bootstrap seeds, and P(gain at most 0) is about 2.3%.
    - `burns`: gold F1 0.79 to 0.89, but the blinded population read favoured it on only 15 of 25 (60%, below the 20 of 25 bar). **Does not ship**, as in WP4.
  - **Effect on the frozen build:** 1,625 fact rows at 1,013 facilities change tags; 22 emergency C-section facility-lines move between grade 0 and 1 (6 up, 16 down); **0 facility-lines cross the grade 2 gate on any line, so no recommendation changes.** WP4b spend $7.13.
- **Model version (frozen):** `student:wp4b-2b_r2[ob_delivery]+student:s1-1b[h24,icu,ventilator]+regex:v0.2[rest]`.
- **Other models:** the drive-time model (section 5). The calibrated probability model was dropped (D048).

---

## 7. What happens when nothing reliable is nearby

**In one sentence.** The tool says plainly that this dataset shows no clear evidence within reach, then points to the nearest real option and what else exists.

**Plainly.** Dialysis from Jokihat, a village in Bihar. The tool does not invent an answer and does not say "there is no dialysis". It says, in the shipped app:

> **No facility in this dataset shows clear evidence of dialysis within about 1 hour's estimated drive.** This is a gap in our data, not proof that no care exists.
> **Nearest one that does:** Raiganj Government Medical College And Hospital, about 1 h 30 min (it could be 55 min to 2 h 30 min).
> **Also nearby, not verified:** places that list kidney care but never say they do dialysis, each with the reason.
> **Next step:** call the district hospital, or your ANM or block health officer, and ask where dialysis is available nearer to Jokihat.
> *This dataset mostly covers private facilities with a web presence. A public hospital nearby may not appear here.*

Where an outside list has a nearby public unit, a separate section "Also on government or registry lists nearby (not matched to a record in this dataset)" follows (section 8). For emergency C-section and NICU, the next step also says: "In an emergency, call 108 for an ambulance. For a pregnant woman or a sick newborn, 102 is free transport in most states."

**Technically.**

- Answer `status = "no_reliable_option"`, with `beyond_radius` (the nearest recommendable facility beyond the window) and `unverified_nearby` (claim-only or blocked facilities inside it, each with a reason).
- **Is the silence right?** In the sealed evaluation the ranker abstained on 39 of 140 queries, and the judges found a facility inside the window whose record clearly shows the service in 0 of the 39 (0%, 95% CI 0% to 9%). That is a thin lower bound: in 31 of the 39 no facility had any signal for the service inside the window.
- Earlier, in the 26-scenario judged run, all 10 no-card answers were judged correct, and all 4 scenarios written to expect abstention did abstain.
- **How often it abstains:** 7 of 23 resolved places (30.4%) in the 26-scenario run, the same for v0 and v1. Over 1,500 random post offices, averaged over the 12 lines, the frozen ranker abstains at 12.9% of origins (44.6% with windows halved, 1.4% doubled).
- Wording rule: "no facility in this dataset shows clear evidence of", never "there is no" (D028, D048).

---

## 8. Outside data: what we use, what we do not, and why

**In one sentence.** Outside data shapes the method (roads, terrain, medical standards) and appears as dated facts on cards and in a separate lane, but it never decides which hospital is recommended.

**Plainly.**

- Road trips and a terrain map tell us how long a drive takes.
- WHO and Indian standards tell us what "corroborated" should mean.
- A government or registry list can appear on a card as a dated line, for example "Government list (LaQshya, NHSRC, 8 Apr 2026, page 35 row 1109): maternity operating theatre certified, valid to 31 Dec 2026".
- A hospital that appears only on an outside list is shown in a separate, labeled lane, never as a card. It is never recommended, because the brief requires citing the facility's own text and weak name matching would put wrong hospitals on cards.

**Technically.**

| Outside source | Use | Affects ranking? |
| --- | --- | --- |
| OSRM / OpenStreetMap roads, 934 trips | Fits the drive-time formula | Yes (reach) |
| Malaria Atlas Project friction surface (CC BY 4.0) | State terrain factors | Yes (reach) |
| WHO 2009, FRU 2025, FBNC 2025, PM-JAY, PMNDP | Recipe v0.3 rules | Yes (evidence) |
| Natural Earth outline and state borders | Outside-India check; India shape and borders on the map | No |
| LaQshya list of 8 Apr 2026 | 63 card links on 62 records (current and lapsed, D051); 779 lane rows | No |
| AERB radiotherapy licences | 79 card facts (oncology); 425 lane rows | No |
| National Cancer Grid | 54 card facts (oncology); no lane rows (terms) | No |

- **Why the facility's own website is not an independent check:** for 4,734 of the 8,385 facilities with a website, that website is already one of the record's scraped sources (D040).
- **LaQshya linkage:** all 121 candidate links were read by an AI agent (Hruday's spot-check is still to do); 63 are correct, 52 wrong and 6 unsure. The matcher alone is 55% precise (90% on strict name matches). Only the 63 correct links produce lines; they cover 62 records. 16 of those records hold a current maternity-theatre certificate, and 1 of the 16 has a record that names a caesarean.
- **Research:** `reports/outside_data_research.md` covers 81 sources, 64 of them verified (D057).
- **Augmentation result (D055 plan, D057 terms, D060 result):**
  - **Card facts:** 196 in all (LaQshya 63, AERB 79, NCG 54), only from link bands whose agent-checked precision passed a one-sided 95% lower bound of 0.90. AERB's automatic band: 79 same of 81 decided, lower bound 0.924 (0.879 if the 3 unsure verdicts were counted as wrong). NCG's: 54 of 54, lower bound 0.946. The links are agent-read.
  - **Lane:** 1,204 rows (LaQshya 779, AERB 425), only from lists whose terms allow reuse with acknowledgement. Rows that might be ours (unread or unsure candidates, near-miss scores, nearby look-alike names, records linked by another list) are removed first. A residual 5% to 8% of AERB lane rows may still be in our data under a different PIN (upper bound 18%), so the heading says "not matched to a record in this dataset", never "not in this dataset".
  - **Withheld:** the NHP blood bank directory (57 card facts built, but reachable only through GitHub re-hosts, which D057 rules out); MoHFW trauma centres (no link band passed, best 0.807, and the terms require permission); NABI burn-care providers (no band passed, 0.878; all rights reserved); NCG lane rows (all rights reserved).
  - **Terms (D057):** the app shows derived facts only (source, what the list says, date, citation), never the tables; raw files stay out of git; official pages may be cited through dated Wayback snapshots; sites whose robots.txt disallows Claude agents were not fetched.
  - **In the app:** the lane shows under answers with cards and under abstentions, in its own section, with the line "Outside data, cited to the list. Not checked against our records' text, and never used to pick the options above." Rows placed only at a district centre say "Position: district only, so no distance is given".

---

## 9. The simulation: what it was, what it became

**In one sentence.** It started as fixed paths replayed on the map; it shipped as a seeded agent swarm where you can check, add or close a hospital and watch the effect.

**Plainly.**

- **What it was (option A, D045):** nearby villages (post office areas) animated toward the results, with a "trust claims alone" toggle to show the detour.
- **What shipped (D056, D062):**
  - **Agents decide as they go.** Each village's patients pick a destination by a stated rule. "Trust specialty claims" means the nearest place that lists the specialty; "trust the evidence" follows the ranker's rule. They move at speeds from the drive-time model, and re-decide when the world changes or when they reach a place whose record shows no evidence.
  - **What-if tools.** Check a record (what if Virtue Foundation confirmed it), add a facility, close a facility, with undo, reset, and real against what-if counters.
  - **"Which record should be checked first?"** A button that tries checking each nearby claim-only record and shows the one that brings the most villages within the window of evidence-backed care. That is a concrete verification priority for Virtue Foundation.
  - **Real examples from testing (from rehearsal, not in findings.json; keep out of recorded material):** dialysis at Jokihat starts with 11 of 170 nearby villages within the 1-hour window of evidence-backed care; adding a facility there brings it to 159 of 170. Emergency C-section at Lucknow already has all 402 nearby villages within 2 hours, so adding a facility honestly shows no change.
  - "Village" is defined on screen: one India Post office area within about 50 km.

**Technically.**

- The engine is in `app/src/sim/`, fed by `/api/sim` (post office points with state factors, candidate facilities with evidence status, flags, windows and allowance), and runs in the browser on the deck.gl GPU layer. The old `/api/trip` endpoint remains, but the app uses the simulator.
- **No probabilities or random outcomes:** what happens on arrival comes from the record's evidence status (D045). `/api/simulate`, the probability version, reports unavailable because no probability model was built.
- An optional capacity mode is allowed only as a user-set assumption, default off, labeled "assumption, not from data".
- Seeded, so a recording repeats exactly. What-if edits are labeled "what-if", with undo and reset.
- **Known open items (D062 and the overnight test):** villages a check would bring in are not individually ringed; the claims-policy run on Lucknow takes about 18 seconds, longer than the planned 15-second beat; "Which record should be checked first?" needs a scroll at 900 px height.

---

## 10. The map

**In one sentence.** The shipped map shows the searched place with nearby names and borders, zooms out to the shape of India, and expands to full screen.

**Plainly.** You said the map made no sense. What shipped (D062):

- the searched place, nearby districts and states with names, and state borders;
- an India locator inset, and a "Show all of India" zoom with the place marked;
- an expanded full-screen mode with scroll-to-zoom and pan;
- a simulation that explains itself, with a legend, play control and a one-line takeaway.

**Technically.**

- No third-party tile service (D009).
- India's shape comes from about 137,000 post office points plus the Natural Earth outline and admin-1 state borders (public domain).
- Labels come from the gazetteer; a declutter step drops overlapping labels, and the simulation's count labels hide below zoom 6.
- Back closes the expanded map (`#map` history entry), and each new search can be undone with Back.
- **Known open items:** the map canvas is not keyboard-pannable and has no focus ring; in the India view the simulation panel covers north India, so close it before "Show all of India" on camera; Jaipur markers 2 and 3 overlap; the island map fit does not include the Kavaratti origin; in keyword mode the legend still says "Option with evidence in its record".

---

## 11. How we stand out

**In one sentence.** The obvious tool gives a confident wrong answer on the brief's own example; ours quotes the evidence, shows why it skipped nearer places, and turns uncertainty into a phone call.

**Plainly.**

- **The cold open.** On the brief's Lucknow C-section question (PIN 226001), the obvious query (filter by obstetrics, sort by distance) returns as row 1 a fertility-clinic record 0.95 km away whose 141 facts never mention a caesarean, and whose facts place it in Noida and Bareilly.
- **Ours** returns BNK Hospital about 3.5 km away, quoting "Performs Lower Segment Caesarean Section (LSCS)".
- **It is typical, not cherry-picked.** At 356 of the 414 post offices within 50 km of Lucknow, the nearest obstetrics listing has no fact naming a caesarean.

**Technically.**

- **Competitive landscape** (from the brainstorm workflow; its four "judges" were simulated personas, not real feedback):
  - The CMU brief matches Track 3 ("Referral Copilot") of the June 2026 DAIS "Apps & Agents for Good" hackathon, which used the same data.
  - Its winners already did citation guards, Hindi, and merged-record signals, so those features are table stakes.
  - Two CMU teams have public repos; one is close to our card design but was verified only on synthetic data.
- **Adopted package (D044), as shipped:**
  1. the cold open (SQL on the Marketplace table: 4,532 rows pass the filmed obstetrics filter; reproduced in pandas);
  2. "Why not the nearer ones?" plus the gate funnel. At 226001 in the frozen build: 9,989 records, 9,671 with a usable location, 468 that claim the service within about 6 hours, 47 recommendable (29 blocked by a flag), 13 within the 2-hour window, 3 cards. The funnel tiles are not clickable, so drop the "clickable funnel" beat from the script;
  3. "Before you go, ask" (373 of 432 C-section-capable records have no blood fact, so their cards ask about blood);
  4. the sealed evaluation (section 12): the ranker's top pick clearly shows the service 89% of the time, the specialty filter's 20%;
  5. the public-system beat: public why-not rows now carry their LaQshya line and a masked call button (Nashik's District Hospital), and the outside-list lane;
  6. one numbers file (`reports/findings.json`: 1,763 numbers with sources, 7 explicit nulls where no machine-readable source exists, plus a `--check` mode, D067).
- **Shown miss:** the dental operating theatre counted toward a C-section grade (D051).
- **Filming:** the saved SQL result, with names blurred.

---

## 12. How we know it works

**In one sentence.** Each layer is tested on its own, then the whole tool is judged on scenarios and a sealed, fair comparison.

**Plainly.** Gold labels test the tagger, frozen cases test the resolver, AI agent reviews test the trust checks, and separate AI judges grade the answers. A sealed evaluation compared our tool with the obvious tools on random places, with judges who never saw which tool made which pick. The headline: our ranker is far better than filtering by specialty, about as good as a simpler "any fact names it" tool, and its caution costs a nearer supported option in about one answer in six.

**Technically.**

| Question | Evidence | Status |
| --- | --- | --- |
| Tagger | Final mix macro F1 0.916 (0.898 to 0.931) on 777 agent-labeled gold facts, with intervals and agent reads of the population | Done |
| Grade rules | Cited standards; 232 changed records read | Done |
| Resolver | 54/55 frozen cases | Done |
| Trust checks | Agent-review precision per check; merged-evidence block 26/30 | Done |
| Answers (v0) | 26 scenarios, 3 separate AI judges: of 48 cards, 31 right (65%), 16 defensible (33%), 1 wrong (2%); 10 of 10 abstentions right | Done |
| v0 against v1, blinded | Round 1 (before the fix round): judges preferred v1 8, v0 5, tie 13 (sign test p = 0.58). Round 2 (fresh judges, new seed): v1 10, v0 4, tie 12 (p = 0.18); marks v1 12 right, 6 defensible, 0 wrong; v0 12, 5, 1 | Done: v1 leans better, not proven better (D058, D061) |
| Against the obvious tools | Sealed evaluation, below | Done, sealed half run once (D066) |
| Card quotes | Every quote on every sealed-run card equals the cited fact: 451 of 451 | Done (an integrity check, not a reading-accuracy measure) |
| Human check of the judges | 40 items in `data/labels/eval_sealed/human_labels_sealed.csv` | **Not labeled yet; no human agreement figure exists** |

**Sealed evaluation design** (`reports/eval_sealed_protocol.md`):

- **Sample:** 120 random geocoded, non-suspect post offices, stratified by state, split 60 dev and 60 sealed. Services: emoc and dialysis at every point, plus a third line at a random 20 points per half: 140 queries per half.
- **Tools compared**, each with the same reach rule and the same right to abstain: (a) specialty filter, (b) nearest facility with any service fact, (c) our ranker.
- **Judges:** Haiku and Sonnet, with Opus on disagreements. The judges are blind to which tool made the pick.
- **Human check:** 40 human labels from Hruday check the judges.
- **Seals:** the protocol is hashed and sealed. The reach amendment (all tools on the v1-final drive-time windows) was sealed as Amendment 3 (`84a7bdd6...`) after the build froze at 05:15 ET, later than the 04:00 ET the protocol named (D065).

**Sealed half, run once on the frozen build** (`reports/eval_sealed_results.md`; 95% Wilson intervals):

| | Specialty filter | Any fact names it | Our ranker |
| --- | --- | --- | --- |
| Places answered | 115 of 140 (82%) | 101 of 140 (72%) | 101 of 140 (72%) |
| Top pick's record clearly shows the service | 23 of 115 (20%; 14% to 28%) | 85 of 101 (84%; 76% to 90%) | 90 of 101 (89%; 82% to 94%) |
| Top pick also judged sensible for the place | 19 of 115 (17%) | 55 of 101 (54%) | 63 of 101 (62%) |
| Skipped a nearer supported option | 1 of 115 (1%) | 4 of 101 (4%) | 16 of 101 (16%; 10% to 24%) |
| Wrong top pick, share of all queries | 92 of 140 (66%) | 16 of 140 (11%) | 11 of 140 (8%) |

- **Against the specialty filter:** a wide margin, 89% against 20%. For emergency C-section the specialty filter's top pick clearly showed the service in 4 of 58 queries.
- **Against the any-fact baseline: not shown better on the sealed half.** Both answer the same 101 queries and give the same top pick in 76. Where they differ, the ranker's pick is supported and the baseline's is not in 6 queries, the reverse in 1 (sign test p = 0.125). On the dev half the same count was 9 against 1 (p = 0.021). The direction holds on both halves; the sealed half alone does not establish it.
- **Cost of our caution:** of the 16 skipped nearer options, 12 are on the ranker's own answer as card 2 or 3 (the evidence allowance put a better-evidenced record first, as designed). 4 are real grading misses: the nearer facility is graded 1 by recipe v0.3 while the judges read its record as clearly showing the service.
- **The ranker's 11 wrong picks:** 4 ICU (an ICU but no ventilator care), 5 dialysis (text about another hospital or centre, a children's unit only, or a generic procedure list), 2 emergency C-section (a merged multi-city record; caesareans without emergency care stated).
- **Judge agreement:** Q1 ("record clearly shows the service") kappa 0.87, reliable. Q2 ("sensible for the place") kappa 0.44, so the "sensible" row is a direction only. A crude keyword read of the judges' reasons suggests most of the 34 picks judged not sensible cite a merged record (Q2 kappa 0.44, a direction only).

**Dev half, for reference.** Under the old km radii and the v0 ranker: specialty filter 25 of 112 (22%), any fact 69 of 83 (83%), ranker 76 of 82 (93%); the ranker skipped a nearer supported facility in 23 of 82 (28%). Rerun under the window rule on the frozen build: 26 of 114 (23%), 81 of 93 (87%), 89 of 93 (96%); skipped a nearer option in 16 of 93. The dev half shaped design decisions, so it is not clean evidence.

- **Rubric check:** only 8 of 107 judge "yes" votes (7.5%) rested on a specialty listing alone, under the 10% threshold fixed in advance, so the rubric stayed as it was (D053).
- **Overnight user testing** (`reports/overnight_test_and_fix.md`): five testers (demo rehearsal, ASHA worker on a phone, skeptical judge, QA edge cases, numbers auditor). Of 9 high issues, 7 fixed and 2 partly fixed; of 27 medium issues, 21 fixed, 3 partly fixed, 3 open or rejected with reasons. Full suite at the sealed commit: 1,120 tests pass.

---

## 13. Databricks

**In one sentence.** The app runs on Databricks Free Edition with the frozen build and answers through its own URL; the pipeline notebook ran on serverless compute on an earlier build, and the final bundle was built locally.

**Technically.**

- **Workspace:** `https://dbc-96b6f0b2-f8fd.cloud.databricks.com`, with the CLI authenticated (profile DEFAULT).
- **Marketplace catalog:** `databricks_virtue_foundation_dataset_dais_2026`. Serverless Starter Warehouse `ee1fa7958ed99cf2`.
- **App:** `vf-care-finder` at https://vf-care-finder-7474645226493940.aws.databricksapps.com (workspace login required, not public).
- **Final deployment:** `01f1e65c...`, deployed Nov 22 at 10:10 UTC (05:10 ET), state RUNNING. It serves the frozen bundle (manifest `520a5159...` matches `reports/FROZEN_BUILD.json`) with ranker v1, recipe v0.3, flags `wp11_v3` and the overnight UI fixes.
- **Verified through the deployed URL at 06:00 ET:** health ok (9,989 facilities, 12 lines, search on); C-section at 226001 returns 3 cards (grades 3, 2, 2); dialysis at Jokihat returns "no reliable option".
- **Free Edition limits (D034):** serverless only, about 3 apps per account, one Vector Search endpoint. **An app stops 24 hours after its last start or deploy.** This deployment should run until about 05:10 ET on Nov 23, past judging, but check it is running before judging and restart it if not.
- **Demo mode is not set in `dbapp/app.yaml`.** Record with `?demo=1` in the URL and check that the "Demo: names and phone digits hidden" chip shows before each take.
- **10 MB file limit:** the keyword-search matrix was 20 MB in one file. It is now written as row blocks under 8 MB each and stacked back on load, with a shape check (D043). The largest bundle file is `places.parquet` at 6.9 MB.
- **Notebook:** `notebooks/01_pipeline.py` reads the Marketplace tables with Spark and runs the same Python steps. It was fixed to apply the saved WP4 student and to survive the trust-check step.

---

## 14. Judging criteria and deadline

From cmudsc.com/datathon-fall2026 (D047):

| Criterion | Organizers' words | Our answer |
| --- | --- | --- |
| Exploration | "Uncover novel datasets and provide insightful analysis" | Outside lists (196 card facts, 1,204 lane rows), 934 routed trips, published standards; 418 of 4,489 records with an obstetrics specialty label name a caesarean |
| Modeling | "Develop advanced models to find meaningful data relationships" | Teacher and student tagger with blinded ship rules (round 2: Opus teacher, fine-tuned encoder on one tag); the drive-time model with out-of-fold error; the sealed evaluation |
| Impact | "Propose actionable and impactful policies" | Four proposals in the README ("What we propose"): referral lists filtered on cited evidence (20% against 89% sealed top-pick precision), blood recorded with every C-section claim (373 of 432 lack it), verification calls chosen by reach ("which record to check first"), a shared facility identifier across registries (matcher alone 55%) |

- **Submission:** code, analysis and a video through the organizers' form, tentatively due **Sunday Nov 22, 12:30 PM**.
- **Judging:** 1 to 4 PM.

---

## 15. What we will not claim

- No probabilities, "likely offers", "calibrated" or "validated".
- No "there is no care here", and no "cannot". Only "no facility in this dataset shows clear evidence".
- Drive times are estimates from straight-line distance at free-flow speed, not road travel times.
- Time windows are planning thresholds, not clinical deadlines.
- A hospital missing from a government list is not evidence that it lacks the service. Lane rows are "not matched to a record in this dataset", never "not in this dataset".
- The ranker beats the specialty filter, but it is not shown to beat the any-fact baseline on the sealed half (p = 0.125). v1 is not shown to beat v0 (p = 0.18).
- The gold set, link checks and scenario judges are agent-made; Hruday's 40 labels are the human check, and they are not done yet. Use "sealed and hashed", never "pre-registered".
- "Web sources" are not independent: facebook.com is a source in 88.5% of records and justdial.com in 54%.

---

## 16. What Hruday needs to do

1. **About 15 minutes:** label the 40 shuffled evaluation items in `data/labels/eval_sealed/human_labels_sealed.csv`, with the tool hidden (instructions: `reports/HUMAN_LABELING.md`). Until then no human agreement figure exists.
2. **About 20 minutes:** spot-check the LaQshya, AERB and NCG lines that will appear on screen (for example Nashik's District Hospital and Jaslok Hospital, Mumbai).
3. **Before recording:** open the app with `?demo=1` and confirm the "Demo" chip; close the simulation panel before "Show all of India"; skip the clickable-funnel beat; film "Add" on Jokihat and "Check" on Lucknow.
4. **Morning:** confirm the app is running (restart if not), record the video, and submit through the form by 12:30 PM.

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
| Student | The small classifier trained on the teacher's labels: logistic regression for 3 tags, a fine-tuned small encoder for `ob_delivery` |
| Gold set | 777 agent-labeled facts used only for scoring, never training |
| F1 | Balance of precision (tags right when given) and recall (right tags found); 1.0 is perfect |
| Macro F1 | F1 averaged over tags, each tag counting equally |
| Wilson interval | A 95% range for a proportion that behaves well with small counts |
| Paired bootstrap | Resampling the same rows for two methods to test whether one is really better |
| Sealed | A protocol hashed and committed before results exist, so it stays fixed whatever the results |
| Frozen build | The bundle and tables hashed in `reports/FROZEN_BUILD.json`; nothing may change them after the freeze |
| Bundle | The small set of files the app loads at startup |
| Any-fact baseline | The sealed evaluation's middle tool: the nearest facility with any fact naming the service |
| Lane | The separate section listing outside-list facilities not matched to a record in this dataset |
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
| ~00:20 | Map, agent swarm and what-if requirements added to the UI workflow; outside-list augmentation planned (D055, D056) |
| ~01:05 | Terms posture for outside lists (D057) |
| ~01:10 | v1-final ranker integrated; first blinded v0 against v1: v1 not shown better (D058) |
| ~01:30 | Tagger round 2 result: Opus teacher, `ob_delivery` ships with the fine-tuned encoder (D059); outside lists: 196 card facts, 1,204 lane rows (D060) |
| ~02:20 | Root fixes: merged-evidence block, places-v3, v1 why-not; second blinded v0 against v1: v1 leans better, p = 0.18 (D061) |
| ~02:35 | UI overhaul shipped: referral-note answer, labeled map, agent simulator (D062) |
| ~02:40 | Overnight run plan (D063) |
| ~02:55 | Final assembly: `ob_delivery` promoted, regrade, `wp11_v3`, complete bundle with outside lists and masked phones (D064) |
| ~02:56 to 03:30 | Five testers use the real UI; fixes made and checked in the browser |
| ~05:10 | Redeployed to Databricks (deployment `01f1e65c`) |
| ~05:15 | Build frozen; reach amendment sealed as Amendment 3 (D065) |
| ~05:20 to 05:25 | Sealed half run once and scored (D066) |
| ~05:55 | `reports/findings.json` rebuilt: 1,763 numbers, 7 explicit nulls (D067) |
| ~06:00 | This explainer and the status board updated to the final state |
| 12:30 PM | Submission (tentative) |
