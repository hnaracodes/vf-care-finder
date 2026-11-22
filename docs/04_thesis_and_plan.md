# Thesis and plan

Re-scoped Nov 21, 20:15 ET to the official challenge (D023). The earlier "care desert or data desert" plan is in git history (`7609dbb`) and in decisions D001 to D022.

## The question

A health worker types a service and a place. Which facilities should the patient go to, why should anyone believe that, and how sure are we?

Examples from the brief: dialysis near Jaipur; an emergency C-section near Lucknow; dialysis from a village in rural Bihar; a high-risk delivery in Maharashtra; trauma care near a highway accident spot.

## Why this is hard on this dataset

The obvious tool filters the table for a matching specialty and sorts by distance. On this data that gives confident wrong answers:

- A specialty is a claim, not a service. 4,489 facilities list `gynecologyAndObstetrics`; far fewer have any fact that names a caesarean.
- Some records merge two facilities, so a capability may belong to a different building.
- Some locations contradict the record's own PIN. WP1 already found 282 facilities more than 100 km from their PIN's area with no way to settle which is right, and moved 785 to their PIN.
- Some services rest on a single scraped sentence from a single website.
- No evidence is not evidence of absence. A public hospital that is missing from the table, or has no web page, cannot be recommended. The tool must say that it has no reliable answer, not that care does not exist.

So the tool is built around the evidence, not the search box.

## The tool in one paragraph

Input: a service (pick from 12, or type anything) and a location (PIN, city, district, or coordinates). The resolver turns the location into a point with a stated precision. For the service, every facility already has a grade from 0 to 3, built by rules over tagged facts, with the facts that justify it. Trust checks flag merged records, location conflicts, implausible claims and thin evidence. The ranker gates on grade and flags, then scores what is left with three visible parts: evidence, distance, source trust. It returns three cards, each quoting the facility's own text for every claim, with a plain-language confidence and what would make it wrong. If nothing reliable is within the service's radius, it says "no reliable option nearby" and shows what it does know. A small map shows the point, the results, and a trip view of nearby villages travelling to them.

## The user

A non-technical user: a community health worker, an NGO coordinator, a district planner. They need one screen, plain words, no grades or acronyms without explanation, and a clear signal when the answer is weak. The brief requires "a clear non-technical user workflow."

## How it maps onto the brief's rules

| Brief requirement | How the tool meets it | Work package |
| --- | --- | --- |
| Run on Databricks Free Edition | Databricks App; pipeline runs as a notebook job on serverless | WP8, WP12 |
| Use the provided facility dataset | Marketplace tables: facilities and the PIN directory | WP1, WP2, WP12 |
| Clear non-technical workflow | One screen: service, place, three cards; plain-language labels | WP8 |
| Cite facility text for every claim, score or ranking | Every card line quotes a fact with its field; the score parts link to the facts behind them | WP3, WP5, WP8 |
| Communicate uncertainty | Confidence label per card, named flags, location precision, abstention | WP5, WP11, WP2 |
| WHY this facility | Quoted evidence, distance, flags, and the reason it beat the next one | WP5, WP8 |
| HOW did you decide | "How we decided" panel: recipe for the service, gates, score formula and weights, known limits | WP5, WP8 |

## The pipeline

Offline and precomputed, rerunnable from the command line and as a Databricks job.

1. Clean (WP1, done). `silver/facilities.parquet`, `silver/facts.parquet`, with the location check against each facility's PIN (`coord_check`).
2. Gazetteer (WP2). `silver/places.parquet`: PINs, districts and post office names with a point and a precision radius, from the PIN directory.
3. Tag and grade (WP3, then WP4 if a teacher key arrives). Every fact gets evidence tags; rules turn tags into a grade per service line, with the supporting facts attached.
4. Trust checks (WP11). Per facility: possible merged record, location conflict, implausible claim for the facility type; per facility and service: evidence strength (strong, moderate, thin).
5. Bundle (WP12). One small set of files the app loads at start.

At query time, in the app (WP5 engine, WP8 app):

6. Resolve the location (WP2 resolver).
7. Rank: gates, score, abstention (WP5).
8. Render: three cards, the "how we decided" panel, the small map and the trip view (WP8).

## Service lines

Twelve lines, one recipe each (`06_capability_recipes.md`, v0.2). The ids `emoc`, `dialysis`, `icu` are kept from v0.1.

| Id | Shown to the user as | Default radius (km) | Facilities with a mention in facts |
| --- | --- | --- | --- |
| `emoc` | Emergency C-section and high-risk delivery | 50 | 439 (C-section) |
| `nicu` | Newborn intensive care (NICU) | 75 | 707 |
| `dialysis` | Dialysis | 50 | 1,046 (v0.1) |
| `icu` | Intensive care with ventilator | 75 | 2,048 (v0.1) |
| `cardiac_surgery` | Heart surgery (bypass, valve) | 250 | 612 |
| `cath_lab` | Heart attack care (angiography, angioplasty) | 100 | 933 |
| `mri` | MRI scan | 100 | 944 |
| `ct` | CT scan | 75 | 1,521 |
| `trauma` | Trauma and accident care | 50 | 1,477 |
| `burns` | Burn care | 200 | 231 |
| `neurosurgery` | Head injury and brain surgery | 150 | 1,013 |
| `oncology` | Cancer treatment (chemotherapy, radiotherapy) | 250 | 404 |

Mention counts are rough regex counts over `silver/facts.parquet` (Nov 21), to show each line has enough to grade. They are not grades. Radii are straight-line product defaults (D028), not clinical guidance.

