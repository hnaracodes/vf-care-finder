# WP13: Outside data and the calibration model

New Nov 21 (D040). **Re-scoped by D048: no calibration model tonight.** The track is time-boxed and descriptive: link LaQshya first, then at most 2 more sources (1 agent, 1.5 h each, go/no-go at 00:30), show outside lists only as dated card facts from links with agent-checked precision lower bound >= 0.90, and report descriptive agreement tables with Wilson intervals. Phase T below is retained as future work only. Size: about 8 hours wall-clock with up to 15 agents, run as workflows by the orchestrator. Runs in parallel with the core build. Feeds WP5 (probability mode, D039) and WP8 (simulation, D038).

## Goal

Independent evidence of which facilities offer which services, used to (1) train a facility-level model `P(service offered | record)` that replaces the hand-set evidence and trust weights, (2) measure how often each grade and confidence level is right, and (3) add credible facilities missing from the table, shown separately and cited to their list.

## Phases

| Phase | Work | Output |
| --- | --- | --- |
| R. Research | Find, verify and test-match sources (workflow `outside-data-research`, running since Nov 21 21:00) | research report, source shortlist, coverage matrix per service line |
| A. Acquire | One agent per chosen source: download within terms, parse to one schema | `silver/ext_<source>.parquet`, entries in `data/external/SOURCES.md` |
| M. Link | Match to our facilities: block on PIN or district, score name, address and distance, agent adjudication for borderline pairs, hand-check 100 pairs per source | `silver/ext_links.parquet` with match score and method; match precision per source |
| V. Labels | Turn links into labels per facility and line: positive when an independent source lists the service; weak negative only where a source lists a facility's full specialty set without it; agreement across sources | `labels/service_labels.parquet` |
| T. Train | Logistic regression and gradient boosting on record features (grade, evidence strength, tags, facts, domains, website, type, beds, flags), grouped CV by state, reliability curve, threshold for "recommendable" from held-out precision | `gold/service_probability.parquet`, `reports/calibration.md` |
| I. Integrate | Ranker probability mode, app wording, simulation metric | via WP5 and WP8 |

## Rules

- Independence (D040): a facility's own website, or any of its `source_domains`, never makes a label.
- Absence from a list is not a negative unless the list is complete for that facility. Say which labels are weak.
- Outside facilities added to results are labeled with their list and date, never mixed into the dataset's own evidence.
- No logins, registrations or form submissions; respect terms and robots.txt; record license and retrieval time for every file.
- Report the calibration honestly, including lines where the model does not beat the grade alone.
