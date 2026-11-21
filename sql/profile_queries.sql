-- Profiling queries run in the Databricks SQL editor on Nov 21, 2026.
-- Kept for provenance: docs/03_data_profile.md cites their results.
-- Reformatted for readability (the originals packed results into JSON cells); the logic is the same.
-- They run against the shared tables, which include the 30 truncated rows,
-- so some counts are slightly higher than the local numbers.
--
-- Catalog: databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset

-- 1. Value distributions for the categorical fields
WITH t AS (SELECT * FROM databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset.facilities),
u AS (
  SELECT 'org' f, organization_type v FROM t
  UNION ALL SELECT 'ftype', facilityTypeId FROM t
  UNION ALL SELECT 'optype', operatorTypeId FROM t
  UNION ALL SELECT 'country', address_countryCode FROM t
  UNION ALL SELECT 'vol', acceptsVolunteers FROM t
  UNION ALL SELECT 'affil', affiliationTypeIds FROM t
  UNION ALL SELECT 'docs', numberDoctors FROM t
  UNION ALL SELECT 'beds', capacity FROM t
  UNION ALL SELECT 'yr', yearEstablished FROM t
  UNION ALL SELECT 'source', source FROM t
  UNION ALL SELECT 'area', area FROM t
  UNION ALL SELECT 'staff', affiliated_staff_presence FROM t
  UNION ALL SELECT 'recency', recency_of_page_update FROM t
),
g AS (SELECT f, left(coalesce(v, 'NULL'), 26) v, count(*) c FROM u GROUP BY 1, 2),
r AS (SELECT *, row_number() OVER (PARTITION BY f ORDER BY c DESC) rn FROM g)
SELECT f, count(*) nd,
       concat_ws(' ; ', collect_list(CASE WHEN rn <= 6 THEN concat(v, '=', c) END)) top
FROM r GROUP BY f ORDER BY f;

-- 2. Top specialties (raw mentions, NOT deduped per facility) and top state strings
WITH t AS (SELECT * FROM databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset.facilities
           WHERE address_countryCode = 'IN'),
s AS (SELECT explode(from_json(specialties, 'array<string>')) v FROM t),
sg AS (SELECT v, count(*) c, row_number() OVER (ORDER BY count(*) DESC) rn FROM s GROUP BY v),
st AS (SELECT address_stateOrRegion v, count(*) c, row_number() OVER (ORDER BY count(*) DESC) rn FROM t GROUP BY 1)
SELECT concat('spec_', floor((rn - 1) / 9)) k, concat_ws(' ; ', collect_list(concat(v, '=', c))) v
FROM sg WHERE rn <= 45 GROUP BY 1
UNION ALL
SELECT concat('state_', floor((rn - 1) / 9)), concat_ws(' ; ', collect_list(concat(v, '=', c)))
FROM st WHERE rn <= 45 GROUP BY 1
UNION ALL SELECT 'n_spec_distinct', cast(count(*) AS string) FROM sg
UNION ALL SELECT 'n_state_distinct', cast(count(*) AS string) FROM st
ORDER BY 1;

-- 3. Fill rates and array sizes
WITH t AS (
  SELECT *, from_json(specialties, 'array<string>') sp, from_json(procedure, 'array<string>') pr,
         from_json(equipment, 'array<string>') eq, from_json(capability, 'array<string>') cp
  FROM databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset.facilities
  WHERE address_countryCode = 'IN')
SELECT count_if(size(sp) > 0) spec, count_if(size(pr) > 0) proc, count_if(size(eq) > 0) equip, count_if(size(cp) > 0) cap,
       round(avg(CASE WHEN size(sp) > 0 THEN size(array_distinct(sp)) END), 1) spec_avg,
       round(avg(CASE WHEN size(pr) > 0 THEN size(pr) END), 1) proc_avg,
       round(avg(CASE WHEN size(eq) > 0 THEN size(eq) END), 1) equip_avg,
       round(avg(CASE WHEN size(cp) > 0 THEN size(cp) END), 1) cap_avg,
       count_if(latitude BETWEEN 6 AND 38 AND longitude BETWEEN 68 AND 98) latok,
       count_if(address_zipOrPostcode RLIKE '^[0-9]{6}$') zip6,
       count(DISTINCT address_zipOrPostcode) nzip, count(DISTINCT address_city) ncity,
       count_if(numberDoctors RLIKE '^[0-9]+$') docs, count_if(capacity RLIKE '^[0-9]+$') beds,
       count_if(operatorTypeId = 'public') pub
