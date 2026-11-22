# Decisions log

Append one entry whenever you make a choice another agent or Hruday would want to know about: a default taken for an open question, a threshold, a contract change, a dropped feature. Newest at the bottom. Keep entries to a few lines.

Format: `D<number> | date time ET | who | decision | why`

---

D001 | Nov 21 13:10 | Claude (chat) with Hruday | Project is "care desert or data desert", built on evidence-graded capabilities. | A facility-count map would mostly measure web presence: 202 of 755 districts show zero facilities.

D002 | Nov 21 13:10 | Claude (chat) | Access is measured as straight-line distance from post offices to the nearest graded facility, not as facilities per district. | Gives every district a score and sidesteps the zero-count problem.

D003 | Nov 21 13:35 | Hruday | The project must include real ML. Two models: a fact tagger distilled from an LLM, and a district outcome model. | Hruday's call. Each model has to change the answer.

D004 | Nov 21 13:43 | Hruday | The presentation centerpiece is an interactive, simulation-style map. | Hruday's experience with what lands at hackathons.

D005 | Nov 21 13:48 | Hruday | Deliverable is a submitted video, due Nov 22. Agents may run overnight. | Stated by Hruday. Exact time and length limit still open; see `00_brief.md`.

D006 | Nov 21 13:50 | Claude (chat) | Emergency obstetric care is the flagship service line; dialysis and ICU are secondary. | Only obstetrics has NFHS utilization outcomes, so only there can the care-versus-data split be tested.

D007 | Nov 21 13:50 | Claude (chat) | Corroboration uses distinct domains in `source_urls`, not `source_types`. | Checked locally: `source_types` matches the number of facts for only 226 of 9,970 facilities.

D008 | Nov 21 13:50 | Claude (chat) | Raw data is kept as the CSV export in `data/raw`, not versioned, with checksums in `MANIFEST.json`. | The data only existed as a Databricks share. Files total 97 MB.

D009 | Nov 21 13:50 | Claude (chat) | The map is drawn from post office points, with no boundary polygons or tile server required. | Removes two failure points for a recorded demo, and avoids a second district crosswalk for polygons.

D010 | Nov 21 13:50 | Claude (chat) | Facility grades come from rules over fact tags; the model tags facts and does not grade facilities directly. | Keeps every grade explainable by quoted facts.

D011 | Nov 21 15:10 | Hruday | Add a patient swarm to the map, approach A: precomputed nearest-facility targets animated in the browser, same coverage rule as WP7. Capacity-based agent simulation (B) is deferred. | Goal is to show that people in care deserts are actually stranded. Capacity would rest on sparse, unverified bed counts. Spec: `docs/superpowers/specs/2026-11-21-patient-swarm-design.md`.

D012 | Nov 21 15:10 | Claude (orchestrator) | Python environment is a uv venv on Python 3.12 at `.venv`. Run everything with `.venv/bin/python`. | The default python3 is 3.14; ML wheels (torch, lightgbm) are safer on 3.12.

D013 | Nov 21 15:10 | Claude (orchestrator) | Overnight run is orchestrated by one Claude session: agents work in the main checkout on disjoint files, and the orchestrator alone edits the status board and this log and makes one commit per work package. | Avoids concurrent edits to shared files and git index locks.

D014 | Nov 21 15:10 | Claude (orchestrator) | No ANTHROPIC_API_KEY in `.env`, so WP3 part 3 (teacher labels) is blocked. This run does WP3 part 1 (regex baseline) only. | Hruday has not provided a key yet.

D015 | Nov 21 16:25 | Hruday (organizers, opening ceremony) | Outside data and scraping to augment the dataset are encouraged. Resolves the open question in `00_brief.md`. Every external source goes in `data/external/SOURCES.md` with URL, license and retrieval date, and is flagged on the methods card. | Organizers' guidance, relayed by Hruday.

D016 | Nov 21 16:25 | Claude (orchestrator) | External public-facility data is used two ways, kept separate: (1) as an out-of-sample check on the rule quadrants, never as an input to them; (2) as an "augmented access" layer reported side by side with the original. Prefer official downloads and open APIs over scraping portals; no personal details are submitted to any site. | Using the same data to define and to validate data deserts would be circular. Portals such as PMJAY and data.gov.in need logins or have terms we have not checked.

D017 | Nov 21 17:00 | WP2b agent | OSM health facilities come from the HOT OSM export on HDX (11 Oct 2026 snapshot, ODbL) because Overpass was overloaded (5 failed attempts). It lacks the operator and emergency tags. A tiled Overpass fallback is in `scripts/wp2b_external.py --osm-source overpass`. | Overpass unavailable.

