# Patient swarm: design

Status: draft for Hruday's approval. Nov 21, 2026. Size: small change, extends WP7 (simulator) and WP8 (app).

## Intent

Make "care desert" physical. The viewer watches patients leave their post office toward obstetric care. In care deserts they stop short and are stranded. In data deserts they are not really stranded: care likely exists but is not in this data. Upgrading hospitals visibly rescues the right dots, and the naive plan visibly wastes picks on dots that were never truly stranded.

## Goals

- An animated swarm layer in the existing map, driven by the same coverage rule as WP7: a post office is reached if a facility at grade 2 or better (or the slider's grade) for the current service line is within R km straight-line. R = 30 or 60, as in WP7.
- Works with manual upgrades (click a grade 1 facility) and with the greedy playback from `scenarios.json`, naive and desert-aware.
- Shows the care-desert versus data-desert difference in motion.
- A live counter: post offices stranded, before and after. "Est. people" only if WP7 adds census population.
- Deterministic, so a story-mode take can be redone frame for frame.

## Non-goals

- No hospital capacity, queues, arrival rates, or travel time. That is approach B, deferred.
- No road network. Straight-line, as everywhere else in the project.
- No new pipeline model. The swarm only visualizes numbers WP5, WP6 and WP7 already compute.

## Anti-goals (a failure even if it works)

- Dots read as individual people. Each dot is one post office area; the legend says so.
- Data-desert dots look stranded. That would show exactly the claim the project argues against.
- Any on-screen number that does not come from the data files, or that differs from `scenarios.json` for the same radius.

## Approaches

- A, chosen by Hruday: precomputed nearest-facility targets per post office, animated in the browser, recomputed in one pass when a site is upgraded. Cheap, deterministic, consistent with WP7.
- B, deferred: full agent simulation with arrivals over time, bed capacity, and turn-aways. Feels more like a simulation, but capacity would rest on sparse, unverified `n_beds`, which breaks the "no illustrative figures" rule. A is built so B can add a per-tick step later.
- C, folded into A: a close-up vignette of two neighbouring districts. Not built separately; story mode zooms A into a care desert next to a data desert.

## How it behaves

- Origin: each geocoded post office. Target: its nearest facility at the active grade for the active service line.
- Motion: every dot moves at the same on-screen speed in km. A dot whose target is more than R km away travels R km along the line and stops, so stranded dots pile up on a visible ring of reach.
- States and colors (color-blind safe, each with a text label in the legend):
  - reached: arrives, settles at the facility
  - stranded: stops at R km, care desert color
  - care likely exists, not in this data (est.): a stranded dot in a district whose `quadrant_model` is data desert. It stays at its post office and pulses; it does not travel to an invented location. District level on purpose, because NFHS evidence is district level.
- Upgrade: promoting a facility recomputes the target only for post offices within R km of it, then replays just those dots. Counter and coverage curve update.
- Greedy playback: steps through `scenarios.json` one site at a time. The naive plan rescues many "care likely exists" dots (wasted picks); the desert-aware plan rescues stranded ones. That contrast is video beat 9.
- Density: at national zoom, animate a deterministic subset (default 1 in 5, chosen by hash of `po_id`) over the static dot layer; at district zoom, animate all. Builder may tune the ratio for frame rate.

## Data

One addition to the App data contract, which patient mode also needs:

- `po_near_<line>_g<k>.u16`: index into `facilities.json` of the nearest facility at grade k or better, per post office. About 2.7 MB for 3 lines by 3 grades.
- `districts.json` `quadrant` object carries `quadrant_model` and `p_real_gap` (already produced by WP6).

Total app data must stay under about 15 MB. The contract change goes into `docs/07_data_contracts.md` with a decisions-log entry.

## Left to the builder

- How to interpolate on the GPU (time uniform with per-dot origin, target, stop fraction is the suggested way) versus deck.gl TripsLayer.
- Exact colors, speed, easing, and subset ratio, within the presentation rules in `docs/09_presentation.md`.
