"""WP13 / D055: AERB radiotherapy licence list, parsed, located and candidate-linked.

Source: AERB, "Radiotherapy centres licenced by Atomic Energy Regulatory Board (As on
April, 2025)", https://aerb.gov.in/images/PDF/Radiotheraphy/RSD3.pdf, read from the
Internet Archive capture of 2026-01-04 (see data/external/aerb_rt/SOURCE.md).

Reads (shared data dir):
  external/aerb_rt/RSD3_20260104.pdf      the list (untrusted download; parsed, never executed)
  silver/facilities.parquet               our records
  silver/pin_crosswalk.parquet            PIN -> districts of its post offices
  gold/facility_duplicates.parquet        WP11 duplicate pairs (one-to-one is relaxed inside a cluster)
  silver/places.parquet via vf.locate     gazetteer for PIN-area and district-centre positions

Writes (atomic, with .meta.json sidecars):
  silver/ext_aerb_rt.parquet              one row per list row (727)
  silver/ext_links_aerb_rt.parquet        every candidate pair (list_uid, facility_id, score, band,
                                          reader_verdict, accepted, then detail columns)
  labels/ext_link_check_aerb_rt.csv       auto and middle band pairs for independent readers (cap 150)
  reports/ext_aerb_rt.md                  what was done, counts, limits

Linkage (reports/score_methodology_research.md, strict):
  * Records that are AYUSH, dental, veterinary or pharmacies are never candidates.
  * Blocking: same PIN; or same district (any district of either PIN's post offices) plus
    a rare shared name token. The list has no coordinates, so the 2 km rule does not apply.
  * Score: IDF-weighted soft Jaccard of name tokens, in [0, 1]; location words (the list's
    city and district, our city) are removed from both names first, and the best of the
    full name and the name before "a unit of" is used.
  * Bands: auto (score >= AUTO_MIN, mutual best one-to-one, clusters excepted), middle
    (MIDDLE_MIN <= score, or a high score that is not mutual best), low.
  Accept stage: readers fill labels/ext_link_read_aerb_rt.csv. Per band, precision =
  same / (same + different) with a one-sided 95% Clopper-Pearson lower bound; a band is
  accepted only if the bound is at least 0.90, and inside it only pairs marked same are
  links (accepted = True). List rows get link_status (linked, reader_same_band_not_accepted,
  not_in_dataset) and lane_eligible for the "not in this dataset" lane.

Usage:
  .venv/bin/python scripts/wp13_ext_aerb_rt.py            # accept stage only once readers' file exists
  .venv/bin/python scripts/wp13_ext_aerb_rt.py --rebuild  # re-parse and re-link too (check file stays frozen)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from rapidfuzz import fuzz

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from vf import locate, paths  # noqa: E402
from vf import public_list as pl  # noqa: E402

SCRIPT = "scripts/wp13_ext_aerb_rt.py"
SOURCE_ID = "aerb_rt"
SOURCE_NAME = "AERB list of licensed radiotherapy centres"
PUBLISHER = "Atomic Energy Regulatory Board (AERB), Government of India"
LIST_DATE = "2025-04"  # "As on April, 2025"
LIST_DATE_TEXT = "April 2025"
SOURCE_URL = "https://aerb.gov.in/images/PDF/Radiotheraphy/RSD3.pdf"
ARCHIVE_URL = "https://web.archive.org/web/20260104182025id_/https://aerb.gov.in/images/PDF/Radiotheraphy/RSD3.pdf"
PDF_NAME = "RSD3_20260104.pdf"
PDF_SHA256 = "9524824f9d4274782d3e21c414b192a142092bbeda189ffaa511dbbb60797141"
RETRIEVED = "2026-11-22T05:23:24Z"
WHAT = "licensed by AERB for radiotherapy"
LINK_VERSION = "aerb_rt_v1"

AUTO_MIN = 0.70
MIDDLE_MIN = 0.35
RARE_DF = 25          # a token in at most this many names (ours plus the list) is rare
CHECK_CAP = 150
SEED = 20261122

HEADER = ["Institution Name", "City", "State", "Pincode"]
CHECK_COLS = ["item_id", "list_name", "list_address", "list_pin", "list_district", "our_facility_id",
              "our_name", "our_address_or_city", "our_pin", "km_apart", "score", "band"]

# Records that can never be a radiotherapy centre's record (hospital-list rule).
REJECT_NAME = re.compile(
    r"\b(?:dental|dentist|dentistry|dentofacial|orthodont\w*|endodont\w*|ayurved\w*|ayush|homoeo\w*|homeo\w*|"
    r"unani|siddha|naturopath\w*|yoga|panchakarma|veterinary|vet|animal|pet clinic|pet hospital|pet care|pets)\b")
#: A name that also says it is a general or cancer hospital is kept (e.g. "multi speciality and dental").
KEEP_NAME = re.compile(r"\b(?:cancer|oncology|multi speciality|medical college|general hospital|institute of medical)\b")
REJECT_TYPES = {"dentist", "pharmacy"}

#: Legal and list-specific words dropped before scoring (they say who owns a unit, not which unit).
LEGAL = frozenset("""pvt private ltd limited llp inc co company corporation m s ms unit a an of the and
deptt dept department radiotherapy radiation trust society""".split())
#: Words too common in this list or our names to make a token "rare" (they still carry IDF weight).
NOT_RARE = pl.GENERIC | LEGAL | frozenset("""cancer oncology centre institute hospital research care health
healthcare speciality super specialty multi superspeciality superspecialty clinic clinics nursing home medical
sciences memorial foundation charitable mission""".split())
EXPAND = {"aiims": "all india institute medical sciences", "rc": "research centre", "res": "research",
          "inst": "institute", "ctr": "centre", "superspeciality": "super speciality",
          "superspecialty": "super speciality", "multispeciality": "multi speciality",
          "multispecialty": "multi speciality", "hospitals": "hospital", "centres": "centre"}
_UNIT_OF = re.compile(r"\(?\s*\b(?:a\s+)?unit\s+(?:no\.?\s*\w+\s+)?of\b.*$", re.I)
_PAREN = re.compile(r"\([^)]*\)?")


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
    dd = paths.data_dir()
    rel = str(path.relative_to(dd)) if dd in path.parents else str(path)
    meta = {"output": rel, "rows": int(rows), "created_at_utc": utcnow(), "git_commit": git_head(),
            "script": SCRIPT, "source_id": SOURCE_ID, "link_version": LINK_VERSION, **extra}
    body = json.dumps(meta, indent=2, default=str) + "\n"
    write_atomic(path.with_name(path.name + ".meta.json"), lambda p: p.write_text(body))


def clean(s) -> str:
    return " ".join(str(s or "").replace("‐", "-").split())


# ---------------------------------------------------------------------------
# 1. Parse
# ---------------------------------------------------------------------------

def parse_pdf(pdf: Path) -> pd.DataFrame:
    import pdfplumber

    sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
    if sha != PDF_SHA256:
        raise SystemExit(f"{pdf} sha256 {sha} does not match SOURCE.md ({PDF_SHA256})")
    rows, text_pins = [], 0
    with pdfplumber.open(pdf) as doc:
        for pno, page in enumerate(doc.pages, start=1):
            text_pins += len(re.findall(r"(?m)\b\d{6}\s*$", page.extract_text() or ""))
            r_on_page = 0
            for table in page.extract_tables():
                for cells in table:
                    cells = [clean(c) for c in cells]
                    if cells == HEADER:
                        continue
                    if len(cells) != 4 or not re.fullmatch(r"\d{6}", cells[3]):
                        raise SystemExit(f"page {pno}: unexpected row {cells}")
                    r_on_page += 1
                    rows.append({"page": pno, "row_on_page": r_on_page, "name_raw": cells[0],
                                 "city_raw": cells[1], "state_raw": cells[2], "pin_raw": cells[3]})
    df = pd.DataFrame(rows)
    if len(df) != text_pins:
        raise SystemExit(f"table rows {len(df)} != PINs in page text {text_pins}")
    df.insert(0, "list_seq", np.arange(1, len(df) + 1))
    df.insert(0, "list_uid", [f"aerb_rt-p{p:02d}-r{r:02d}" for p, r in zip(df.page, df.row_on_page)])
    return df


# ---------------------------------------------------------------------------
# 2-3. Tidy and locate
# ---------------------------------------------------------------------------

STATE_FIX = {"Orissa": "Odisha", "Pondicherry": "Puducherry"}


def locate_rows(df: pd.DataFrame, pin_cw: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["name"] = out["name_raw"]
    out["pin"] = out["pin_raw"]
    out["state"] = out["state_raw"].map(lambda s: STATE_FIX.get(s, s))
    rank1 = pin_cw.sort_values("rank").drop_duplicates("pin").set_index("pin")
    out["district"] = out["pin"].map(rank1["district_pin"]).map(lambda s: locate.nice(s) if isinstance(s, str) else None)
    out["pin_state"] = out["pin"].map(rank1["state_pin"]).map(lambda s: locate.nice(s) if isinstance(s, str) else None)
    out["address"] = out.apply(lambda r: f"{r.city_raw}, {r.state_raw} {r.pin}", axis=1)
    lat, lon, prec, note, how = [], [], [], [], []
    for r in out.itertuples():
        res = locate.resolve(r.pin)
        if res.status == "ok" and res.kind == "pin":
            lat.append(res.lat); lon.append(res.lon); prec.append(res.precision_km)
            note.append("PIN area"); how.append(res.how)
            continue
        res = locate.resolve(r.city_raw, state=r.state)
        if res.status == "ok" and res.kind in ("district", "city"):
            lat.append(res.lat); lon.append(res.lon); prec.append(res.precision_km)
            note.append("district centre"); how.append(res.how)
            if r.district is None:
                out.loc[r.Index, "district"] = locate.nice(res.district)
        else:
            lat.append(None); lon.append(None); prec.append(None)
            note.append("not located"); how.append(res.how)
    out["lat"], out["lon"], out["position_precision_km"] = lat, lon, prec
    out["position_note"], out["position_how"] = note, how
    out["district"] = out["district"].fillna(out["city_raw"])
    out["what"] = WHAT
    # The list states no licence dates per row: the list itself is dated, rows are not.
    out["status"] = "undated"
    out["list_date"] = LIST_DATE
    out["valid_from"] = None
    out["valid_to"] = None
    out["citation"] = [f"AERB radiotherapy licence list (as on {LIST_DATE_TEXT}), page {p}, row {r}; {SOURCE_URL}"
                       for p, r in zip(out.page, out.row_on_page)]
    out["source_id"] = SOURCE_ID
    out["source_name"] = SOURCE_NAME
    out["publisher"] = PUBLISHER
    out["source_url"] = SOURCE_URL
    out["retrieved"] = RETRIEVED
    out["raw"] = [json.dumps(dict(zip(HEADER, [a, b, c, d])), ensure_ascii=False)
                  for a, b, c, d in zip(out.name_raw, out.city_raw, out.state_raw, out.pin_raw)]
    cols = ["list_uid", "list_seq", "name", "address", "pin", "district", "state", "lat", "lon",
            "position_precision_km", "position_note", "position_how", "pin_state", "what", "status", "list_date",
            "valid_from", "valid_to", "citation", "page", "row_on_page", "source_id", "source_name", "publisher",
            "source_url", "retrieved", "name_raw", "city_raw", "state_raw", "pin_raw", "raw"]
    return out[cols]


# ---------------------------------------------------------------------------
# 4. Link
# ---------------------------------------------------------------------------

def tokens(name: str) -> list[str]:
    t = pl.normalise_name(name)
    words = []
    for w in t.split():
        words.extend(EXPAND.get(w, w).split())
    return [w for w in words if len(w) > 1 and w not in LEGAL]


def name_variants(name: str) -> list[list[str]]:
    """Full name, and the name before 'a unit of' / a parenthesis (owner removed)."""
    vs = [tokens(name)]
    short = _UNIT_OF.sub("", name)
    short = _PAREN.sub(" ", short)
    if short.strip() and short.strip() != name.strip():
        vs.append(tokens(short))
    return [v for v in vs if v]


def soft_jaccard(a: list[str], b: list[str], idf: dict, default_idf: float) -> float:
    """IDF-weighted soft Jaccard in [0, 1]: tokens pair greedily when their edit ratio
    is at least 85; a pair contributes sim x mean weight to the overlap, and the union
    counts each paired weight once."""
    A, B = list(dict.fromkeys(a)), list(dict.fromkeys(b))
    if not A or not B:
        return 0.0
    w = lambda t: idf.get(t, default_idf)
    pairs = []
    for i, x in enumerate(A):
        for j, y in enumerate(B):
            s = 1.0 if x == y else fuzz.ratio(x, y) / 100.0
            if s >= 0.85 and min(len(x), len(y)) >= 3 or x == y:
                pairs.append((s, i, j))
    pairs.sort(reverse=True)
    used_a, used_b, inter, shared = set(), set(), 0.0, 0.0
    for s, i, j in pairs:
        if i in used_a or j in used_b:
            continue
        used_a.add(i); used_b.add(j)
        m = (w(A[i]) + w(B[j])) / 2
        inter += s * m
        shared += m
    union = sum(w(t) for t in A) + sum(w(t) for t in B) - shared
    return float(min(1.0, max(0.0, inter / union))) if union > 0 else 0.0


def haversine_km(lat1, lon1, lat2, lon2):
    if any(v is None or (isinstance(v, float) and math.isnan(v)) for v in (lat1, lon1, lat2, lon2)):
        return None
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(h))


def cluster_keys(fac: pd.DataFrame, dups: pd.DataFrame | None) -> dict:
    """facility_id -> cluster key: WP11 cluster_id, unioned with duplicate pairs."""
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    for fid, cid in zip(fac.facility_id, fac.cluster_id):
        union(fid, "c:" + str(cid))
    if dups is not None:
        for a, b in zip(dups.facility_id_a, dups.facility_id_b):
            union(a, b)
    return {fid: find(fid) for fid in fac.facility_id}


def is_rejected(row) -> bool:
    if row.facility_type in REJECT_TYPES:
        return True
    n = " ".join(EXPAND.get(w, w) for w in pl.normalise_name(row.name).split())
    return bool(REJECT_NAME.search(n)) and not KEEP_NAME.search(n)


def link(lst: pd.DataFrame, fac: pd.DataFrame, pin_cw: pd.DataFrame, dups) -> tuple[pd.DataFrame, dict]:
    stats = {}
    fac = fac.copy()
    fac["rejected"] = [is_rejected(r) for r in fac.itertuples()]
    stats["our_records"] = int(len(fac))
    stats["our_rejected_ayush_dental_vet_pharmacy"] = int(fac.rejected.sum())
    pool = fac[~fac.rejected].copy()
    ckey = cluster_keys(fac, dups)

    # district sets per PIN: every district any post office of the PIN falls in
    pdist = defaultdict(set)
    for p, s, d in zip(pin_cw.pin, pin_cw.state_pin, pin_cw.district_pin):
        pdist[str(p)].add((s, d))

    # IDF over our names plus the list's names
    docs = [set(tokens(n)) for n in pool.name] + [set(tokens(n)) for n in lst.name]
    df_ = Counter(t for d in docs for t in d)
    N = len(docs)
    idf = {t: math.log((N + 1) / (c + 1)) + 1.0 for t, c in df_.items()}
    default_idf = math.log(N + 1) + 1.0
    rare = {t for t, c in df_.items() if c <= RARE_DF and t not in NOT_RARE and len(t) >= 3}

    pool["toks"] = [set(tokens(n)) for n in pool.name]
    pool["dists"] = [pdist.get(str(p), set()) if isinstance(p, str) else set() for p in pool.pin]
    by_pin = defaultdict(list)
    by_dist = defaultdict(list)
    for i, r in zip(pool.index, pool.itertuples()):
        if isinstance(r.pin, str) and r.pin:
            by_pin[r.pin].append(i)
        for d in r.dists:
            by_dist[d].append(i)

    rows = []
    for L in lst.itertuples():
        ltoks = set(tokens(L.name))
        ldists = pdist.get(L.pin, set())
        cand = {}
        for i in by_pin.get(L.pin, []):
            cand.setdefault(i, set()).add("same_pin")
        for d in ldists:
            for i in by_dist.get(d, []):
                shared = (ltoks & pool.at[i, "toks"]) & rare
                if shared:
                    cand.setdefault(i, set()).add("same_district_rare_token")
        if not cand:
            continue
        loc_words = set(tokens(L.city_raw)) | set(tokens(L.district or ""))
        for d in ldists:
            loc_words |= set(tokens(d[1]))
        lvars = [[t for t in v if t not in loc_words] or v for v in name_variants(L.name)]
        for i, blocks in cand.items():
            r = pool.loc[i]
            our_loc = loc_words | set(tokens(r.city_raw or ""))
            ovars = [[t for t in v if t not in our_loc] or v for v in name_variants(r["name"])]
            best, bv = 0.0, ""
            for li, lv in enumerate(lvars):
                for oi, ov in enumerate(ovars):
                    s = soft_jaccard(lv, ov, idf, default_idf)
                    if s > best:
                        best, bv = s, f"list:{'full' if li == 0 else 'short'}/ours:{'full' if oi == 0 else 'short'}"
            shared_rare = sorted((ltoks & r.toks) & rare)
            rows.append({
                "item_id": L.list_uid, "facility_id": r.facility_id, "cluster_key": ckey.get(r.facility_id),
                "block": "+".join(sorted(blocks)), "same_pin": "same_pin" in blocks,
                "same_district": bool(ldists & r.dists), "shared_rare_tokens": shared_rare,
                "score": round(best, 4), "score_variant": bv,
                "km_apart": haversine_km(L.lat, L.lon, r.lat, r.lon),
                "list_position_precision_km": L.position_precision_km,
                "our_coord_check": r.coord_check,
                "list_name": L.name, "list_address": L.address, "list_pin": L.pin, "list_district": L.district,
                "our_name": r["name"], "our_address_or_city": ", ".join(x for x in [r.city_raw, r.state_raw] if isinstance(x, str) and x),
                "our_pin": r.pin, "our_facility_type": r.facility_type, "our_operator_type": r.operator_type,
            })
    c = pd.DataFrame(rows)
    stats["rare_tokens"] = len(rare)
    if c.empty:
        return c, stats

    # one-to-one: mutual best between list item and our cluster
    c = c.sort_values(["item_id", "score"], ascending=[True, False]).reset_index(drop=True)
    best_item = c.groupby("item_id")["score"].transform("max")
    best_clu = c.groupby("cluster_key")["score"].transform("max")
    c["rank_in_item"] = c.groupby("item_id")["score"].rank(method="min", ascending=False).astype(int)
    c["rank_in_cluster"] = c.groupby("cluster_key")["score"].rank(method="min", ascending=False).astype(int)
    # a tie at the top across two different clusters is not one-to-one
    top_clusters = c[c.score.eq(best_item)].groupby("item_id")["cluster_key"].nunique()
    top_items = c[c.score.eq(best_clu)].groupby("cluster_key")["item_id"].nunique()
    c["mutual_best"] = (c.score.eq(best_item) & c.score.eq(best_clu)
                        & c.item_id.map(top_clusters).eq(1) & c.cluster_key.map(top_items).eq(1))
    c["band"] = np.where((c.score >= AUTO_MIN) & c.mutual_best, "auto",
                         np.where(c.score >= MIDDLE_MIN, "middle", "low"))
    c["km_apart"] = c["km_apart"].astype(float).round(1)
    return c, stats


def check_sample(c: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Every auto and middle pair, capped at CHECK_CAP; above the cap, sample per band in
    proportion to band size (seeded) and record it."""
    pool = c[c.band.isin(["auto", "middle"])]
    n = {b: int((pool.band == b).sum()) for b in ("auto", "middle")}
    info = {"cap": CHECK_CAP, "band_sizes": n, "sampled": False, "seed": SEED}
    if len(pool) <= CHECK_CAP:
        out = pool
    else:
        tot = sum(n.values())
        k_auto = min(n["auto"], max(min(n["auto"], 60), round(CHECK_CAP * n["auto"] / tot)))
        k_mid = min(n["middle"], CHECK_CAP - k_auto)
        k_auto = min(n["auto"], CHECK_CAP - k_mid)
        out = pd.concat([pool[pool.band == "auto"].sample(k_auto, random_state=SEED),
                         pool[pool.band == "middle"].sample(k_mid, random_state=SEED)])
        info.update(sampled=True, taken={"auto": int(k_auto), "middle": int(k_mid)},
                    rule="simple random sample within each band, seeded; auto gets its share of the cap "
                         "but at least 60 (enough for a 0.90 lower bound) when available")
    out = out.sort_values(["item_id", "score"], ascending=[True, False]).rename(
        columns={"facility_id": "our_facility_id"})
    return out[CHECK_COLS].copy(), info


