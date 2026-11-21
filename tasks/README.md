# Work packages

One file per work package (WP). Each has a goal, inputs, outputs, steps, and acceptance checks. Outputs are defined in `docs/07_data_contracts.md`.

## Dependency graph

```
WP0 bootstrap
 ├─ WP1 silver facilities ──┬─ WP3 teacher labels + regex baseline ─┬─ WP4 fact tagger (Model A)
 │                          │                                       │
 ├─ WP2 geography + NFHS ───┴─ WP5 access scores ───────────────────┴─ WP6 outcome model + quadrants (Model B)
 │                                                                       │
 │                                                                     WP7 simulator
 │                                                                       │
 └─ WP8 demo app (starts at once, on mock data) ─────────────────────── WP9 story, video, submission

WP10 stretch: only after WP9 has a complete first cut
```

What can run in parallel:

- At the start: WP1, WP2, and WP8 (three agents).
- After WP1: WP3. WP5 can start as soon as WP3 has written the regex baseline grades.
- WP4 and WP6 are the two modeling tracks and can overlap.

## Suggested overnight schedule (Eastern Time)

Assumes the video is recorded Sunday morning. Adjust when Hruday confirms the deadline.

| When | Target |
| --- | --- |
| Sat by 16:00 | WP0, WP1, WP2 done. WP8 shows real post office dots with mock distances |
| Sat by 19:00 | WP3 baseline grades and gold set done. WP5 first pass done. App reads real baseline data |
| Sat by 23:00 | WP3 teacher labels done. WP4 first student trained. WP6 first model and quadrants |
| Sun by 03:00 | WP4 final grades, WP5 rerun, WP6 final, WP7 scenarios. App on final data |
| Sun by 07:00 | WP9: story beats filled with real numbers, autoplay take recorded, methods drawer complete |
| Sun morning | Hruday records voiceover and submits |

The end-to-end path on obstetrics alone must be complete before any polish or stretch work. A finished single service line beats three unfinished ones.

## Status board

Update your row when you start, when you finish, and when you are blocked. One line of notes at most.

| WP | Title | Status | Owner | Branch | Notes |
| --- | --- | --- | --- | --- | --- |
| 0 | Bootstrap | done | orchestrator | main | py3.12 venv, 10 tests pass, data verified |
| 1 | Silver facilities | todo | | | |
| 2 | Geography and NFHS | todo | | | |
| 3 | Teacher labels and regex baseline | todo | | | |
| 4 | Fact tagger (Model A) | todo | | | |
| 5 | Access scores | todo | | | |
| 6 | Outcome model and quadrants (Model B) | todo | | | |
| 7 | Simulator | todo | | | |
| 8 | Demo app | todo | | | |
| 9 | Story, video, submission | todo | | | |
| 10 | Stretch | todo | | | |

Status values: `todo`, `doing`, `blocked`, `done`.

## Rules for every work package

- Read `CLAUDE.md` and your WP file fully before writing code.
- Write outputs exactly as the contract says. If the contract is wrong, fix the contract in the same commit and log it.
- Put pipeline code in `scripts/wp<N>_<name>.py` so each step can be rerun from the command line. Shared helpers go in `src/vf/`.
- Every script prints a short summary of what it wrote (row counts and the two or three numbers a reviewer would check).
- Finish with the acceptance checks in your WP file, and paste the results into your final message.
- Running unattended: do not stop to ask. Take the default, log it in `docs/08_decisions_log.md`, move on.
- Report what is true. If a model is weak or a finding does not hold, write that down; do not tune until it looks good.
