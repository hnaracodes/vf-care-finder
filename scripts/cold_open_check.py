"""Cold open check (D044 item 2): the obvious SQL query, replicated and compared.

The cold open shows the "obvious tool" on the sponsor's table: specialty mentions
obstetrics, nearest to PIN 226001 (Lucknow), top 3. ``sql/cold_open_lucknow.sql``
holds that query (query 1) and a check of row 1's own record (query 2).

This script:

1. replicates both queries in pandas on the raw CSV (all 10,088 rows, via vf.io),
   with the same filters, the same point and the same haversine;
2. compares them with the warehouse result saved in ``reports/cold_open.md``
   (or a JSON file given with ``--warehouse-json``): same top 3, same distances,
   same fact counts for row 1;
3. runs our ranker on the same point (vf.locate.resolve + vf.rank.rank, emoc) and
   prints card 1 with its quotes, checking each quote is verbatim in silver/facts.

Usage:
    python scripts/cold_open_check.py                      # compare with the saved result
    python scripts/cold_open_check.py --warehouse-json F   # compare with a saved response file
    python scripts/cold_open_check.py --run-warehouse --save F
        # run both statements on the warehouse (2 queries; Free Edition has a daily
        # quota, so do this once before filming) and save the responses to F

Exit code 0 only if every check passes. Reads data, writes nothing unless --save.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vf import io, locate, paths, rank  # noqa: E402

SQL_FILE = ROOT / "sql" / "cold_open_lucknow.sql"
REPORT = ROOT / "reports" / "cold_open.md"
RESULT_MARKER = "<!-- cold-open-warehouse-result -->"
WAREHOUSE_ID = "ee1fa7958ed99cf2"
PIN = "226001"
# The resolver's point for PIN 226001, as typed into the SQL. The check below
# fails if the resolver ever moves it, so the SQL and the app stay on one point.
SQL_LAT, SQL_LON = 26.84967105, 80.94397185
EARTH_R = 6371.0088
CSECTION_RX = r"c[- ]?section|caesar|cesar|\blscs\b"
LOCATION_RX = r"located in|located at|branch|network|centers? in|centres? in"


def haversine(lat1, lon1, lat2, lon2):
    p1, p2 = np.radians(lat1), np.radians(lat2)
    a = np.sin((p2 - p1) / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(np.radians(lon2 - lon1) / 2) ** 2
    return 2 * EARTH_R * np.arcsin(np.sqrt(a))


def try_cast(s: pd.Series) -> pd.Series:
    """SQL try_cast(x AS DOUBLE): 'null', empty or malformed becomes NaN."""
    return pd.to_numeric(s.where(~s.map(io.is_null)), errors="coerce")


# ---------------------------------------------------------------------------
# pandas replication of the two queries
# ---------------------------------------------------------------------------

def foil_candidates(raw: pd.DataFrame, lat0: float, lon0: float, country_filter: bool = True) -> pd.DataFrame:
    """Query 1 without the LIMIT: every row that passes its filters, nearest first."""
    lat, lon = try_cast(raw["latitude"]), try_cast(raw["longitude"])
    spec = raw["specialties"].where(~raw["specialties"].map(io.is_null), "")
    m = spec.str.lower().str.contains("obstetric", regex=False)
    m &= lat.between(6, 38) & lon.between(68, 98)
    if country_filter:
        m &= raw["address_countryCode"] == "IN"
    out = raw.loc[m, ["unique_id", "name", "address_city", "address_zipOrPostcode", "address_countryCode"]].copy()
    out["name"] = out["name"].str.replace("\x00", "", regex=False)
    out["km"] = haversine(lat0, lon0, lat[m], lon[m])
    return out.sort_values(["km", "unique_id"], kind="mergesort")


def row_facts(row: pd.Series) -> list[str]:
    """Query 2's fact list: non-empty items of capability, procedure, equipment."""
    out = []
    for f in ("capability", "procedure", "equipment"):
        out += [x for x in io.parse_list(row[f]) if isinstance(x, str) and x.strip() != ""]
    return out