# ---------------------------------------------------------------------------
# 5. Accept: join the readers' verdicts, accept bands, classify list rows
# ---------------------------------------------------------------------------

VERDICTS = {"same", "different", "unsure"}
P0, ALPHA = 0.90, 0.05
NEAR_MISS_MIN = 0.20   # a low-band mutual-best pair at or above this may still be the same place
LOOKALIKE_KM = 10.0    # lane check: any of our records this close ...
LOOKALIKE_MIN = 85     # ... whose distinctive name words match this well (token set ratio) blocks the lane


#: One-off agent audit of the lane (22 Nov, after the look-alike rule): 40 lane-eligible rows drawn with
#: DataFrame.sample(40, random_state=7), each read against every record of ours within 40 km by name and a keyword
#: search across all records. Not a reader label file; reported as a residual-error estimate only.
LANE_AUDIT = {
    "sample": 40, "seed": 7,
    "appear_in_dataset": {"aerb_rt-p08-r05": "NIMHANS, Bengaluru 560029: our record is named only 'NIMHANS'",
                          "aerb_rt-p20-r26": "Sharda Hospital, Greater Noida: our record carries PIN 270001"},
    "possibly_in_dataset": {"aerb_rt-p16-r35": "V.S. Hospital, Chennai 600031 vs our 'VS Hospitals', Chennai 600010"},
}


