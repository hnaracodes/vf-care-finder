# WP9: Story, video, submission

Size: medium (about 2 hours of agent work, plus Hruday's recording). Depends on everything. Last.

## Goal

A finished, truthful story: real numbers in every beat, a voiceover script, a recorded take, and the submission materials.

## Inputs

- `docs/09_presentation.md` (draft beats, checklist)
- All gold outputs, `gold/model_a_report.json`, `gold/model_b_report.json`, the three lists in `reports/`
- The app from WP8 on real data

## Outputs

- `app/public/data/story.json` with final beats and captions
- `reports/voiceover_script.md`: the words for each beat, timed
- `reports/findings.md`: one page with the headline numbers and where each came from (file and column), so every claim in the video can be traced
- `reports/methods_and_limits.md`: half a page
- A screen recording of the autoplay take, if the agent can record; otherwise clear instructions for Hruday
- `README.md` updated for a public reader

## Steps

1. Pick the opening district for beats 1 and 2: a real post office where the nearest claimed obstetric facility is close and the nearest evidenced one is far. Choose a striking but typical case, and say how typical it is (what share of post offices have a gap at least that large).
2. Fill every bracketed value in the draft beats from the data. Write each number into `reports/findings.md` with its source.
3. Check the story against the results. If a planned beat is not supported (for example Model B shows no signal), rewrite the beat to say what was found. Do not bend the result to fit the script.
4. Write the voiceover script. About 130 to 150 words per minute. Read it aloud against the autoplay timings and adjust the timings.
5. Make the 90-second cut.
6. Write the methods and limits half-page, and make sure the same limits appear on the limits card in the app.
7. Final checks from the recording checklist in `docs/09_presentation.md`.
8. Hand off to Hruday: a short message with the hosted link, the script, what to record, and anything he should double-check before submitting.

## Acceptance checks

- [ ] No bracketed placeholder remains in `story.json` or the script
- [ ] Every number in the script appears in `reports/findings.md` with a source file
- [ ] The video contains no mock data
- [ ] Limits are stated in the video, not only in the repo
- [ ] Both cuts fit their time targets when read aloud
- [ ] Writing has no em-dashes and no filler (Hruday's preference)

## Pitfalls

- Do not write "X has no facility that can do a C-section". Write "no facility in this dataset shows evidence of it".
- Do not present straight-line distance as travel time.
- If the gold set was agent-labeled, say so in the methods note.
- Do not submit anything on Hruday's behalf. Prepare everything and stop.
