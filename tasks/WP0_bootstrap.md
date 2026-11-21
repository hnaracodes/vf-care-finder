# WP0: Bootstrap

Size: small (15 minutes). Blocks everything else. Run by the first agent in the main checkout.

## Goal

A working repo and environment that every later agent can rely on.

## Steps

1. Confirm the raw data: `python scripts/verify_data.py` must end with `raw data verified`.
2. `git init`, then make the first commit of the scaffold. Raw data is ignored by `.gitignore`; check that `git status` does not list the CSVs.
3. Create a virtual environment and install: `pip install -e ".[dev]"`. Add extras (`llm`, `ml`, `viz`) only in the work packages that need them.
4. Run `pytest -q`. All tests must pass, including the data-dependent ones.
5. Copy `.env.example` to `.env`. Check which keys Hruday has filled in. If `ANTHROPIC_API_KEY` is missing, note it on the status board: WP3 needs it.
6. Read `docs/00_brief.md`. If Hruday has answered any open question in chat, write the answer there and log it.
7. If several agents will work in parallel, create one git worktree per work package branch (`wp1-silver`, `wp2-geo`, `wp8-app`, and so on). All of them share one data folder through `vf.paths`.

## Acceptance checks

- [ ] `python scripts/verify_data.py` exits 0
- [ ] `pytest -q` passes
- [ ] First commit exists and contains no file from `data/raw` except `MANIFEST.json` and `README` files
- [ ] Status board updated

## Pitfalls

- Do not commit `.env`.
- Do not move or rename the files in `data/raw`; the loaders and manifest expect those names.
