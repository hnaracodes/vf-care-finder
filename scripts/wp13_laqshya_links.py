"""WP13 / D044 item 5: link LaQshya-certified units to our facility records.

Reads (shared data dir):
  silver/ext_laqshya.parquet, silver/facilities.parquet,
  silver/pin_crosswalk.parquet, silver/district_crosswalk.parquet,
  gold/capability_grades.parquet (emoc grade, and whether a fact names a caesarean)
  data/labels/laqshya_link_review.csv (hand review; created on the first run)

Writes (atomic, with .meta.json sidecars):
  silver/laqshya_links.parquet       one row per candidate (LaQshya unit x our record)
  labels/laqshya_link_review.csv     every candidate, review decisions preserved across runs
  reports/laqshya_links.md           counts, precision, the Nashik case end to end

Linking never uses the OSM point matches in ext_laqshya (red team: 715 of 717
placements came from place-name tokens). Lines are produced only for links a
reviewer marked correct.

Usage:
  python scripts/wp13_laqshya_links.py            # build, merge the review, report
  python scripts/wp13_laqshya_links.py --as-of 2026-11-21
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from vf import paths, public_list as pl  # noqa: E402

SCRIPT = "scripts/wp13_laqshya_links.py"
REVIEW_COLS = ["facility_uid", "facility_id", "lq_name", "lq_type", "lq_district_raw", "district_key",
               "our_name", "our_operator", "our_type", "our_pin", "our_city", "our_district_keys",
               "match_scope", "core_set", "core_sort", "link_score", "rank_in_unit", "n_candidates",
               "lq_rows", "review", "review_reason", "reviewed_by"]
NASHIK_FID = "6720cccc-3f42-4bf6-9bc7-33a16492741c"


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def git_head():
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=paths.code_root(), capture_output=True,
                              text=True, check=True).stdout.strip()
    except Exception:
        return None


def write_atomic(path: Path, write) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        write(tmp)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def write_meta(path: Path, rows: int, extra: dict) -> None:
    meta = {"output": str(path.relative_to(paths.data_dir())) if paths.data_dir() in path.parents else str(path),
            "rows": int(rows), "created_at_utc": utcnow(), "git_commit": git_head(), "script": SCRIPT,
            "link_version": pl.LINK_VERSION, **extra}
    body = json.dumps(meta, indent=2, default=str) + "\n"
    write_atomic(path.with_name(path.name + ".meta.json"), lambda p: p.write_text(body))


def lq_rows_text(certs) -> str:
    return "; ".join(f"p{c['page']} r{c['sno']} {c['department'] or 'dept not stated'} "
                     f"{c['certification_status']} to {c['validity_date']}" for c in certs)


def merge_review(cands: pd.DataFrame, review_path: Path) -> pd.DataFrame:
    """Review CSV rows for every candidate, keeping earlier decisions."""
    old = pd.read_csv(review_path, dtype=str, keep_default_na=False) if review_path.exists() else None
    c = cands.copy()
    c["lq_rows"] = c["certs"].map(lq_rows_text)
    c["our_district_keys"] = c["our_district_keys"].map(lambda ks: " / ".join(ks))
    c = c[[x for x in REVIEW_COLS if x not in ("review", "review_reason", "reviewed_by")]]
    if old is not None and len(old):
        keep = old[["facility_uid", "facility_id", "review", "review_reason", "reviewed_by"]]
        c = c.merge(keep, on=["facility_uid", "facility_id"], how="left")
        stale = old.merge(c[["facility_uid", "facility_id"]], how="left", indicator=True)
        n_stale = int((stale["_merge"] == "left_only").sum())
        if n_stale:
            print(f"  note: {n_stale} reviewed pairs are no longer candidates (kept out of the CSV)")
    for col in ("review", "review_reason", "reviewed_by"):
        if col not in c:
            c[col] = ""
        c[col] = c[col].fillna("")
    return c[REVIEW_COLS]


def caesarean_named(grades: pd.DataFrame) -> pd.Series:
    e = grades[grades["service_line"] == "emoc"].set_index("facility_id")
    return e["tags_present"].map(lambda t: "csection" in list(t) if t is not None else False)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--as-of", default=None, help="date for 'current' certificates (default today)")
    args = ap.parse_args(argv)
    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()

    silver, gold, labels = paths.silver_dir(), paths.gold_dir(), paths.labels_dir()
    lq = pd.read_parquet(silver / "ext_laqshya.parquet")
    fac = pd.read_parquet(silver / "facilities.parquet")
    pdk = pl.pin_district_keys(pd.read_parquet(silver / "pin_crosswalk.parquet"),
                               pd.read_parquet(silver / "district_crosswalk.parquet"))
    grades = pd.read_parquet(gold / "capability_grades.parquet")

    pool = pl.public_pool(fac)
    cands = pl.candidate_links(lq, fac, pdk)
    print(f"LaQshya units {lq.facility_uid.nunique()}, rows {len(lq)}; our public pool {len(pool)} "
          f"({pool.pool_reason.value_counts().to_dict()})")
    print(f"candidates {len(cands)} ({cands.match_scope.value_counts().to_dict()}), "
          f"units {cands.facility_uid.nunique()}, records {cands.facility_id.nunique()}")

    review_path = labels / pl.REVIEW_FILE
    rev = merge_review(cands, review_path)
    write_atomic(review_path, lambda p: rev.to_csv(p, index=False))
    write_meta(review_path, len(rev), {
        "grain": "one row per candidate link (LaQshya facility_uid x our facility_id)",
        "review_values": list(pl.REVIEW_VALUES),
        "reviewed": int(rev["review"].isin(pl.REVIEW_VALUES).sum()),
    })

    links = pl.apply_review(cands, rev.replace({"": None}))
    emoc = grades[grades["service_line"] == "emoc"].set_index("facility_id")
    links["emoc_grade"] = links["facility_id"].map(emoc["grade"]).astype("Int64")
    links["emoc_grade_label"] = links["facility_id"].map(emoc["grade_label"])
    links["caesarean_named"] = links["facility_id"].map(caesarean_named(grades)).fillna(False).astype(bool)
    links["has_current_maternity_ot"] = links["certs"].map(
        lambda cs: any(c.get("department") == "Maternity OT" and pl.is_current(c, as_of) for c in cs))
    links["link_version"] = pl.LINK_VERSION
    out = silver / pl.LINKS_FILE
    write_atomic(out, lambda p: links.to_parquet(p, index=False))
    summ = pl.summarise(links, as_of)
    write_meta(out, len(links), {
        "columns": list(links.columns),
        "grain": "one row per candidate link (LaQshya facility_uid x our facility_id); lines only for review == correct",
        "inputs": ["silver/ext_laqshya.parquet", "silver/facilities.parquet", "silver/pin_crosswalk.parquet",
                   "silver/district_crosswalk.parquet", "gold/capability_grades.parquet",
                   "labels/" + pl.REVIEW_FILE],
        "source_url": pl.SOURCE_URL, "list_date": pl.LIST_DATE.isoformat(), "retrieved": pl.RETRIEVED.isoformat(),
        "as_of": as_of.isoformat(), "osm_points_used": False,
        "summary": {k: v for k, v in summ.items() if k != "current_ot_facility_ids"},
    })

    report = build_report(lq, fac, pool, links, summ, as_of, grades)
    rpath = paths.code_root() / "reports" / "laqshya_links.md"
    write_atomic(rpath, lambda p: p.write_text(report))

    print(f"review: {summ['review_counts']}")
    if summ["precision_correct_over_decided"] is not None:
        print(f"precision (correct / (correct + wrong)): {summ['precision_correct_over_decided']:.3f}")
    print(f"correct links {summ['correct_links']} -> records {summ['correct_records']}; "
          f"with a current maternity OT certificate ({as_of}): {summ['records_with_current_maternity_ot']}")
    ok = links[links.review.eq("correct") & links.has_current_maternity_ot].drop_duplicates("facility_id")
    print(f"  of those, a fact names a caesarean: {int(ok.caesarean_named.sum())}")
    print(f"wrote {out}\nwrote {review_path}\nwrote {rpath}")
    return 0


def _pct(a, b):
    return f"{a} of {b} ({100 * a / b:.0f}%)" if b else f"{a} of {b}"


def build_report(lq, fac, pool, links, summ, as_of, grades) -> str:
    rc = summ["review_counts"]
    n = len(links)
    c, w, u = rc.get("correct", 0), rc.get("wrong", 0), rc.get("unsure", 0)
    unrev = rc.get("unreviewed", 0)
    ok = links[links.review.eq("correct")]
    ok_rec = ok.drop_duplicates("facility_id")
    cur = ok[ok.has_current_maternity_ot].drop_duplicates("facility_id")
    cur_cs = cur[cur.caesarean_named]
    by_scope = links.groupby(["match_scope", "review"]).size().unstack(fill_value=0)
    pl_obj = pl.PublicList(links)

    L = []
    L.append("# LaQshya links: the government list beside our records\n\n")
    L.append(f"Built by `{SCRIPT}` on {utcnow()}. Certificates are called current when valid on or after "
             f"{as_of.isoformat()}. Source: LaQshya master list of {pl.fmt_date(pl.LIST_DATE)} (NHSRC), "
             f"retrieved {pl.RETRIEVED.isoformat()}, {pl.SOURCE_URL}.\n")
    L.append("\n## Findings\n\n")
    L.append(f"- **Link precision:** {c} of {c + w} decided candidates are correct "
             f"({100 * c / (c + w):.0f}%); {u} more are unsure and {unrev} unreviewed, out of {n} candidates. "
             "Every candidate was reviewed by an agent (Claude), not by Hruday. "
             "Only the correct ones produce a line.\n" if (c + w) else "- No reviewed candidates yet.\n")
    L.append(f"- **Records with a correct link:** {len(ok_rec)} of our records "
             f"({ok.facility_uid.nunique()} LaQshya units).\n")
    L.append(f"- **Current maternity OT certificate:** {len(cur)} of those {len(ok_rec)} records have a maternity "
             f"operating theatre certificate valid on {as_of.isoformat()}.\n")
    verb = "has" if len(cur_cs) == 1 else "have"
    L.append(f"- **Of those {len(cur)}, {len(cur_cs)} {verb} a record with a fact that names a caesarean** "
             f"({', '.join(cur_cs.our_name) or 'none'}; the WP4 `csection` tag in "
             "`gold/capability_grades.parquet`, emoc line). For the other "
             f"{len(cur) - len(cur_cs)}, the government list certifies the maternity operating theatre while "
             "the record never names a caesarean: a gap in the record, not evidence about the hospital.\n")
    L.append("- These counts describe how our records line up with one government list. They are not a "
             "measure of how many public hospitals are missing from the data, and none is computed here.\n")

    L.append("\n## Method\n\n")
    L.append(f"1. LaQshya units: {lq.facility_uid.nunique()} (from {len(lq)} list rows: one row per unit and "
             "department). Each unit already has an NFHS `district_key` from WP2b.\n")
    L.append(f"2. Our candidate pool: {len(pool)} records ({', '.join(f'{v} {k}' for k, v in pool.pool_reason.value_counts().items())}). "
             "Public operator type is a requirement, except that a missing or `private` operator is let in when "
             "the name itself says it is a government unit, so a mislabelled district hospital can be reviewed.\n")
    L.append("3. Our district: the record's PIN through `silver/pin_crosswalk` (every district the PIN's offices "
             "fall in) and `silver/district_crosswalk`. Record coordinates and the OSM points in `ext_laqshya` "
             "are not used.\n")
    L.append("4. Names: lowercased, abbreviations expanded (DH, SDH, CHC, MC, Govt, ...), generic words removed "
             "to get a core (\"District Hospital, Nashik\" to \"nashik\"); rapidfuzz token_set and token_sort "
             "ratios on the cores; unit type from the name (DH, SDH, CHC, MC) checked for agreement.\n")
    L.append("5. Candidates: same district and core token_set at least 70 (or, with no core, full-name match at "
             "least 90); or same state, another district, core token_sort at least 95 with a 4-letter core word "
             "and no type conflict. Up to 2 per unit.\n")
    L.append("6. Review: every candidate read by hand (names, unit type, town, our record's facts when needed) "
             "and marked correct, wrong or unsure with a reason, in `data/labels/laqshya_link_review.csv`.\n")

    L.append("\n## Review counts\n\n")
    L.append("| scope | " + " | ".join(by_scope.columns) + " |\n|---|" + "---|" * len(by_scope.columns) + "\n")
    for scope, row in by_scope.iterrows():
        L.append(f"| {scope} | " + " | ".join(str(int(x)) for x in row.values) + " |\n")
    L.append(f"| all | " + " | ".join(str(int(by_scope[col].sum())) for col in by_scope.columns) + " |\n")
    L.append("\nPrecision by matcher feature (correct / (correct + wrong); unsure left out):\n\n")
    L.append("| feature | value | correct | wrong | unsure | precision |\n|---|---|---|---|---|---|\n")
    feats = {
        "unit type agrees": links["type_compatible"].astype("boolean").map(
            lambda v: "not stated" if pd.isna(v) else ("agrees" if v else "conflicts")),
        "strict core match (token_sort >= 90)": (links["core_sort"] >= 90).map({True: "yes", False: "no"}),
        "candidate rank in unit": links["rank_in_unit"].astype(str),
        "operator in our record": links["our_operator"].fillna("missing"),
    }
    for name, col in feats.items():
        t = pd.crosstab(col, links["review"])
        for val, row in t.iterrows():
            cc, ww, uu = int(row.get("correct", 0)), int(row.get("wrong", 0)), int(row.get("unsure", 0))
            prec = f"{100 * cc / (cc + ww):.0f}%" if cc + ww else "n/a"
            L.append(f"| {name} | {val} | {cc} | {ww} | {uu} | {prec} |\n")
    wrong_reasons = links[links.review.eq("wrong")]["review_reason"].fillna("").map(
        lambda s: s.split(":")[0].strip().lower() if s else "")
    if len(wrong_reasons):
        L.append("\nWhy the wrong ones were wrong (first clause of the reason):\n\n")
        for k, v in wrong_reasons.value_counts().items():
            L.append(f"- {k}: {v}\n")

    L.append("\n## Records with a current maternity OT certificate\n\n")
    L.append("| our record | listed as | emoc grade | fact names a caesarean | line |\n|---|---|---|---|---|\n")
    for r in cur.sort_values("our_name").itertuples():
        line = pl_obj.line(r.facility_id, as_of=as_of)
        L.append(f"| {r.our_name} | {r.lq_name} | {r.emoc_grade_label} | {'yes' if r.caesarean_named else 'no'} | "
                 f"{line['text'] if line else ''} |\n")

    L.append("\n## District Hospital, Nashik, end to end\n\n")
    L.extend(nashik_section(fac, links, grades, pl_obj, as_of))

    L.append("\n## Against the earlier pass\n\n")
    L.append("The stand-out brainstorm quoted a score-research pass (scratchpad, not in the repo): 78 candidates, "
             "40 correct, 31 wrong, 13 records with a current maternity OT certificate, 1 of them naming a "
             "caesarean (Murshidabad Medical College). This pass reviews a wider candidate set with its own "
             "rules; the figures above replace those.\n")
    L.append("\n## Limits\n\n")
    L.append("- The review is one agent's reading. Hruday's spot-check of the current-certificate links is still "
             "to do before any count goes on screen.\n")
    L.append("- A unit with no candidate is not evidence that the hospital is missing: our names may differ "
             "beyond what the matcher accepts, or the record's PIN may sit in another district.\n")
    L.append("- Some correct links attach to records whose own PIN points to another district (state-pass "
             "links). The line still describes the listed unit; the record's location problem is WP11's.\n")
    L.append("- LaQshya certifies a labour room or a maternity operating theatre on a date. A current "
             "certificate is outside evidence that the unit was assessed; it is not a guarantee of service today.\n")
    return "".join(L)


def nashik_section(fac, links, grades, pl_obj, as_of) -> list[str]:
    out = []
    rec = fac[fac.facility_id == NASHIK_FID]
    if rec.empty:
        return [f"- Facility id {NASHIK_FID} not found in `silver/facilities.parquet`.\n"]
    r = rec.iloc[0]
    out.append(f"- Our record: **{r['name']}**, `{NASHIK_FID}`, operator `{r.operator_type}`, PIN {r.pin}, "
               f"city {r.city_raw}, coord_check `{r.coord_check}`.\n")
    g = grades[(grades.facility_id == NASHIK_FID) & (grades.service_line == "emoc")]
    if len(g):
        g = g.iloc[0]
        out.append(f"- Emergency obstetric care grade: **{g.grade_label}** (grade {g.grade}, evidence "
                   f"{g.evidence_strength}); tags present: {', '.join(g.tags_present)}; missing for the next "
                   f"grade: {', '.join(g.missing_for_next)}.\n")
        out.append(f"- A fact naming a caesarean: {'yes' if 'csection' in list(g.tags_present) else 'none in the record'}.\n")
    lk = links[links.facility_id == NASHIK_FID]
    for x in lk.itertuples():
        out.append(f"- Candidate link: LaQshya unit `{x.facility_uid}` \"{x.lq_name}\" ({x.lq_type}, "
                   f"{x.district_key}), scope {x.match_scope}, core match {x.core_set:.0f}/{x.core_sort:.0f}, "
                   f"review **{x.review}**: {x.review_reason}\n")
        for cert in x.certs:
            out.append(f"  - list row: page {cert['page']} row {cert['sno']}, {cert['department']}, "
                       f"{cert['certification_status']}, result {cert['result_date']}, valid to "
                       f"{cert['validity_date']} ({'current' if pl.is_current(cert, as_of) else 'expired'} on "
                       f"{as_of.isoformat()})\n")
    line = pl_obj.line(NASHIK_FID, as_of=as_of)
    out.append(f"- `public_line(\"{NASHIK_FID}\")` returns:\n\n  > {line['text'] if line else 'None'}\n")
    if line:
        out.append(f"\n  page {line['page']}, row {line['row']}, department {line['department']}, "
                   f"validity {line['validity']}, retrieved {line['retrieved']}.\n")
    return out


if __name__ == "__main__":
    raise SystemExit(main())
