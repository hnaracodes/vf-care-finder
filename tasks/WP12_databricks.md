# WP12: Databricks port and bundle

New in the re-scope (D023, D033, D034). Size: medium (about 2 hours, part of it waiting on Hruday's login). Depends on WP1 to WP5 and WP11 for the final run; the skeleton can start at once. Feeds WP8.

## Goal

The brief's first requirement: "Run on Databricks Free Edition." The pipeline runs there as a notebook job against the Marketplace tables, writes Delta tables and the app bundle, and the app is deployed there.

## Needs from Hruday

**Done Nov 21 (D042):** CLI authenticated (profile DEFAULT). Catalog `databricks_virtue_foundation_dataset_dais_2026`, schema `virtue_foundation_dataset`, tables `facilities`, `india_post_pincode_directory`, `nfhs_5_district_health_indicators`. Warehouse `ee1fa7958ed99cf2` (Serverless Starter). The items below are kept for reference.

- A Free Edition workspace with the Marketplace listing "Virtue Foundation Dataset (DAIS 2026)" added to a catalog. Write down the catalog and schema names here once known.
- The Databricks CLI authenticated on this machine. The agent installs the CLI; Hruday runs `! databricks auth login --host <workspace-url>` in the session. Agents never handle the password.

## Outputs

- `notebooks/01_pipeline.py`: a Databricks notebook (source format) that installs the repo's `vf` package, reads the Marketplace tables with Spark, converts to pandas (10,000 and 165,627 rows), runs WP1, WP2 Part A and B, WP3 regex, WP11 and the bundle export, and writes the results
- `src/vf/io.py`: a `source="databricks"` path that reads the tables instead of `data/raw` CSVs, returning the same frames (the NUL-byte and `null` handling stays in one place)
- `scripts/wp12_bundle.py`: writes `dbapp/bundle/` from gold and silver (facility index, grades with evidence, flags, places, keyword index), with a `manifest.json` of row counts, recipe version and git commit
- Delta tables in the workspace: `facilities_silver`, `facts_silver`, `capability_grades`, `facility_flags`, `places`. They make the data browsable in the workspace and usable by a Genie space (WP10)
- `dbapp/README.md`: how to deploy, the URL, and how to restart the app

## Steps

1. Skeleton now: `wp12_bundle.py` from the local outputs, and `io.py`'s Databricks path behind a flag, tested locally by feeding it a pandas frame shaped like the Spark table.
2. Check that the Marketplace tables match the local CSV export: row counts, column names, and the checksums' row-level equivalent (count of `null` strings, number of rows with NUL bytes). Record any difference.
3. Notebook: run end to end on serverless. Compare every printed summary with the local run. Differences are bugs.
4. Deploy: `databricks apps create` then `databricks apps deploy` from `dbapp/`. Confirm the five brief scenarios on the deployed URL.
5. Write the restart steps for Hruday (D034).

## Acceptance checks

- [ ] Notebook runs on Free Edition serverless without manual steps beyond the documented catalog name
- [ ] Row counts and grade counts on Databricks equal the local run (printed side by side)
- [ ] App deployed; the five brief scenarios answered on the deployed URL
- [ ] `dbapp/README.md` has deploy and restart steps

## Pitfalls

- Free Edition is serverless only; no custom clusters, no init scripts. `%pip install` in the notebook is the way in.
- Outbound network from serverless may be restricted. The pipeline must not call external APIs; teacher labels, if any, are made locally and uploaded as a table.
- Deploying into Hruday's workspace is fine. Making anything public (sharing the app outside the workspace, publishing the data) is not: prepare it and stop.