FROM t;

-- 4. Keyword evidence counts over the fact fields
WITH t AS (
  SELECT lower(concat_ws(' ', capability, procedure, equipment, description)) x
  FROM databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset.facilities
  WHERE address_countryCode = 'IN')
SELECT count_if(x RLIKE r'\bicu\b|intensive care') icu,
       count_if(x RLIKE r'\bnicu\b|neonatal intensive') nicu,
       count_if(x RLIKE 'dialysis') dialysis,
       count_if(x RLIKE 'c-section|caesarean|cesarean|lscs') csec,
       count_if(x RLIKE 'blood bank') bloodbank,
       count_if(x RLIKE 'ventilator') vent,
       count_if(x RLIKE r'\bmri\b') mri,
       count_if(x RLIKE r'\bct\b') ct,
       count_if(x RLIKE 'cath lab|angioplasty') cath,
       count_if(x RLIKE 'chemotherapy|radiotherapy|radiation therapy') onc,
       count_if(x RLIKE '24/7|24x7|24 hours|24 hrs|24-hour') h24,
       count_if(x RLIKE 'ambulance') amb,
       count_if(x RLIKE 'ayushman|pmjay|cghs') scheme,
       count_if(x RLIKE 'cashless|insurance') ins,
       count_if(x RLIKE 'operation theat|operating theat|operating room') ot,
       count_if(x RLIKE 'anesthe|anaesthe') anes,
       count_if(x RLIKE 'cataract') cataract,
       count_if(x RLIKE 'ultrasound|sonography') usg,
       count_if(x RLIKE 'x-ray|xray') xray,
       count_if(x RLIKE 'teleconsult|telemedicine|online consult|video consult') tele,
       count_if(x RLIKE 'emergency|trauma|casualty') emerg,
       count_if(x RLIKE 'rated [0-9]|ratings|reviews') ratings,
       count_if(x RLIKE 'wheelchair') wheel,
       count_if(x RLIKE 'delivery|labour|labor room|maternity') matern
FROM t;

-- 5. District coverage through an exact PIN join (each PIN assigned to its most common district)
WITH p AS (
  SELECT cast(pincode AS string) pin, statename, district, count(*) c
  FROM databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset.india_post_pincode_directory
  GROUP BY 1, 2, 3),
p1 AS (SELECT * FROM p QUALIFY row_number() OVER (PARTITION BY pin ORDER BY c DESC) = 1),
f AS (SELECT * FROM databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset.facilities
      WHERE address_countryCode = 'IN'),
j AS (
  SELECT p1.statename, p1.district, count(*) n,
         count_if(f.operatorTypeId = 'public') pub, count_if(f.facilityTypeId = 'hospital') hosp
  FROM f JOIN p1 ON f.address_zipOrPostcode = p1.pin GROUP BY 1, 2),
d AS (SELECT count(DISTINCT statename, district) nd, count(DISTINCT pin) npin FROM p),
amb AS (SELECT count(*) na FROM (SELECT pin FROM p GROUP BY pin HAVING count(*) > 1)),
top AS (SELECT sum(n) s FROM (SELECT n FROM j ORDER BY n DESC LIMIT 20))
SELECT sum(n) matched, count(*) districts_with_fac, max(nd) total_districts, max(npin) pins,
       max(na) ambig_pins, percentile(n, 0.5) median, count_if(n <= 2) le2, count_if(n <= 5) le5,
       count_if(n >= 50) ge50, max(s) top20, count_if(hosp = 0) no_hosp, count_if(pub = 0) no_pub
FROM j CROSS JOIN d CROSS JOIN amb CROSS JOIN top;