def distinctive(name) -> str:
    return " ".join(sorted({t for t in tokens(name) if t not in NOT_RARE}))


def lookalikes(lst: pd.DataFrame, fac: pd.DataFrame) -> dict:
    """list_uid -> (facility_id, name, km, ratio) of the best nearby look-alike among our records, ignoring
    the blocking rules. It catches records the linker cannot see (missing or wrong PIN, other district)."""
    f = fac[~pd.Series([is_rejected(r) for r in fac.itertuples()], index=fac.index)].copy()
    f = f[f.lat.notna() & f.lon.notna()]
    f["core"] = [distinctive(n) for n in f.name]
    f = f[f.core != ""]
    flat, flon = np.radians(f.lat.astype(float).to_numpy()), np.radians(f.lon.astype(float).to_numpy())
    out = {}
    for r in lst.itertuples():
        core = distinctive(r.name)
        if not core or pd.isna(r.lat):
            continue
        la, lo = math.radians(r.lat), math.radians(r.lon)
        km = 6371.0 * 2 * np.arcsin(np.sqrt(np.sin((flat - la) / 2) ** 2
                                            + np.cos(la) * np.cos(flat) * np.sin((flon - lo) / 2) ** 2))
        idx = np.flatnonzero(km <= LOOKALIKE_KM)
        best = None
        for i in idx:
            q = fuzz.token_set_ratio(core, f.core.iat[i])
            if q >= LOOKALIKE_MIN and (best is None or q > best[3]):
                best = (f.facility_id.iat[i], f.name.iat[i], round(float(km[i]), 1), round(float(q), 1))
        if best:
            out[r.list_uid] = best
    return out


