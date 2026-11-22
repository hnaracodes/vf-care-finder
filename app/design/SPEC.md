# Build spec: "Answer first" results, real-geography map, agent simulator with what-if tools

Design lead's spec, Nov 22 2026. Four engineers build it in parallel: A (results UI), B (map), C (server payload), D (agent simulator and what-if tools). Section 3 is the JSON contract between C and A, B and D. Section 2 says which files each engineer owns. Do not edit files you do not own.

## 0. Decision

**Chosen design:** "Answer first: the referral note". It was the unanimous winner: all three judges scored it highest on clarity and trust, and it is the only direction that follows v1-final everywhere (minutes first, window in hours, nearest first with the allowance).

**What we take from the other designs:**

- From Map story:
  - an on-map sim bar with the title, the two-mode switch, the rule sentence, Play/Pause/Replay, a progress bar, a clock and "1 s = N min";
  - agents shown as heads with short trails and nothing drawn ahead of them;
  - agents that turned after a what-if edit drawn in purple, with the legend line "On the road when you made the change, so it turned";
  - the "next choice" rule applied once;
  - the per-village state factor;
  - the filter for the fake latitude 15.59 line;
  - verify-first as a top-3 list with Try buttons;
  - signed deltas;
  - the phone bottom sheet with a 4-button tool row;
  - one labelled marker at country zoom, and a zoom ladder.
- From Compare:
  - the striped purple "WHAT-IF VIEW · n edits · not real data" banner, with Undo and Reset;
  - lettered grey diamonds (A, B, C ...) in "Why not the nearer ones?" that match marks on the map;
  - "Real data | What-if | Change" counter columns;
  - the sentence "The gap here is distance, not checking.";
  - the data-gap funnel on the abstention screen;
  - the LaQshya service filter;
  - a beyond-window card listed only once;
  - the state taken from the map position.
- Fixes asked for by the judges:
  - The live tally is labelled as patient dots, so its unit cannot be confused with the villages in the takeaway.
  - Trust-claims mode shows no pre-drawn fan of spokes.

**Hruday's question: does today's simulation let you add or fix a nearby hospital? No.**

- `/api/trip` (`dbapp/server.py` `build_trip`) works out one fixed destination per post office on the server (`nearest_recommended`, `nearest_claim`). The browser only animates those fixed tracks (`app/src/swarm.ts` `RouteSwarm`).
- There is no verify, add or close. Nothing re-decides.
- `/api/simulate` is switched off, because it needs probabilities (D045).

This spec replaces that with the following:

1. One `/api/sim` payload per query, holding the world: villages, facilities with their evidence status, the window and the allowance.
2. Patient agents in the browser that decide at run time by a stated rule, and decide again while travelling when the world changes.
3. What-if tools to verify a record, add a facility or close one, with live before/after counters, undo and reset.
4. "Which record should be verified first?"

## 1. Non-negotiables (every engineer)

**Wording**
- Plain words. No em-dashes.
- Never write these on screen: probability, likely, calibrated, chance, confidence score, grade 2, emoc, corroborated, supported, capability, recommendable, source trust, score.
- Never write "there is no care", "does not have" or "cannot do". Write "no clear evidence of X in this dataset" or "we found no fact naming X".
- The only conditional allowed is the counterfactual "if that place cannot help".

**Numbers**
- Every number on screen comes from `/api/answer` or `/api/sim`, or from the browser's run of the stated rules on the `/api/sim` payload. No constants that look like data, and no mock numbers.
- Minutes when the card has them, km as the fallback. A card on a shared PIN point says "position approximate" and never shows a number of km for itself.

**Colours.** Each colour has one meaning. Use these tokens in `app/src/colors.ts`. A adds them; B and D import them.

| token | hex | meaning |
|---|---|---|
| `INK` | `#2b2620` | text |
| `PAPER` | `#f5f0e6` | page and India land |
| `SURROUND` | `#d9d4ca` | sea and neighbouring countries on the map |
| `EVIDENCE` | `#17684f` | the record shows the service: pins, quote marks, the answer rule, evidence agents |
| `CLAIM` | `#a95f00` | lists or claims it, but its record shows no evidence: diamonds, first-trip agents, the hold ring, the abstention rule |
| `WHATIF` | `#6a3ea1` | what-if only: the tag, the banner, + pins, turned agents, after-values. Never used for real data |
| `NEUTRAL` | `#8c8478` | nearer records not recommended (grey diamonds), with no strike-through |
| `ORIGIN` | `#c0392b` | the searched place only |
| `BORDER` | `#9a7b55` | state borders |

**Fonts and offline**
- Serif (`"Iowan Old Style", Palatino, Georgia, serif`) for the answer sentence, quotes and takeaways. System sans for the UI.
- No web fonts, no tile service (D009) and no new npm dependencies. `@deck.gl/extensions` is not installed: solid lines only, and draw dashes in JS where one is required.

**Masking**
- Phones are always shown masked (`call.phone`). `tel:` links only appear when the server sends `call.tel`.
- Names in "Why not the nearer ones?" and in the simulator's list of facilities that are not cards are masked when the server says `demo_masked: true`.

**Running and working**
- Run the server on your own port: A 8851, B 8852, C 8853, D 8854:
  `/Users/hrudaynara/Projects/CMU Datathon/.venv/bin/python -m uvicorn server:app --app-dir "/Users/hrudaynara/Projects/CMU Datathon/dbapp" --port <PORT>`
- Front-end dev: `VF_API_PORT=<PORT> pnpm dev` (A adds `VF_API_PORT` to `vite.config.ts`, defaulting to 8000).
- Do not commit. Save screenshots to `reports/figures/ui/build_<A|B|C|D>_<name>.png`.
- Stop your server when done.

## 2. File ownership

| Engineer | Owns (may create and edit) | Must not touch |
|---|---|---|
| **A** results UI | `app/index.html`, `app/src/main.ts`, `app/src/render.ts`, `app/src/text.ts`, `app/src/types.ts`, `app/src/api.ts`, `app/src/style.css`, `app/src/colors.ts`, `app/vite.config.ts` | map and sim files |
| **B** map | `app/src/minimap.ts`, which becomes a thin re-export of `app/src/map/index.ts`; new `app/src/map/*.ts`; `app/src/map/map.css`; new `app/src/assets/geo/*`; new `app/scripts/build_geo_assets.py` | `swarm*.ts`, `app/src/sim/*` |
| **C** server | `dbapp/server.py`, new `dbapp/sim_payload.py`, `tests/test_server.py`, new `tests/test_sim_payload.py` | `src/vf/*` (rank, services, locate are owned by others; questions, why_not and public_list are called, not edited), `dbapp/vf/*`, `dbapp/bundle/*`, `scripts/wp12_bundle.py` |
| **D** simulator | new `app/src/sim/*.ts`, `app/src/sim/sim.css`, `app/src/swarm-layer.ts`, `app/src/swarm.ts` (to retire) | results and map files |

Shared-file rules:

- `types.ts`, `api.ts` and `colors.ts` belong to A.
- **A's first task (15 minutes, before anything else):** paste section 3.4 into `types.ts` verbatim, add `api.sim()` and the colour tokens, and tell the others. B and D import from there.
- B and D need a type that is not in 3.4? Ask A. Do not edit A's files.
- `vite.config.ts` has `publicDir: false`. Geo assets are therefore imported with `?url` from `app/src/assets/geo/`. Vite emits them as files, because `assetsInlineLimit: 0` is set.

## 3. The contract (C produces, A, B and D consume)

### 3.1 `/api/answer` additions

Everything already in the answer stays, with the same names. C adds the fields below after `rank.rank` / `rank.search` returns. All fields are optional on the TS side. A falls back gracefully when one is absent, for example when the v0 ranker runs.

Answer level:

| field | type | how C builds it |
|---|---|---|
| `origin` | `{short_name, label, precision_km, loose, too_coarse, state_display}` | `short_name` = `rank._place_name(location)` (for example "Lucknow", "PIN 226001", "Jokihat"). `loose` = `window.origin_loose`. `too_coarse` = `precision_km > window.km_equivalent`, which hides the number on screen. `state_display` = title-cased `location.state` |
| `window.hours_text` | string | `vf.travel.fmt_hours(window.minutes)`: "1 hour", "2 hours" |
| `window.reach_text` | string | `vf.travel.fmt_minutes(window.reach_minutes)`: "6 h" etc. |
| `why_not` | strip dict | `why_not.why_not_strip(idx, line, res, ans, mask_names=DEMO)`. Then add `letter` ("A", "B", ...) to each item in order. Set `in_sim: true` when the item's `facility_id` is in the `/api/sim` facility set with status `claim_only` or `held_back` (same mask, see 3.2) |
| `funnel` | `{steps:[{step,label,n}], consistency}` | `why_not.funnel(idx, line, res, answer_dict=ans, max_ids=0, mask_names=DEMO)`, with the `dropped[].ids` lists removed so the payload stays small |
| `outside_nearby` | list or absent | only if `vf.public_list.annotate_answer` exists (v2, D055); pass it through unchanged |
| `demo_masked` | bool | `DEMO = os.environ.get("VF_DEMO_MODE") == "1" or request param demo=1`. The param can only turn masking on, never off |
| `further_away` | list | the existing list, minus any card whose `facility_id` equals `beyond_radius.facility_id`. Raiganj is listed once |

Per card (in `results`, `beyond_radius` and `further_away`):

| field | type | how |
|---|---|---|
| `name_short`, `name_rest` | string, string or null | split `name` at the first comma: "Phular Superspeciality Hospital" / "Road No-02, Juran Chappra, Muzaffarpur - 842001" |
| `time` | `{short, range, loose}` or null | null when `minutes` is null (sea). `short` = "about " + `fmt_minutes(minutes)`. `range` = `fmt_minutes(minutes_low)` + " to " + `fmt_minutes(minutes_high)`. `loose` = `window.origin_loose`. When loose, `short` becomes the range, for example "1 min to 45 min" |
| `state_display` | string | title-cased `idx.state_at(card.lat, card.lon)`, falling back to `card.state`. This fixes "Uttar Dinajpur" being shown as the state |
| `crosses_state` | bool | `state_display != origin.state_display` |
| `quotes` | `[{text, field, requirement_keys[], marks:[[s,e]]}]` | `card.evidence` merged by normalised quote text, keeping the first item's order and taking the union of the requirement keys. A requirement key is the `questions.REQUIREMENT_PHRASE` key that the requirement string starts with, for example "caesarean section (WHO 2009 ...)" gives "caesarean section". `marks` are the spans matched by `why_not.literal_pattern(line)` (and by every requirement's tag words, if the pattern helper exposes them), in the quote text after `questions.mask_numbers` |
| `missing_phrases` | string[] | `REQUIREMENT_PHRASE[key]` for each `card.missing`, for example "a blood bank or blood transfusion" |
| `questions` | `[{question, why}]` | `questions.questions_for(card, line, infos.get(fid), max_questions=3)`. For search cards pass `service_text=query`. Drop `source` |
| `call` | `{has_phone, phone, tel, note}` | `questions.call_info(fid, BOOK, reveal=not DEMO)` |
| `official_domain` | string or null | the host of `sources.official_website` without "www.". It is null when the host is a link shortener or a map (goo.gl, maps.app.goo.gl, maps.google.*, google.com/maps, bit.ly, tinyurl) |
| `public_list` | line or null | `PUBLIC.annotate(ans)` (v1). **Then set it to null unless `service == "emoc"`**, because LaQshya certifies maternity OT and labour room only |
| `outside_lines` | list or absent | v2 `annotate_answer`, if present |
| `sim_index` | int or null | this card's index `i` in `/api/sim` `facilities` |

Cache `record_info`, the phone book and the public list at startup. They do not load per request. The added work must keep the existing time limit in `test_brief_scenarios_answer_in_time`.

### 3.2 `/api/sim` (new)

```
GET /api/sim?service=&q=&state=&place_id=&demo=
```

The parameters and errors are the same as `/api/trip`. For `service=other` or an unresolved place it returns `status: "unavailable"` with a plain `reason`. It is built by `dbapp/sim_payload.py:build_sim(s, service, res, ans, demo)` and must finish in under 300 ms on the bundle.

**Villages:**

1. Start from the post office points in `idx.post_offices`.
2. Drop `placeholder` rows.
3. Drop artefact latitude rows: any exact `lat` value shared by 100 or more rows. Today these are 15.59 and 15.5934, about 745 rows, which draw a fake line across India.
4. Drop rows without lat or lon, and collapse duplicate (lat, lon) points.
5. Keep points within `SIM_VILLAGE_KM = 50` km of the origin, nearest first, capped at 3000.
6. Each village's terrain factor is `vf.travel.state_factor(idx.state_at(lat, lon)).value`. When that is None (sea), use the origin's `window.factor`. When that is also None, the payload says `status: "unavailable"`, reason "Drive times are not modelled for island origins."

**Facilities:**

1. Compute `d = rank._distances(idx, lat, lon)`.
2. Take `g = rank.gate_masks(idx, line, d, res)`.
3. The set is `g["in_reach"]`, which is exactly the funnel's `claim_within_reach` step, minus `idx.dup_drop` positions (duplicate listings of one place are merged into the preferred record), sorted by `d`, capped at 2500.
4. `status`:
   - `evidence` when `g["rec_mask"][p]`;
   - `held_back` when grade is 2 or more and the record is blocked;
   - otherwise `claim_only`.
5. `strong` = status is evidence and grade is 3 or more. A strong record gets the allowance.
6. `approximate` = `idx.approx[p]`.
7. Masked names: when `demo` is on and the facility is not a card, use `why_not.masked_name(idx, p)`.

**Counters (server reference).** C computes the baseline with exactly the rules in section 7.3. D's browser code must reproduce the same values from the same payload. That parity is the acceptance test that ends the 22 vs 23 and 27 vs 30 disagreements.

Expected values (probe on the current bundle, Nov 22 01:10; the test checks parity, not these constants):

| case | villages | within window | average min | first stop no evidence |
|---|---|---|---|---|
| Lucknow 226001 C-section | about 411 | about 411 | about 27.4 | about 353 |
| Jokihat dialysis | about 173 | about 11 | about 94 | about 23 |
| Jaipur dialysis | about 234 | about 229 | about 27.5 | about 110 |
| Kotputli trauma | about 323 | about 323 | about 37.2 | about 130 |

`/api/trip` stays as it is (shape, tests). The UI stops using it.

### 3.3 `/api/sim` response

```json
{
  "status": "ok",
  "version": "sim_v1",
  "service": "emoc", "service_label": "Emergency C-section and high-risk delivery",
  "service_phrase": "emergency C-section and high-risk delivery",
  "origin": {"lat": 26.8497, "lon": 80.9440, "short_name": "PIN 226001", "label": "PIN 226001, Lucknow, Uttar Pradesh",
             "precision_km": 2.0, "state_display": "Uttar Pradesh", "factor": 1.01},
  "window": {"minutes": 120, "allowance": 15, "hours_text": "2 hours", "km_equivalent": 108.3,
             "reach_km": 399.3, "reach_minutes": 360, "basis_short": "<first sentence of window.basis>"},
  "travel": {"a": 2.30, "b": 0.842, "text": "<vf.travel.MODEL_TEXT>"},
  "villages": {"n": 411, "radius_km": 50,
               "lon": [..], "lat": [..], "pin": [".."], "factor": [..]},
  "facilities": [
    {"i": 0, "facility_id": "d93e...", "name": "BNK Hospital", "lat": 26.86, "lon": 80.97,
     "status": "evidence", "strong": true, "grade": 3, "approximate": false,
     "operator": "private", "km_from_origin": 3.5, "card_rank": 1, "why_not_letter": null, "name_masked": false}
  ],
  "summary": {"n_villages": 411, "within": 411, "avg_minutes": 27.4, "n_no_evidence_in_reach": 0,
              "first_no_evidence": 353, "first_claim_only": 340, "first_held_back": 13,
              "extra_minutes_mean": 6.9,
              "n_facilities": 464, "n_evidence": 47, "n_claim_only": 400, "n_held_back": 17,
              "n_listing_records": 469, "n_merged_duplicates": 5},
  "context": {"labels": [{"name": "Lucknow", "kind": "district", "lat": 26.85, "lon": 80.95, "prio": 1}]},
  "notes": ["Straight-line paths; times from the drive-time estimate, free-flow.",
            "Each village is a post office area; offices sharing one point are one village."],
  "timing_ms": 41.0
}
```