D018 | Nov 21 17:00 | WP2b agent | Augmented access per post office = min(original grade 2+ emoc distance, distance to the nearest LaQshya facility located on an OSM point). Main layer uses every LaQshya facility; maternity-OT-only and valid-certificate-only are sensitivity checks. 537 of 1,261 LaQshya facilities are located only to district and are excluded from distances. | Keeps the original layer intact for side-by-side comparison (D016).

D019 | Nov 21 17:00 | WP2b agent | Certificate expiry is measured against the retrieval date (21 Nov 2026); expiry against the list date (8 Apr 2026) is also stored. Only 324 facilities hold a certificate valid today. | An expired certificate still shows the unit existed.

D020 | Nov 21 17:00 | WP2b agent | Public-name rule for OSM extended with Taluk, Area, Sadar, Rural and Referral Hospital; a private marker (Pvt, Ltd, Nursing Home) overrides it. | Common Indian public hospital names.

D021 | Nov 21 17:00 | WP2b agent | Census 2011 population split to post-2011 districts is unreliable (wrong parents found in spot checks) and is not used anywhere yet. Every split value is flagged `is_estimate`. | Parent chosen by nearest-post-office vote.

D022 | Nov 21 17:05 | Claude (orchestrator, verified independently) | Finding: adding located LaQshya facilities brings 126 of 137 rule data deserts and 84 of 98 rule care deserts below the 90.2 km "far" cut (medians 130 to 33 km and 123 to 40 km). Valid certificates only: 69 and 53. "Far in the scraped table" is mostly a blind spot for both groups. Story reframing proposed to Hruday, not yet decided. | Recomputed from `post_office_access` and `ext_laqshya` without the agent's script; counts match.

---

## Re-scope to the official challenge (Nov 21 evening)

D023 | Nov 21 20:15 | Hruday | **Re-scope.** The project was planned against the wrong brief. The official challenge ("Your challenge this weekend: find the right care", summarized in `00_brief.md`) asks for a tool on Databricks Free Edition that takes a service and a location and returns the best facilities, quotes facility text for every claim, and states uncertainty. Keep: WP1, the PIN lookup from WP2, WP3 and WP4 (evidence tags and grades with quoted facts), the distance code from WP5. Drop: WP6, WP7, the NFHS work, the India-wide desert map. Change: widen to about 12 services plus a search fallback; add checks for merged records, location conflicts and thin evidence; build a location resolver; rank with a transparent score; ship as a Databricks App with three result cards and a small map; return "no reliable option nearby" when evidence is weak. Supersedes D001, D002, D004, D006 and the Model B half of D003. | The official brief, read Nov 21. D001 to D022 stay in this log as history.

D024 | Nov 21 20:15 | Claude (orchestrator) | Nothing from the pre-pivot run is deleted. It is committed as a snapshot (`7609dbb`, branch `rescope`). WP6 and WP7 are marked dropped; their scripts, outputs and tests stay but are not maintained. `silver/nfhs*.parquet`, `gold/post_office_access.parquet`, `gold/district_access.parquet`, `gold/district_quadrants.parquet` and `app/public/data/*` are retired outputs: nothing new may read them. | Keeps the history recoverable and the pivot diff readable.

D025 | Nov 21 20:15 | Claude (orchestrator) | Twelve service lines, each with one recipe: `emoc` (emergency C-section and high-risk delivery), `nicu`, `dialysis`, `icu` (with ventilation), `cardiac_surgery`, `cath_lab` (heart attack care: angiography and angioplasty), `mri`, `ct`, `trauma`, `burns`, `neurosurgery`, `oncology`. Existing ids `emoc`, `dialysis`, `icu` are kept so WP3 code and tests carry over. Anything else goes to the search fallback, `search`. | Covers every service the brief names (dialysis, emergency C-section, NICU, cardiac surgery, MRI, burn care) plus the brief's trauma scenario and common emergencies. Each has 200 or more facilities mentioning it in facts (checked on `silver/facts.parquet`: burns is smallest at 231).

D026 | Nov 21 20:15 | Claude (orchestrator) | The search fallback is keyword retrieval (TF-IDF over fact text and specialty names) and its results are capped at "claimed" confidence, labeled "keyword match, no service recipe". Databricks Vector Search is a stretch item, not the default. | Free Edition allows one Vector Search endpoint; a keyword index needs no endpoint and its matches can be quoted exactly.

D027 | Nov 21 20:15 | Claude (orchestrator) | Ranking is a transparent additive score shown in parts: evidence (from the grade and evidence strength), distance (decay over a per-service radius), source trust (the existing `source_confidence`), minus named penalties for trust flags. Default weights 0.5, 0.3, 0.2. Hard gates come before the score: only grade 2 or higher with no blocking flag can be "recommended". Weights are set by reading results on the scenario suite, never fit to labels, and stated in the app. | There is no ground truth for "best facility"; a score whose parts a user can read is the honest choice and meets the brief's "show reasoning" rule.

