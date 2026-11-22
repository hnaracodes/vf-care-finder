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