The numbers in this example are placeholders for the shape only, never for display.

- **`card_rank`**: the facility's 1-based position in `results`, or for `beyond_radius` 1 when there are no results; otherwise null.
- **`why_not_letter`**: matches `answer.why_not.items[].letter`.
- **`context.labels`**: up to 120 names for map labels near the origin, from `places.parquet`:
  - every `district` and `city` place within 250 km of the origin (prio 1 for the origin's own district, 2 otherwise);
  - post office places within 60 km, thinned to one per 6 km grid cell by keeping the largest `n_offices` (prio 3);
  - names title-cased.
  B adds the India-wide state and district labels from its own asset.

### 3.4 TypeScript (A pastes this into `types.ts`)

```ts
export interface TimeText { short: string; range: string; loose: boolean }
export interface Quote { text: string; field: string; requirement_keys: string[]; marks: [number, number][] }
export interface CallInfo { has_phone: boolean; phone?: string | null; tel?: string | null; note?: string | null }
export interface Question { question: string; why?: string }
export interface PublicLine { text: string; source?: string; validity?: string; current?: boolean; outside_data?: boolean }
export interface OutsideLine { source_id: string; source_name: string; status: 'current' | 'lapsed' | 'undated'; text: string }
// Card gains (all optional):
//   name_short, name_rest, time: TimeText | null, state_display, crosses_state, quotes: Quote[],
//   missing_phrases: string[], questions: Question[], call: CallInfo, official_domain: string | null,
//   public_list: PublicLine | null, outside_lines: OutsideLine[], sim_index: number | null,
//   minutes, minutes_low, minutes_high, minutes_label, distance_label, position_approximate,
//   window_min, inside_window, level_note   (v1 ranker fields already on the wire)
export interface WhyNotItem {
  letter: string; facility_id: string; name: string; name_masked: boolean; operator?: string | null;
  lat: number | null; lon: number | null; distance_km: number | null; distance_label: string;
  position_approximate: boolean; reason_code: string; reason_plain: string; tone: string; in_sim?: boolean;
  quote_or_absence?: { kind: 'quote' | 'absence'; text: string; field?: string; negated?: boolean } | null;
}
export interface WhyNot { items: WhyNotItem[]; n_nearer: number; n_approx_nearer: number; heading: string; masked: boolean }
export interface FunnelStep { step: string; label: string; n: number }
export interface Origin { short_name: string; label: string; precision_km: number | null; loose: boolean; too_coarse: boolean; state_display: string }
export interface Window { minutes: number; allowance: number; hours_text?: string; reach_text?: string; km_equivalent?: number;
  reach_km?: number; basis?: string; note?: string; origin_loose?: boolean; kind?: string }
// Answer gains: origin?: Origin; window?: Window; why_not?: WhyNot; funnel?: { steps: FunnelStep[] };
//   further_away?: Card[]; outside_nearby?: unknown[]; demo_masked?: boolean; rank_version?: string

export type FacStatus = 'evidence' | 'claim_only' | 'held_back';
export interface SimFacility { i: number; facility_id: string; name: string; lat: number; lon: number; status: FacStatus;
  strong: boolean; grade: number; approximate: boolean; operator?: string | null; km_from_origin: number;
  card_rank: number | null; why_not_letter: string | null; name_masked: boolean }
export interface SimSummary { n_villages: number; within: number; avg_minutes: number | null; n_no_evidence_in_reach: number;
  first_no_evidence: number; first_claim_only: number; first_held_back: number; extra_minutes_mean: number | null;
  n_facilities: number; n_evidence: number; n_claim_only: number; n_held_back: number;
  n_listing_records: number; n_merged_duplicates: number }
export interface SimLabel { name: string; kind: 'district' | 'city' | 'post_office'; lat: number; lon: number; prio: number }
export interface SimPayload {
  status: 'ok' | 'unavailable'; reason?: string; version: string;
  service: string; service_label: string; service_phrase: string;
  origin: { lat: number; lon: number; short_name: string; label: string; precision_km: number | null; state_display: string; factor: number };
  window: { minutes: number; allowance: number; hours_text: string; km_equivalent: number; reach_km: number; reach_minutes: number; basis_short?: string };
  travel: { a: number; b: number; text: string };
  villages: { n: number; radius_km: number; lon: number[]; lat: number[]; pin: string[]; factor: number[] };
  facilities: SimFacility[]; summary: SimSummary; context: { labels: SimLabel[] }; notes: string[];
}
```

`api.ts` gets `export const sim = (q: Query) => get<SimPayload>('sim', params(q));`. `main.ts` fires `answer` and `sim` in parallel for each query. An error from `sim` must never block the answer.

## 4. Results layout (A)

### 4.1 Laptop, 1280x800

The page is a CSS grid, `.layout`, with these areas:

```
header    (one line, full width)
origin    (one line, full width)
note      | map      <- map column 600px, sticky top 12px, spans note+backups+folds+footer rows
backups   | map
folds     | map
footer    | map
```

- **Header, one line:** the brand "Find care you can trust", then the chips "Care: Emergency C-section" and "Near: PIN 226001, Lucknow", then a [Change] button that reopens the form.
  - Before the first search, and after Change, the full form and the example chips show instead.
  - After a search the form collapses into the chips.
- **Origin line:**
  - Default: "From the middle of {origin.label} (good to about {round(precision_km)} km)."
  - When `origin.loose` is true, the line is tinted amber-light with ink text: "The patient could be anywhere within about {round(precision_km)} km of the middle of {short_name}. Type the patient's PIN for one drive time."
  - When `origin.too_coarse` is true, it reads: "This place is large, so drive times below are ranges. Type the patient's PIN for one drive time." Do not show the km number (this covers the Kotputli 163 km resolver bug).
  - For coordinates: "From the point you gave."
- **The note** (the answer card): white on paper, 5px left rule in `EVIDENCE`, or `CLAIM` on an abstention. See 4.3.
- **Backups:** heading "Other options, nearest first", then `results[1..2]` as rows (4.4). Then the further-away line (4.5).
- **Folds**, collapsed `<details>`:
  - "Why not the nearer ones?" (4.6)
  - "How we decided" (4.7)
- **Footer**, the page-wide caveats said once, in one grey paragraph: "Times are estimated drive times from straight-line distance, free-flow: real trips take longer. Records come from web pages, which can be out of date. Most records here are private facilities; public hospitals are often thinly described online."
- **Map column:** B's inline map, 430px tall. Under it, B's legend. Then A's **trip teaser card** (4.8).

Above the fold at 800px: the header, the origin line, the whole note up to and including the Call button, and backup row 1 (row 2 is fine below). The map is fully visible.

### 4.2 Phone, 390x844

The grid areas reorder to header, origin, note, map (230px tall), teaser, backups, folds, footer.

- The chips wrap and are never truncated.
- Headline 20.5px.
- In the note, hide the "Also in its record" and Sources lines (they move into a fold, "Sources and more").
- The Call button is full width, at least 48px tall.

The answer headline and the Call button must be above the fold at 844px.

### 4.3 The note: anatomy and exact templates

All string building lives in `text.ts`, as pure functions with unit-testable inputs. `c` = `results[0]`. In the abstention case `c` = `beyond_radius`, inside the nested block of 4.9.

1. **Headline** (serif, 23px):
   - When `c.time` is not loose: `Go to {c.name_short}, {c.time.short} away. {claimSentence(service, c)}.`
   - When `c.time.loose`: `Go to {c.name_short}, {c.time.range} away depending on where the patient is. {claimSentence}.`
   - When there is no `time`: `Go to {c.name_short}, {kmShort(c)} away in a straight line. {claimSentence}.`
   - `claimSentence` comes from a table keyed by service:
     - emoc: "Its record says it performs C-sections"
     - dialysis: "Its record names a dialysis service"
     - trauma: "Its record names emergency trauma care"
     - For the other lines, and as the fallback: "Its record names {requirementWord(first quote's first key)}"
   - Use `claimSentence` only when `c.quotes.length > 0`. Otherwise: "Its record lists {service_phrase}."
   - Example: "Go to BNK Hospital, about 7 min away. Its record says it performs C-sections."
2. **Distance line:** `{time.short} estimated drive (range {time.range}), {distance_label}. {typeWord(type)}, {operatorWord(operator)}, {city}.`
   - Loose: drop "(range ...)".
   - `position_approximate`: the distance part is "position approximate".
   - `crosses_state`: append ` In {state_display}: across the state border.`
3. **Lead quote** (serif, with a green left rule):
   - The lead quote is the first quote whose `requirement_keys` include the line's main key (`emoc` "caesarean section", `dialysis` "dialysis unit, machines or nephrologist", ...; the first key in `quotes[0]` is the fallback).
   - Render the text with each `marks` span wrapped in `<mark class="ev">` (green text, light green background).
   - Over 140 characters, show ±60 characters around the first mark with "..." at the cut ends.
   - Under the quote: `fieldWords(field)`. The words are:
     - `procedure`: "from its list of procedures"
     - `equipment`: "from its equipment list"
     - `capability`: "from its list of services"
     - `specialties`: "from its list of specialties"
     - `description`: "from its description"
     - anything else: "from its record"
4. **Also line** (sans 14px): "Also in its record: "{q}" {fieldWords}; "{q}" {fieldWords}." Up to 2 other quotes. A quote covering two requirements appears once.
5. **How sure** (a 3-bar glyph plus one sentence). See `sureLine(c)` below.
6. **Public list line**, when `c.public_list`: one line in a dashed outline box with the tag "Government list (outside this dataset)", then `public_list.text`. For each of `c.outside_lines`: `text`, same style. Never mixed into the quotes.
7. **Next step** (after a dashed rule):
   - When `call.has_phone`: the button `[Call {call.phone}]`. It is a `tel:` link only if `call.tel` is set; otherwise a disabled-looking button. Caption: "Number from its record. Digits hidden for the demo." when `demo_masked`, else "Number from its record. It may be out of date."
   - When there is no phone: "No phone number in its record."
   - Then "Before you go, ask:" and an ordered list of `questions[].question` (max 3). `why` goes in a `title` tooltip only.
8. **Sources** (one grey line):
   - With a domain: "Found on {n_domains} websites, including its own ({official_domain})."
   - Otherwise: "Found on {n} websites." Use "1 website" when n is 1.
9. **Cautions** (only facility-specific ones; omit the block when empty):
   - When `position_approximate`: "Its map position is approximate: we placed it at the centre of its PIN area."
   - Each `flags[]` entry, at any severity, apart from location_conflict when `position_approximate` already covers it: the first sentence of `flag.reason`, run through the masking rule (no digits runs of 8 or more).
   - When `missing_phrases` is non-empty: "Not found in its record: {joined with ', ' and ' or '}." This merges "what we could not find" with the matching caution; never show both.

**`sureLine(c)`**: the level word comes from `c.confidence`:

| `c.confidence` | level word |
|---|---|
| high | "Strong record evidence." (3 bars) |
| medium | "Some record evidence." (2 bars) |
| low | "Thin record evidence." (1 bar) |

The reason is the first of these rules that applies. Only one reason is ever shown.

1. `position_approximate` and `location_precision_km ≥ 10`: "Its record names {lead word}, but its position is known only to within about {round(location_precision_km)} km."
2. `position_approximate`: "Its record names {lead word}, but its map position is approximate."
3. A flag of severity "major": "Its record names {lead word}, but {lower-first(flag.reason first sentence)}."
4. A flag whose `flag` contains "place" or "merge", for example `place_conflict`: "Its record names {lead word}, but its own text also names another place, so it may mix two facilities."
5. Only one quote names the main key: "Only one sentence in its record names {lead word}."
6. `missing_phrases` is non-empty: "Its record names {lead word}, but not {missing_phrases joined with ' or '}."
7. Otherwise: "Its record names {all requirement words joined with commas and 'and'}{location_note contains 'consistent' ? ', and its map position matches its PIN' : ''}."

Example: "Strong record evidence. Its record names C-section, a blood bank and an operating theatre, and its map position matches its PIN." `confidence_reason` (the server text) is never shown.

**`requirementWord(key)`**: a short noun for each `REQUIREMENT_PHRASE` key. The keys that matter most:

| key | word |
|---|---|
| caesarean section | "C-section" |
| blood bank or transfusion | "a blood bank" |
| anaesthesia or operating theatre | "an operating theatre" |
| dialysis unit, machines or nephrologist | "a dialysis unit" |
| intensive care unit | "an ICU (intensive care)" |
| ventilator | "a ventilator" |
| newborn intensive care unit | "a newborn ICU" |
| CT scan | "a CT scan" |
| MRI scan | "an MRI scan" |
| cath lab, angiography or angioplasty | "a cath lab" |
| 24-hour or emergency service | "round-the-clock service" |
| 24-hour service | "round-the-clock service" |

Write the full table in `text.ts`. Where a key has no entry, use the key itself.

### 4.4 Backup rows (`results[1]`, `results[2]`)

Rows are compact. The left side holds the number badge, `name_short`, the `name_rest` sub-line in grey, and "{typeWord}, {operatorWord}, {city}". The right side shows `time.short` big, with `distance_label` or "position approximate" under it.

1. The order sentence (`orderSentence(prev, c)`). The first rule that applies:
   - The allowance put this card ahead of a nearer card: `c.parts.allowance_applies` and `c.minutes > prev.minutes`. "Listed before a nearer option because its record names everything the care needs, and it is at most {window.allowance} minutes further."
   - `round(c.minutes) == round(prev.minutes)`, or km equal at the shown precision: "About the same distance as {prev.name_short}; listed after it because its record shows less." when `c.grade < prev.grade`, else "About the same distance as {prev.name_short}."
   - Otherwise: "Next nearest with evidence in its record."
   - It never names a facility that is not on screen, and never prints "X against X".
2. The lead quote and its field words (same rules as 4.3).
3. The sure line.
4. Cautions.
5. A fold, "Call, questions and sources", holding items 7 and 8 of 4.3.

Approximate rows use a hollow number badge, matching the hollow pin on the map.

### 4.5 Further away

One line, shown only if `further_away` is non-empty:

"Further away (beyond {window.hours_text}): {name_short}, {time.short}{position_approximate ? ' (position approximate)' : ''}; ..."

Up to 3 entries, each clickable to focus its pin.

### 4.6 "Why not the nearer ones?" (fold)

- The summary line is `why_not.heading` as given.
- Each item shows:
  - a grey diamond with its `letter`;
  - `name` (masked names appear as the server sends them);
  - `distance_label`;
  - `reason_plain` in grey;
  - when `quote_or_absence.kind == 'quote'`, its text with field words (when `negated`, prefix "Its record says: "); when it is `absence`, its text in italics.
- Items with `in_sim` get a link button "What if it were verified?". It opens the expanded map with the simulator and applies a verify edit to that facility (`sim.openWithEdit({type:'verify', facility_id})`).
- The closing line, verbatim: "Grey means our rule did not recommend it from its record. It does not mean the place cannot help."
- Hovering an item highlights its diamond on the map (`map.highlight('whynot', letter)`).

### 4.7 "How we decided" (fold)

1. **Funnel:** a horizontal row of plain cells, from `funnel.steps`: `{n}` big and `label` small. For Lucknow: 9,989 / 9,671 / 469 / 47 / 13 / 3. Replace the last label with "shown here, nearest first".
2. **Rule paragraph** (verbatim, with values filled in): "We keep a facility only if a sentence in its own record names the service and nothing serious is wrong with the record. We list them nearest first by estimated drive. One exception: a record that names everything the care needs can go first if it is at most {window.allowance} minutes further."
3. **Window:** "Window: {window.hours_text} estimated drive. {first sentence of window.basis}" Then `window.note` as given. Shown as is, this covers both "The 60 minutes is ours" and the Lancet cite.
4. **Citations** (verbatim): "Every quote is the exact text of a fact in the facility's record, with the list it came from. We cannot tell which web page each fact came from."
5. **Nested "Technical detail" fold:** `how_we_decided.travel_model`, the `recipe` text, and the `limits` list. Show no formula, weights or decimals, even if present.

### 4.8 Trip teaser card (under the legend, needs `/api/sim`)

- Title: "Where would nearby villages go?"
- Sentence, from `sim.summary`: "If they trusted specialty claims alone, {first_no_evidence} of {n_villages} nearby villages would go first to a place with no evidence of {service_phrase} in its record."
- If `first_no_evidence == 0`, use instead: "{within} of {n_villages} nearby villages are within {hours_text} of a place whose record shows {service_phrase}."
- Buttons: [Play it on the map] calls `sim.open({mode:'claims', autoplay:true})`. [Try a what-if] calls `sim.open({mode:'evidence', tool:'verify'})`.
- Hidden when `sim.status != 'ok'`.

### 4.9 Abstention (`status == 'no_reliable_option'`)

The note gets the amber rule.

1. **Headline:** "No clear evidence of {service_phrase} within {window.hours_text} of {origin.short_name} in this dataset."
2. **Line 2** (verbatim): "This is not proof that no care exists nearby. Most records here are private facilities, and public hospitals are often thinly described online."
3. **Next step** (verbatim pattern): "Ask the district hospital or the ASHA supervisor about {service_phrase} nearer to {origin.short_name}."
4. **Gap line** (from `funnel`): "{n claim_within_reach} records list or claim {service_phrase} within about {window.reach_text}; {n recommendable} have a sentence naming it; {n within_radius} are within {window.hours_text}."
5. **Nested block:** "Nearest place whose record names {service_phrase}". It holds `beyond_radius`, using the full anatomy of 4.3 items 2 to 9. The headline is not repeated.
6. **"About as far":** `further_away[0..1]` (deduped) as backup rows. Then the further-away line for the rest.
7. **Outside lane:** when `outside_nearby` is present: "Also on government or registry lists nearby (not in this dataset)". Items are unnumbered, with "position approximate (PIN area)" when the server says so.
8. **What-if teaser** (purple, dashed border, with the WHAT-IF tag):
   - It needs `sim.previewAdd(origin)` (section 7.6) to return `after.within > before.within`.
   - "What if a {service_phrase} unit opened in {origin.short_name}? {after.within} of {n_villages} nearby villages would be within {hours_text} of one, up from {before.within}."
   - Button: [See it on the map], which opens the simulator with that add edit applied.

### 4.10 Landing, ambiguous and errors

- **Landing:** the intro line (verbatim): "Pick the care and the place. We show up to 3 facilities, quote their own records, and say when nothing reliable is near." Then the example chips. The map shows India with the examples (B: `map.showLanding(examples)`). A resolves each example with `/api/resolve` on boot and passes `{label, lat, lon}`.
- **Ambiguous:** "{n} places are called {typed}. Which one?" Buttons read "{name}, {state}", with a small "district" or "post office" tag. The map shows the candidates as clickable points (`map.showCandidates`).
- **Not found and outside India:** keep the existing plain sentences.

## 5. Map (B)

### 5.1 Assets

These are built once by `app/scripts/build_geo_assets.py`, which runs with the repo `.venv`, reads the bundle, and writes to `app/src/assets/geo/`:

- **`india_states.geojson`**: copy `app/design/answer_first/india_states.geojson` (Natural Earth 1:10m admin-1, India only, 36 features, 166 KB). Keep the closed-ring fix in any re-simplification.
- **`india_outline.json`**: decode `vf.locate._INDIA_OUTLINE_B85` to a GeoJSON MultiPolygon.
- **`po_points.bin`**: every bundle post office point after the 3.2 filter (placeholder rows and the artefact latitude rows dropped), deduped. Encode as uint16 pairs quantised to the India bbox (lon 68.0 to 97.5, lat 6.5 to 37.2), about 450 KB.
- **`labels_india.json`**: state names with hand-checked label points (the polygon centroid, nudged inside the polygon); district names (754, from `places.parquet` kind=district, with lat/lon); and neighbour country labels with fixed points: Nepal, Bangladesh, Pakistan, China, Bhutan, Myanmar, Sri Lanka.

Commit nothing. The script prints sizes. The total must be under 1.5 MB.

### 5.2 Layers, bottom to top (deck.gl `MapView`, one `Deck`)

1. **Container background:** `SURROUND`.
2. **India land:** `GeoJsonLayer` states, filled `PAPER`, no stroke.
3. **Post office dots:** `ScatterplotLayer` over a binary attribute. Radius 0.9px below zoom 6, 1.4px from 6 to 9, 2px from zoom 9. Colour `#8a7f6d` at alpha 0.42. At country zoom these draw India.
4. **State borders:** `PathLayer` of polygon rings, `BORDER`, 1px, alpha 0.7. Solid lines (no dash extension).
5. **India outline:** `PathLayer`, ink at alpha 0.8, 1.2px.
6. **Window ring:** a 128-gon of `window.km_equivalent` around the origin. Thin `EVIDENCE` line at alpha 0.5, labelled "{hours_text} estimated drive" at its top. On an abstention it is labelled "{hours_text}: no record here shows {service_phrase}"; keep that short.
7. **Precision circle:** a faint `ORIGIN` fill, alpha 0.06, only when `precision_km ≥ 2`.
8. **Leader line:** on an abstention, a dashed line (JS-split segments, 1 km on and 1 km off, scaled by zoom) from the origin to the beyond card, labelled with its `time.short`.
9. **Why-not diamonds:** `NEUTRAL`, lettered, shown at zoom 9 and above.
10. **Result pins:** numbered circles, `EVIDENCE`. Approximate positions get a hollow pin (white fill, green 2px ring, green number). The beyond card is pin 1.
11. **Origin marker:** an `ORIGIN` dot with a white stroke.
12. **Labels:** `TextLayer`, with a paper halo (`outlineWidth` 3, `outlineColor` `PAPER`, `fontSettings.sdf: true`). Shown by zoom:
    - state names below zoom 7, uppercase, letter-spaced, `#7a6a55`;
    - district names from zoom 6.5 to 11;
    - `sim.context.labels` from zoom 9;
    - the searched place, bold ink, always;
    - neighbour countries below zoom 7, in grey.
    - Declutter in JS on view change (debounce 80 ms): project to screen, keep in priority order (searched place, prio 1, states, prio 2, prio 3), and drop any label whose box overlaps a kept one by more than 2px.
13. **Edge arrows** (P2): for prio 1 and 2 labels outside the view and within 150 km, a DOM chip at the view edge: "Hardoi 101 km →".

**Overlapping pins:** pins within 14px of each other on screen fan out in a ring of radius 18px, with a thin leader line to the true point. Recompute on zoom.

**Collapse at country zoom:** below zoom 7, results, diamonds and the window ring collapse into one labelled marker at the origin: "{short_name}: {n} options within {hours_text}", or "{short_name}: no clear evidence nearby".

### 5.3 Controls and behaviour

- **Controller:** `scrollZoom: true`, `dragPan`, `touchZoom`, `doubleClickZoom`. `minZoom` 3.6, `maxZoom` 14. Clamp the view centre to lon 66 to 99, lat 5 to 38.
- **Inline map, 430px** (230px on phone):
  - top-right: [Expand map];
  - top-left: [+] [-];
  - bottom-left: the India inset, 120x130 (90x100 on phone), always visible;
  - bottom-right: the scale bar and a north arrow ("N ↑").
  - The hint "Scroll or pinch to zoom, drag to move" shows for 3 s on the first pointer enter.
- **Expanded:** a full-viewport overlay. Re-parent the same Deck canvas into the overlay; do not create a second Deck. After re-parenting, call `deck.setProps({width:'100%', height:'100%'})` and redraw.
  - Top bar: "Map of {short_name}" on the left. On the right: [Show all of India], [Zoom to {short_name}], [Close map]. Esc also closes.
  - The zoom ladder: All of India (z 4), State (z 6.5), District (z 9), Neighbourhood (z 11.5), as small buttons under the +/-.
  - The right rail slot, 400px wide on laptop and empty by default, is filled by D. On phone the map takes the top 46vh and a bottom sheet slot sits under it.
- **Show all of India:** `FlyToInterpolator`, 1200 ms, to `{lon: 80.5, lat: 22.5, zoom: fitIndia}`. **Zoom to place:** fit the window ring, plus the beyond card when there is one.
- **Default fit after an answer:**
  - with results: fit the origin, all result pins, and the 5 km padded box of their extent;
  - on an abstention: fit the origin and the beyond card. The Bihar and West Bengal labels and the border must show for Jokihat.
- **India inset:** an SVG drawn once from `india_states.geojson` with an equirectangular fit to the bbox. A red rectangle shows the current view bounds; below 8px it becomes a red dot. Clicking the inset shows all of India.
- **Legend:** a DOM list that shows only the symbols currently drawn (B builds it from the active layers). Rows:

| symbol | label |
|---|---|
| green numbered pin | "Option with evidence in its record (number = order)" |
| hollow pin | "Position approximate" |
| grey diamond | "Nearer record not recommended (see Why not the nearer ones?)" |
| red dot | "Searched place" |
| ring | "{hours_text} estimated drive" |

  D appends its own rows when the simulator is active (`map.setExtraLegend(rows)`).

### 5.4 Public API (B exports; A and D call only these)

```ts
export interface MapExample { label: string; lat: number; lon: number }
export class MapHost {
  constructor(inlineEl: HTMLElement);
  showLanding(examples: MapExample[], onPick: (i: number) => void): void;
  showCandidates(c: Candidate[], onPick: (i: number) => void): void;
  showAnswer(a: Answer, sim: SimPayload | null): void;     // pins, diamonds, ring, labels, fit
  highlight(kind: 'result' | 'whynot', key: number | string | null): void;
  focus(kind: 'result' | 'further', idx: number): void;    // fly to a card's pin
  expand(opts?: { rail?: boolean }): { rail: HTMLElement; sheet: HTMLElement; mapEl: HTMLElement; topbar: HTMLElement };
  collapse(): void;
  isExpanded(): boolean;
  fit(bounds: [[number, number], [number, number]], padPx?: number, ms?: number): void;
  setOverlayLayers(layers: Layer[]): void;                  // D's layers, drawn above pins and below labels
  setExtraLegend(rows: { swatch: string; label: string }[]): void;
  setBanner(el: HTMLElement | null): void;                  // D's WHAT-IF banner, full width over the map top
  setMapClickHandler(fn: ((lngLat: [number, number], picked: unknown) => void) | null): void;
  setCursor(c: 'default' | 'crosshair'): void;
  onPick: (kind: 'result' | 'whynot' | 'further', key: number | string) => void;
  onViewChange: (vs: ViewState) => void;
  get viewState(): ViewState;
  readonly deck: Deck;                                      // read-only use by D for project/unproject
}
```

`minimap.ts` becomes `export { MapHost as MiniMap } from './map'` during migration. A switches `main.ts` to `MapHost`. Remove the old trip toggles from `index.html`.

## 6. Placement and controls of the simulator (D, inside B's expanded map)

**Entry points:**

- the teaser buttons (4.8);
- "What if it were verified?" (4.6);
- the abstention what-if teaser (4.9);
- [Where would villages go?] in the expanded map's top bar, added by D through the topbar element returned by `expand()`.

**Layout, laptop.** The map has an on-map **sim bar** (top-centre, max 760px, paper with 92% alpha), the **banner** above it when there are edits, a **takeaway bar** at the bottom-centre, and the **rail** on the right.

**Sim bar, one row plus one line:**

- Row: title "Where would nearby villages go?", then the segmented control **[Trust the record's evidence | Trust specialty claims alone]**, then [▶ Play] / [❚❚ Pause], [↺ Replay], a progress bar, the clock "minute 26", and "1 s = 5 min".
- Line 2: the rule sentence for the selected mode (verbatim):
  - Evidence: "Each dot goes to the nearest place whose record shows {service_phrase} within {hours_text}. A record that names everything the care needs may be chosen if it is at most {allowance} minutes further."
  - Claims: "Each dot goes to the nearest place that lists {service_phrase}. If that place's record shows no evidence, the dot waits, then goes on to the nearest place whose record shows it: if that place cannot help."
- Caption under it, small (verbatim): "Each moving dot is a patient from a nearby village, drawn at its post office. No route is drawn in advance: each dot picks its target by the rule, and picks again if the map changes. Straight-line paths; times from the drive-time estimate, free-flow."

**Takeaway bar** (serif, one sentence, from 7.3; it changes with the mode and with edits):

- Claims: "If they trusted specialty claims alone, {first_no_evidence} of {n} nearby villages would go first to a place with no evidence of {service_phrase} in its record. If that place cannot help, the trip is about {extra} min longer on average."
- Evidence: "{within} of {n} nearby villages are within {hours_text} of a place whose record shows {service_phrase}. The average estimated drive to the nearest one is {avg} min."
- With edits, prefix "What-if: " and colour the numbers that changed in `WHATIF`.

**Rail, top to bottom:**

1. Live tally (agents, with the unit stated): "Patient dots ({k} per village): {a} arrived, {m} on the way, {s} stopped first at a place with no evidence."
2. A counters table with rows and columns:

| | Real data | What-if | Change |
|---|---|---|---|
| Villages within {hours_text} of a place with evidence | 411 of 411 | ... | +0 |
| Average estimated drive to the nearest one | 27 min | ... | -2 min |
| Villages whose first stop has no evidence (if they trust claims) | 353 | ... | -41 |

   The What-if and Change columns appear only when there are edits, in `WHATIF`. If `n_no_evidence_in_reach > 0`, add a note under the table: "{k} villages have no place with evidence in reach."
3. **What-if box:** dashed `WHATIF` border, with the tag "WHAT-IF".
   - Tools: [Verify a record] [Add a facility] [Close a facility].
   - The edit list, one line each: "What-if: verified {name}", "What-if: new {service_phrase} unit near {nearest label}", "What-if: closed {name}", each with [Undo].
   - Then [Undo last] and [Reset to real data].
4. [Which record should be verified first?] and its result (7.7).
5. **Capacity (P2):** a toggle "Assume each place handles at most [6] patients per hour", default off, tagged "Assumption, not from data".
6. Footer: "Same seed, same run, every time (seed 7)."

**Banner** (when there are edits): a full-width purple striped bar over the map top, reading "WHAT-IF VIEW · {n} edit(s) · not real data", with [Undo last] and [Reset to real data].

**On-map legend rows** (via `setExtraLegend`), only those relevant to the current mode:

| mode | swatch | label |
|---|---|---|
| evidence | green dot | "Patient heading to a place whose record shows it" |
| claims | amber dot | "Patient heading to a place that lists it, no evidence in its record" |
| claims | amber ring | "Waiting: that place's record shows no evidence" |
| claims | amber dashed | "The extra trip, if that place cannot help" |
| always | amber diamond | "Lists it, no evidence in its record" |
| with edits | purple dot | "On the road when you made the change, so it turned" |
| with edits | purple + | "What-if facility" |

**Phone:** the map is the top 46vh. The sheet below is ordered: title, mode switch, takeaway, player row, live tally, the tool row [Verify] [Add] [Close] [Verify first?], the what-if result in one purple line with [Undo], the counters table, and finally the rule sentence and caption. The banner wraps.

## 7. Agent simulator (D)

### 7.1 Files

| file | contents |
|---|---|
| `sim/world.ts` | `World`, built from `SimPayload`: typed arrays and edits |
| `sim/rules.ts` | the pure rules: `minutes`, `chooseEvidence`, `chooseClaims`, `counters`, `verifyFirst`, `previewAdd` |
| `sim/agents.ts` | the event-driven agent engine |
| `sim/layers.ts` | deck layers |
| `sim/panel.ts` | sim bar, rail, banner, sheet, takeaway |
| `sim/index.ts` | `Simulator`, the public API |
| `sim/sim.css` | styles |

Rework `swarm-layer.ts` (7.5). Delete `swarm.ts` after B's map no longer imports it.

### 7.2 World

- **Facilities:** `n` from the payload plus added what-ifs. Typed arrays `lon`, `lat`, `status` (0 evidence, 1 claim_only, 2 held_back), `strong`, `open`, `verified`, `added`.
- **Effective evidence:** `ev[j] = open[j] && (status[j]==0 || verified[j] || added[j])`. A verified or added facility is never `strong`, so it gets no allowance.
- **Matrices:** `KM` (V×F Float32, haversine with R 6371.0088) and `MIN` (`factor[v] * a * KM^b`), precomputed at load. Lucknow is about 411×464, which takes under 10 ms. An added facility appends a column.
- **Edits:** `{type: 'verify' | 'close' | 'add', facility_id?, lon?, lat?, id}`. Every edit increments `world.version`. Undo or reset rebuilds the flags from the remaining edits and increments `version`.

### 7.3 Rules (the server's `sim_payload.py` mirrors these exactly)

Let `W` = `window.minutes` and `A` = `window.allowance`. For village `v`:

- `nearestEv(v)` = `min over j with ev[j]` of `MIN[v][j]`; `Infinity` if there is none.
- **within** = the count of villages with `nearestEv(v) ≤ W`.
- **avg_minutes** = the mean of `nearestEv(v)` over villages where it is finite. Round to 1 decimal for parity and display as an integer. `n_no_evidence_in_reach` = the count of villages where it is infinite.
- **first(v)** = `argmin over j with open[j]` of `KM[v][j]`. Ties go to the lower `i`. Any status counts.
- **first_no_evidence** = the count of `v` with `!ev[first(v)]`, split by status into claim_only and held_back. Under what-if, verified places count as evidence.
- **extra_minutes_mean**: over the villages counted in `first_no_evidence` that also have a finite `nearestEv`, the mean of `MIN[v][first(v)] + factor[v] * a * km(first(v), j*)^b - nearestEv(v)`. Here `j*` = the evidence facility nearest to `first(v)`'s position. Round to 1 decimal.
- **Evidence choice for a dot at point p** (factor `f` = its village's): `m_j = f * a * km(p, j)^b` over `ev[j]`. If any `m_j ≤ W`, pick `argmin(m_j - (strong[j] ? A : 0))` among them (ties to the lower `m_j`, then the lower `i`). Otherwise pick `argmin m_j`. If there is no `ev` at all, the dot is **stuck**.
- **Claims choice at p:** `argmin km(p, j)` over `open[j]`, excluding places this dot already visited.

### 7.4 Agents (event-driven; positions are analytic between events)

- **Count:** `k` = 3 per village on laptop and 2 on phone, reduced so that `V*k ≤ 6000`.
- **Depart time:** `hash01(seed, v, idx) * 24` sim-minutes. `hash01` is FNV-1a of the string `${seed}|${v}|${idx}`, divided by 2^32. Seed 7.
- **Agent state:** `x0, y0` (leg start), `tx, ty`, `t0`, `dur`, `target`, `phase` (one of waiting, moving, holding, arrived, stuck), `referred`, `turned`, `visited` (a small array).
- **Leg duration:** `f * a * km^b` sim-minutes. Movement is linear in time along the straight line, so speed comes from the drive-time model.
- **Engine loop:** each animation frame advances `simTime += dtReal * speed`. It then processes, in time order, every event up to `simTime`:
  - departures: decide from the village's row of `MIN`;
  - arrivals;
  - hold ends;
  - `world.version` changes.
  - It keeps a min-heap keyed by event time.
- **Arrival, evidence mode:** if `ev[target]` holds, the dot is arrived. If the target closed while the dot was travelling, the version-change step will already have re-decided.
- **Arrival, claims mode:**
  - If `ev[target]`: arrived.
  - Otherwise, the first time: phase holding for `HOLD = 5` sim-minutes, an amber pulsing ring, and the facility's "stopped here" tally increments. When the hold ends, set `referred = true` and go to the evidence-nearest place from where the dot stands: `argmin m_j` over `ev`, with no window and no allowance. The leg is drawn as an amber dashed trail.
  - This is applied **once**: a referred dot never takes another claims hop. If there is no evidence place, the dot is stuck, with an amber ring that stays.
- **World change** (`version` increments, mid-run):
  - For each moving dot: compute its current position, set it as `x0, y0`, then decide again with the current mode's rule. A referred dot uses the evidence-nearest rule. If the new target differs from the old one, set `turned = true` (the dot is purple from then on).
  - Holding dots re-decide when their hold ends.
  - Arrived dots stay arrived.
- **Speed:**
  - Default speed is chosen so that the 90th-percentile settle time plays in about 12 s: `speed = max(5, ceil(p90/12))` sim-min per second. The 90th percentile is estimated from the depart time plus `nearestEv`, or the claims path, per village.
  - The label shows "1 s = {speed} min".
  - Play ends when every dot has arrived or is stuck. Replay resets to t = 0 with the current world.
- **Determinism:** no `Math.random`. The same seed and the same edits applied at the same sim-minutes give the same frames. Expose `window.__vf.sim = {seek(t), play(), pause(), setMode(m), apply(edit, atMinute?), reset(), counters(), state()}`. `seek` re-runs from t = 0 to t with no rendering, so frame-exact screenshots repeat.

### 7.5 Rendering (`sim/layers.ts`, through `map.setOverlayLayers`)

- **Heads:** `SwarmLayer`, one instance holding every agent.
  - Per-agent attributes: `getPosition` (leg start), `getTarget`, `getTiming = [t0, dur, state, phase]`, `getFillColor`.
  - Rebuild the typed arrays only on ticks with events (a dirty flag). The `time` uniform moves the dots every frame.
  - **Shader change** (D owns `swarm-layer.ts`): remove the hard-coded moving grey. Colour is always `instanceFillColors`. `state` 2 is a pulse (holding); `state` 3 is "arrived", where alpha fades to 0 over 3 sim-minutes after `t0 + dur`.
  - Radius 3.5px laptop, 3px phone.
  - Colours: moving to an evidence place `EVIDENCE`; claims first leg to a place with no evidence `CLAIM`; referred leg `CLAIM`; turned `WHATIF`; stuck `CLAIM` with state 2.
- **Trails** (`LineLayer`, rebuilt at 10 Hz): for each moving dot, the segment from its position 6 sim-minutes ago, clamped to the leg start, to its current position. Width 1.5px, colour as the head, alpha 0.6. Referred legs are drawn dashed: split into 0.6 km on and 0.6 km off pieces.
- **Road so far** (`LineLayer`, append-only): every completed or cut leg, width 1px, alpha 0.12. Never draw any segment ahead of a dot.
- **Facilities:**
  - evidence: small green dots, with the result pins coming from B on top;
  - claim_only: amber diamonds (`IconLayer` is not needed; use a `ScatterplotLayer` with a square plus a 45° rotation, or a `TextLayer` "◆");
  - held_back: amber hollow diamonds;
  - closed: grey with a purple ×;
  - verified: a purple ring around the amber diamond;
  - added: a purple "+" with the label "What-if: new {service_phrase} unit".
- **Tallies:** a `TextLayer` beside each facility that has arrivals or stops: "{n} arrived" in green, "{n} stopped here" in amber. Show only the top 8 of each by count, to avoid clutter.
- **Camera on open:** fit the 50 km village ring plus every facility any dot targets in the first decision pass. For Jokihat this includes Raiganj.

### 7.6 What-if tools

- **Verify a record:** the cursor becomes a crosshair. Clicking an amber diamond (pick radius 12px) adds `{type:'verify'}`. The rail also offers a list "Records nearby that list it without evidence", sorted by `km_from_origin`, top 10, each with [Verify]. Held-back items carry the note "(its evidence is held back by a warning in its record)".
- **Add a facility:**
  - Click empty ground. A popover shows "Add a what-if {service_phrase} unit here?", the line "Service: {service_label} (the one you searched)", and [Add] [Cancel].
  - [Add] creates `{type:'add', lon, lat}` with the name "What-if unit {n}". The edit-list label uses the nearest label from `context.labels`.
- **Close a facility:** click any facility pin, diamond or what-if. The popover says "Close {name} in this what-if?"; [Close] adds `{type:'close'}`.
- **Every edit** shows the banner, updates the counters within 200 ms, and re-decides the dots that are moving.
- **Undo** removes one edit; **Reset** removes all of them.
- **`previewAdd(lon, lat)`** returns the counters before and after without changing the world (it is used by A's abstention teaser): `sim.previewAdd(origin) -> {before, after}`.

### 7.7 "Which record should be verified first?"

- **Candidates:** every open facility with status claim_only or held_back that is not already verified.
- **For each candidate `j`:**
  - `newNearestEv(v) = min(nearestEv(v), MIN[v][j])`;
  - `newFirstNoEv` changes only for villages with `first(v) == j`;
  - `extra` is recomputed only for the affected villages.
  - This is O(V) per candidate, using the precomputed `nearestEv` and `first`.
- **Ranking order:**
  1. gain in within (descending);
  2. gain in first-stop-no-evidence (descending);
  3. average minutes saved (descending);
  4. `km_from_origin` (ascending);
  5. `facility_id`.
- **Output:** the top 3 rows. Each row: "{rank}. {name}, {km_from_origin} km from {short_name}: within {hours_text} {b} → {a}; first stops with no evidence {b} → {a}; average drive {b} → {a} min [Try it]". [Try it] applies the verify edit.
- **Verdict line, the first that applies:**
  - Best `gain_within == 0` and `gain_first == 0`: "We tried verifying each of the {n} nearby records that list {service_phrase} without evidence. None would bring a village within {hours_text}. The gap here is distance, not checking."
  - `before.within == n_villages`: "All {n} villages already have a place with evidence within {hours_text}. Checking {name} first would cut first stops with no evidence from {b} to {a}."
  - Otherwise: "Checking {name} first would bring {gain} more villages within {hours_text} of a place with evidence."
- **Footnote** (verbatim): "A verification priority for Virtue Foundation, not a claim that any of these places offers {service_phrase}."
- **Performance:** run on the main thread with typed arrays. Log the time with `performance.now()`. If the Lucknow sweep is over 50 ms, move it to a module Web Worker (`new Worker(new URL('./sweep.worker.ts', import.meta.url), {type:'module'})`).

### 7.8 Capacity mode (P2)

- Off by default.
- When on, a facility accepts at most `N` arrivals per sliding 60 sim-minutes. Extra dots wait at the facility, drawn as a grey ring, and are counted as "waiting at a full place (assumption)".
- It is deterministic. It never changes the three counters.
- Its label always carries "Assumption, not from data".

## 8. Server (C) details

- Add `@app.get("/api/sim")`, calling `sim_payload.build_sim(state, service, res, ans, demo)`. Reuse `do_answer` so the cards and the gates match. Cache per `(service, place_id or q)` with an LRU of 64.
- Add the answer enrichments of 3.1 in an `enrich_answer(s, service, res, ans, demo)` function called inside `api_answer` after `do_answer`. Keep `do_answer` itself unchanged so `/api/trip` and the tests are untouched.
- Load `questions.load_record_info(bundle)`, `questions.load_phone_book()` (or `build_phone_book` from the bundle when needed) and `public_list.load(bundle)` once in `State.load()`. Wrap each in try/except: a module that fails sets a field in `/api/health` (`extras_errors`) and its answer fields are left out. It must never 500.
- `public_list` v2: if `hasattr(public_list, "annotate_answer")`, call it with `(ans, service, res.lat, res.lon, res.state)`, then apply the emoc-only filter to the v1 `public_list` field.
- Tolerate both rankers: if `ans.get("window")` is missing (v0), set `origin.loose=false` and `time=null`, take the window from `services.WINDOWS[service]`, and still build `/api/sim` with `S.WINDOWS`.

Tests (`tests/test_server.py` and the new `tests/test_sim_payload.py`):

1. The existing tests still pass.
2. `/api/answer` for `emoc`/226001 has `why_not.items[*].letter`, `funnel.steps[-1].n == len(results)`, `results[0].call.phone` matching `xxxx`, `results[0].questions` with 1 to 3 items, `quotes` with no duplicate text, and the step `claim_within_reach` n equal to `sim.summary.n_listing_records`.
3. Dialysis at Jokihat: `beyond_radius.facility_id` is not in `further_away`, `beyond_radius.state_display == "West Bengal"`, `crosses_state` is true, and `public_list` is null on every card.
4. `/api/sim` emoc 226001: the village count is between 300 and 3000; no village has a latitude in {15.59, 15.5934}; `summary` equals a pure-numpy recomputation in the test (same rules); `n_evidence` equals the funnel's `recommendable` minus the merged duplicates among them; and it runs in under 300 ms.
5. `/api/sim` returns `unavailable` for `other` and for Aurangabad (ambiguous).
6. With `VF_DEMO_MODE=1`, `call.tel` is null and the why-not names are masked.
7. Write the summaries for the 4 example cases to `reports/figures/ui/build_C_sim_summaries.json` for D's parity check.

## 9. Integration order and cut lines

**P0, must ship for the video:**

- A: steps 0 (types, api, colours, vite port); 4.1 to 4.3; 4.9; the footer.
- B: 5.1; layers 1 to 7, 10 to 12; the inset; Expand; Show all of India; +/-.
- C: `/api/sim`; enrich `call`, `questions`, `quotes`, `time`, `origin`, `why_not` and `funnel`.
- D: 7.2 to 7.5; the evidence and claims modes; the sim bar; the takeaway; Verify and Add; counters with before/after; the banner; Undo and Reset.

**P1:**

- A: backups, the further-away line, the why-not fold, the how-we-decided fold, the teaser, phone order.
- B: decluttered labels, fanned pins, the country-zoom collapse, landing and candidates.
- C: `public_list`, `official_domain`, `state_display`, dedupe.
- D: Close, verify-first, the phone sheet, the abstention `previewAdd`.

**P2:** edge arrows, the zoom ladder, capacity mode, the Web Worker.

Merge order:

1. C's contract and A's types land first.
2. B's `MapHost` with `setOverlayLayers` lands before D wires its layers. Until then, D develops against a standalone harness page, `app/sim-dev.html`, which D owns and which is not shipped.
3. A wires D's `Simulator` last:

```ts
const sim = new Simulator(map);
sim.load(simPayload, answer);
sim.open({mode, autoplay, tool, edit});
sim.previewAdd(origin);
sim.close();
```

## 10. Acceptance checks (each engineer runs and screenshots their part)

**A** (`build_A_*`):
- Lucknow 226001 laptop fold: the headline reads exactly "Go to BNK Hospital, about 7 min away. Its record says it performs C-sections.", Call shows `+91 xxxxxxxx77`, and backup 1 is above the fold.
- Jokihat abstention: headline in hours, West Bengal named, Raiganj listed once.
- Jaipur dialysis: the loose origin line and minute ranges.
- Phone views of Lucknow and Jokihat: headline and Call above the fold.
- grep the built DOM for the banned words in section 1: none may appear.

**B** (`build_B_*`):
- Lucknow inline: state border, Lucknow in bold, district labels, inset with its box.
- Expanded, then Show all of India: India drawn by dots and borders, with the single labelled marker.
- Jokihat: Bihar and West Bengal labels, the border, and Nepal in the surround.
- Landing with the examples. Scroll-zoom works in both inline and expanded. No black frames at any zoom.

**C:** the pytest output, the timing, and `build_C_sim_summaries.json`.

**D** (`build_D_*`):
- Browser counters equal C's summaries for all 4 cases. Log both.
- Claims mode at Lucknow, mid-run frame: amber heads and trails, hold rings, no pre-drawn spokes.
- Verify-first at Lucknow: the top row with real before → after.
- A Jokihat add at the origin mid-run: purple turned dots, the banner, and the counters table with Change.
- Verify-first at Jokihat: shows "The gap here is distance, not checking." if no candidate gains.
- 3,000 dots or more at 50 fps or better (Chrome performance panel or a frame-time log). An edit recompute under 200 ms (logged).
- `__vf.sim.seek(30)` twice gives identical screenshots.

## 11. Issues for other owners (do not fix here; list them in your report)

- `vf.locate`: Kotputli (PIN 303108) precision 163 km. The UI hides the number with `too_coarse`.
- `vf.rank`: the loose-origin `minutes_label` "about 1 min to 45 min". The UI builds its own text from `time` instead. The `why_here` and `confidence_reason` wording is v0-style; the UI does not show them.
- Bundle (`scripts/wp12_bundle.py`): drop the post office rows on the artefact latitudes (15.59, 15.5934) and placeholders at the source. Rebuild so `dbapp/vf/questions.py` is v2.
- `vf.public_list`: filter LaQshya lines by service at the source. The server applies an emoc-only filter for now.
- `/api/trip` still uses a 3 x 50 km reach and card-only targets. The UI no longer uses it; retire it after the video.
