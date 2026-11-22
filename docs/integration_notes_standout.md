# Stand-out modules: integration notes

Written by the orchestrator from the standout-prep workflow results (Nov 21-22). One section per module.

## questions

Status: done; review: fix_needed

INTEGRATION.md (questions v2)

Public API (the old calls still work; new things are additive):
- questions_for(card, service_line: str, record=None, *, service_text: Optional[str] = None, max_questions: int = 4) -> list[dict{question, why, source}]. Call it in the server or ranker per card: questions_for(card.to_dict(), line, infos.get(card.facility_id)). Pass service_text=query for search cards.
- call_info(facility_id: str, phone_book: Optional[PhoneBook] = None, *, reveal: bool = False) -> dict{facility_id, has_phone, phone, masked, tel, source, shared_with, note}. Use it for the Call button. tel is set only when reveal=True and VF_DEMO_MODE is not 1. New: a record with only a foreign or malformed number returns has_phone=False with a note saying it does not look like a valid Indian number.
- load_record_info(data_dir=None, *, with_phones=True) -> dict[str, RecordInfo]. Load once at startup.
- save_record_info(infos, path, meta=None) -> Path. Use it in scripts/wp12_bundle.py to write record_info.parquet plus its sidecar. It now has an h24_quote_cotags column. A parquet saved by v1 without that column still loads: the field becomes None and the wording falls back to the neutral 'The record says "...", but no fact in the record says X is available round the clock.' Quotes are masked again on load.
- build_phone_book(raw, duplicates=None) -> PhoneBook. duplicates is the gold/facility_duplicates.parquet frame or (a, b) pairs. load_phone_book() reads the duplicates file automatically.
- New helpers: mask_numbers(text) -> text with runs of 8+ digits replaced by '[phone number]'; is_negative(text) -> bool (R.is_negated plus 'lack(s|ing)' and 'not open/working/running/functioning').
- RecordInfo has a new last field: h24_quote_cotags: Optional[frozenset] = None. It is keyword-safe, and positional construction of the first 7 fields is unchanged.

Required before deploy or filming: rerun scripts/wp12_bundle.py so dbapp/vf/questions.py is regenerated from this v2 source. The copy there is stale (23:03, missing REQUIREMENT_PHRASE). I did not run it because that script and its output are owned by another workflow.

Open issues:
- dbapp/vf/questions.py is still the stale v1 copy. Rerun scripts/wp12_bundle.py (another workflow owns it) before deploy or filming.
- recipes.py was being edited by another workflow during this round: import briefly failed with KeyError 'v0.3' at 23:18. I waited for it to import again, then ran all checks.
- tests/test_findings.py has 2 failures. They are another workflow's and unrelated to vf.questions.
- The 'only for something else' quote shows the tagged fact but does not name the other service. That is acceptable but could name it in a later pass.
- Contract note for docs/07: record_info.parquet gains column h24_quote_cotags (list<string>, nullable), and quotes are digit-masked. Logging that in 07 and the decisions log is left to the integrator.

## why_not

Status: done; review: fix_needed

INTEGRATION.md (why_not v2)

Signatures:
- why_not.why_not_strip(index, service_line: str, resolution, answer_dict: Mapping | None, k: int = 5, mask_names: bool = False) -> dict
  Returns {items, n_nearer, n_approx_nearer, first_card, cutoff_km, heading, masked, version}.
- why_not.why_not_nearer(index, service_line, resolution, answer_dict, k=5, mask_names=False) -> list. Same items as the strip.
- why_not.funnel(index, service_line, resolution, *, answer_dict=None, max_ids=50, detail_steps=("recommendable", "within_radius", "shown"), mask_names=False) -> dict
  Consistency is now {matches_ranker: True | False | None, mismatches, gates_compared}.
- why_not.literal_hits(index, line, pos) -> [{text, field, fact_id, negated}]
- why_not.literal_pattern(line) -> re.Pattern | None
- why_not.masked_name(index, pos) -> str
- why_not.funnel_counts(f) -> list[int]

Server (dbapp/server.py), after the existing rank call:
  ans = rank.rank(idx, line, res).to_dict()
  ans["why_not"] = why_not.why_not_strip(idx, line, res, ans, mask_names=DEMO)
  funnel on demand: why_not.funnel(idx, line, res, answer_dict=ans, mask_names=DEMO)
