# WP4: Fact tagger (Model A)

Size: large (about 3 hours). Depends on WP3. Feeds WP5 (rerun), WP6, WP8, WP9.

## Goal

A trained model that tags facts, an honest comparison against regex and the LLM teacher, and final facility grades.

## Inputs

- `silver/facts.parquet`
- `labels/teacher_facts.parquet` (training labels)
- `labels/gold_facts.csv` (evaluation only)
- `src/vf/recipes.py` (grade rules)

## Outputs

- `gold/fact_tags.parquet` (all facts, `method = student`)
- `gold/capability_grades.parquet` overwritten with `method = student`
- `gold/model_a_report.json`: per-tag precision, recall, F1 for regex, teacher, and each student; training set size; cost and speed
- Saved model under `data/models/` (not versioned)
- `scripts/wp4_*.py`

## Steps

1. Split teacher labels into train and validation by `text_norm`. Keep the gold set out entirely.
2. Student 1, the fast baseline: sentence embeddings plus one-vs-rest logistic regression (or TF-IDF plus logistic regression if embeddings are slow). Tune the per-tag threshold on validation.
3. Student 2, if time allows: fine-tune a small encoder for multi-label classification. Use Apple Silicon acceleration if available. Stop early; this is not the place to spend the night.
4. Evaluate every system on the gold set. Report per-tag and macro precision, recall, F1, with the number of gold positives beside each tag so small-sample scores are visible.
5. Pick the student to ship by gold F1 on the evidence tags. If the fine-tuned model is not clearly better than the baseline, ship the baseline and say so.
6. Run the chosen student over all facts. Write `gold/fact_tags.parquet`.
7. Recompute facility grades with the rules in `vf.recipes` and overwrite `gold/capability_grades.parquet`. Fill `evidence` with the facts that triggered each grade.
8. Compare grade counts across methods (regex, student) per service line and write the table into the report. Large shifts need a one-line explanation.
9. Tell WP5 to rerun on the new grades.

## Acceptance checks

- [ ] Results table with three or four systems, per-tag metrics, and gold positive counts
- [ ] The gold set was not used in training or threshold selection (state this in the report)
- [ ] `gold/fact_tags.parquet` covers every fact in `silver/facts.parquet`
- [ ] Grades written, with evidence quotes, and grade counts reported per service line and method
- [ ] Cost and throughput reported for teacher versus student (facts per second, dollars per 100,000 facts)

## Pitfalls

- With about 400 gold facts, per-tag scores are noisy. Report counts, and do not claim a difference of two points means anything.
- The student learns the teacher's mistakes. If the teacher is weak on a tag, the student will be too; say which tags are unreliable.
- Do not let the model grade facilities directly (D010).
- If WP3 had no API key and you trained on regex labels, the student can only imitate the regex. Report it as a fallback, not as a result.
