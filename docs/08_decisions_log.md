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
