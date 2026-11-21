# Care desert or data desert?

CMU Datathon, Fall 2026. Hruday Nara.

Where in India can people not reach life-saving care, and where does it only look that way because the data is missing?

## Status

Scaffold only. The context, data, contracts, and work packages are in place. The pipeline, models, map, and video are built by Claude Code agents from the work packages in `tasks/`.

## Start here

| If you are | Read |
| --- | --- |
| A coding agent | `CLAUDE.md`, then `tasks/README.md` |
| Hruday, launching agents | `tasks/KICKOFF_PROMPTS.md` |
| Anyone wanting the idea | `docs/04_thesis_and_plan.md` |
| Anyone wanting the data | `docs/02_data_dictionary.md`, `docs/03_data_profile.md` |

## Layout

```
CLAUDE.md              context every agent loads first
docs/                  brief, dataset listing, data dictionary, profile, plan, recipes, contracts, decisions, presentation
tasks/                 one work package per step, the status board, kickoff prompts
data/raw/              the three source CSVs (not versioned) and MANIFEST.json
data/silver, gold/     pipeline outputs (not versioned)
src/vf/                shared loader and path helpers
scripts/               verify_data.py now; one script per work package later
sql/                   the profiling queries run in Databricks
app/                   the interactive map (built in WP8)
reports/               findings, lists, script (built in WP6 and WP9)
tests/                 loader tests
```

## Quick check

```bash
python scripts/verify_data.py
pip install -e ".[dev]"
pytest -q
```

## Data

Three tables from the Databricks Marketplace listing "Virtue Foundation Dataset (DAIS 2026)": 10,000 health facilities in India, the India Post PIN code directory, and NFHS-5 district health indicators. The raw files are not in the repository. See `data/README.md`.
