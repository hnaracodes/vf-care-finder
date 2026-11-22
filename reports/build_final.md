# Final build (overnight-final, stage 1: data assembly)

Built Nov 22 2026, 02:40 to 03:00 ET, on branch `rescope` at commit 8890572 with uncommitted changes (listed at the end). Nothing was deployed, pushed or published in this stage. LLM spend in this stage: 0 USD (project total across the three ledgers under `data/labels/`: 12.34 USD).

## Build id

| Part | Value |
| --- | --- |
| Recipe (grades the ranker gates on) | v0.3 (`gold/capability_grades_v03.parquet`, D052); v0.2 kept in `gold/capability_grades.parquet` |
| Tagger `model_version` | `student:wp4b-2b_r2[ob_delivery]+student:s1-1b[h24,icu,ventilator]+regex:v0.2[rest]` |
| Fact tags sha256 | `1bc6faf5da8df74ece73edcc50a1bb95d3dcb029770f7fd1a1f84f0b8645f982` (matches the WP4b record) |
| Trust checks | `wp11_v3` |
| Places | `places-v3` |
| Ranker | `v1` |
| Outside lists | `public_list_v2`; record info `questions_v2` |
| sha256 `gold/capability_grades_v03.parquet` | `aa32480bf4c819e8c281107e463f855aac9e27e3929fffe4f52f7ade567b7875` |
| sha256 `gold/capability_grades.parquet` (v0.2) | `6380e5173aefc12fdfaec222292c942895fc2bcbd4f49028cdecf1541116fe1f` |
| sha256 `gold/facility_flags.parquet` | `76f947d1319e74a2c08813b48f176ce4c440569bee3ed0006399a7166c055525` |
| sha256 `gold/facility_duplicates.parquet` | `8830f4144480b3d2ebafca040d8c8b1adf095e0392abad76cf409151f69af3d7` |
| sha256 `dbapp/bundle/manifest.json` | `225936a29f4bbb0f63c2a4cd7152ce5af26044958d5905e70bc6b20723110cce` (created 2026-11-22T07:48:00Z) |
| sha256 bundle `grades.parquet` | `16a032dcbdf95b1a0d8ef376425acf907c7f81fe93c1f4d6e3d26af9ac43e41d` |
| sha256 bundle `flags.parquet` | `b2789019b0ccf2db77aa9dee7a883bc372abea0370895e7332731bfc1b820eab` |
| sha256 bundle `record_info.parquet` | `12753a0772cfff8a8d09ad76882510e8777392819356790e96cf35d80039d30b` |

The bundle's grades and flags files differ from gold only in masked quote digits (below), so their hashes differ from the gold hashes.

## 1. WP4b promotion (D059) and regrade

Ran `scripts/wp4b_promote.py` (new), then `scripts/wp3_regrade_v03.py`, then `scripts/wp11_trust_checks.py`.

- The candidate's sha256 matched the recorded value; same columns, rows (451,105) and fact_id order as the old tags; 1,625 rows with different tags, as WP4b recorded.
- Backups in `data/models/wp4b/`: `fact_tags_pre_wp4b.parquet`, plus the pre-promotion v0.2 and v0.3 grades and flags, each with its sidecar.
- v0.2 regrade: 22 of 119,868 facility-lines change, all emoc (0 to 1: 6, 1 to 0: 16). v0.3 regrade: the same 22. **0 lines cross the grade 2 gate on any line, as D059 predicted.**
- The v0.3 script's checks all passed, including "v0.2 regrade of the frozen tags equals gold/capability_grades.parquet, every cell: 0 differing cells". v0.3 counts are as in D052 (emoc grade 3: 54; NICU grade 2+: 694; cath lab grade 3: 205; dialysis grade 3: 456).
- WP11: blocked grade 2+ lines stay at 1,010 of 10,957, the same as before. One small difference: the flags file went from 3,664 to 3,654 rows. 14 per-line merge blocks went away and 4 new ones appeared, all on emoc lines at grade 1. This happened because the merge-line rule reads the line's tagged facts, and ob_delivery tags changed. The ranker never recommends grade 1 lines, so recommendations do not change. WP11's scenario-suite impact on this run: with all checks on, 7 scenarios change and 1 changes status.

## 2. Bundle

`scripts/wp12_bundle.py` now writes everything the UI server needs: facilities, v0.3 grades, wp11_v3 flags, duplicates, places-v3, post offices, terrain table, record info with call numbers, LaQshya links, the outside card facts and lane, and the keyword index. Results:

