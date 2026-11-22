# WP8: The Databricks App

Re-scoped Nov 21 (D023, D032, D033). The old WP8 built an India-wide map (`app/`, Vite, TypeScript, deck.gl) with a patient swarm, simulator and story mode. That app is retired as a product. Its map and swarm code is reused for the small map and the trip view.

Size: large. Can start at once against v0.1 grades, a stub resolver and stub flags. Depends on WP5 for real answers. Feeds WP9.

## Goal

A Databricks App a non-technical user can use in one minute: pick a service, type a place, get three cited cards or an honest "no reliable option nearby", see why, and see it on a small map.

## Architecture (D033)

```
dbapp/                      deployed folder (databricks apps deploy)
  app.yaml                  command: uvicorn server:app, port from DATABRICKS_APP_PORT
  requirements.txt          fastapi, uvicorn, pandas, pyarrow, numpy, scikit-learn, rapidfuzz
  server.py                 FastAPI: /api/services, /api/resolve, /api/answer, /api/trip, static files
  vf/                       copy of src/vf (or an installed wheel), so rank and locate are the tested code
  bundle/                   WP12 output: facilities, grades, flags, places, keyword index (target under 50 MB)
  static/                   prebuilt front end (vite build of app/), committed
app/                        front end source (Vite, TypeScript, deck.gl), rewritten around the query screen
```

The server loads the bundle once at start and answers in process. No SQL warehouse per request. Develop and test locally with `uvicorn` before deploying.

## The screen

1. Top: "What care is needed?" (12 services by plain name, plus "Something else" which takes free text and uses the fallback) and "Where is the patient?" (one box: PIN, city, district, village, or coordinates; examples as placeholder text). One button.
2. Location line, always shown: "Searching around PIN 302001, Jaipur, Rajasthan (precise to about 3 km)". When ambiguous: "Which Aurangabad?" with the candidates as buttons.
3. Three result cards, or the abstention banner. Each card:
   - name, type, public or private, straight-line distance (rounded to the location precision), confidence in words with its reason
   - "Why we think it offers this": the recipe requirements, each with a quoted fact and its field
   - "What we could not find": the missing pieces for the next level up
   - "Be careful": flags in plain words, with the quoted text that triggered them
   - "Score": three small bars (evidence, distance, source trust) and any penalties, each with its number
   - sources: number of web domains, the domains, official website if any; "from this facility's record, compiled from N web sources" (D031)
4. Abstention: "No facility in this dataset shows reliable evidence of dialysis within 50 km of X." Then the nearest reliable option beyond the radius, and "Also nearby, not verified": claim-only facilities with why they are not recommended. Then one line: "This dataset mostly covers private facilities with a web presence. A public hospital nearby may not appear here."
5. "How we decided" panel, collapsed by default: the recipe for this service in plain words, the gates with counts ("812 facilities within 150 km; 37 with supported evidence; 3 shown"), the score formula and weights, the radius and why, the known limits.
6. Small map, to the right of the cards or below them on narrow screens: the searched point with its precision circle, the three results numbered like the cards, unverified nearby facilities as hollow markers, nearby post offices as faint dots for context. No basemap tiles (D009). Clicking a marker highlights its card.

## The trip view (D032, last, cut if late)

On the small map, a "Show trips" toggle animates nearby post offices (up to about 3,000, non-suspect, one per placeholder point) travelling to their nearest recommended facility among the results. A second toggle, "If we trusted claims alone", sends them first to the nearest facility that only claims the service; dots whose target has no supporting evidence then re-route, and a counter shows the extra straight-line km: "412 villages would first go to a facility with no evidence of a C-section, then travel 38 km more on average." `/api/trip` returns the points and both target sets; reuse `app/src/swarm.ts` and `swarm-layer.ts`. Every number comes from the API.

## Steps

1. `dbapp/server.py` with the four endpoints, against v0.1 grades and stubs. `pytest` for the API using FastAPI's test client.
2. Rewrite `app/` around the query screen. Keep `geo.ts`, `colors.ts`, `swarm.ts`, `swarm-layer.ts`; retire the story mode, quadrant view and simulator (leave the files, remove them from the build).
3. Cards, abstention, location line, "How we decided", in that order. Then the small map. Then the trip view.
4. Plain-language pass: read every label as a health worker would. No "grade 2", "emoc", "g3" on screen; use the names in `vf.services`.
5. Deploy to Databricks Free Edition with WP12, test there, record the URL in `dbapp/README.md`.

## Acceptance checks

- [ ] Runs locally with `uvicorn` and on Databricks Free Edition; URL recorded
- [ ] The brief's five scenarios work end to end, each in under 2 seconds
- [ ] Every claim on a card has a quote; every number on screen comes from the API; no hard-coded figures
- [ ] Abstention, ambiguous place, unknown place, fallback search and an outside-India point each show a clear message
- [ ] Usable at 1280 by 800 and at phone width; keyboard reachable; colors not the only signal
- [ ] A person who has not seen it can get an answer for "dialysis near Jaipur" without help (ask Hruday, or describe the test you ran)

## Pitfalls

- Databricks Apps stop 24 hours after the last start or deploy (D034). Restart before judging.
- Do not build the front end on Databricks; ship `static/` prebuilt.
- Keep the bundle small and free of raw data dumps. The facilities table's reuse terms are unchecked; the app lives in Hruday's workspace, not on a public host.
- Show distances rounded to the location precision. "2.3 km" for a PIN-centre location is false precision.
