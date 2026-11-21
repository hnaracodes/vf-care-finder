# WP3: Teacher labels and regex baseline

Size: medium to large (about 3 hours, part of it waiting on API calls). Depends on WP1. Blocks WP4; unblocks WP5 early.

## Goal

Three things, in this order:

1. Regex baseline tags and grades, so downstream work starts within the first hour.
2. A hand-labeled gold set for honest evaluation.
3. LLM teacher labels for training Model A.

## Inputs

- `silver/facilities.parquet`, `silver/facts.parquet`
- `docs/06_capability_recipes.md` (taxonomy, regexes, grade rules, prompt requirements)

## Outputs

- `gold/capability_grades.parquet` with `method = regex` (first deliverable)
- `labels/gold_facts.csv`
- `labels/teacher_facts.parquet`
- `src/vf/recipes.py` (tag taxonomy, regexes, and the grade rules as code, reused by WP4)
- `scripts/wp3_*.py`

## Steps

### Part 1: regex baseline (do this first)

1. Implement the tag regexes and grade rules from the recipes doc in `src/vf/recipes.py`.
2. Tag every fact, grade every facility on all three service lines, and write `gold/capability_grades.parquet`.
3. Check the counts against the recipes doc. Tell WP5 on the status board that baseline grades exist.

### Part 2: gold set

4. Sample about 400 distinct facts (`text_norm`), stratified: for each evidence tag, facts the regex matched, plus near misses (for example facts containing "kidney", "renal", "critical", "operation", "delivery" that the regex did not tag), plus random facts.
5. Label them. If Hruday is available, export a CSV for him. Otherwise label them yourself, carefully, one by one, and set `labeled_by = agent`.
6. Score the regex against the gold set: per-tag precision, recall, F1. This is the baseline row of the results table.

### Part 3: teacher labels

7. Write the teacher prompt per the recipes doc. Batch facts, temperature 0, JSON out, cache every response on disk.
8. Spot-check 50 teacher labels by hand before scaling.
9. Label 20,000 to 30,000 distinct facts: all regex-matched facts, all near misses, and a random sample of the rest. There are about 290,000 distinct facts in total; you do not need them all.
10. Score the teacher against the gold set. This is the second row of the results table.
11. Track spend. Stop at the budget cap in `.env` (`VF_LLM_BUDGET_USD`).

## Acceptance checks

- [ ] Baseline grades written within the first hour, counts consistent with the recipes doc
- [ ] Gold set has about 400 rows and at least 25 positives per evidence tag, or a note on which tags fell short
- [ ] Results table so far: regex and teacher, per-tag precision, recall, F1
- [ ] Teacher cache is on disk and a rerun makes no new API calls
- [ ] Total API spend reported

## Pitfalls

- A specialty word is not a service. "Nephrology department" is not `dialysis`.
- Negations exist. "No ICU facility" must get no evidence tag.
- The gold set must never be used for training or for choosing thresholds.
- If there is no API key, do parts 1 and 2, mark part 3 blocked, and tell Hruday. WP4 can still train on regex labels as a fallback, but say clearly that this was done.
