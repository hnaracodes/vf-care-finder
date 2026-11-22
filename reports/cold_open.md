# Cold open: the obvious query on the brief's Lucknow question

D044 item 2. Run Nov 21, 23:03 ET. Query: `sql/cold_open_lucknow.sql`. Check: `scripts/cold_open_check.py` (all 9 checks pass).

## Finding

The obvious query answers "Which hospital near Lucknow can handle an emergency C-section?" with a fertility clinic whose record never mentions a caesarean. Row 1 is 0.95 km from PIN 226001. Its record lists 141 facts, and none of them mentions a caesarean; nor does any other text field in the row. Our ranker's first card on the same point is BNK Hospital, 3.5 km straight-line, High confidence, quoting "Performs Lower Segment Caesarean Section (LSCS)", "On-site Blood Bank" and "Modular Operation Theatre".

Caveat: this is a fact about one record, not about the clinic. It shows what the record says, not what the clinic can or cannot do.

## The point

`vf.locate.resolve("226001")` returns 26.84967105, 80.94397185: "PIN 226001, Lucknow, Uttar Pradesh", the middle of its post offices, precise to about 2 km. The SQL has this point typed in, and the check script fails if the resolver ever moves it.

## The query

Specialty mentions obstetrics (`lower(specialties) LIKE '%obstetric%'`), straight-line haversine distance from the point, nearest first, top 3. The same top 3 comes out with the exact label `gynecologyAndObstetrics`, so the result does not depend on how generous the filter is.

The raw table's traps are handled in the SQL:

- `latitude` and `longitude` are DOUBLE in the Marketplace table (Unity Catalog schema) but strings in the CSV export. `try_cast` covers both. A comparison with the string `'null'` would fail on the DOUBLE column under ANSI mode, so the query never makes one.
- The 88 junk rows fail `address_countryCode = 'IN'`. In pandas, 0 junk rows pass the other filters even without that line.
- The 30 truncated rows have no coordinates, and 0 of them pass.
- A bounding box (lat 6 to 38, lon 68 to 98) keeps out stray points.
- The export's 2 NUL bytes sit in two dental clinics' names and are stripped from the displayed name.
- 4,532 of the 10,088 raw rows pass the filters.

## Warehouse result

Serverless Starter Warehouse `ee1fa7958ed99cf2`. Two statements, two warehouse queries in total, both SUCCEEDED.

