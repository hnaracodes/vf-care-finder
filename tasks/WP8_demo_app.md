# WP8: Demo app (interactive map)

Size: large (runs the whole night alongside the data work). Depends only on WP0 to start. Feeds WP9.

## Goal

The interactive map described in `docs/09_presentation.md`: evidence slider, patient mode, simulator, quadrant view, a scripted story mode for the recording, and a "how we know" drawer.

Start immediately. Do not wait for real results: build against mock data in the contract's shapes, then swap in real files as they land.

## Inputs

- `docs/09_presentation.md` (what it must do and why)
- `docs/07_data_contracts.md`, "App data" section (file shapes)
- `silver/post_offices.parquet` once WP2 lands; until then read coordinates straight from the raw PIN file with `vf.io`

## Outputs

- `app/`: a static single-page app that builds to plain files
- `scripts/wp8_mock_app_data.py`: writes mock app data with real post office coordinates and plausible fake distances and grades
- A hosted copy and its URL, written into `app/README.md`

## Steps

1. Scaffold the app in `app/`. Suggested: Vite, TypeScript, and deck.gl for a WebGL point layer. Any stack is fine if it meets the constraints below. Bundle dependencies; no CDN at runtime.
2. Mock data first: real coordinates for about 151,000 post offices, random but plausible distances and grades. Mark it clearly as mock in `meta.json` and show a visible MOCK badge in the app whenever `meta.json` says so.
3. Base scene: post office dots colored by distance on a fixed km scale, with a legend. Pan and zoom. Check frame rate.
4. Evidence slider: four stops. Recolors dots and hides facilities below the threshold. Counter for the share within R km.
5. Service line tabs: obstetrics, dialysis, ICU.
6. Patient mode: click a post office, draw lines to the nearest facility per grade with distances. Click a facility to open its evidence panel (quoted facts, tags, number of domains).
7. Quadrant view: dots take their district's quadrant color. Click for the district panel: NFHS numbers, access, residual, how the district was matched.
8. Simulator: click to upgrade or add a site and recompute coverage in the browser; play button for the precomputed greedy sequence; naive versus desert-aware toggle; coverage curve.
8b. Patient swarm: the animated layer specified in `docs/superpowers/specs/2026-11-21-patient-swarm-design.md` (approach A, approved by Hruday, D011). It reuses the simulator's coverage rule and recompute.
9. Story mode: reads `story.json`; spacebar advances; an autoplay flag plays it with fixed timings for a clean recording. Smooth camera moves between beats.
10. "How we know" drawer fed from `meta.json`.
11. Swap in real data when WP7 exports it. The MOCK badge must disappear on its own.
12. Host it (any static host) and record the URL.

## Acceptance checks

- [ ] Loads from a local static server with the network disabled
- [ ] Holds a smooth frame rate while dragging the slider with all points visible
- [ ] Every number on screen is read from the data files; no hard-coded figures
- [ ] MOCK badge shows on mock data and is gone on real data
- [ ] Story mode autoplay runs start to finish without input, identically each time
- [ ] Legends, units, and "est." labels present; quadrant colors are distinguishable without relying on red versus green
- [ ] Readable when the window is recorded at 1920 by 1080 and viewed at half size
- [ ] In-browser coverage numbers match `scenarios.json` for the same radius

## Pitfalls

- Do not add a basemap tile service. The dots are the map (D009).
- Do not let design work outrun the data. A plain map with real numbers beats a beautiful one with mock numbers; the final video must not contain mock data.
- Keep per-frame work off the main path: use typed arrays and update only the attributes that changed.
- Coordinates are lon, lat for most WebGL libraries. Check with one known city before trusting the picture.