def row1_summary(row: pd.Series, km: float) -> dict:
    facts = row_facts(row)
    low = [x.lower() for x in facts]
    text = " ".join(str(row[c]) for c in ("name", "description", "specialties", "capability", "procedure", "equipment")
                    if not io.is_null(row[c])).lower()
    loc = sorted({x for x, l in zip(facts, low) if re.search(LOCATION_RX, l)})
    return {
        "city": row["address_city"],
        "pin": row["address_zipOrPostcode"],
        "km_straight_line": round(float(km), 2),
        "n_facts": len(facts),
        "n_distinct_facts": len(set(facts)),
        "n_facts_mentioning_caesarean": sum(bool(re.search(CSECTION_RX, l)) for l in low),
        "any_text_field_mentions_caesarean": "yes" if re.search(CSECTION_RX, text) else "no",
        "location_facts": loc,
    }


# ---------------------------------------------------------------------------
# warehouse: run once, or read the saved responses
# ---------------------------------------------------------------------------

def sql_statements() -> list[str]:
    body = "\n".join(l for l in SQL_FILE.read_text().splitlines() if not l.lstrip().startswith("--"))
    stmts = [s.strip() for s in body.split(";") if s.strip()]
    if len(stmts) != 2:
        raise SystemExit(f"expected 2 statements in {SQL_FILE}, found {len(stmts)}")
    return stmts


def _cli(args: list[str], payload: dict | None = None) -> dict:
    cmd = ["databricks", "api", *args]
    if payload is not None:
        cmd += ["--json", json.dumps(payload)]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def run_on_warehouse(stmt: str) -> dict:
    r = _cli(["post", "/api/2.0/sql/statements"], {
        "warehouse_id": WAREHOUSE_ID, "statement": stmt, "wait_timeout": "50s",
        "on_wait_timeout": "CONTINUE", "format": "JSON_ARRAY", "disposition": "INLINE",
    })
    sid = r.get("statement_id")
    t0 = time.time()
    while r.get("status", {}).get("state") in ("PENDING", "RUNNING") and time.time() - t0 < 600:
        time.sleep(5)  # polling reads the same statement; it is not a new query
        r = _cli(["get", f"/api/2.0/sql/statements/{sid}"])
    return r


def read_saved(path: Path | None) -> dict:
    if path is not None:
        return json.loads(path.read_text())
    text = REPORT.read_text()
    if RESULT_MARKER not in text:
        raise SystemExit(f"no saved warehouse result in {REPORT}; pass --warehouse-json")
    block = text.split(RESULT_MARKER, 1)[1]
    m = re.search(r"```json\n(.*?)\n```", block, re.S)
    return json.loads(m.group(1))


def rows_of(resp: dict) -> list[dict]:
    """Statement API response (or the compact form saved in the report) to row dicts."""
    if "rows" in resp:
        return resp["rows"]
    state = resp.get("status", {}).get("state")
    if state != "SUCCEEDED":
        raise SystemExit(f"statement {resp.get('statement_id')} did not succeed: {resp.get('status')}")
    cols = [c["name"] for c in resp["manifest"]["schema"]["columns"]]
    return [dict(zip(cols, r)) for r in resp["result"].get("data_array", [])]