def cp_lower(k: int, n: int, alpha: float = ALPHA) -> float:
    """One-sided Clopper-Pearson lower bound for a binomial proportion k/n."""
    from scipy.stats import beta
    if n == 0 or k == 0:
        return 0.0
    return float(beta.ppf(alpha, k, n - k + 1))


def read_verdicts(path: Path) -> pd.DataFrame:
    r = pd.read_csv(path, dtype=str, keep_default_na=False)
    need = {"item_id", "verdict", "our_facility_id"}
    if not need <= set(r.columns):
        raise SystemExit(f"{path}: missing columns {sorted(need - set(r.columns))}")
    r["verdict"] = r["verdict"].str.strip().str.lower()
    bad = r[~r.verdict.isin(VERDICTS | {""})]
    if len(bad):
        raise SystemExit(f"{path}: unknown verdicts {sorted(bad.verdict.unique())}")
    if r.duplicated(["item_id", "our_facility_id"]).any():
        raise SystemExit(f"{path}: duplicate (item_id, our_facility_id) rows")
    r = r.rename(columns={"our_facility_id": "facility_id", "verdict": "reader_verdict", "note": "reader_note"})
    if "reader_note" not in r:
        r["reader_note"] = ""
    return r[["item_id", "facility_id", "reader_verdict", "reader_note"]]


def accept(cands: pd.DataFrame, chk: pd.DataFrame, reads: pd.DataFrame, lst: pd.DataFrame, fac: pd.DataFrame):
    """Returns (links with reader_verdict and accepted, list rows with link status, band table, info)."""
    c = cands.drop(columns=[x for x in ("reader_verdict", "reader_note", "accepted", "band_accepted")
                            if x in cands.columns]).copy()
    key_chk = set(zip(chk.item_id, chk.our_facility_id))
    key_read = set(zip(reads.item_id, reads.facility_id))
    key_cand = set(zip(c.item_id, c.facility_id))
    info = {"check_rows": len(chk), "reader_rows": len(reads),
            "reader_rows_not_in_check_file": len(key_read - key_chk),
            "reader_rows_not_in_candidates": len(key_read - key_cand),
            "check_rows_without_reader_row": len(key_chk - key_read)}
    c = c.merge(reads, on=["item_id", "facility_id"], how="left")
    c["reader_verdict"] = c["reader_verdict"].replace("", np.nan).fillna("unread")
    c["reader_note"] = c["reader_note"].fillna("")

    bands = []
    for b in ("auto", "middle", "low"):
        s = c[c.band == b]
        same, diff, uns = (int((s.reader_verdict == v).sum()) for v in ("same", "different", "unsure"))
        n = same + diff
        lb = cp_lower(same, n)
        bands.append({"band": b, "pairs": len(s), "read": same + diff + uns, "same": same, "different": diff,
                      "unsure": uns, "precision": (same / n) if n else None, "lower_95": lb,
                      "accepted": bool(n and lb >= P0)})
    bt = pd.DataFrame(bands)
    ok = set(bt.loc[bt.accepted, "band"])
    c["band_accepted"] = c.band.isin(ok)
    c["accepted"] = c.band_accepted & c.reader_verdict.eq("same")

    # list-row status
    g = c.groupby("item_id")
    linked = g["accepted"].any()
    any_same = g["reader_verdict"].agg(lambda v: (v == "same").any())
    any_unsure = g["reader_verdict"].agg(lambda v: (v == "unsure").any())
    unread_am = c[c.band.isin(["auto", "middle"]) & c.reader_verdict.eq("unread")].item_id.unique()
    near = c[c.band.eq("low") & c.mutual_best & (c.score >= NEAR_MISS_MIN)].item_id.unique()
    acc_ids = c[c.accepted].groupby("item_id")["facility_id"].agg(lambda v: sorted(set(v)))

    out = lst.drop(columns=[x for x in ("link_status", "linked_facility_ids", "lane_eligible", "lane_exclusion",
                                        "lookalike_facility_id", "lookalike_name", "lookalike_km")
                            if x in lst.columns]).copy()
    look = lookalikes(out, fac)
    uid = out.list_uid
    is_linked = uid.map(linked).fillna(False).astype(bool)
    is_same = uid.map(any_same).fillna(False).astype(bool)
    out["link_status"] = np.where(is_linked, "linked",
                                  np.where(is_same, "reader_same_band_not_accepted", "not_in_dataset"))
    out["linked_facility_ids"] = [acc_ids.get(u, []) for u in uid]
    excl = np.full(len(out), "", dtype=object)
    nid = out.link_status.eq("not_in_dataset").to_numpy()
    # first reason wins, strictest first
    for mask, why in ((uid.map(any_unsure).fillna(False).astype(bool).to_numpy(), "reader_unsure"),
                      (uid.isin(unread_am).to_numpy(), "unread_auto_or_middle_candidate"),
                      (uid.isin(near).to_numpy(), "near_miss_low_candidate"),
                      (uid.isin(list(look)).to_numpy(), "nearby_lookalike_record"),
                      (out.position_note.eq("not located").to_numpy(), "not_located")):
        excl = np.where(nid & (excl == "") & mask, why, excl)
    out["lane_exclusion"] = np.where(nid, excl, out.link_status)
    out["lane_eligible"] = nid & (excl == "")
    out["lookalike_facility_id"] = [look.get(u, (None,))[0] for u in uid]
    out["lookalike_name"] = [look[u][1] if u in look else None for u in uid]
    out["lookalike_km"] = [look[u][2] if u in look else None for u in uid]

    multi = c[c.accepted].groupby("facility_id")["item_id"].nunique()
    info.update({
        "bands_accepted": sorted(ok),
        "accepted_pairs": int(c.accepted.sum()),
        "list_rows_linked": int(is_linked.sum()),
        "facilities_linked": int(c.loc[c.accepted, "facility_id"].nunique()),
        "facilities_linked_to_more_than_one_list_row": {k: int(v) for k, v in multi[multi > 1].items()},
        "list_rows_reader_same_band_not_accepted": int((out.link_status == "reader_same_band_not_accepted").sum()),
        "list_rows_not_in_dataset": int(nid.sum()),
        "list_rows_lane_eligible": int(out.lane_eligible.sum()),
        "lane_exclusions": out.loc[nid, "lane_exclusion"].replace("", "eligible").value_counts().to_dict(),
        "unread_pairs_in_accepted_bands": int((c.band_accepted & c.reader_verdict.eq("unread")).sum()),
    })
    return c, out, bt, info


