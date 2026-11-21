# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# CMU Datathon: care desert or data desert?

Hruday Nara's entry for the CMU Datathon (Fall 2026). The deliverable is a submitted video built around an interactive map, due Sunday Nov 22, 2026. You are one of several Claude Code agents doing the work, possibly unattended overnight.

## The project in six lines

- Data: 10,000 Indian health facilities scraped from the web (messy, 88% private), every post office in India, and a district health survey (NFHS-5).
- Question: where can people not reach life-saving care, and where does it only look that way because the data is missing?
- Step 1: grade what each facility can do, with evidence. A trained model tags the facts; rules turn tags into grades.
- Step 2: measure straight-line distance from every post office to the nearest facility at each evidence grade.
- Step 3: compare with the survey. Poor access plus poor outcomes is a care desert. Poor access plus good outcomes is a data desert. A second model makes that call.
- Step 4: simulate where the next upgrades should go, show it all in an interactive map, record the video.

## State of the repo

This is a scaffold. As of Nov 21, 13:50 ET:

- Done: raw data in `data/raw` (verified), the shared loader `src/vf/io.py`, path helpers, `scripts/verify_data.py`, tests, all docs and work packages.
- Not started: every pipeline step, both models, the simulator, the app, the video. See `tasks/README.md` for the live status board.
- WP0 not run yet (checked Nov 21, 14:05 ET): the folder is not a git repo, there is no virtualenv, and the default `python3` (miniforge, 3.14) has no pandas or pytest. Until WP0 runs, `verify_data.py` and `pytest` fail with `ModuleNotFoundError`, and the branch and worktree rules below cannot apply.

## Read in this order

1. `docs/00_brief.md`: the event, the deliverable, open questions and their defaults
2. `docs/04_thesis_and_plan.md`: what we are building and why
3. `docs/03_data_profile.md`: what the data looks like, and the traps
4. `docs/07_data_contracts.md`: the files each step must produce
5. `tasks/README.md`, then your work package file

Reference when needed: `docs/02_data_dictionary.md` (every column), `docs/06_capability_recipes.md` (tags and grade rules), `docs/09_presentation.md` (the map and the video), `docs/05_ideas_backlog.md`, `docs/08_decisions_log.md`.

## Data traps

These have already cost time once. Do not rediscover them.

1. Load raw data with `vf.io`, never a bare `pd.read_csv`. The facilities file has NUL bytes.
2. Missing values are the string `null`. `notna()` is true everywhere.
3. 88 junk rows and 30 truncated rows exist. `vf.io.read_facilities()` drops the junk and flags the truncated.
4. Never count facilities per district as a measure of care. 202 districts show zero. Use distance.
5. Dedupe `specialties` per facility. Labels repeat inside a row.
6. `source_types` is not per-fact provenance. Use distinct domains in `source_urls`.
7. `address_stateOrRegion` has 234 values. Do not group or join on it.
8. A join on PIN fans out. The PIN directory's grain is post office.
9. PIN directory coordinates include `NA`, malformed strings, and points outside India.
10. NFHS `*` is missing, not zero. Values in parentheses are low-sample estimates.
11. NFHS has 706 districts and the PIN directory 755. Names differ. Use the crosswalk from WP2.
12. No evidence is not evidence of absence. Write "no evidence of", never "does not have".

## Working rules

- Shared data: `vf.paths.data_dir()` resolves to one data folder for every git worktree. It is not versioned. Write outputs atomically and only to the files your work package owns.
- Two roots: `paths.data_dir()` is shared across worktrees (the main checkout's `data/`, or `VF_DATA_DIR`). `paths.code_root()` and `paths.app_data_dir()` (`app/public/data/`) are per worktree and committed. App JSON goes in the second, never the first.
- Code layout: each work package's pipeline lives in `scripts/wp<N>_<name>.py` and must rerun from the command line and print a short summary. Only helpers shared across packages go in `src/vf/`.
- Contracts: produce exactly what `docs/07_data_contracts.md` says. To change a contract, edit that file in the same commit and add a line to the decisions log.
- Branches: one per work package, named `wp<N>-<slug>`. Small commits. Do not push or open pull requests unless Hruday asks.
- Status: update your row in `tasks/README.md` when you start, finish, or get blocked.
- Unattended: do not stop to ask a question. Take the documented default or the most reasonable reading, record it in `docs/08_decisions_log.md`, and continue. The exception is anything irreversible or public: submitting, publishing data, spending past the budget. Prepare it and stop.
- Honesty: report real numbers, including weak ones. If a model does not beat its baseline or a planned finding does not hold, say so and adjust the story. Never put an illustrative or mock number in a final deliverable.
- Verification: finish each work package by running its acceptance checks and reporting the results.
- Secrets: keys live in `.env`, which is never committed. Respect `VF_LLM_BUDGET_USD`.
- Raw data: do not commit it and do not publish it. The two government files are open data; the facilities table came from a Databricks Marketplace listing whose reuse terms have not been checked.

## Writing style for anything a human will read

Hruday's preferences, which apply to the video script, captions, README, and reports:

- No em-dashes. Use commas, colons, or separate sentences.
- Plain and direct. No filler, no hype.
- Lead with the finding, then the number, then the caveat.

## Commands

```bash
python scripts/verify_data.py      # raw data present and intact
pip install -e ".[dev]"            # core deps; add llm, ml, viz as needed
pytest -q                          # loader tests (data tests skip if data/raw is absent)
pytest -q tests/test_io.py::test_parse_nfhs_value   # one test
pytest -q -k nfhs                  # tests matching a keyword
```

```python
from vf import io, paths
fac = io.read_facilities()         # 10,000 real rows, strings, with row_status
pins = io.read_pincodes_raw()      # 165,627 post offices
nfhs = io.read_nfhs_raw()          # 706 districts
io.parse_list(fac.capability[0])   # JSON array cell to list
io.parse_nfhs_value("(29.5)")      # (29.5, "low_sample")
paths.silver_dir(), paths.gold_dir()
```
