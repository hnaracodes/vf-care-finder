-- Cold open: the obvious tool, run on the sponsor's own table.
-- Question from the brief: "Which hospital near Lucknow can handle an emergency C-section?"
-- The obvious answer: rows whose specialty mentions obstetrics, nearest first, top 3.
--
-- Fixed point: PIN 226001 (Lucknow), as vf.locate.resolve("226001") returns it:
--   lat 26.84967105, lon 80.94397185, "the middle of its post offices", precise to about 2 km.
-- Distance: straight-line (haversine), Earth radius 6371.0088 km, the same as vf.geo.
--
-- Traps in the raw table handled here (docs/02_data_dictionary.md, docs/03_data_profile.md):
--   * latitude and longitude are DOUBLE in the Marketplace table (Unity Catalog schema, Nov 21)
--     but strings in the CSV export, where missing is the string 'null'. try_cast works on both
--     and turns 'null' or a malformed number into SQL NULL instead of failing the query.
--     Never compare them to 'null' directly: on a DOUBLE column that cast fails under ANSI mode.
--   * 88 junk rows (description fragments split into rows) have no country code 'IN':
--     the country filter drops them; they also have no coordinates and no specialties.
--   * 30 truncated rows have no coordinates: the distance filter drops them.
--   * a coordinate outside India would win nothing: a bounding box keeps lat 6 to 38, lon 68 to 98.
--   * 2 names in the export carry a NUL byte (two dental clinics): stripped from the displayed name.
--   * missing fact arrays are 'null' (or SQL NULL): from_json returns NULL and coalesce gives [].
--
-- Run once each on the Serverless Starter Warehouse; results in reports/cold_open.md.
-- Replicated in pandas on the raw CSV by scripts/cold_open_check.py.

-- Query 1: the foil (this is the one on screen)
WITH f AS (
  SELECT unique_id,
         regexp_replace(name, r'\x00', '') AS name,
         address_city,
         address_zipOrPostcode,
         try_cast(latitude AS DOUBLE) AS lat,
         try_cast(longitude AS DOUBLE) AS lon
  FROM databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset.facilities
  WHERE address_countryCode = 'IN'
    AND lower(specialties) LIKE '%obstetric%'
),
d AS (
  SELECT *,
         2 * 6371.0088 * asin(sqrt(
           pow(sin(radians(lat - 26.84967105) / 2), 2)
           + cos(radians(26.84967105)) * cos(radians(lat)) * pow(sin(radians(lon - 80.94397185) / 2), 2)
         )) AS km
  FROM f
  WHERE lat BETWEEN 6 AND 38 AND lon BETWEEN 68 AND 98
)
SELECT name,
       address_city AS city,
       address_zipOrPostcode AS pin,
       round(km, 2) AS km_straight_line,
       unique_id
FROM d
ORDER BY km, unique_id
LIMIT 3;

-- Query 2: row 1's own record, checked on the same table.
-- Picks row 1 with the same logic as query 1 (no id typed in), then counts its facts
-- (non-empty items of capability, procedure and equipment), how many mention a caesarean,
-- whether any text field of the row mentions one, and lists the facts that say where it is.
WITH f AS (
  SELECT *,
         try_cast(latitude AS DOUBLE) AS lat_d,
         try_cast(longitude AS DOUBLE) AS lon_d
  FROM databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset.facilities
  WHERE address_countryCode = 'IN'
    AND lower(specialties) LIKE '%obstetric%'
),
row1 AS (
  SELECT unique_id, address_city, address_zipOrPostcode, capability, procedure, equipment,
         2 * 6371.0088 * asin(sqrt(
           pow(sin(radians(lat_d - 26.84967105) / 2), 2)
           + cos(radians(26.84967105)) * cos(radians(lat_d)) * pow(sin(radians(lon_d - 80.94397185) / 2), 2)
         )) AS km,
         CASE WHEN lower(concat_ws(' ', name, description, specialties, capability, procedure, equipment))
                   RLIKE r'c[- ]?section|caesar|cesar|\blscs\b'
              THEN 'yes' ELSE 'no' END AS any_text_field_mentions_caesarean
  FROM f
  WHERE lat_d BETWEEN 6 AND 38 AND lon_d BETWEEN 68 AND 98
  ORDER BY km, unique_id
  LIMIT 1
),
facts AS (
  SELECT r.address_city, r.address_zipOrPostcode, r.km, r.any_text_field_mentions_caesarean, x AS fact
  FROM row1 r
  LATERAL VIEW explode(concat(
    coalesce(from_json(nullif(r.capability, 'null'), 'array<string>'), cast(array() AS array<string>)),
    coalesce(from_json(nullif(r.procedure, 'null'), 'array<string>'), cast(array() AS array<string>)),
    coalesce(from_json(nullif(r.equipment, 'null'), 'array<string>'), cast(array() AS array<string>))
  )) t AS x
  WHERE trim(x) <> ''
)
SELECT address_city AS city,
       address_zipOrPostcode AS pin,
       round(km, 2) AS km_straight_line,
       count(*) AS n_facts,
       count(DISTINCT fact) AS n_distinct_facts,
       count_if(lower(fact) RLIKE r'c[- ]?section|caesar|cesar|\blscs\b') AS n_facts_mentioning_caesarean,
       any_text_field_mentions_caesarean,
       concat_ws(' | ', array_sort(collect_set(
         CASE WHEN lower(fact) RLIKE 'located in|located at|branch|network|centers? in|centres? in' THEN fact END
       ))) AS location_facts
FROM facts
GROUP BY address_city, address_zipOrPostcode, km, any_text_field_mentions_caesarean;