def loc_quality(df: pd.DataFrame) -> dict:
    p = df.position_precision_km.astype(float)
    return {"rows": len(df), "PIN area": int((df.position_note == "PIN area").sum()),
            "district centre": int((df.position_note == "district centre").sum()),
            "not located": int((df.position_note == "not located").sum()),
            "median_km": float(p.median()) if len(df) else None,
            "le_5km": int((p <= 5).sum()), "gt_10km": int((p > 10).sum())}


def acceptance_report(c, out, bt, info) -> str:
    L = ["\n## Acceptance (reader verdicts)\n\n"]
    L.append(f"Readers' file `data/labels/ext_link_read_{SOURCE_ID}.csv`: {info['reader_rows']} rows, every one "
             f"matching a row of the check file ({info['reader_rows_not_in_check_file']} unmatched, "
             f"{info['check_rows_without_reader_row']} check rows unread). Precision per band = same / (same + "
             "different); unsure verdicts are left out of both counts. Lower bound: one-sided 95% Clopper-Pearson. "
             f"A band is accepted only if its lower bound is at least {P0:.2f}; inside an accepted band only pairs "
             "the reader marked same become links.\n\n")
    L.append("| band | pairs | read | same | different | unsure | precision | 95% lower bound | accepted |\n"
             "|---|---|---|---|---|---|---|---|---|\n")
    for r in bt.itertuples():
        prec = "n/a" if r.precision is None or (isinstance(r.precision, float) and math.isnan(r.precision)) \
            else f"{r.precision:.3f}"
        lb = f"{r.lower_95:.3f}" if r.read else "n/a"
        L.append(f"| {r.band} | {r.pairs} | {r.read} | {r.same} | {r.different} | {r.unsure} | {prec} | {lb} | "
                 f"{'yes' if r.accepted else 'no'} |\n")
    a = bt.set_index("band").loc["auto"]
    if a.read:
        strict_lb = cp_lower(int(a.same), int(a.read))
        L.append(f"\nSensitivity: counting unsure verdicts as wrong, the auto band's precision is "
                 f"{a.same / a.read:.3f} with lower bound {strict_lb:.3f}, which "
                 f"{'still passes' if strict_lb >= P0 else 'would fail'} the {P0:.2f} rule. The rule as set "
                 "(D055, research note) leaves unsure out; the unsure pairs themselves are never links.\n")
    acc = ", ".join(info["bands_accepted"]) or "none"
    L.append(f"\nAccepted bands: **{acc}**. Accepted links: {info['accepted_pairs']} pairs, "
             f"{info['list_rows_linked']} list rows, {info['facilities_linked']} of our records.")
    if info["facilities_linked_to_more_than_one_list_row"]:
        L.append(f" Records linked to more than one list row: {len(info['facilities_linked_to_more_than_one_list_row'])} "
                 "(the list has more than one licence entry for the same hospital).")
    L.append(f" {info['unread_pairs_in_accepted_bands']} pairs in accepted bands were not in the reader sample, so they "
             "are not links: the rule keeps only pairs a reader marked same.\n\n")
    wrong = c[c.band_accepted & c.reader_verdict.eq("different")]
    if len(wrong):
        L.append("Pairs marked different inside an accepted band (the band's error, kept out):\n\n")
        for r in wrong.itertuples():
            L.append(f"- {r.list_name} ({r.list_pin}) vs {r.our_name} ({r.our_pin}): {r.reader_note}\n")
        L.append("\n")

    nid = out[out.link_status == "not_in_dataset"]
    lane = out[out.lane_eligible]
    st = out.link_status.value_counts()
    L.append("## List rows: in our dataset or not\n\n| status | list rows |\n|---|---|\n")
    L.append(f"| linked (accepted link, gives a card fact) | {int(st.get('linked', 0))} |\n")
    L.append(f"| reader marked same, band not accepted (in our dataset, no card fact) | "
             f"{int(st.get('reader_same_band_not_accepted', 0))} |\n")
    L.append(f"| not in our dataset (no accepted link, no candidate marked same) | {len(nid)} |\n")
    L.append(f"| of which eligible for the nearby lane | {len(lane)} |\n")
    L.append(f"| total | {len(out)} |\n\n")
    L.append("\"Not in our dataset\" is the task's definition. It is not proof of absence: the linker's recall is not "
             "measured. For the nearby lane, which must show only facilities that are clearly not any of our "
             "records, a not-in-dataset row is also excluded when (first reason counted):\n\n")
    ex = info["lane_exclusions"]
    L.append(f"- a reader marked a candidate unsure: {ex.get('reader_unsure', 0)}\n")
    L.append(f"- it has an auto or middle candidate no reader saw: {ex.get('unread_auto_or_middle_candidate', 0)}\n")
    L.append(f"- it has a low-band mutual-best candidate scoring {NEAR_MISS_MIN:.2f} or more (a possible match the "
             f"score missed): {ex.get('near_miss_low_candidate', 0)}\n")
    L.append(f"- one of our records within {LOOKALIKE_KM:.0f} km has a look-alike name (token set ratio of the "
             f"distinctive words at least {LOOKALIKE_MIN}, ignoring PIN and district), which catches records the "
             f"linker cannot block on, such as a missing or wrong PIN: {ex.get('nearby_lookalike_record', 0)}\n")
    L.append(f"- it could not be located: {ex.get('not_located', 0)}\n")
    L.append(f"- eligible: {ex.get('eligible', 0)}\n\n")
    k1, k2, na = len(LANE_AUDIT["appear_in_dataset"]), len(LANE_AUDIT["possibly_in_dataset"]), LANE_AUDIT["sample"]
    from scipy.stats import beta as _beta
    up = float(_beta.ppf(0.95, k1 + k2 + 1, na - k1 - k2))
    L.append(f"Residual error of the lane, one-off agent audit (22 Nov): {na} lane-eligible rows (seeded sample, seed "
             f"{LANE_AUDIT['seed']}) read against every record within 40 km and a keyword search of all records. "
             f"{k1} appear to be our records and {k2} possibly: "
             + "; ".join(LANE_AUDIT["appear_in_dataset"].values()) + "; possibly "
             + "; ".join(LANE_AUDIT["possibly_in_dataset"].values())
             + f". So roughly {k1 / na:.0%} to {(k1 + k2) / na:.0%} of lane rows may be in our data under another "
             f"name or PIN (one-sided 95% upper bound {up:.0%}). The lane wording should say \"not linked to a record "
             "in this dataset\" rather than claim absence. An earlier audit of 40 rows before the look-alike rule "
             "found the same kind of misses (Batra Hospital, Delhi, with no PIN on our record; Regency Hospital, "
             "Kanpur, with a Lucknow PIN), which the rule now removes.\n\n")
    L.append("Location quality (positions are PIN-area or district-centre points, never the site):\n\n"
             "| rows | count | PIN area | district centre | not located | median precision km | precision <= 5 km "
             "| precision > 10 km |\n|---|---|---|---|---|---|---|---|\n")
    for lab, d in (("all list rows", out), ("linked", out[out.link_status == "linked"]),
                   ("not in our dataset", nid), ("lane eligible", lane)):
        q = loc_quality(d)
        med = f"{q['median_km']:.1f}" if q["median_km"] is not None and not math.isnan(q["median_km"]) else "n/a"
        L.append(f"| {lab} | {q['rows']} | {q['PIN area']} | {q['district centre']} | {q['not located']} | {med} | "
                 f"{q['le_5km']} | {q['gt_10km']} |\n")
    la = out[out.lane_exclusion == "nearby_lookalike_record"]
    L.append(f"The look-alike rule also measures the linker's recall from below: of its {len(la)} exclusions, many "
             "are plainly the same hospital, for example "
             + "; ".join(f"{r.name} ({r.pin}) and our {r.lookalike_name} at {r.lookalike_km:.1f} km"
                         for r in la[la.name.str.contains("HINDUJA|SCIENCES BHUBANESWAR|BELGAUM|Paras HMRI", case=False)]
                         .head(4).itertuples())
             + ". The strict blocking missed them (different PIN and no rare shared token, or our record's PIN is "
             "wrong). They are candidates for a second reader pass, not links.\n")
    L.append("\nVerdicts come from agent readers (D055: agent-checked precision), not from Hruday, and the "
             "readers saw the band and score. The 95% bound covers sampling error in the band, not reader error.\n")
    L.append("\n`silver/ext_aerb_rt.parquet` carries `link_status`, `linked_facility_ids`, `lane_eligible` and "
             "`lane_exclusion` per list row; `silver/ext_links_aerb_rt.parquet` carries `reader_verdict` "
             "(same, different, unsure, unread) and `accepted` per candidate pair.\n")
    return "".join(L)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

