# App: the interactive map

A static single-page app (Vite, TypeScript, deck.gl) that draws India from about 151,000 post office dots. It has an evidence slider, service line tabs, patient mode, a quadrant view, a simulator with the patient swarm, story mode for the recording, and a "How we know" drawer. Everything is bundled: no CDN, no basemap tiles, no server. It works with the network off.

- What it must do: `docs/09_presentation.md`
- Build plan and acceptance checks: `tasks/WP8_demo_app.md`
- Patient swarm: `docs/superpowers/specs/2026-11-21-patient-swarm-design.md`
- Data it reads: `docs/07_data_contracts.md`, "App data". Files live in `app/public/data/` and are committed.

Hosted URL: not deployed. This run built against mock data and did not host anything.

## Run and build

Needs Node 22 and pnpm.

```bash
cd app
pnpm install          # once
pnpm dev              # http://localhost:5173, live reload
pnpm typecheck        # TypeScript check only
pnpm build            # typecheck, then writes app/dist/ (plain static files)
pnpm preview          # serves app/dist at http://localhost:4173
```

`app/dist/` is self-contained, with the data copied in. Any static server works, for example `python3 -m http.server 8000 --directory app/dist`. Opening `index.html` from the file system does not work, because browsers block `fetch` from `file://`.

## Using it

- Tabs: service line (Obstetrics, Dialysis, ICU) and view (Distance, Care or data gap, Simulator).
- Evidence slider: Any facility, Claimed, Supported, Corroborated. Recolours the dots on the fixed km scale and hides facilities below the stop. The counter gives the share of post offices within the chosen radius (30 or 60 km).
- Distance view: click a dot for one patient's trip (lines to the nearest facility at each level, with km). Click a facility, or a row in the panel, for its quoted evidence, tags and number of independent web domains.
- Care or data gap view: dots take their district's quadrant colour. Toggle model or thresholds. Click a dot for the district panel.
- Simulator view: patients leave their post office for the nearest facility at the slider's grade. Click a facility to upgrade it, or click empty space to add a site. "Play 10 picks" replays the greedy plan from `scenarios.json`, naive or desert-aware. The panel checks every pick against `scenarios.json` and shows "match" or "MISMATCH".
- Keys: `Space` starts story mode or goes to the next beat, `ArrowLeft` goes back a beat, `Escape` leaves story mode, and `f` toggles a frame rate meter.

## Story mode and recording

- `?story` starts story mode; `Space` advances.
- `?autoplay` plays every beat with the durations in `story.json`. The app clock moves in whole 1/60 s steps, colour transitions are off, and the swarm's start offsets come from a hash of each dot's index. Every take is therefore identical (checked with `__vf.simulateStory()`, below).
- `story.json` is the script. Each beat sets `camera`, `view` (`distance`, `quadrant`, `swarm`), `grade`, optional `line` and `radius_km`, `caption`, and optional actions: `pulse` or `patient` (`{lon, lat}`, snapped to the nearest post office), `sweep` (slider stops to step through), `show_counter_change`, `highlight` (a quadrant), `panel` (`drawer`, `lists`, `limits`), and `scenario` (`{plans: [...], steps}`).
- The current `story.json` is a mock draft written by `scripts/wp8_mock_app_data.py` from the beats in `docs/09_presentation.md`. Bracketed values such as `[N]` are placeholders. WP9 owns this file: replace every bracket with a real number before recording.

Recording checks: record at 1920 by 1080. Captions are about 35 px. The MOCK badge must not be visible in the final take.

## Swapping in real data

WP7's export (`scripts/wp7_export_app_data.py`) replaces the mock files in `app/public/data/`. Then rebuild with `pnpm build`. Nothing in the code changes. The MOCK badge disappears on its own when `meta.json` has `"mock": false`, or no `mock` key.

What the app expects, beyond the contract table:

- `meta.json`
  - `binary`: a list of `{file, dtype, length}` for every binary file. `dtype` is `float32` or `uint16`, and `length` is the number of post offices. The app loads only what is listed and checks each length.
  - `service_lines`: optional, `[{key, label, long}]`.
  - `grades`: optional, `[{k, label}]`.
  - `radii_km`: the radius buttons, for example `[30, 60]`.
  - `simulator`: optional, `{min_grade, upgrade_to_grade, earth_radius_km}`.
  - `no_index`: the u16 sentinel, default 65535.
  - `counts`: for the drawer, `{facilities, facilities_private_share, post_offices_total, post_offices_geocoded, districts, nfhs_districts, districts_matched: {method: n}}`.
  - `models.fact_tagger`: `{gold_set: {n_facts, labeled_by}, systems, per_tag: [{tag, regex: {p, r, f1}, ...}]}`.
  - `models.outcome_model`: `{target, cv_scheme, metric, without_access: {mean, sd}, with_access: {mean, sd}}`.
  - `limits`: a list of strings.
  - `generated_at`, `git_commit`, `versions`.
- `po_dist_<line>_g<k>.f32` and `po_near_<line>_g<k>.u16` for k = 1 to 3. `po_near` indexes into `facilities.json`; 65535 means no facility at that grade. The slider's "Any facility" stop is computed in the browser from `facilities.json`, so it needs no file.
- `facilities.json`: as in the contract. Optional `tags: {emoc: [...], ...}` fills the evidence tags in the facility panel.
- `districts.json`: `quadrant` is keyed by service line. Each entry is `{quadrant_rule, quadrant_model, p_real_gap, access_value, outcome_value, outcome_flag, predicted_outcome, residual}`, or `null` when the district is not scored. Quadrant strings are matched loosely: anything containing "care", "data", "cost" or "served". `nfhs` holds the selected indicators, and `nfhs_flags` (optional) holds their flags. `access` is keyed by line, as `{min_grade, dist_km_p50, share_within_30km, share_within_60km, n_post_offices}`.
- `scenarios.json`: `{earth_radius_km, upgrade_to_grade, settings: [...]}`. Each setting is `{service_line, radius_km, min_grade, n_post_offices, baseline_covered, baseline_coverage, naive: [...], desert_aware: [...]}`. Each step is `{step, kind, facility_id | lat+lon, marginal_gain, newly_covered, covered, cumulative_coverage, district_key, quadrant_model}`. `covered` (an integer) lets the app check each pick exactly. Without it, the app compares `cumulative_coverage` within half a post office.
- `story.json`: hand-authored by WP9. The export must not overwrite it.

Coverage rule, which must stay identical in `src/geo.ts`, `src/sim.ts` and WP7: haversine with earth radius 6371.0088 km (operation order as in `haversineKm`). Distances are rounded to float32. A post office is covered when `dist <= R`. An upgrade sets the site to `upgrade_to_grade` (3). One upgraded or new site is one pass over the post office array.

Total app data must stay under about 15 MB (the mock set is 13.8 MB).

## Checks you can run in the browser console

- `__vf.checkScenarios()`: replays every scenario step in the browser and returns mismatches per setting and plan (all 0 on the mock set).
- `__vf.simulateStory()`: runs the whole autoplay take without drawing and returns the state at each beat boundary. Two calls after reloads must match.
- `__vf.seekStory(seconds)`: jumps the take to a time and freezes it, for screenshots.
- `__vf.fps()`: recent frame rate.

## Code map

- `src/data.ts`: loaders and types for every app data file
- `src/geo.ts`: haversine, great-circle interpolation, nearest-point grid
- `src/sim.ts`: in-browser simulator (upgrade, add site, coverage)
- `src/swarm.ts` and `src/swarm-layer.ts`: patient swarm state, and a GPU layer that interpolates every dot from one time uniform
- `src/app.ts`: state, deck.gl layers, picking, camera, simulator panel
- `src/story.ts`: story mode, autoplay, trace and seek
- `src/ui.ts`: panels, legend, drawer and lists (labels only; all numbers come from the data)
- `scripts/wp8_mock_app_data.py` (repo root): writes the mock data set
