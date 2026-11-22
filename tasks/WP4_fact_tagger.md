# WP4: Fact tagger (Model A)

Unchanged in purpose by the re-scope (D023), widened to recipe v0.2's 24 evidence tags. **Conditional: runs only if WP3 Part 3 produced teacher labels.** The API key is in place (D037). Without them, WP4 does not run, and regex v0.2 ships as the tagger.

Size: large (about 3 hours). Depends on WP3. Feeds WP5, WP11.

## Goal

A trained tagger that beats the regex on gold F1 for the tags that decide grades, an honest comparison of regex, teacher and student, and final grades.

## Inputs

- `silver/facts.parquet`
- `labels/teacher_facts.parquet` (training labels)
- `labels/gold_facts.csv` (evaluation only)
- `src/vf/recipes.py` v0.2 (grade rules)

## Outputs

- `gold/fact_tags.parquet` and `gold/capability_grades.parquet` overwritten with `method = student`, only if the student wins (step 5)
- `gold/model_a_report.json` and an updated `reports/model_a_results.md`
- Saved model under `data/models/` (not versioned)
- `scripts/wp4_*.py`

## Steps

1. Split teacher labels into train and validation by `text_norm`. Keep the gold set out entirely.
2. Student 1: sentence embeddings (or TF-IDF) plus one-vs-rest logistic regression, per-tag thresholds tuned on validation.
3. Student 2, if time allows: a small fine-tuned encoder. Stop early.
4. Score regex v0.2, teacher, and each student on the gold set: per tag, with gold positive counts.
5. Ship per tag, not globally: for each tag, use the student only where its gold F1 interval is above the regex's. Record which tags use which method in `model_version`. If the student wins on no tag, ship regex and say so.
6. Recompute grades with the v0.2 rules, and print grade-count changes per line against regex. Explain any shift over 10%.
7. Tell WP5 and WP11 to rerun.

## Acceptance checks

- [ ] Results table: regex, teacher, students, per tag, with gold positive counts
- [ ] Gold set not used in training or thresholds (stated in the report)
- [ ] Grade counts per line and method reported
- [ ] Cost and throughput for teacher versus student

## Pitfalls

- With about 25 to 40 gold positives per tag, scores are noisy. Do not claim a 2-point difference means anything.
- The student learns the teacher's mistakes.
- The model tags facts; rules grade facilities (D010).