LINK_COLS_FIRST = ["list_uid", "facility_id", "score", "band", "reader_verdict", "accepted"]


def build(args, silver, labels, read_path):
    """Parse, locate and link. With readers' verdicts present, the check file is frozen (not rewritten)."""
    pdf = paths.external_dir() / SOURCE_ID / PDF_NAME
    raw = parse_pdf(pdf)
    pin_cw = pd.read_parquet(silver / "pin_crosswalk.parquet")
    pin_cw["pin"] = pin_cw["pin"].astype(str)
    lst = locate_rows(raw, pin_cw)

    fac = pd.read_parquet(silver / "facilities.parquet")
    dpath = paths.gold_dir() / "facility_duplicates.parquet"
    dups = pd.read_parquet(dpath) if dpath.exists() else None
    cands, stats = link(lst, fac, pin_cw, dups)
    cands["link_version"] = LINK_VERSION
    cpath = labels / f"ext_link_check_{SOURCE_ID}.csv"
    if read_path.exists():
        chk = pd.read_csv(cpath, dtype={"list_pin": str, "our_pin": str})
        sample_info = json.loads(cpath.with_name(cpath.name + ".meta.json").read_text())["sampling"]
        print(f"readers' file exists: check file {cpath.name} kept as is (frozen)")
    else:
        chk, sample_info = check_sample(cands)
        write_atomic(cpath, lambda p: chk.to_csv(p, index=False))
        write_meta(cpath, len(chk), {
            "grain": "one row per candidate pair for independent readers (auto and middle bands)",
            "sampling": sample_info,
            "reader_file": f"labels/ext_link_read_{SOURCE_ID}.csv (written by readers, not by this script)",
        })
    cands["in_check_file"] = cands.set_index(["item_id", "facility_id"]).index.isin(
        chk.set_index(["item_id", "our_facility_id"]).index)
    return raw, lst, cands, chk, stats, sample_info


