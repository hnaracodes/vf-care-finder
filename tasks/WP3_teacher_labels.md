# WP3: Recipes, gold set, teacher labels

Re-scoped Nov 21 (D023, D025). Parts 1 and 2 are done for recipe v0.1 (3 service lines, 13 evidence tags). This round widens them to recipe v0.2: 12 service lines, 24 evidence tags.

Size: medium to large (about 3 hours, plus API wait if a key arrives). Depends on WP1. Feeds WP4, WP5, WP11.

## Done so far (v0.1)

- `src/vf/recipes.py`: tags, negation and VBAC handling, grade rules, `select_evidence`, `source_confidence`
- `gold/capability_grades.parquet` and `gold/fact_tags.parquet` with `method = regex`
- `data/labels/gold_facts.csv`: 429 facts, agent-labeled
- `reports/model_a_results.md`: regex macro F1 0.90 (0.88 to 0.92) over 13 tags; weakest `ob_delivery` 0.61, `ventilator` 0.84, `icu` 0.85

## Goal

Every one of the 12 service lines graded 0 to 3 from tagged facts, each grade carrying the quoted facts that justify it, and every new tag measured against hand labels.

## Inputs

- `silver/facilities.parquet`, `silver/facts.parquet`
- `docs/06_capability_recipes.md`, section "v0.2: twelve service lines" (the spec for the new tags and rules)

## Outputs

- `src/vf/recipes.py` at `RECIPE_VERSION = "v0.2"`, with v0.1 still reproducible via `version="v0.1"`
- `gold/fact_tags.parquet` and `gold/capability_grades.parquet` (contract v2: 12 lines, structured evidence, evidence strength)
- `data/labels/gold_facts.csv` extended: at least 25 positives for every v0.2 tag, the old 429 rows unchanged
- `reports/model_a_results.md` updated with per-tag scores for v0.2
- `labels/teacher_facts.parquet` only if a key arrives (Part 3)

## Steps

### Part 1: recipe v0.2

1. Read the 11 new tags and the grade rules for the 9 new lines in the recipes doc. Before writing any regex, read 50 random facts that match a loose pattern for each new tag. Note what the loose pattern gets wrong, then write the regex. Do not look at gold labels while doing this.
2. Imaging (`mri`, `ct`) needs a rule the v0.1 engine cannot express: corroborated when the tag appears in two different fields (for example equipment and procedure) or in two distinct facts. Extend `LineRule` with a `min_distinct_facts` or `fields` condition rather than special-casing.
3. `capability_grades` gains structured evidence: for each grade, the fact ids, texts and fields that satisfied each required group, so a card can say "C-section: 'performs LSCS round the clock' (procedure)". Keep `evidence` (list of texts) for old readers.
4. Add `n_supporting_facts` and `evidence_strength` (`strong`, `moderate`, `thin`; rule in the recipes doc).
5. Run on all facts and print grade counts per line. Spot-read 10 grade 2+ facilities per new line and note anything wrong.

### Part 2: extend the gold set

6. Draw new facts stratified by the v0.2 tags (regex hits, near misses such as "cardiology OPD" for `cath_lab` or "scan" for `ct`, and random), skipping texts already in the file. Aim for 25 to 30 positives per new tag.
7. Label them carefully, one by one, `labeled_by = agent`. Label against the tag definitions, not the regex.
8. Score v0.2 on the whole gold set. Report every tag, the macro F1, and the three weakest tags with their failure modes, in the existing report format.

### Part 3: teacher labels (unblocked, D037)

9. As in v0.1: batched JSON, temperature 0, disk cache keyed by text and recipe version, 50 spot checks before scaling, stop at `VF_LLM_BUDGET_USD`. Label 20,000 to 30,000 distinct facts covering all 24 tags.

## Acceptance checks

- [ ] v0.1 tags give identical results to before on the old gold rows (regression test)
- [ ] Grade counts per line printed; every line has at least 50 facilities at grade 2 or higher, or a note on why not
- [ ] Every grade 2+ row has at least one evidence fact id, and each quoted text exists verbatim in `silver/facts.parquet` (test)
- [ ] Gold set: at least 25 positives per v0.2 tag, or a note on which fell short
- [ ] Per-tag precision, recall, F1 for v0.2 in `reports/model_a_results.md`
- [ ] `pytest -q tests/test_recipes.py` passes

## Pitfalls

- A specialty word is not a service. "Cardiology department" is not `cath_lab`; "oncology OPD" is not `chemo`.
- Referral is not service: "we refer cases for MRI" or "tie-up with X diagnostics" must not tag `mri`.
- Wish lists are not equipment: "need for a CT scanner" (see the `ventilator` false positives in v0.1).
- "Trauma" in psychology text ("trauma counselling") is not trauma care.
- `burns` is the smallest line (about 231 facilities with a mention). Check its precision first.
- The gold set must never be used for training, regex writing, or thresholds.
