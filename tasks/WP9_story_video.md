# WP9: Demo, video, submission

Re-scoped Nov 21 (D023). Size: medium (about 2 hours of agent work, plus Hruday's recording). Depends on WP8 and WP12. Last.

## Goal

Submission materials that show the tool answering real questions truthfully: a scripted demo on the deployed app, a short video if Hruday still submits one, and a write-up that answers the brief's WHY and HOW for a reader who never opens the code.

The official brief states no submission format, length or judging rubric (`00_brief.md`). Until Hruday says otherwise: a video of about 3 minutes with a 90-second cut, the app URL, and the repo.

## Inputs

- The deployed app (WP8, WP12)
- `reports/ranking_scenarios.md` (WP5), `reports/trust_checks.md` (WP11), `reports/model_a_results.md` (WP3, WP4), `reports/resolver_eval.md` (WP2)
- `docs/09_presentation.md` (demo beats and checklist)

## Outputs

- `reports/demo_script.md`: the queries to type, in order, with what each shows and the voiceover, timed
- `reports/findings.md`: every number in the demo and write-up with its source file
- `reports/methods_and_limits.md`: one page answering WHY and HOW (recipes, gates, score, abstention, checks, evaluation numbers, limits)
- `README.md` rewritten for a public reader: what the tool does, how to run it locally and on Databricks, how it decides
- A screen recording, if the agent can record; otherwise instructions for Hruday

## Steps

1. Choose the demo queries from the scenario suite, not new ones. Use the brief's own examples first. Include one abstention and one case where a trust flag changes the answer. Say in `findings.md` how typical each one is (for example "the nearest claimed facility had no supporting evidence in X of the 15 scenarios").
2. Every number in the script comes from the app or a report and goes into `findings.md` with its source.
3. If a planned beat does not hold (the scenario check shows wrong answers, a flag is imprecise), rewrite the beat to say what was found.
4. Script at about 140 words per minute. Read it against the real app. Make the 90-second cut.
5. Restart the app (D034) shortly before Hruday records or submits.
6. Hand off to Hruday: URL, script, what to record, what to double-check.

## Acceptance checks

- [ ] Every number in the script is in `findings.md` with a source
- [ ] Demo contains no mock data and no query invented to look good
- [ ] Limits are said in the video and shown in the app, not only in the repo
- [ ] Both cuts fit their time when read aloud
- [ ] No em-dashes, no filler (Hruday's preference)

## Pitfalls

- Write "no facility in this dataset shows evidence of", never "there is no".
- Straight-line distance is not travel time.
- The gold set and flag reviews were agent-labeled. Say so.
- Do not submit anything on Hruday's behalf. Prepare everything and stop.