def load_built(silver, labels):
    """Read the outputs of an earlier build (for the accept stage alone)."""
    lst = pd.read_parquet(silver / f"ext_{SOURCE_ID}.parquet")
    lpath = silver / f"ext_links_{SOURCE_ID}.parquet"
    cands = pd.read_parquet(lpath)
    if "item_id" not in cands.columns:
        cands = cands.rename(columns={"list_uid": "item_id"})
    stats = json.loads(lpath.with_name(lpath.name + ".meta.json").read_text())["stats"]
    cpath = labels / f"ext_link_check_{SOURCE_ID}.csv"
    chk = pd.read_csv(cpath, dtype={"list_pin": str, "our_pin": str})
    sample_info = json.loads(cpath.with_name(cpath.name + ".meta.json").read_text())["sampling"]
    return lst, lst, cands, chk, stats, sample_info


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--rebuild", action="store_true",
                    help="re-parse and re-link even when readers' verdicts exist (the check file stays frozen)")
    args = ap.parse_args(argv)
    silver, labels = paths.silver_dir(), paths.labels_dir()
    read_path = labels / f"ext_link_read_{SOURCE_ID}.csv"
    if read_path.exists() and not args.rebuild:
        print(f"accept stage only (readers' file {read_path.name} exists; pass --rebuild to re-link)")
        raw, lst, cands, chk, stats, sample_info = load_built(silver, labels)
    else:
        raw, lst, cands, chk, stats, sample_info = build(args, silver, labels, read_path)

    acc_info, bt = None, None
    if read_path.exists():
        reads = read_verdicts(read_path)
        fac = pd.read_parquet(silver / "facilities.parquet")
        cands, lst, bt, acc_info = accept(cands, chk, reads, lst, fac)

    out = silver / f"ext_{SOURCE_ID}.parquet"
    write_atomic(out, lambda p: lst.to_parquet(p, index=False))
    list_meta = {
        "grain": "one row per row of the AERB list (one licensed radiotherapy centre entry)",
        "columns": list(lst.columns), "source_url": SOURCE_URL, "retrieved_from": ARCHIVE_URL,
        "retrieved": RETRIEVED, "pdf_sha256": PDF_SHA256, "list_date": LIST_DATE,
        "status_rule": "undated: the list is dated (as on April 2025) but gives no licence dates per row",
        "position_notes": lst.position_note.value_counts().to_dict(),
        "inputs": [f"external/{SOURCE_ID}/{PDF_NAME}", "silver/pin_crosswalk.parquet", "silver/places.parquet"],
    }
    if acc_info:
        list_meta["link_status"] = lst.link_status.value_counts().to_dict()
        list_meta["lane_rule"] = ("lane_eligible: not_in_dataset (no accepted link, no candidate a reader marked "
                                  "same) and none of: a reader-unsure candidate, an unread auto or middle "
                                  f"candidate, a low-band mutual-best candidate scoring >= {NEAR_MISS_MIN}, "
                                  f"one of our records within {LOOKALIKE_KM} km with a look-alike name "
                                  f"(token set ratio >= {LOOKALIKE_MIN}), no position")
        list_meta["inputs"].append(f"labels/ext_link_read_{SOURCE_ID}.csv")
    write_meta(out, len(lst), list_meta)

    lpath = silver / f"ext_links_{SOURCE_ID}.parquet"
    links = cands.rename(columns={"item_id": "list_uid"})
    first = [x for x in LINK_COLS_FIRST if x in links.columns]
    links = links[first + [x for x in links.columns if x not in first]]
    write_atomic(lpath, lambda p: links.to_parquet(p, index=False))
    band_counts = links.band.value_counts().to_dict()
    link_meta = {
        "grain": "one row per candidate pair (AERB list row x our facility record); a link is final only where "
                 "accepted is true",
        "columns": list(links.columns),
        "rules": {"blocking": "same PIN, or same district plus a rare shared token (df <= %d); the list has no "
                              "coordinates so the 2 km rule is unused" % RARE_DF,
                  "rejected_records": "AYUSH, dental, veterinary names; facility_type dentist or pharmacy",
                  "score": "IDF-weighted soft Jaccard of name tokens (pair at edit ratio >= 85), location words "
                           "removed, best of full and owner-stripped name; bounded [0,1]",
                  "auto": f"score >= {AUTO_MIN} and mutual best one-to-one (WP11 clusters count as one)",
                  "middle": f"score >= {MIDDLE_MIN} and not auto", "low": f"score < {MIDDLE_MIN}",
                  "acceptance": "a band is accepted only if readers' one-sided 95% Clopper-Pearson lower bound of "
                                "precision same/(same+different) >= 0.90; accepted = band accepted and reader "
                                "marked same; unread pairs are never accepted"},
        "band_counts": band_counts, "stats": stats,
        "inputs": [f"silver/ext_{SOURCE_ID}.parquet", "silver/facilities.parquet", "silver/pin_crosswalk.parquet",
                   "gold/facility_duplicates.parquet"],
    }
    if acc_info:
        link_meta["reader_file"] = f"labels/ext_link_read_{SOURCE_ID}.csv"
        link_meta["bands"] = json.loads(bt.to_json(orient="records"))
        link_meta["acceptance"] = acc_info
    write_meta(lpath, len(links), link_meta)

    report = build_report(raw, lst, None, cands, chk, stats, sample_info, acc_info)
    if acc_info:
        report = report.replace("\n## Method\n", acceptance_report(cands, lst, bt, acc_info) + "\n## Method\n", 1)
    rpath = paths.code_root() / "reports" / f"ext_{SOURCE_ID}.md"
    write_atomic(rpath, lambda p: p.write_text(report))

    print(f"AERB list rows {len(lst)}; located: {lst.position_note.value_counts().to_dict()}")
    print(f"candidate pairs {len(cands)}, list rows with a candidate {cands.item_id.nunique()}, bands {band_counts}")
    print(f"check file {len(chk)} rows (sampled: {sample_info['sampled']})")
    if acc_info:
        for r in bt.itertuples():
            print(f"band {r.band}: read {r.read} (same {r.same}, different {r.different}, unsure {r.unsure}), "
                  f"lower 95% {r.lower_95:.3f}, accepted {r.accepted}")
        print(f"accepted links {acc_info['accepted_pairs']} pairs, {acc_info['list_rows_linked']} list rows; "
              f"not in dataset {acc_info['list_rows_not_in_dataset']}, lane eligible "
              f"{acc_info['list_rows_lane_eligible']}")
    else:
        print("no readers' file yet: no link is accepted")
    print(f"wrote {out}\nwrote {lpath}\nwrote {rpath}")
    return 0


