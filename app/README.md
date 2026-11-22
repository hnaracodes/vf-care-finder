# App: the query screen

The front end of the Databricks App (`tasks/WP8_databricks_app.md`). A health worker picks a service and types a place. The screen shows up to three facilities with quoted evidence, or "No reliable option nearby", plus a "How we decided" panel and a small map with a trip view. Vite, TypeScript, deck.gl. No basemap tiles, no CDN.

The India-wide map (story mode, quadrants, simulator) is retired (D023). Its sources are in `src/legacy/`, excluded from the build and the typecheck. `geo.ts`, `colors.ts`, `swarm-layer.ts` are reused; `swarm.ts` is rewritten as a route swarm on the same approach.

## Run and build

Needs Node 22 and pnpm.

```bash
cd app
pnpm install
pnpm dev          # http://localhost:5173; /api goes to uvicorn on 127.0.0.1:8000 if it runs
pnpm typecheck
pnpm build        # typecheck, then writes ../dbapp/static/ (committed, served by dbapp/server.py)
```

The build uses a relative base, so it works at the root of the Databricks App URL or under a prefix. `app/public/data` (retired India map data, D024) is not copied: `publicDir` is off.

## No fixtures in the code path

Every answer comes from `dbapp/server.py`, which runs `vf.rank`, `vf.locate` and `vf.services` on the bundle. There is no fixture, mock or stub path in the front end or the server: if the server does not answer, the screen says so. `src/fixtures/` and `scripts/make_fixtures.py` are kept as history only; they are excluded from the typecheck and the build and nothing imports them.

Run the server first for development: `cd dbapp && ../.venv/bin/python server.py` (port 8000), then `pnpm dev`.

## URL

`?service=dialysis&q=Jaipur` runs a query on load (also `text=` for "Something else", `place_id=` for a chosen candidate). Use it to redo a recording take. `window.__vf.seek(t)` freezes the trip animation at time `t` seconds and `window.__vf.settle()` jumps to its end; playback is frame-stepped and seeded by a hash of each dot's index, so every take is identical.

## API the screen calls

All endpoints are `GET` and take the same parameters: `service` (one of the 12 ids, or `other`), `text` (free-text service, with `other`), `q` (what was typed as the place), `place_id` (optional, a candidate picked after an ambiguous place). Errors may return `{"detail": "..."}`; the screen shows that text.

| Endpoint | Returns |
| --- | --- |
| `/api/services` | `{"services": [{id, label, radius_km, recipe_text, graded}], "fallback": {...}, "simulate_available": bool}`. The screen calls `/api/simulate` only when `simulate_available` is true |
| `/api/resolve?q=` | `Resolution` (WP2): `status` (`ok`, `ambiguous`, `not_found`, `outside_india`), `label`, `how`, `lat`, `lon`, `precision_km`, `kind`, `place_id`, `candidates: [{label, place_id, kind, lat, lon, precision_km}]` |
| `/api/answer` | `vf.rank.Answer` and `Card` as JSON, unchanged (plus `timing_ms`). The screen reads `Card.parts` (`raw` 0 to 1 values, `weights`, weighted contributions, `combine`), `Card.flags`, `Card.sources.also_listed_as`, and in `how_we_decided`: `headline`, `recipe`, `gates: [{gate, rule, n, of, n_blocked}]`, `formula`, `weights`, `penalties`, `tie_rule`, `radius_km`, `reach_km`, `score_status`, `notes`, `limits` |
| `/api/trip` | `{status, service, radius_km, facilities: [{facility_id, name, lat, lon, kind: "recommended" or "claim_only", rank}], points: [[lon, lat]], nearest_recommended: [int], nearest_claim: [int], summary: {n_points, n_first_unsupported, extra_km_mean}, notes: [str]}`. Indexes point into `facilities`, -1 for none. `nearest_claim` is the nearest facility that claims the service at all (recommended or not). `status: "unavailable"` hides the trip toggles |
| `/api/simulate` | `{status: "ok", service, n_origins, policies: [{policy, label, expected_km, share_second_trip}], facilities: [{facility_id, lat, lon, p_offers}], origins: [[lon, lat]], orders: {policy: [[facility index, ...] per origin]}, notes}`. Anything else, or an HTTP error, keeps "Simulate arrivals" hidden |

Every number on screen comes from these responses. The front end adds words only: confidence labels, field names, and distance rounding. A card's distance is the straight line from the searched point, rounded to the facility's location precision (anything inside it reads "within about N km"). The searched point's own precision is stated once, in the location line, with a prompt to type a PIN or town when it is 10 km or more.