| | Statement id | Time |
| --- | --- | --- |
| Query 1 (the foil) | `01f1e629-9ed6-132a-a17c-f2cba906b09d` | 8.4 s, cold start included |
| Query 2 (row 1's record) | `01f1e629-a6d3-1046-9812-32d255b6907c` | 3.0 s |

| Row | Name | City | PIN | km (straight-line) | Facts | Mention a caesarean | Our EmOC grade |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Indira IVF Kanpur (blur on screen) | Kanpur | 208001 | 0.95 | 141 | 0 | claimed |
| 2 | Balrampur Hospital | Lucknow | 224176 | 1.97 | 63 | 0 | claimed |
| 3 | Kamla Devi Hospital & Lucknow Test Tube Baby Centre | Lucknow | 226018 | 2.30 | 65 | 0 | claimed |

The pandas replication on the raw CSV (`vf.io.read_facilities_raw()`, all 10,088 rows) gives the same three ids in the same order, the same names, cities and PINs, and distances that differ by 0.0000 km. It also gives the same row 1 counts and the same 10 location facts.

## Row 1's record

- City "Kanpur", PIN 208001, private clinic. Its map point is 0.95 km from the Lucknow point, which is 73.7 km from its PIN's area (WP1 status `kept_unresolved`).
- 141 facts (capability 49, procedure 49, equipment 43; 99 distinct). This equals `silver/facts.parquet`.
- 0 facts mention a caesarean. The pattern is `c[- ]?section|caesar|cesar|\blscs\b`, which is broader than the tagger's. No text field in the row matches it either: not the name, description, specialties or fact arrays.
- Its 10 location and network facts, verbatim (the same on the warehouse and in pandas):
  - Located in Noida Sector 18
  - Located in Bareilly, Uttar Pradesh, India
  - Indira IVF Fertility Centre & Sam Hospital is located in Chandauli, Uttar Pradesh
  - In Vitro Fertilization (IVF) is offered at Indira IVF Center in Chandauli
  - Indira IVF operates an IVF Centre in Ghaziabad.
  - Reputed as one of the best IVF centers in Noida by patients
  - Part of Indira IVF Centers network including Lucknow center
  - Part of Indira IVF network with 150+ centers across 20+ states in India
  - Indira IVF network operates 165+ centers across India
  - Has 340+ IVF doctors in the Indira IVF network
- WP11 gives it two major flags:
  - `location_conflict`: "The record's map point is 74 km from its PIN code's area, and its city text does not settle which is right."
  - `possible_merge`: its text places it in Bareilly while its PIN and city put it in Kanpur Nagar, and its website is given by 4 other records in other districts.
- Our ranker lists it under "Also nearby, not verified" at 1.0 km.
- Reading: "Part of Indira IVF Centers network including Lucknow center" suggests the map point is the real Lucknow branch. The trap is a chain's facts merged into one record, not a wrong location. Do not say the clinic is in the wrong place.

## Our answer on the same point

`vf.rank.rank(load_index(), "emoc", resolve("226001"))` gives status `ok`, with grades from `student` (WP4) and the score marked provisional. The `dbapp/bundle` build of 22:36 gives the same three cards and quotes.

**Card 1: BNK Hospital**, private hospital, Lucknow.
- 3.5 km straight-line; location from the record, consistent with its PIN code, precise to about 1 km.
- Corroborated, High confidence: "corroborated by 5 facts, strong evidence, no warnings, precise location."
- Quotes, each verbatim in `silver/facts.parquet` by fact id:
  - caesarean section: "Performs Lower Segment Caesarean Section (LSCS)" (procedure)
  - blood bank or transfusion: "On-site Blood Bank" (equipment)
  - anaesthesia or operating theatre: "Modular Operation Theatre" (equipment)
- Score 0.926 = 0.5 x 1.0 evidence + 0.3 x 0.932 distance + 0.2 x 0.733 trust, no penalties.
- Sources: 5 web domains, official website bnkmedicare.com.
- Why here: "Ranked above Elite Hospital: stronger evidence (corroborated, against supported)."
- Could be wrong: "Web pages can be stale: the record says what pages claimed, not what is available today."
- 47 facts, 2 of them name a caesarean.

Cards 2 and 3: Elite Hospital (5.8 km, Medium) and Krishna Medical Centre (5.8 km, Medium).

Funnel: of 9,989 records, 9,671 have a usable location, 118 claim the service within 150 km, 14 are recommendable, and 9 are within the 50 km radius.

## Why not the nearer ones (for the "Why not" strip and for questions)

- Using the raw coordinates the SQL sees, 6 obstetrics rows lie nearer than BNK Hospital (3.499 km).
- One of the 6 names a caesarean: Sanjeevani Hospital, 3.48 km by its raw point, "Operation Delivery (LSCS)".
  - WP1 moved it to its PIN's centre, 56.6 km away, because the raw point was 59 km from its PIN's area.
  - Its record also places it in Varanasi while its PIN says Unnao (`possible_merge`, minor).
  - Our ranker scores it 13th, at 57.0 km, Low confidence.
- Our own list has one recommendable record nearer than BNK: Fatima Hospital, 3.4 km, quoting "Performs cesarean births".
  - It ranks 9th, Low confidence, with two penalties.
  - Its raw point was 281 km from its PIN's area, so it is shown at its PIN's centre and its position is approximate.
- So "nearest evidence, not nearest claim" holds for the top card. Checked over all 10,000 records, by both raw and pipeline coordinates: only Fatima Hospital and Sanjeevani Hospital are nearer than BNK Hospital and have a fact naming a caesarean, and both records' locations contradict their own PINs.

## On screen: wording and caveats

- **Blur the clinic's name** in the SQL result and on the record view. Show city "Kanpur", PIN 208001 and the distance.
- Say "**its record never mentions a caesarean**". Never say "it cannot do a caesarean" or "it does not offer one".
- Counter: "**141 facts, 0 mention a caesarean**". Both counts come from query 2 on the warehouse and match pandas.
- Location lines to show: "Located in Noida Sector 18" and "Located in Bareilly, Uttar Pradesh, India".
- Distance: the SQL row reads 0.95 km. The point is precise to about 2 km, so the voiceover should say "about a kilometre away", not "0.95 km".
- Rows 2 and 3: leave them unhighlighted and do not caption them.
  - Balrampur Hospital is a public hospital. Its record has no caesarean fact, but no evidence is not evidence of absence.
  - Never strike through a silent public hospital.
- Our card: "BNK Hospital, 3.5 km straight-line, High confidence", with the LSCS and blood bank quotes. Caption: "Same data. Same question. Nearest evidence, not nearest claim."
- Regenerate these numbers from the frozen bundle before filming: `python scripts/cold_open_check.py` re-runs everything except the warehouse.

## Reproduce

```bash
python scripts/cold_open_check.py                       # pandas + ranker, compared with the result below
python scripts/cold_open_check.py --run-warehouse --save F   # 2 warehouse queries; once only (Free Edition quota)
```

Warehouse queries used tonight: 2 of the allowed 3.

## Saved warehouse result

Compact form of the Statement API responses (`format JSON_ARRAY`, `disposition INLINE`). The check script reads this block.

<!-- cold-open-warehouse-result -->
```json
{
 "warehouse_id": "ee1fa7958ed99cf2",
 "run_at": "2026-11-21 23:03:13 EST",
 "query1": {
  "statement_id": "01f1e629-9ed6-132a-a17c-f2cba906b09d",
  "state": "SUCCEEDED",
  "rows": [
   {"name": "Indira IVF Kanpur", "city": "Kanpur", "pin": "208001", "km_straight_line": "0.95", "unique_id": "da54b8c0-9b5b-407c-8f23-d73104fc5cd5"},
   {"name": "Balrampur Hospital", "city": "Lucknow", "pin": "224176", "km_straight_line": "1.97", "unique_id": "705cc566-7ec3-40f3-aaec-4b114fc6d2da"},
   {"name": "Kamla Devi Hospital & Lucknow Test Tube Baby Centre", "city": "Lucknow", "pin": "226018", "km_straight_line": "2.3", "unique_id": "02a2f652-376e-4cf7-bd25-b33b328297a4"}
  ],
  "seconds": 8.4
 },
 "query2": {
  "statement_id": "01f1e629-a6d3-1046-9812-32d255b6907c",
  "state": "SUCCEEDED",
  "rows": [
   {"city": "Kanpur", "pin": "208001", "km_straight_line": "0.95", "n_facts": "141", "n_distinct_facts": "99", "n_facts_mentioning_caesarean": "0", "any_text_field_mentions_caesarean": "no", "location_facts": "Has 340+ IVF doctors in the Indira IVF network | In Vitro Fertilization (IVF) is offered at Indira IVF Center in Chandauli | Indira IVF Fertility Centre & Sam Hospital is located in Chandauli, Uttar Pradesh | Indira IVF network operates 165+ centers across India | Indira IVF operates an IVF Centre in Ghaziabad. | Located in Bareilly, Uttar Pradesh, India | Located in Noida Sector 18 | Part of Indira IVF Centers network including Lucknow center | Part of Indira IVF network with 150+ centers across 20+ states in India | Reputed as one of the best IVF centers in Noida by patients"}
  ],
  "seconds": 3.0
 }
}
```
