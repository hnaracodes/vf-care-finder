# Presentation: the map and the video

The deliverable is a submitted video. The video is a screen recording of an interactive map, driven by a scripted story mode, with Hruday's voiceover. Everything in this doc serves that recording.

## Why a map

The finding is geographic and the decision is "where". A viewer should see India change when the evidence bar moves, and see coverage change when a facility is upgraded.

## The three interactions

Each one carries a finding. If an interaction does not carry a finding, cut it.

### 1. Evidence slider (claims versus evidence)

A slider with four stops: any claim, claimed, supported, corroborated. As it moves right, facility markers disappear and the post office dots recolor as the nearest qualifying facility gets farther away. A counter shows the share of post offices within a chosen distance.

The numbers behind it for obstetrics: 4,585 facilities claim the specialty, 443 mention a C-section.

### 2. Patient mode (one person's trip)

Click any post office. Lines fan out to the nearest facility at each evidence level, labeled with distance: nearest claimed, nearest supported, nearest corroborated. Clicking a facility opens its evidence: the quoted facts, its tags, and how many independent sites mention it.

This is the opening of the video: one place, one emergency, two very different distances.

### 3. Simulator (decisions)

- Click a facility to upgrade it, or click empty space to add one. Coverage recomputes instantly and the counter moves.
- A play button runs the optimizer: the next 10 upgrades appear one by one while a coverage curve climbs.
- A toggle switches between the naive optimizer and the desert-aware one. The naive one spends picks on districts where care exists but is invisible in the data. The desert-aware one uses Model B to avoid that. Showing the difference is how the ML earns its place on screen.

## Layers and visual language

- Base: about 151,000 post office dots. They draw the shape of India with no boundary file.
- Dot color: distance to the nearest qualifying facility, on one sequential scale with a fixed legend in km. Use the same scale in every scene so the viewer can compare.
- Facility markers: colored by grade, sized modestly. Hidden when below the slider's threshold.
- District view: dots take the color of their district's quadrant (care desert, data desert, cost barrier, served). Four colors that stay distinguishable for color-blind viewers, each with a text label.
- Dark background, one accent color, large type. The recording will be watched at small sizes.
- Every number on screen has a unit and a short label. Estimates are marked "est.".

## Story mode

A scripted sequence of beats, advanced with the spacebar (and with a hidden autoplay for clean takes). Each beat sets the camera, the active layers, the slider position, and a one-line caption. Stored in `app/public/data/story.json` so the script can be edited without touching code.

Draft beats for a 3-minute video. Replace every bracketed value with a real one from the data; do not record with placeholders.

| # | Time | On screen | Voiceover idea |
| --- | --- | --- | --- |
| 1 | 0:00 | Dark map, one post office pulsing in [district] | A woman here goes into labor and starts bleeding. Where does she go? |
| 2 | 0:15 | Patient mode: lines to nearest claimed and nearest evidenced facility | The data lists [N] maternity facilities within [X] km. Only one shows any evidence it can do a C-section. It is [Y] km away. |
| 3 | 0:35 | Zoom out, drag the evidence slider across India | Same story across the country: [4,585] claim obstetrics, [443] show evidence. Watch the map. |
| 4 | 0:55 | Counter: share of post offices within [R] km, before and after | Coverage drops from [A]% to [B]%. |
| 5 | 1:10 | Switch to quadrant colors | But is that a care gap or a data gap? This dataset is 88% private clinics with websites. |
| 6 | 1:25 | Highlight data deserts, show institutional birth rate | In these districts, [P]% of births still happen in institutions. Care exists. The data cannot see it. |
| 7 | 1:40 | Highlight care deserts | In these, it does not. These are the real gaps. |
| 8 | 1:55 | Methods card: two models, metrics | How we know: a fact tagger at [F1] against hand labels, and an outcome model that improves by [delta] when access is added. |
| 9 | 2:15 | Simulator: play 10 upgrades, naive then desert-aware | Where should the next ten upgrades go? The naive plan wastes [k] of them. Ours does not. |
| 10 | 2:40 | Three lists side by side | Three lists Virtue Foundation can act on: send care, send data collectors, fix cost. |
| 11 | 2:55 | Limits card, repo and map links | What this cannot tell you, and where to try it. |

Also prepare a 90-second cut: beats 1 to 4, 6, 7, 9, 10.

## The "how we know" drawer

One panel inside the app, also shown as beat 8:

- Fact tagger: per-tag precision, recall, F1 for regex, teacher, and student, plus the size and labeler of the gold set.
- Outcome model: cross-validated error with and without access features, and the cross-validation scheme.
- Data: 10,000 facilities, 88% private; 151,000 geocoded post offices; 706 districts, with how many matched exactly.
- Limits, in plain words (copy from `04_thesis_and_plan.md`).

## Honesty rules for the screen

- Say "no evidence of", never "does not have".
- Distances are straight-line km. If a time is shown, label it as a rough estimate and state the assumed speed.
- "People" counts need census population (WP7, optional). Without it, count post offices and say so.
- Never show a number that was not computed from the data. No illustrative figures in the final cut.

## Technical constraints

- A static single-page app. No server, so it can be hosted for free and opened from a link in the submission.
- Must work with no network after load: bundle the libraries, do not depend on a map tile service.
- 60 frames per second with 151,000 points. A WebGL point layer (deck.gl or similar) handles this easily; SVG does not.
- What-if recompute happens in the browser: one new site means one pass over the post office array.
- Deterministic playback: the same story produces the same frames every time, so a take can be redone.
- Recording at 1920 by 1080. Check that captions are readable at half size.

## Recording checklist (WP9)

- [ ] Every bracketed value replaced with a real one
- [ ] Autoplay take recorded, plus a manual take
- [ ] Voiceover script timed against the beats
- [ ] 3-minute and 90-second cuts exported
- [ ] Map hosted and the link tested in a clean browser
- [ ] Repo link, with raw data excluded
- [ ] Limits card included