def compact(resp: dict) -> dict:
    return {"statement_id": resp.get("statement_id"), "state": resp.get("status", {}).get("state"),
            "rows": rows_of(resp)}


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--warehouse-json", type=Path)
    ap.add_argument("--run-warehouse", action="store_true")
    ap.add_argument("--save", type=Path)
    ap.add_argument("--json", type=Path, help="write the local results here (for the report)")
    a = ap.parse_args()

    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, evidence: str):
        checks.append((name, bool(ok), evidence))

    # 0. the point
    res = locate.resolve(PIN)
    check("resolver point equals the SQL literal",
          res.status == "ok" and abs(res.lat - SQL_LAT) < 1e-7 and abs(res.lon - SQL_LON) < 1e-7,
          f"resolve('{PIN}') = {res.lat:.8f}, {res.lon:.8f}, {res.precision_km} km, '{res.label}'")

    # 1. pandas replication on all 10,088 raw rows
    raw = io.read_facilities_raw()
    status = io.facility_row_status(raw)
    cand = foil_candidates(raw, SQL_LAT, SQL_LON)
    top = cand.head(3)
    no_country = foil_candidates(raw, SQL_LAT, SQL_LON, country_filter=False)
    junk_pass = int((no_country["address_countryCode"] != "IN").sum())
    trunc_ids = set(raw.loc[status == "truncated", "unique_id"])
    check("no junk row passes the filters, even without the country filter", junk_pass == 0,
          f"{len(raw):,} raw rows, {int((status == 'junk').sum())} junk; junk rows passing without the country filter: {junk_pass}")
    check("no truncated row passes", not (set(cand["unique_id"]) & trunc_ids),
          f"{len(trunc_ids)} truncated rows; {len(cand):,} rows pass query 1's filters")
    m_exact = raw["specialties"].str.contains("gynecologyAndObstetrics", regex=False)
    m_exact &= raw["address_countryCode"] == "IN"

    r1 = raw.loc[raw["unique_id"] == top.iloc[0]["unique_id"]].iloc[0]
    local_q2 = row1_summary(r1, top.iloc[0]["km"])

    # 2. the warehouse result
    if a.run_warehouse:
        if a.save is None:
            raise SystemExit("--run-warehouse needs --save PATH")
        stmts = sql_statements()
        resp = {"query1": run_on_warehouse(stmts[0]), "query2": run_on_warehouse(stmts[1]),
                "warehouse_id": WAREHOUSE_ID, "run_at": time.strftime("%Y-%m-%d %H:%M:%S %Z")}
        a.save.write_text(json.dumps(resp, indent=1, ensure_ascii=False))
        print(f"saved warehouse responses to {a.save}")
        saved = resp
    else:
        saved = read_saved(a.warehouse_json)
    wh1, wh2 = rows_of(saved["query1"]), rows_of(saved["query2"])

    same_ids = [r["unique_id"] for r in wh1] == list(top["unique_id"])
    km_diff = max(abs(float(w["km_straight_line"]) - round(float(k), 2)) for w, k in zip(wh1, top["km"])) if wh1 else 99
    same_fields = all(w["name"] == t["name"] and w["city"] == t["address_city"] and w["pin"] == t["address_zipOrPostcode"]
                      for w, (_, t) in zip(wh1, top.iterrows()))
    check("warehouse and pandas give the same top 3 (ids, order)", same_ids and len(wh1) == 3,
          "; ".join(f"{w['unique_id'][:8]} {w['km_straight_line']} km" for w in wh1))
    check("same names, cities, PINs; distances within 0.01 km", same_fields and km_diff <= 0.01,
          f"max distance difference {km_diff:.4f} km")
    w2 = wh2[0] if wh2 else {}
    keys = ["city", "pin", "n_facts", "n_distinct_facts", "n_facts_mentioning_caesarean", "any_text_field_mentions_caesarean"]
    diffs = {k: (w2.get(k), local_q2[k]) for k in keys if str(w2.get(k)) != str(local_q2[k])}
    if abs(float(w2.get("km_straight_line", 99)) - local_q2["km_straight_line"]) > 0.01:
        diffs["km_straight_line"] = (w2.get("km_straight_line"), local_q2["km_straight_line"])
    wh_loc = sorted(x for x in (w2.get("location_facts") or "").split(" | ") if x)
    if wh_loc != local_q2["location_facts"]:
        diffs["location_facts"] = (len(wh_loc), len(local_q2["location_facts"]))
    check("row 1 record check: warehouse equals pandas", not diffs, json.dumps(diffs) if diffs else
          f"{local_q2['n_facts']} facts, {local_q2['n_facts_mentioning_caesarean']} mention a caesarean, "
          f"any text field: {local_q2['any_text_field_mentions_caesarean']}")

    # 3. row 1 in our pipeline: silver facts, grade, flags
    silver, gold = paths.silver_dir(), paths.gold_dir()
    facts = pd.read_parquet(silver / "facts.parquet", columns=["fact_id", "facility_id", "field", "text"])
    sfac = pd.read_parquet(silver / "facilities.parquet")
    grades = pd.read_parquet(gold / "capability_grades.parquet")
    flags = pd.read_parquet(gold / "facility_flags.parquet")
    emoc = grades[grades["service_line"] == "emoc"].set_index("facility_id")
    fid1 = r1["unique_id"]
    f1 = facts[facts["facility_id"] == fid1]
    check("silver facts agree with the raw row on row 1's fact count", len(f1) == local_q2["n_facts"],
          f"silver/facts.parquet: {len(f1)} facts ({f1['field'].value_counts().to_dict()})")
    foil_rows = []
    for _, t in top.iterrows():
        fid = t["unique_id"]
        s = sfac[sfac["facility_id"] == fid].iloc[0]
        g = emoc.loc[fid] if fid in emoc.index else None
        fl = flags[flags["facility_id"] == fid]
        ff = facts[facts["facility_id"] == fid]
        foil_rows.append({
            "unique_id": fid, "name": t["name"], "city": t["address_city"], "pin": t["address_zipOrPostcode"],
            "km": round(float(t["km"]), 2), "operator": s["operator_type"], "facility_type": s["facility_type"],
            "coord_check": s["coord_check"],
            "coord_pin_km": None if pd.isna(s["coord_pin_km"]) else round(float(s["coord_pin_km"]), 1),
            "n_facts": int(len(ff)),
            "n_facts_caesarean": int(ff["text"].str.lower().str.contains(CSECTION_RX, regex=True).sum()),
            "emoc_grade": None if g is None else int(g["grade"]),
            "emoc_grade_label": None if g is None else str(g["grade_label"]),
            "flags": fl[["flag", "severity", "reason"]].to_dict("records"),
        })

    # 4. our ranker on the same point
    idx = rank.load_index()
    ans = rank.rank(idx, "emoc", res).to_dict()
    card = ans["results"][0] if ans["results"] else None
    fact_text = dict(zip(facts["fact_id"], facts["text"]))
    verbatim = card is not None and all(fact_text.get(e["fact_id"]) == e["quote"] for e in card["evidence"])
    check("ranker returns cards for emoc at PIN 226001", ans["status"] == "ok" and card is not None,
          f"status {ans['status']}; cards: " + "; ".join(f"{c['name']} {c['distance_km']} km {c['confidence']}"
                                                     for c in ans["results"]))
    check("card 1's quotes are verbatim in silver/facts.parquet", verbatim,
          f"{len(card['evidence']) if card else 0} quotes checked by fact_id")
    in_foil = card is not None and card["facility_id"] in set(top["unique_id"])
    gates = {g["gate"]: g.get("n") for g in ans["how_we_decided"].get("gates", [])}
    of = next((g.get("of") for g in ans["how_we_decided"].get("gates", []) if g.get("of")), None)
    card_rank_in_foil = None
    if card is not None:
        pos = list(cand["unique_id"]).index(card["facility_id"]) if card["facility_id"] in set(cand["unique_id"]) else None
        card_rank_in_foil = None if pos is None else pos + 1

    # 5. the neighbourhood: how many foil candidates are nearer than card 1
    # (raw coordinates, as the SQL sees them; card 1 itself excluded)
    nearer, nearer_cs, card_raw_km = cand.iloc[:0], [], None
    if card is not None and card["facility_id"] in set(cand["unique_id"]):
        card_raw_km = float(cand.loc[cand["unique_id"] == card["facility_id"], "km"].iloc[0])
        nearer = cand[(cand["km"] < card_raw_km) & (cand["unique_id"] != card["facility_id"])]
        for _, t in nearer.iterrows():
            ff = facts[facts["facility_id"] == t["unique_id"]]
            hits = ff.loc[ff["text"].str.lower().str.contains(CSECTION_RX, regex=True), "text"].tolist()
            if hits:
                g = emoc.loc[t["unique_id"]] if t["unique_id"] in emoc.index else None
                nearer_cs.append({"name": t["name"], "km": round(float(t["km"]), 2), "quotes": hits,
                                  "emoc_grade_label": None if g is None else str(g["grade_label"]),
                                  "flags": flags.loc[flags["facility_id"] == t["unique_id"], ["flag", "severity"]]
                                  .to_dict("records")})

    out = {
        "resolution": res.to_dict(),
        "n_raw_rows": int(len(raw)), "n_junk": int((status == "junk").sum()),
        "n_truncated": int((status == "truncated").sum()),
        "n_pass_query1": int(len(cand)), "n_listing_gynecologyAndObstetrics": int(m_exact.sum()),
        "foil_top3": foil_rows,
        "row1_record": local_q2,
        "warehouse": {"query1": wh1, "query2": wh2,
                      "statement_ids": [saved["query1"].get("statement_id"), saved["query2"].get("statement_id")],
                      "run_at": saved.get("run_at")},
        "ranker": {"status": ans["status"], "gates": gates, "gate_of": of,
                   "score_status": ans["how_we_decided"].get("score_status"),
                   "grades_method": ans["how_we_decided"].get("grades_method"),
                   "cards": [{k: c[k] for k in ("facility_id", "name", "distance_km", "confidence", "grade_label")}
                             for c in ans["results"]],
                   "card1": card, "card1_in_foil_top3": in_foil, "card1_position_in_foil_list": card_rank_in_foil,
                   "card1_km_from_raw_coordinates": None if card_raw_km is None else round(card_raw_km, 3),
                   "foil_rows_nearer_than_card1": int(len(nearer)),
                   "foil_rows_nearer_with_a_caesarean_fact": nearer_cs},
    }
    if a.json:
        a.json.write_text(json.dumps(out, indent=1, ensure_ascii=False, default=str))

    # summary
    print(f"Point: {res.label} -> {res.lat:.8f}, {res.lon:.8f} (precise to about {res.precision_km:g} km)")
    print(f"Raw rows {len(raw):,} (junk {out['n_junk']}, truncated {out['n_truncated']}); "
          f"rows passing query 1's filters: {len(cand):,}")
    print("Obvious query, top 3 (pandas):")
    for i, r in enumerate(foil_rows, 1):
        print(f"  {i}. {r['name']} | city {r['city']} | PIN {r['pin']} | {r['km']:.2f} km | "
              f"{r['n_facts']} facts, {r['n_facts_caesarean']} mention a caesarean | emoc {r['emoc_grade_label']} | "
              f"location {r['coord_check']} ({r['coord_pin_km']} km from its PIN)")
    print(f"Row 1 record: {local_q2['n_facts']} facts ({local_q2['n_distinct_facts']} distinct), "
          f"{local_q2['n_facts_mentioning_caesarean']} mention a caesarean; any text field: "
          f"{local_q2['any_text_field_mentions_caesarean']}")
    for x in local_q2["location_facts"]:
        print(f"    - {x}")
    print(f"Row 1 flags: {[(f['flag'], f['severity']) for f in foil_rows[0]['flags']]}")
    if card:
        print(f"Our card 1: {card['name']} | {card['distance_km']} km | {card['confidence']} | {card['grade_label']}")
        for e in card["evidence"]:
            print(f"    {e['requirement']}: \"{e['quote']}\" ({e['field']})")
    print(f"Funnel: of {of} -> " + ", ".join(f"{k} {v}" for k, v in gates.items()))
    print(f"Foil rows nearer than card 1 (raw coordinates, {card_raw_km if card_raw_km is None else round(card_raw_km, 3)} km): "
          f"{len(nearer)}; with a fact naming a caesarean: {len(nearer_cs)}")
    for x in nearer_cs:
        print(f"    {x['name']} {x['km']} km, emoc {x['emoc_grade_label']}, flags {[(f['flag'], f['severity']) for f in x['flags']]}: {x['quotes']}")
    print("\nChecks:")
    for name, ok, ev in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {ev}")
    return 0 if all(ok for _, ok, _ in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