Anything else ("cataract surgery", "snake bite antivenom") goes to the search fallback: keyword retrieval over fact text and specialties, results labeled "keyword match, no service recipe" and never above "claimed" confidence (D026).

## The ranking

Two steps, both shown to the user (D027).

Gates, in order:

1. Location usable: drop facilities whose location is `excluded_unresolved`.
2. Within reach: candidates within 3 times the service radius, at most 50.
3. Recommendable: grade 2 (supported) or 3 (corroborated), and no blocking flag (a major location conflict, or an implausible claim for the facility type). Everything else can still appear, but only in the "also nearby, not verified" list.

Score for recommendable candidates, each part from 0 to 1:

```
score = 0.5 * evidence + 0.3 * distance + 0.2 * trust - penalties
evidence = grade part (supported 0.7, corroborated 1.0) adjusted by evidence strength (thin -0.2, strong +0.1, capped at 1)
distance = exp(-km / radius)
trust    = source_confidence (distinct web domains, official website, page recency; vf.recipes)
penalties = named, each shown: possible merged record 0.15, minor location conflict 0.10
```

The card shows each part as a bar with its number and the facts or fields behind it. Ties within 0.02 are broken by distance and said to be ties.

Abstention: if no recommendable facility is within the radius, the answer is "No reliable option nearby" (D028), followed by the nearest recommendable facility beyond the radius (with its distance) and any claim-only facilities inside it, labeled "claim not supported by the record". The wording is "no facility in this dataset shows reliable evidence of X within N km", never "there is no X".

Confidence label per card, in plain words:

- High: corroborated, strong or moderate evidence, no flags, location precise to the facility or the PIN.
- Medium: supported, or corroborated with one minor flag.
- Low: anything recommendable that is thin, flagged, or located only to a district.

## The trust checks (WP11)

| Check | Signal | Effect |
| --- | --- | --- |
| Location conflict | `coord_check` from WP1; state and city text against the PIN's state and district | `excluded_unresolved`: dropped. `kept_unresolved`: major, blocking. `moved_to_pin`: minor; precision becomes the PIN's spread |
| Possible merged record | Name joins two facility names; facts or description name another facility; the record's web sources belong to different organizations; specialty breadth far above its facility type; one website shared by facilities in different cities | Penalty and a card warning quoting the text that triggered it |
| Implausible claim | Service needing a hospital (cardiac surgery, neurosurgery, ICU, burns, cath lab) claimed by a clinic, dentist, doctor or pharmacy record | Blocking for that service |
| Thin evidence | One supporting fact, one web domain, no official website, or support only in free text | Evidence strength `thin`; lowers the evidence part |

Each check is precision-tested by hand on a sample (WP11) and its numbers are reported, including when it is weak.

## The ML

The ML is Model A, the fact tagger (WP3 teacher, WP4 student). It turns 460,000 noisy facts into evidence tags, and it is what grades rest on. Grades stay rules over tags (D010) so every grade can be explained by quoting facts.

- Baseline: regex recipe v0.1, macro F1 0.90 on a 429-fact gold set (13 tags, agent-labeled, hard cases over-sampled). v0.2 adds 11 tags for the new lines; the gold set is extended to cover them.
- Teacher and student: blocked until an API key is provided (D036). If they run, they ship only if they beat regex on gold F1, per tag. If not, regex ships and the report says so.

Model B (district outcomes) is dropped. The ranking is deliberately not learned: there are no labels for "best facility", and a learned ranker could not explain itself.

## Evaluation

There is no ground truth for "the right facility", so each part is checked on its own and the whole is checked on scenarios.

1. Tagger: per-tag precision, recall, F1 on the gold set (existing, extended in WP3).
2. Resolver: about 40 hand-written queries (PINs, cities, districts, villages, ambiguous names, coordinates, misspellings) with the expected district; report the hit rate and every miss.
3. Trust checks: precision on a hand-reviewed sample of flagged records; how many results each check changes.
4. Scenario suite: the brief's five queries plus at least ten more across services and regions, including remote places where abstention is expected. For each, the top 3 are read by hand against the quoted facts and marked right, defensible, or wrong. Report the counts.
5. External cross-check, optional: LaQshya-certified maternity units (WP2b) near `emoc` scenario points. How many of them does the tool recommend, and how many are missing from the dataset?

## What "done" looks like

- A Databricks App on Free Edition: service and place in, three cited cards or an abstention out, a "how we decided" panel, a small map with the trip view.
- Twelve recipes plus the fallback, with tagger scores per tag.
- Trust flags with measured precision.
- A scenario report with every query, its answer, and the hand check.
- A short video built on the app, if Hruday still submits one (`00_brief.md`).

## Limits to state openly

- Straight-line distance is not travel time.
- The dataset is a sample of mostly private, web-visible facilities. Public hospitals are under-covered, so "no reliable option nearby" often means "not in this data".
- Grades describe what web pages say, not what a facility can do today. Pages can be stale.
- Citations name the fact and its field, not the page it came from: the data has no per-fact provenance (D031).
- The gold set was labeled by an agent, not a clinician.
- Weights and radii are stated defaults, not fitted or clinically validated.

## Order of work

See `tasks/README.md`. In short: WP2 resolver, WP3 v0.2 recipes and WP11 checks run in parallel; WP5 ranker after WP3 and WP11; WP8 app starts at once on the existing grades; WP12 port and WP9 last. `emoc` and `dialysis` must work end to end before anything else is polished.
