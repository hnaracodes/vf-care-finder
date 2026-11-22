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
| 0 | Bootstrap | done | orchestrator | main | py3.12 venv at `.venv` |
| 1 | Silver facilities | done | | rescope | 9,989 facilities, about 460k facts, `coord_check` against PIN |
| 2 | Geography and location resolver | doing | | | Part A done (post offices, PIN crosswalk). Part B resolver todo. NFHS dropped |
| 2b | External data | done, optional | | rescope | OSM, LaQshya. Only for the WP10 B cross-check |
| 3 | Recipes, gold set, teacher labels | doing | | | v0.1 done: 13 tags, regex macro F1 0.90 on 429 gold facts. v0.2 (12 lines) todo. Part 3 blocked: no API key (D036) |
| 4 | Fact tagger (Model A) | blocked | | | Needs teacher labels |
| 5 | Ranking engine | todo | | | Old access tables done and retired; `haversine_km` carries over |
| 6 | Outcome model and quadrants | dropped | | | D023. Rule quadrants were done; kept as history |
| 7 | Simulator | dropped | | | D023 |
| 8 | Databricks App | todo | | | Old India map app retired; map and swarm code reused for the trip view (D032) |
| 9 | Demo, video, submission | todo | | | |
| 10 | Stretch | todo | | | |
| 11 | Trust checks | todo | | | New |
| 12 | Databricks port and bundle | todo | | | Needs Hruday: workspace, Marketplace catalog name, `databricks auth login` |

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
