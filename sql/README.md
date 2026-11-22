# SQL

Queries run on the Databricks Marketplace tables, kept for provenance. They read
`databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset` and write nothing.

| File | What it is | Checked by |
| --- | --- | --- |
| `profile_queries.sql` | Profiling queries behind `docs/03_data_profile.md` (Nov 21) | the numbers in that doc |
| `cold_open_lucknow.sql` | The video's cold open (D044 item 2): the obvious query for the brief's Lucknow C-section question, and a check of row 1's own record | `scripts/cold_open_check.py`, results in `reports/cold_open.md` |

## The cold open query

Query 1 is the "obvious tool": rows whose specialty mentions obstetrics, nearest
straight-line to PIN 226001 (Lucknow), top 3. The point is typed in as the resolver
returns it (`vf.locate.resolve("226001")`: 26.84967105, 80.94397185, precise to about 2 km).
The check script fails if the resolver ever moves that point.

Query 2 picks row 1 with the same logic and counts its facts, how many mention a
caesarean, and lists the facts that say where it is.

The raw table's traps are handled in the SQL, so it does not break when run live:

- `latitude` and `longitude` are DOUBLE in the Marketplace table but strings in the CSV
  export. `try_cast` works on both. Comparing a DOUBLE column with the string `'null'`
  fails under ANSI mode, so the query never does that.
- Missing values are the string `'null'` (or SQL NULL); `from_json` and `coalesce` turn
  a missing fact array into an empty one.
- The 88 junk rows fail `address_countryCode = 'IN'`. The 30 truncated rows have no
  coordinates. A bounding box (lat 6 to 38, lon 68 to 98) keeps any stray point out.
- Two names in the export carry a NUL byte; it is stripped from the displayed name.

## Running it

Paste query 1 into the SQL editor on the Serverless Starter Warehouse to film it. Or run
both statements from the command line (2 warehouse queries) and compare with pandas:

```bash
python scripts/cold_open_check.py --run-warehouse --save /path/to/responses.json
python scripts/cold_open_check.py --warehouse-json /path/to/responses.json
python scripts/cold_open_check.py      # compares with the result saved in reports/cold_open.md
```

Free Edition shuts compute down for the rest of the day if usage passes its fair-use quota.
Run these once before filming, not in a loop.