D028 | Nov 21 20:15 | Claude (orchestrator) | Abstention: the tool says "no reliable option nearby" when no facility within the service radius is recommendable (grade 2 or higher, no blocking flag). It then shows the nearest recommendable facility beyond the radius, if any, and any claim-only facilities inside it, labeled "claim not supported by the record". Default radii (straight-line km): emoc 50, trauma 50, dialysis 50, ct 75, icu 75, nicu 75, mri 100, cath_lab 100, neurosurgery 150, burns 200, cardiac_surgery 250, oncology 250. | Brief: "communicate uncertainty instead of presenting weak evidence as fact." Radii are product defaults for a straight-line search, not clinical guidance, and the app says so.

D029 | Nov 21 20:15 | Claude (orchestrator) | Location resolver order: coordinates, then 6-digit PIN, then district name, then city or post office name (exact, then fuzzy within a state). An ambiguous name (for example Aurangabad, in Bihar and in Maharashtra) returns candidates for the user to pick, never a silent guess. Every resolution reports how it was resolved and a precision radius in km. Built from the PIN directory only. | Organizers' note: "do not present inferred geography as exact unless verified."

D030 | Nov 21 20:15 | Claude (orchestrator) | Explanations on result cards are assembled from templates and quoted facts. No LLM writes free text into a recommendation. An LLM may be used offline as the WP3 teacher and, as a stretch, to parse a free-text query into service and location. | The brief requires citing facility text for every important claim; generated paraphrase can state things the record does not.

D031 | Nov 21 20:15 | Claude (orchestrator) | Citations quote the fact text and name its field (capability, procedure, equipment, description). They cannot name the web page a fact came from: the data has no per-fact provenance (`source_types` and `source_urls` are per facility, trap 6). Cards say "from this facility's record, compiled from N web sources" and list the domains. | Honest about what the data supports.

D032 | Nov 21 20:15 | Hruday | Keep the patient simulation as a local trip view: for the searched location, nearby post offices travel to the top results, and a "trust claims only" toggle shows the detour when the nearest claimed facility has no supporting evidence. Built last in WP8, cut if late. The India-wide swarm and simulator are dropped. | Chosen by Hruday from three options. It shows the brief's warning ("a confident wrong answer can cost a patient hours") directly.

D033 | Nov 21 20:15 | Claude (orchestrator) | Architecture: the pipeline precomputes grades, flags and a gazetteer; the Databricks App loads that bundle and resolves, ranks and abstains in process (Python, FastAPI), serving a prebuilt static front end reused from `app/` (deck.gl). No SQL warehouse is queried per request. The same pipeline code runs as a Databricks notebook job on Free Edition reading the Marketplace tables (WP12). | 10,000 facilities fit in memory; skipping a warehouse avoids serverless cold starts. Reusing the deck.gl code keeps the trip view (D032) cheap.

D034 | Nov 21 20:15 | Claude (orchestrator) | Free Edition constraints to design around (Databricks docs, Nov 21; recheck before deploying): serverless compute only, about 3 apps per account, an app stops 24 hours after it was last started or redeployed, one Vector Search endpoint. The app must be restarted shortly before judging. | A stopped app at judging time would fail the brief's first requirement.

D035 | Nov 21 20:15 | Claude (orchestrator) | Work package changes: WP2 drops NFHS and adds the location resolver; WP3 widens the recipes to 12 lines; WP5 becomes the ranking engine; WP6 and WP7 are dropped; WP8 becomes the Databricks App; WP9 is rewritten around the tool; new WP11 (trust checks) and WP12 (Databricks port). WP2b external data is optional, kept only as a cross-check (LaQshya certifies C-section units). | Follows D023.

D036 | Nov 21 20:15 | Claude (orchestrator) | Teacher labels (WP3 part 3) are still blocked: the `ANTHROPIC_API_KEY` line in `.env` is empty (checked 20:15). WP3 widens the regex recipes and the gold set first; WP4 runs only if a key arrives. | D014 unchanged in substance.

D037 | Nov 21 20:30 | Hruday, verified by Claude (orchestrator) | Supersedes D036: `ANTHROPIC_API_KEY` is now set and valid (models list returned HTTP 200; `claude-haiku-5-5` available, as set in `VF_LLM_MODEL`). WP3 Part 3 (teacher labels) and WP4 are unblocked, within `VF_LLM_BUDGET_USD` = 30. Databricks CLI v1.27.0 installed from the official Homebrew tap (only the `databricks` formula trusted). WP12 still waits on Hruday's workspace, the Marketplace catalog name, and `databricks auth login`. | Key added by Hruday.
