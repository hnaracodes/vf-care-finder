# Work packages

Re-scoped Nov 21, 20:15 ET to the official challenge (D023): a tool on Databricks Free Edition that takes a service and a location and returns the best facilities, citing facility text and stating uncertainty. Plan: `docs/04_thesis_and_plan.md`.

One file per work package (WP). Each has a goal, inputs, outputs, steps, and acceptance checks. Outputs are defined in `docs/07_data_contracts.md`.

## Dependency graph

```
WP0 bootstrap (done)
 ├─ WP1 silver facilities (done) ─┬─ WP3 recipes v0.2 + gold set ──┬─ WP4 student tagger (only if a teacher key arrives)
 │                                │                                 │
 │                                ├─ WP11 trust checks ─────────────┤
 │                                │                                 │
 ├─ WP2 geography (A done) ───────┴─ WP2 B location resolver ───────┴─ WP5 ranking engine ─┐
 │                                                                                          │
 └─ WP8 Databricks App (starts at once on v0.1 grades and stubs) ───────────────────────────┤
                                                                                            │
    WP12 Databricks port and bundle (skeleton at once; final run after WP5) ────────────────┴─ WP9 demo, video, submission

WP10 stretch: only after WP8 answers the five brief scenarios on Databricks
WP6, WP7: dropped (D023)
WP13 outside data + calibration (parallel track, D040) ──────► WP5 probability mode, WP8 simulation (D038, D039)
```

What can run in parallel now: WP2 Part B, WP3 v0.2, WP11, WP8, WP12 skeleton (five agents, disjoint files). WP5 starts as soon as one of WP2 B or WP3 lands, against stubs for the other.

## Schedule (Eastern Time)

Deadline Sunday Nov 22; exact time unconfirmed (default noon ET, `00_brief.md`).

| When | Target |
| --- | --- |
| Sat by 23:00 | WP2 B resolver passing its cases. WP3 v0.2 tags and grades written. WP11 first flags. WP8 server and cards running locally on v0.1 grades |
| Sun by 02:00 | WP5 ranker on real grades and flags; scenario suite run and hand-checked. WP11 precision review done |
| Sun by 05:00 | WP8 app complete locally (cards, abstention, how-we-decided, small map). WP12 notebook and bundle; deployed if Hruday has logged in |
| Sun by 07:00 | Trip view if time allows. WP9 script, findings, methods |
| Sun morning | Hruday checks, restarts the app, records, submits |

`emoc` and `dialysis` must work end to end, deployed, before polish, the trip view, or any stretch work.

## Status board

Update your row when you start, when you finish, and when you are blocked. One line of notes at most.

| WP | Title | Status | Owner | Branch | Notes |
| --- | --- | --- | --- | --- | --- |
| 0 | Bootstrap | done | orchestrator | main | py3.12 venv at `.venv`; ML venv `.venv-ml` for the encoder only |
| 1 | Silver facilities | done | | rescope | 9,989 facilities (9,671 with a usable location), 451,105 facts, `coord_check` against PIN (`wp1-silver-v3`) |
| 2 | Geography and location resolver | done | | rescope | Part A: 165,627 post offices, PIN crosswalk. Part B: 182,755 places, 54/55 frozen cases, places-v3 PIN precision median 4.9 km (D061). NFHS dropped |
| 2b | External data | done, optional | | rescope | OSM, LaQshya; LaQshya now feeds 63 hand-checked card links and 779 lane rows (D051, D060) |
| 3 | Recipes, gold set, teacher labels | done | | rescope | 24 tags, 12 lines; recipe v0.3 shipped (D052); 777 gold facts; Haiku teacher 0.888 macro F1, a tie with regex 0.890 |
| 4 | Fact tagger (Model A) | done | | rescope | Student 1b ships h24, icu, ventilator; WP4b 2b_r2 ships ob_delivery (marginal gain, D059, D064); final mix macro F1 0.916 (0.898 to 0.931); 0 lines cross the grade 2 gate |
| 5 | Ranking engine | done | | rescope | Ranker v1-final (D048, D058): drive-time windows, gates, 15/30 min evidence allowance; abstains at 12.9% of random origins; v1 vs v0 blinded p = 0.18, not proven better (D061) |
| 6 | Outcome model and quadrants | dropped | | | D023. Rule quadrants were done; kept as history |
| 7 | Simulator | dropped | | | D023. Replaced by the in-app agent simulator with what-if tools, built under WP8 (D056, D062) |
| 8 | Databricks App | done | | rescope | Referral-note UI, labeled map with India zoom, agent simulator (D062); overnight test: 7 of 9 high issues fixed, 2 partly; deployed (WP12) |
| 9 | Demo, video, submission | doing | overnight-final | rescope | Sealed eval run once (D066); findings.json rebuilt after the critics' fix round (D068); methods, README (with "What we propose") and demo script done and checked; 40 human labels, the Nashik LaQshya spot-check, recording, creating the repo remote and pushing, and submission are Hruday's |
| 10 | Stretch | dropped | | | Not run before the freeze (D065); item B (LaQshya cross-check) delivered through the outside lists (D060); A, C to F not built |
| 11 | Trust checks | done | | rescope | `wp11_v3`: 2,471 records flagged, 1,010 of 10,957 grade 2+ lines blocked, 75 duplicate pairs; merged-evidence block 26/30 on hand read (D061) |
| 12 | Databricks port and bundle | done | final assembly | rescope | Bundle `--check` ok (D064); frozen at 05:15 ET (D065, manifest 520a5159); deployed as 01f1e65c and verified at 06:00 ET; notebook `notebooks/01_pipeline.py` |
| 13 | Outside data and calibration model | done | orchestrator (workflow) | rescope | Outside lists: 196 card facts, 1,204 lane rows, others withheld by terms (D057, D060). Calibration model dropped (D048) |

Status values: `todo`, `doing`, `blocked`, `done`, `dropped`.

## Rules for every work package

- Read `CLAUDE.md` and your WP file fully before writing code.
- Write outputs exactly as the contract says. If the contract is wrong, fix the contract in the same commit and log it.
- Put pipeline code in `scripts/wp<N>_<name>.py` so each step can be rerun from the command line. Shared helpers go in `src/vf/`.
- Every script prints a short summary of what it wrote (row counts and the two or three numbers a reviewer would check).
- Finish with the acceptance checks in your WP file, and paste the results into your final message.
- Running unattended: do not stop to ask. Take the default, log it in `docs/08_decisions_log.md`, move on.
- Report what is true. If a check is weak or a scenario answer is wrong, write that down; do not tune until it looks good.
- Nothing new may read a retired output (D024): NFHS tables, post office access tables, district quadrants, `app/public/data`.