DEMO is the same flag that masks phone digits. Lookups are cached on the index object. On the bundle, the strip and the funnel each take well under 0.1 s per query.

UI (app/src/render.ts):
- Render item.reason_plain in grey (tone "neutral" in neutral style), never struck through.
- If quote_or_absence.kind == "quote", show quote_or_absence.text with its field. If quote_or_absence.negated is true, show it as the record's own statement.
- Show distance_label as given ("position approximate" carries no number).
- Use the heading as given; it already counts approximate records apart.

Deploy: rerun scripts/wp12_bundle.py copy_vf before deploying. dbapp/vf/why_not.py is identical to src right now: something copied it at 23:20 during this run. Add a server smoke test that imports dbapp's vf.why_not, asserts WHY_NOT_VERSION == "why_not_v2", and asserts funnel(...)["consistency"]["matches_ranker"] is True for 226001 emoc.

Open issues:
- Contract changes the integration pass should record in docs/07_data_contracts.md and the decisions log (I am not allowed to edit them): new reason codes fact_not_counted and fact_states_absence, also used as funnel group codes; new item fields name_masked and n_approx_nearer, plus masked on both outputs; quote_or_absence gains negated and n_hits on quotes and n_fact_rows on absences; consistency gains gates_compared and matches_ranker may be None; n_facts means distinct texts.
- reports/findings.json key cold_open.foil.row1.facts = 141 counts fact rows. The distinct count for that record (Indira IVF Kanpur) is 97. scripts/wp9_findings.py (line 852, not my file) should count distinct normalised texts so the cold-open counter and the strip agree. The 0-caesarean claim holds: literal_hits finds nothing in that record.
- The literal search uses tag patterns, not a full reading. It can still miss a fact that uses none of the listed words (for example 'operative delivery'). That is why the absence line says 'we found', not 'the record does not'.

## public_list

Status: done; review: ship

INTEGRATION.md

Module: vf.public_list. Pure Python. It depends only on pandas and rapidfuzz, and on vf.paths for the default location.

Signatures:
- `load(path: Path | None = None) -> PublicList`: path may be a file, or a folder containing laqshya_links.parquet (for example the WP12 bundle dir). None means silver/laqshya_links.parquet. A missing file gives an empty PublicList (every line is None), never an error.
- `PublicList.line(facility_id: str, as_of: date | None = None, current_only: bool = False) -> dict | None`
- `PublicList.lines(facility_id, as_of=None, current_only=False) -> list[dict]`: every list row behind a correct link, best first.
- `PublicList.annotate(answer: dict, as_of=None, current_only=False) -> dict`: adds a `public_list` key (line dict or None) to every card dict in `Answer.to_dict()` (results, beyond_radius, unverified_nearby), in place, and returns the same dict.
- `public_line(facility_id, as_of=None, current_only=False) -> dict | None`: uses the cached `default()`, which is `load()`.

