# Presentation: the app and the demo

Re-scoped Nov 21 (D023). The earlier version (an India-wide map with an evidence slider, quadrants and a simulator, driven by a story mode) is in git history at `7609dbb`.

The product is the Databricks App (WP8). Any video is a screen recording of real queries on the deployed app, with Hruday's voiceover. Everything here serves one viewer question: "Would I trust this answer for my patient?"

## What the viewer must see

Each beat carries one point from the brief. Cut anything that does not.

1. The tool answers the brief's own questions, in plain words, in seconds.
2. Every claim on a card is a quote from the facility's record.
3. The tool says when it is unsure, and why: confidence labels, flags, location precision.
4. When the evidence is weak, it says "no reliable option nearby" instead of guessing.
5. How it decided is one click away: recipe, gates, score parts, limits.
6. The trip view shows what a confident wrong answer costs: the detour when the nearest claim has no evidence behind it.

## Draft demo beats (about 3 minutes)

Queries come from the WP5 scenario suite, never invented for the camera. Replace every bracketed value with the real one from the app; do not record with placeholders.

| # | Time | On screen | Voiceover idea |
| --- | --- | --- | --- |
| 1 | 0:00 | Empty app. Type "Emergency C-section", "Lucknow" | A woman near Lucknow needs an emergency C-section. Where should she go, and why should we believe it? |
| 2 | 0:15 | Three cards appear; open the first | The top result quotes its own record: [quote]. We list every piece of evidence, and what we could not find. |
| 3 | 0:40 | "Score" bars, then "How we decided" | Evidence counts most, then distance, then how many independent sites back the record. Here is every step from [N] facilities to three. |
| 4 | 1:00 | Small map, "Show trips", then "If we trusted claims alone" | If you trust specialty labels alone, [k] nearby villages go first to a place with no evidence of a C-section, and travel [x] km more. |
| 5 | 1:25 | "Dialysis", a village in rural Bihar | The brief's harder case. [What happens: cards with low confidence, or an abstention.] |
| 6 | 1:45 | Abstention banner and "Also nearby, not verified" | No facility in this dataset shows reliable evidence of dialysis within 50 km. Here is the nearest one that does, and the claims we could not verify. |
| 7 | 2:05 | A card with a flag: merged record or location conflict | This record may combine two hospitals: [quote]. We say so on the card and lower its rank. |
| 8 | 2:25 | "Something else": a free-text service | Services outside our twelve recipes still get an answer, marked as a keyword match with low confidence. |
| 9 | 2:40 | Limits in the "How we decided" panel; the Databricks URL | What it cannot tell you: straight-line distance, a mostly private sample, web pages that may be stale. It runs on Databricks Free Edition. |

90-second cut: beats 1, 2, 4, 6, 7, 9.

## Visual language

- One screen. Inputs at the top, cards on the left, small map on the right; stacked on narrow screens.
- Confidence in words first ("High confidence", "Low confidence: one web source"), color second. Never color alone.
- Quotes look like quotes: a distinct type style and the field name beside each ("procedure").
- Flags use one warning style and always say what triggered them.
- Numbers have units; distances say "straight-line km" once per card and are rounded to the location precision.
- Large type; it will be watched at half size.

## The small map

- No basemap tiles (D009). Context comes from nearby post offices as faint dots.
- The searched point with a precision circle; results as numbered markers matching the cards; unverified facilities as hollow markers.
- Trip view (D032): dots travel along straight lines to their nearest recommended result; the "trust claims alone" toggle shows the detour and a counter. Deterministic playback so a take can be redone.

## Honesty rules for the screen

- Say "no facility in this dataset shows evidence of", never "there is no" or "does not have".
- Distances are straight-line km, never travel time.
- Citations name the fact and its field, not a web page (D031).
- Never show a number that was not computed by the app or a report. No illustrative figures.
- Say the gold set and the flag reviews were agent-labeled.

## Recording checklist (WP9)

- [ ] App restarted shortly before recording (D034); URL tested in a clean browser
- [ ] Every bracketed value replaced with a real one from the app
- [ ] Every number in the script is in `reports/findings.md`
- [ ] Voiceover timed against the real app
- [ ] 3-minute and 90-second cuts exported
- [ ] Limits shown on screen
- [ ] Repo link, with raw data excluded