- 9,989 facilities; 119,868 grade rows; 3,654 flags; 75 duplicate pairs; 182,755 places; 137,748 post office points; 36 terrain rows.
- Record info: 9,989 rows, with 9,697 call numbers from the raw facilities via `vf.io` (`vf.questions.save_record_info(..., phone_book=)`). The server now reads them from the bundle with `load_phone_book(bundle)`. Before this change, the deployed app had no raw file, so its Call button had no number.
- Outside lists (default D057 gates): 133 card facts (AERB 79, NCG 54) plus 63 LaQshya card links in `laqshya_links.parquet`, 196 in all, as D060 says; the lane has 1,204 rows (LaQshya 779, AERB 425). Withheld by terms: NHP blood bank card facts (57), and lane rows from NCG (177), MoHFW trauma (150) and NABI (61).
- Phones: any run of 8 or more digits in a shipped quote is masked as `[phone number]`. That covered 13 grade evidence cells, 4 flag quotes and 842 keyword index texts; the server already applied this mask on display. Two `official_website` values that were really phone links (wa.me, or a listed call number) were removed from the bundle.
- Size: 46.5 MB in total; the largest file is `places.parquet` at 6.9 MB. Every file is under 10 MB.
- `--check`: **ok**. dbapp/vf matches src/vf. All 15 required files are present. Versions are recipe v0.3, wp11_v3 and places-v3. The bundle was built from the current gold grades, flags and fact tags. No file is over 10 MB. Every file under `dbapp/` (bundle, static front end and vf code) was scanned for the 9,352 distinct call numbers, and none appears in clear outside `record_info.parquet` `call_phone`.
- Not in the bundle: `service_probability.parquet`, because WP13 never wrote it (D045 dropped probabilities), so `/api/simulate` reports unavailable. `/api/sim`, the agent simulator, does not need it.

Limitation: the phone scan looks for the normalised call numbers. Other numbers in the raw `phone_numbers` lists can only appear inside quote text, and those quotes are masked by the generic 8-digit rule.

## 3. Tests and front end

- `pytest -q`: **1,113 passed, 9 skipped, 1 xfailed** in 58 s. This includes 3 new tests: the phone-column round trip with masked serving, quote masking that leaves ids alone, and the phone scan.
- `pnpm typecheck`: ok. `pnpm build`: ok, output in `dbapp/static`, byte-identical to the committed build.

## 4. App run from a copy outside the repo

A copy of `dbapp/`, made with the script's `stage_app` (no `.gitignore` files), was placed in the session scratchpad and run on port 8901 with `VF_DATA_DIR` unset. Health showed: mode bundle, vf from dbapp/vf, recipe v0.3, flags wp11_v3, 12 lines, search on, all extras loaded (record info, phone book in 9 ms from the bundle, public list, outside lists, why-not, travel), and no extras errors.

| Scenario | Status | Answer (first cards) | Outside data | /api/sim |
| --- | --- | --- | --- | --- |
| Dialysis, Jaipur | ok | Soni Hospital (g3, about 6 min), NAV Imperial (g3), Garg Child Hospital (g2, the nearest recommendable, shown as the last card) | none nearby | ok: 233 villages, 21 evidence and 32 claim-only facilities |
| Emergency C-section, Lucknow PIN 226001 | ok | BNK Hospital (g3, quotes "Performs Lower Segment Caesarean Section (LSCS)"), Fatima Hospital (g2), Pawar's Clinic (g2) | 3 LaQshya lane lines (Ram Sagar Misra, Jhalkari Bai Mahila, CHC Sarojini Nagar; certificates worded "not renewed on that list") | ok: 402 villages, 47 evidence facilities |
| Dialysis, Jokihat, Bihar | no_reliable_option | Abstains: "This is a gap in our data, not proof that no care exists." The nearest facility with evidence is Raiganj GMCH, about 1 h 30 min | none | ok: 170 villages, 11 inside the window |
| Emergency C-section, Nashik | ok | Hrudayasparsh (g2), Prapti Women's (g2), Sukhatme (g2) | LaQshya card line on District Hospital Nashik ("certified, valid to 31 Dec 2026"); 3 LaQshya lane lines | ok: 140 villages |
| Trauma, Kotputli | ok | Shri Shyam Hospital, Kotputli (g3, about 2 min); Sachkhand (g3, about 51 min); Rainbow, Narnaul (g3) | none (MoHFW trauma list withheld by terms) | ok: 321 villages, 222 evidence facilities |

Extra checks:

- Oncology, Mumbai: an AERB card line on Jaslok Hospital, plus 3 AERB lane lines.
- Oncology, Jaipur: 3 AERB lane lines.
- Emergency C-section, Varanasi: 3 LaQshya lane lines.

Call info: every card shows a masked number (for example `+91 xxxxxxxx21`) with `tel` null. With `reveal=1` the tel link appears; with `reveal=1&demo=1` it stays null. Responses were scanned for the five scenarios, the four extra checks and their `/api/sim` calls, and no call number appeared in clear in any of them.

Seen, not changed in this stage:

- Jaipur dialysis: the third card is a children's hospital with a dialysis fact. This is the D061 known weakness (the nearest recommendable facility is always shown).
- Lucknow: the "no evidence yet" list includes two Kanpur-labelled records placed at a Lucknow PIN centre. They carry location and merge flags that say so, and they are never recommended.

## Files changed (uncommitted)

- New: `scripts/wp4b_promote.py`, `reports/build_final.md`.
- Changed:
  - `scripts/wp12_bundle.py`: outside lists, record info with phones, quote masking, `--check` completeness and phone scan.
  - `src/vf/questions.py` and `dbapp/vf/questions.py`: the opt-in phone columns, `phone_book_from_frame`, and `load_phone_book(source)`.
  - `dbapp/server.py`: the phone book from the bundle.
  - `dbapp/bundle/manifest.json`.
  - `tests/test_bundle.py` and `tests/test_questions.py`.
  - `docs/07_data_contracts.md`: the bundle table.
  - `docs/08_decisions_log.md`: D064.
  - `tasks/README.md`: the WP4 and WP12 rows.
- Data, not versioned: `data/gold/fact_tags.parquet`, both grade files, flags, duplicates, and `data/models/wp4b/`.