Line dict keys: text, source ('LaQshya, NHSRC, master list of 8 Apr 2026'), source_url, page, row (the list's S.No), department ('Maternity OT', 'Labour Room' or None), status, validity (ISO date), current (bool), retrieved ('2026-11-21'), listed_as (the LaQshya unit name), outside_data (True).

Choice order: current certificates before expired; maternity OT before labour room; certified before conditionally certified; then the latest validity. as_of defaults to date.today(), so 'current' is computed when the request is served.

Call sites:
1. scripts/wp12_bundle.py: copy `paths.silver_dir()/'laqshya_links.parquet'` into the bundle (50 KB).
2. dbapp/server.py, once at startup: `PUBLIC = public_list.load(BUNDLE_DIR)`. Per request, after `ans = rank.rank(...)` or `rank.search(...)`: `d = PUBLIC.annotate(ans.to_dict())`, then return d. The server could pass current_only=True if Hruday wants no expired lines on screen.
3. Front end (app/): when `card.public_list` is non-null, show `card.public_list.text` as its own line, styled as outside data and kept apart from the record's quoted evidence. Do not change the card's grade or confidence because of it (D044 item 5: shown beside the record, not merged into it).

No change is needed in vf.rank, vf.locate, vf.recipes or vf.services.

Rebuild: `.venv/bin/python scripts/wp13_laqshya_links.py [--as-of YYYY-MM-DD]`. It reads data/labels/laqshya_link_review.csv and keeps earlier decisions. New candidates arrive with an empty review and produce no line until someone marks them.

Open issues:
- The 63 correct links were judged by one agent, not by Hruday. Before any count goes on screen, Hruday should spot-check the 16 links with a current certificate, which are listed in reports/laqshya_links.md. The brainstorm's '1 of 13' becomes '1 of 16' under this pass.
- Recall is not measured. One spot-check (40 random units with no candidate, in districts where our pool has a record) found no obvious missed link, but that is not a recall number.
- Some correct links land on records with known problems: Medical College and Hospital, Kolkata has a description about another college; several state-pass links sit on records whose PIN is in another district. The line itself is still correct about the listed unit. Location and merge issues belong to WP11.
- For PIN 422001, rank() shows District Hospital, Nashik at distance 0.0 inside unverified_nearby. That is the known D046 bug ('position approximate'), owned by the WP5 integration pass, not by this module.
- No contract change in docs/07_data_contracts.md yet. The orchestrator should add a `silver/laqshya_links.parquet` (WP13) section: one row per candidate link; key columns facility_uid, facility_id, review, review_reason, certs list[struct{sno, page, department, certification_status, result_date, validity_date}], emoc_grade, caesarean_named, has_current_maternity_ot, link_version. Also add the bundle entry laqshya_links.parquet, and a decisions-log line covering the pool rule, the state pass and the unsure policy.

## sealed_eval

Status: done; review: fix_needed

INTEGRATION.md

Guards (scripts/eval_sealed.py):
- check_sealed_inputs(split: str, unseal: bool, stage: str = "picks", root: Path | None = None, protocol: Path | None = None, expected: tuple = EXPECTED_SEALS) -> dict. Returns {protocol, points_sha256, points_sha256_protocol, git, code_sha256}. Sealed half: refuses without --unseal, on a protocol or point-hash mismatch, a seal chain other than EXPECTED_SEALS, or a prompt-hash mismatch. At stage picks or judge it also refuses if git shows anything under GUARDED_PATHS = (reports/eval_sealed_protocol.md, scripts/eval_sealed.py, src/vf). Dev only warns.
- protocol_status(path=None, expected=EXPECTED_SEALS) -> {ok, sealed, sha256, chain, chain_ok, pinned, n_seals, unsealed_amendment}.
- git_guard(root, rel_paths=GUARDED_PATHS) -> {git_commit, clean, tracked, porcelain}. code_hashes(root=None) -> {relpath: sha256}.
- check_judges(specs, path=None); check_sealed_judge_settings(args, picks_doc, info, specs). PROTOCOL_JUDGES and EXPECTED_SEALS are module constants. A future amendment must add its seal to EXPECTED_SEALS in the same commit, and a judge change must edit PROTOCOL_JUDGES.

Wording to replace in the earlier integration note: "each step refuses to run if the protocol seal ... has changed". Use instead: "the sealed half refuses unless the protocol's seal chain equals the chain pinned in the harness, and its picks and judging refuse unless the protocol, the harness and src/vf are committed and unmodified".

results_<split>.json, per tool under tools.<tool>: coverage, precision, precision_excluding_listing_only, precision_excluding_listing_any, pick_yes_listing_only, pick_yes_listing_any, good_pick, abstained_with_support, abstained_with_support_of_all, abstained_with_support_split, skipped_nearer, skipped_nearer_split, wrong_picks_of_all. Each split holds nearest_flagged, nearest_unflagged, nearest_listing_only, nearest_listing_any, nearest_unflagged_not_listing_only. Each rate is {k, n, rate, ci95}.
- REMOVED: tools.ranker.quotes_verbatim_strict and quotes_verbatim_loose, and all_queries.ranker_quotes_verbatim_*.
- ADDED: integrity_checks.card_quotes_match_cited_fact {k, n, rate, ci95, label} (judged prefix) and integrity_checks_all_queries.card_quotes_match_cited_fact (all 140).
- For findings.json and the video: put the 585/585 figure under the label text INTEGRITY_LABEL ("Integrity check, not a judged result: every quote on a card is the exact text of the fact it cites..."). Never put it in the evaluation table.
- meta adds judge_runs, ledger {api_calls, by_judge, usd, first_at, last_at}, flags_stored_with_picks, code_sha256_at_picks, git_at_picks, git_clean_at_picks.

picks_<split>.json adds per query major_flags {facility_id: [flag names]}, plus top-level git and code_sha256. judged_<split>.json adds runs, ledger, code_sha256, git, protocol_seal.

Sealed run, after the 04:00 ET freeze and Hruday's commit:
1. python scripts/eval_sealed.py picks --split sealed --unseal
2. python scripts/eval_sealed.py judge --split sealed --unseal (default --cap-usd 12)
3. python scripts/eval_sealed.py score --split sealed --unseal
4. python scripts/eval_sealed.py human --split sealed --unseal
5. python scripts/eval_sealed.py agreement --split sealed, after the CSV is filled in.

No ranker, server or UI changes are needed for this item.

Open issues:
- The original dry run's stop reason cannot be recovered: a cache-only rerun at 04:13:34Z had already overwritten judged_dev.json. The report uses the ledger instead: 28 judge_dev calls (A 14, B 13, C 1), $0.0965, 04:11:50Z to 04:12:29Z.
- Only 4 dev queries are scored (28 fully judged). The dev numbers cannot be read as rates.
- The guard covers all of src/vf, so the other workflow's uncommitted changes to questions.py, recipes.py and why_not.py must also be committed before the sealed run.
- Decisions log and contracts need entries from the main agent: the seal-chain pin and the git requirement for the sealed half, Amendments 1 and 2, the quote figure relabelled as an integrity check, the new listing-only precision fields, and the key changes in results_<split>.json (findings.json consumers must use integrity_checks.card_quotes_match_cited_fact instead of tools.ranker.quotes_verbatim_strict).

## sql_cold_open

Status: done; review: ship

No changes are needed in the ranker, server or UI. This item feeds the video (WP9) and the numbers file (item 6).

1. Numbers file: run `python scripts/cold_open_check.py --json <path>`. It writes keys resolution, n_raw_rows, n_pass_query1, foil_top3[] (name, km, n_facts, n_facts_caesarean, emoc_grade_label, flags), row1_record (n_facts, n_distinct_facts, n_facts_mentioning_caesarean, any_text_field_mentions_caesarean, location_facts), warehouse (statement_ids, rows), and ranker (gates, card1, cards, foil_rows_nearer_than_card1, foil_rows_nearer_with_a_caesarean_fact). The script reads no warehouse unless `--run-warehouse` is given, so re-running it against the frozen bundle is free.

2. Reusable functions in scripts/cold_open_check.py:
- `foil_candidates(raw: pd.DataFrame, lat0: float, lon0: float, country_filter: bool = True) -> pd.DataFrame`: every row passing query 1's filters, nearest first, with a km column.
- `row1_summary(row: pd.Series, km: float) -> dict`
- `sql_statements() -> list[str]`
- `run_on_warehouse(stmt: str) -> dict`: Statement API through `databricks api post`, then polls the same statement.
- `rows_of(resp: dict) -> list[dict]`

3. "Why not the nearer ones?" strip (WP5/WP8): the obvious query's row 1 already appears in rank(...).unverified_nearby as "Indira IVF Kanpur 1.0 km", so the strip can cite it there with no new code.

4. Filming: paste query 1 from sql/cold_open_lucknow.sql into the Databricks SQL editor. That is one more warehouse query, so run it as part of the live take, not as a rehearsal.

Open issues:
- The ranker's card 1 distance (3.5 km) uses pipeline coordinates. The SQL sees BNK at 3.499 km from raw coordinates, so the two agree.
- Two nearer records name a caesarean (Fatima Hospital 3.4 km, ranked 9th and Low confidence; Sanjeevani Hospital 3.48 km by its raw point). In both, the map point contradicts the record's own PIN. A sharp judge may ask about them, and the report has the answer.
- Row 2, Balrampur Hospital, is a public hospital, and its PIN 224176 is 222.7 km from its map point. Do not caption or highlight it on screen.
- Numbers come from the gold files written tonight (student grades, wp11_v2 flags). Re-run scripts/cold_open_check.py after the 04:00 freeze. It reads no warehouse by default.

## findings

Status: done; review: fix_needed

INTEGRATION.md (findings, fix round):
- Build: `python scripts/wp9_findings.py [--bundle B --out reports/findings.json --laqshya-links F --sealed F --extra G=F]`. It is also callable as `build(bundle, *, data_dir=None, laqshya=None, sealed=None, extras=None, cold_open=True, queries=True) -> dict`, then `write_atomic(obj, out)`.
- Check: `python scripts/wp9_findings.py --check FILE... [--findings reports/findings.json] [--allow 12,7.4] [--strict]`. The function form is `check_text(lines: Iterable[(where, line)], findings: dict, allow=(), strict=False) -> {checked, found, unknown: [{where, number, line, reason}]}`, plus `check_file(path, findings, allow=(), strict=False)`. Run it on the video script and on story JSON before filming. Use --strict on captions written with {{key}}.
- Captions: `render(text, findings) -> str` replaces each {{key}} with that finding's display text, and raises KeyError for a missing or null key. A story or caption file can store `{{query.cold_open_226001.card1.distance_km}}` and render it at build time, for example in wp12_bundle or the front end's story loader.
- Serving: if dbapp/server.py adds /api/findings, serve `public_view(json.load(open('reports/findings.json')))`. That returns {version, created_at_utc, findings: {key: {value, display, kind}}} with no how_computed and no source paths. Do not serve the raw file.
- Typicality caption: use points_nearest_listing_no_caesarean (356) of points_within_50km (414) and distinct_records (41). The bounds are points_upper_bound (366) and distinct_records_upper_bound (45).
- Gadchiroli honesty beat: the caveat in meta.caveats and query.remote_emoc_gadchiroli.distances_below_precision = 1 mean the 73 km figure must not be captioned as a measured gap.

Open issues:
- Check mode is still a token guard. 'Our tagger scores 0.93' passes because 0.93 is the CI upper bound, and '45 minutes ... 23 days' passes because 45 and 23 each match some finding. Only {{key}} captions with --strict fix this, so the video script should use them for key numbers.
- laqshya.* is still null: no hand-labeled link file exists at the candidate paths, and reports/laqshya_links.md belongs to another workflow. The LaQshya ratio must stay off screen until Hruday spot-checks the 13 (D044).
- The bundle was rebuilt by another workflow during this round (now built 04:22:17Z). Rerun the build after the 04:00 freeze bundle is final.
- The ratio miss is reported as one unknown entry, but it subtracts both numbers from found, so the CLI counts can read '13 numbers, 11 in findings, 1 not'. This is cosmetic.

## public_list v2: outside lists (D055, coming before the 04:00 freeze)

`vf.public_list` will gain multi-source outside lists (LaQshya plus up to 5 more: AERB radiotherapy licences, MoHFW trauma care facilities, the NHP blood bank directory, NABI burn-care providers, National Cancer Grid). UI and server code should render these fields whenever present and ignore them when absent. They are ADDED to the answer; nothing existing is renamed or removed.

- `vf.public_list.annotate_answer(answer: dict, service_line: str, lat: float, lon: float, state: str | None) -> dict` returns the same answer with two added fields. The server calls it once per `/api/answer`, after the ranker.
- Per card: `outside_lines`: list of `{source_id, source_name, publisher, list_date, what, status, citation, text}`. `status` is `current`, `lapsed` or `undated`. `text` is the full display sentence and always ends with "Outside data, not from this record." Show it under the card's evidence, styled as outside data (different from quotes).
- Per answer: `outside_nearby`: up to 3 `{source_id, source_name, name, district, state, pin, lat, lon, position_precision_km, position_note, minutes_label, km, what, citation, text}`. These are facilities on an outside list for this service, within the service window, that are NOT in our dataset. Render them as a separate, clearly labeled lane: "Also on government or registry lists nearby (not in this dataset)". Show it on results and, prominently, on the abstention screen as a next step. Never mix these with recommended cards, never number them like results, and show "position approximate (PIN area)" when `position_note` says so.
- Only links whose agent-checked precision has a one-sided 95% lower bound of at least 0.90 produce `outside_lines`. A blood bank listing appears only as a card fact (for emoc, cardiac_surgery, trauma), never in the nearby lane.

### Terms rule for public_list v2 (D057, binding)

- `outside_nearby` (the "not in this dataset" lane) may include entries only from sources whose recorded terms allow reuse with acknowledgement (for example AERB: "may be freely used elsewhere provided acknowledgement of the source is made"; CGHS; DGEHS). Sources with "all rights reserved" or permission-required terms (for example NABI, NABH, MCC, PM-JAY) produce `outside_lines` on our own records only, never lane entries.
- Every `text` names the source and its date; the app never shows a source's table.
- Wayback snapshots of official pages are allowed (cite the snapshot date); unofficial third-party re-hosts are not used.
