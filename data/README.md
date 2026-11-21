# Data folder

```
raw/        the three source tables as exported, plus MANIFEST.json   (inputs, read-only)
external/   anything downloaded from outside, such as census population (record the source)
silver/     cleaned inputs from WP1 and WP2
gold/       results from WP3 to WP7
labels/     gold set and teacher labels from WP3
models/     saved model files from WP4
```

Only `MANIFEST.json`, `README.md`, `.gitkeep`, and `labels/gold_facts.csv` are versioned. Everything else is shared between git worktrees through `vf.paths.data_dir()`.

## What is in raw

| File | Table | Rows | Size |
| --- | --- | --- | --- |
| `facilities.csv` | `facilities` | 10,088 (10,000 real) | 75 MB |
| `india_post_pincode_directory.csv` | `india_post_pincode_directory` | 165,627 | 22 MB |
| `nfhs5_district_health_indicators.csv` | `nfhs_5_district_health_indicators` | 706 | 0.4 MB |

Checksums and expected shapes are in `raw/MANIFEST.json`. Check them with `python scripts/verify_data.py`.

## Where it came from

The data never existed as files. It is a Delta Sharing catalog in Hruday's Databricks workspace:

`databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset`

The three CSVs were exported on Nov 21, 2026 from the Databricks SQL editor.

## How to re-export

1. Open the SQL editor in the Databricks workspace.
2. Run `SELECT * FROM databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset.<table>`.
3. Use the arrow next to Run, then "Run and download all rows", then CSV. The default Run is limited to 1,000 rows; this menu item is not.
4. Rename the download to the file name in the table above and place it in `data/raw`.
5. Run `python scripts/verify_data.py`. A checksum warning is fine if the shapes match, since row order can change.

## Licenses

- PIN directory and NFHS-5: Government Open Data License, India.
- Facilities: from the Databricks Marketplace listing "Virtue Foundation Dataset (DAIS 2026)". Its reuse terms have not been checked. Do not publish the raw table.