def build_report(raw, lst, fac, c, chk, stats, sample_info, acc=None) -> str:
    bc = c.band.value_counts()
    items_with = c.item_id.nunique()
    auto_items = c[c.band == "auto"].item_id.nunique()
    any_mid_or_auto = c[c.band.isin(["auto", "middle"])].item_id.nunique()
    pn = lst.position_note.value_counts()
    mism = lst[lst.pin_state.notna() & (lst.pin_state.str.lower() != lst.state.str.lower())]
    L = []
    L.append("# AERB radiotherapy licence list: parse, positions and candidate links\n\n")
    L.append(f"Built by `{SCRIPT}` on {utcnow()}. Source: {PUBLISHER}, \"Radiotherapy centres licenced by "
             f"Atomic Energy Regulatory Board (As on April, 2025)\", {SOURCE_URL}, read from the Internet Archive "
             f"capture of 4 Jan 2026 because aerb.gov.in refused HTTPS from this machine. Retrieved {RETRIEVED}. "
             f"SHA-256 `{PDF_SHA256[:16]}...`. Terms and provenance: `data/external/{SOURCE_ID}/SOURCE.md`.\n\n")
    L.append("## Findings\n\n")
    if acc:
        bands = acc["bands_accepted"]
        L.append(f"- **{acc['list_rows_linked']} of {len(lst)} list entries are linked to {acc['facilities_linked']} of "
                 f"our records** ({acc['accepted_pairs']} accepted pairs). Accepted bands: "
                 f"{', '.join(bands) if bands else 'none'}; only pairs a reader marked same count. These links give "
                 "card facts.\n")
        L.append(f"- **{acc['list_rows_not_in_dataset']} list entries are not in our dataset** by the working rule "
                 "(no accepted link and no candidate a reader marked same); "
                 f"{acc['list_rows_lane_eligible']} of them pass the stricter test for the nearby lane. "
                 f"{acc['list_rows_reader_same_band_not_accepted']} more were marked same by a reader in a band "
                 "that was not accepted, so they are in our dataset but give no card fact.\n")
    L.append(f"- **The list has {len(lst)} licensed radiotherapy centre entries** in {lst.state.nunique()} states "
             "and union territories, as of April 2025. It gives name, city, state and PIN only: no address line, "
             "no coordinates, no licence dates and no equipment.\n")
    L.append(f"- **{items_with} of {len(lst)} entries have at least one candidate record in our data** under the "
             f"strict blocking rules; {any_mid_or_auto} have a candidate in the auto or middle band, and "
             f"{auto_items} have an auto-band candidate. Candidates are proposals; links are the accepted pairs.\n")
    L.append(f"- Candidate pairs: {len(c)} ({', '.join(f'{k} {int(v)}' for k, v in bc.items())}). "
             f"The reader file has {len(chk)} rows" + (" (sampled, see below)" if sample_info["sampled"] else
                                                       " (every auto and middle pair)") + ".\n")
    L.append(f"- Positions: {int(pn.get('PIN area', 0))} entries placed at their PIN area (India Post offices of the "
             f"PIN), {int(pn.get('district centre', 0))} at a district centre, {int(pn.get('not located', 0))} not "
             f"located. Median position precision "
             f"{lst.position_precision_km.median():.1f} km. These are approximate positions, not the centre's site.\n")
    L.append("- Status is `undated` for every row: the list is dated, but it does not say when each licence was "
             "issued or expires. A row means the centre was on AERB's published list as of April 2025.\n")
    L.append("\n## Method\n\n")
    L.append("1. Parse: pdfplumber table extraction, 4 columns on all 23 pages; every row has a 6-digit PIN; the row "
             f"count equals the count of PINs ending a line in the page text ({len(raw)}). Wrapped names are joined. "
             "Citation is page and row on page.\n")
    L.append("2. Locate: `vf.locate.resolve(PIN)`; if the PIN is not in the India Post directory, the list's city "
             "as a district in its state (position note `district centre`). District is the PIN's main district in "
             "`silver/pin_crosswalk`.\n")
    L.append(f"3. Our records: {stats['our_records']} in `silver/facilities.parquet`; "
             f"{stats['our_rejected_ayush_dental_vet_pharmacy']} rejected as AYUSH, dental, veterinary or pharmacy "
             "and never candidates.\n")
    L.append(f"4. Blocking: same PIN; or same district (any district of either PIN's post offices) plus a shared "
             f"rare name token (in at most {RARE_DF} names across our data and the list, not a generic or legal "
             f"word; {stats.get('rare_tokens', 0)} such tokens). The list has no coordinates, so the 2 km rule does "
             "not apply.\n")
    L.append("5. Score: IDF-weighted soft Jaccard of name tokens, bounded to [0, 1]. Tokens pair when their edit "
             "ratio is at least 85; legal words (pvt, ltd, unit of) are dropped; the list's city and district and "
             "our city are removed from both names; the best of the full name and the name before \"a unit of\" "
             "is used. Abbreviations are expanded as in `vf.public_list` plus AIIMS.\n")
    L.append(f"6. Bands: auto = score at least {AUTO_MIN} and mutual best one-to-one (records in one WP11 duplicate "
             f"cluster count as one); middle = score at least {MIDDLE_MIN} otherwise; low = the rest. Bands "
             "only propose; links are decided in the acceptance stage above. A band is accepted later only if the readers' one-sided 95% lower bound of precision "
             "is at least 0.90 (with every pair correct, that needs at least 29 read pairs).\n")
    L.append("\n## Bands\n\n| band | pairs | list entries | same PIN | median score |\n|---|---|---|---|---|\n")
    for b in ("auto", "middle", "low"):
        s = c[c.band == b]
        if len(s):
            L.append(f"| {b} | {len(s)} | {s.item_id.nunique()} | {int(s.same_pin.sum())} | {s.score.median():.2f} |\n")
    L.append("\n## Reader file\n\n")
    L.append(f"`data/labels/ext_link_check_{SOURCE_ID}.csv`: {len(chk)} rows, columns {', '.join(CHECK_COLS)}. ")
    if sample_info["sampled"]:
        L.append(f"Band sizes {sample_info['band_sizes']} exceed the cap of {CHECK_CAP}; taken "
                 f"{sample_info['taken']} by seeded random sample within each band (seed {SEED}).\n")
    else:
        L.append("Every auto and middle pair is included (no sampling).\n")
    L.append("Readers write `data/labels/ext_link_read_aerb_rt.csv`. `km_apart` is between our record's point and "
             "the list entry's PIN-area point, so it carries the list's position error.\n")
    L.append("\n## Examples (top auto pairs)\n\n| list entry | our record | score | block |\n|---|---|---|---|\n")
    for r in c[c.band == "auto"].sort_values("score", ascending=False).head(8).itertuples():
        L.append(f"| {r.list_name} ({r.list_pin}) | {r.our_name} ({r.our_pin}) | {r.score:.2f} | {r.block} |\n")
    L.append("\n## Limits\n\n")
    L.append("- The live file could not be fetched (TLS reset from aerb.gov.in). The archive copy of 4 Jan 2026 "
             "matches the June 2025 capture, and the radiotherapy page archived in June 2026 still links the same "
             "URL, but a newer live version may exist.\n")
    L.append("- The list licenses a facility for radiotherapy; it does not say what other cancer care it offers. A "
             "listed centre is outside evidence that radiotherapy was licensed, not a statement about our record. "
             "Absence from the list says nothing about a record.\n")
    if len(mism):
        L.append(f"- {len(mism)} entries have a PIN whose post offices are in another state than the one printed "
                 f"(for example {mism.iloc[0]['name']}, {mism.iloc[0]['state']}, PIN {mism.iloc[0]['pin']}). "
                 "Their positions follow the PIN.\n")
    near = c[(c.band == "low") & c.mutual_best & (c.score >= 0.20)]
    L.append("- Recall of the linker is not measured. Records with a different PIN and a generic name in another "
             "district are not candidates by design (strict linkage). Just under the middle band there are "
             f"{len(near)} mutual-best pairs scoring 0.20 to {MIDDLE_MIN}; an unscored read of about 30 of them on "
             "22 Nov found several that look like the same hospital under a brand or trust name (for example "
             "Jawaharlal Nehru Cancer Hospital, Bhopal; K. K. Patel Super Speciality Hospital, Bhuj) and several "
             "that do not (Aster Medcity and Aster Labs). They stay out of the reader file: strict precision is "
             "chosen over recall.\n")
    L.append("- The reader file shows the band and score. Readers are not blind to the matcher's proposal.\n")
    return "".join(L)


if __name__ == "__main__":
    raise SystemExit(main())
