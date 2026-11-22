"""WP13 / D055: MoHFW list of 196 trauma care facilities (trauma care scheme
along national highways), parsed, located and linked (candidates only) to our
facility records.

Reads:
  external/mohfw_trauma/mohfw_list/list_of_trauma_care.pdf
      "List of 196 Trauma Care Facilities and funds allocated" (MoHFW, file dated
      8 Feb 2023), from the Internet Archive capture of the official URL (the old
      MoHFW host no longer resolves). Read as data with pdfplumber, never executed.
  silver/facilities.parquet, silver/pin_crosswalk.parquet,
  gold/facility_duplicates.parquet (WP11 duplicate pairs), silver/places.parquet
  (through vf.locate.resolve)

Writes (atomic, with .meta.json sidecars):
  silver/ext_mohfw_trauma.parquet        one row per list row (196)
  silver/ext_links_mohfw_trauma.parquet  every candidate pair (list row x our record), score and band
  labels/ext_link_check_mohfw_trauma.csv pairs for independent readers (written only before
                                         readers have read; frozen once labels/ext_link_read_* exists)
  reports/ext_mohfw_trauma.md            counts, method, reader verdicts, limits

Once labels/ext_link_read_mohfw_trauma.csv exists (readers write it), the rerun joins
it to the candidates: per band, precision = same / (same + different) with a
one-sided 95% Clopper-Pearson lower bound; a band is accepted only if that bound is
at least 0.90, and inside an accepted band only reader-marked same pairs are links
(`accepted`). Each list row gets a link_status (linked, in_dataset_unaccepted,
unsure, not_in_dataset); only not_in_dataset rows may feed the outside-nearby lane.

Linkage (reports/score_methodology_research.md, strict rules), as in
scripts/wp13_ext_ncg.py:
  blocking: same PIN (unused: the list has no PIN), or within 2 km (unused: no
  coordinates), or same district plus a rare shared name token. The list names a
  government unit by its type and its town ("District Hospital, Srikakulam"), so
  here the town or district word itself may be the rare token, if at most
  RARE_MAX_DF of our record names carry it (decision recorded in the report).
  score: IDF-weighted soft Jaccard on name tokens, bounded to [0, 1], with the
  pair's district, state and same-place city words dropped from both names.
  rejects: AYUSH, dental and veterinary records never enter the pool.
  one-to-one: greedy by score, except records in one WP11 duplicate cluster may
  share a list row; a high pair that loses drops to the middle band.
  bands are provisional sorting for readers; a band is accepted only if its
  one-sided 95% lower bound of precision, from readers' labels, is at least 0.90.

Usage:
  .venv/bin/python -I scripts/wp13_ext_mohfw_trauma.py
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pdfplumber
from rapidfuzz import fuzz
from scipy.stats import beta as beta_dist

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from vf import locate, paths  # noqa: E402
from vf.public_list import GENERIC, normalise_name  # noqa: E402

SCRIPT = "scripts/wp13_ext_mohfw_trauma.py"
SOURCE_ID = "mohfw_trauma"
SOURCE_NAME = "MoHFW list of 196 trauma care facilities"
PUBLISHER = "Ministry of Health and Family Welfare, Government of India"
OFFICIAL_URL = "https://main.mohfw.gov.in/sites/default/files/List%20of%20Trauma%20Care.pdf"
ARCHIVE_URL = ("https://web.archive.org/web/20231215043518id_/"
               "https://main.mohfw.gov.in/sites/default/files/List%20of%20Trauma%20Care.pdf")
PDF_FILE = "mohfw_list/list_of_trauma_care.pdf"
LIST_DATE = "2023-02-08"
WHAT = "trauma care facility funded under the MoHFW trauma care scheme for national highways"
SERVICE_LINE = "trauma"
LINK_VERSION = "mohfw_trauma_v1"

AUTO_MIN = 0.70
MIDDLE_MIN = 0.30
SOFT_MIN = 85.0
RARE_MAX_DF = 10
CHECK_CAP = 150
LOW_PROBE = 15
SEED = 20261122
ACCEPT_LB = 0.90

STOP = frozenset("""and of the a an at in for pvt ltd limited private unit by trust society
foundation india co""".split())
#: Extra abbreviations in this list ("Taluk Hos.", "Taluq", "SD Hospital", "Dist.").
LOCAL_ABBREV = {"hos": "hospital", "taluq": "taluk", "sd": "sub divisional", "govt": "government",
                "dist": "district", "distt": "district"}
REJECT_NAME = re.compile(
    r"\b(?:ayurved\w*|ayush|homoeo\w*|homeo\w*|unani|siddha|naturopath\w*|yoga|panchakarma|"
    r"dental|dentist\w*|dentofacial|orthodont\w*|endodont\w*|veterinar\w*|vet|animal|pets|"
    r"pet (?:clinic|hospital|care|multispeciality))\b", re.I)
KEEP_NAME = re.compile(
    r"\b(?:maternity|children|child|eye|ent|multi\w*|general|nursing home|cancer|onco\w*|heart|"
    r"orthopaedic|orthopedic|surgical|medical cent\w*|medical college|trauma)\b", re.I)
REJECT_TYPES = frozenset({"dentist"})
#: An AYUSH college or hospital, or a dental college, is rejected even though "medical college"
#: or "hospital" would otherwise keep it ("R.B. Ayurvedic Medical College & Hospital").
REJECT_ALWAYS = re.compile(
    r"\b(?:(?:ayurved\w*|ayush|homoeo\w*|homeo\w*|unani|siddha|naturopath\w*)\s+"
    r"(?:medical\s+)?(?:college|hospital|institute)|dental\s+college)\b", re.I)

#: Place queries read by hand for list names whose town is ambiguous in the
#: India Post directory, misspelt, or not a place name as written. Each use is
#: reported in position_detail as a "hand reading".
HAND_PLACE = {
    "Taluk Hospital, Tekkali": "Tekkali, Srikakulam",
    "Taluk Hos. Nayadupet": "Nellore",  # Nayudupeta, SPSR Nellore district
    "District Hospital, Roing": "Roing, Lower Dibang Valley",
    "Civil Hospital, Palanpur": "Palanpur, Banas Kantha",
    "CHC, Jetpur": "Jetpur, Rajkot",
    "Sadar Hospital Daltanganj": "Daltonganj",
    "DH Narsimhapur": "Narsinghpur",
    "MKCG Medical College, Behrampur": "Berhampur, Ganjam",
    "District Hospital Namachi": "Namchi",
    "District Hqr. Hospital, Kovilpatti": "Kovilpatti, Tuticorin",
    "District Hospital, Mehboobnagar": "Mahbubnagar",
    "LLR Hospital & GSVM College, Kanpur": "Kanpur Nagar",
    "Doon Medical College & Hospital": "Dehradun",
    "Govt. Medical College, Goa": "North Goa",  # Bambolim campus
    # the automatic reading picked the wrong district for these (checked by hand)
    "Baliram Kashyap Memorial Medical College, Jagdalpur": "Bastar",  # not Raipur
    "Civil Hospital, Devangiri": "Davangere",  # not Haveri
    "Trauma Hospital, Ramban, Doda": "Ramban",  # Ramban district, carved from Doda in 2007
    "STNM Hospital Gangtok": "East District",
    "MLN Medical College, Allhabad": "Allahabad",
}


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def git_head():
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=paths.code_root(), capture_output=True,
                              text=True, check=True).stdout.strip()
    except Exception:
        return None


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


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
    rel = str(path.relative_to(paths.data_dir())) if paths.data_dir() in path.parents else str(path)
    meta = {"output": rel, "rows": int(rows), "created_at_utc": utcnow(), "git_commit": git_head(),
            "script": SCRIPT, "source_id": SOURCE_ID, "link_version": LINK_VERSION, **extra}
    body = json.dumps(meta, indent=2, default=str) + "\n"
    write_atomic(path.with_name(path.name + ".meta.json"), lambda p: p.write_text(body))


def one_line(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------

def parse_pdf(pdf: Path) -> pd.DataFrame:
    """One row per S. No. The state cell is merged over its rows, so it is
    carried forward (also across pages)."""
    rows, state = [], None
    with pdfplumber.open(pdf) as doc:
        for pg in doc.pages:
            for table in pg.extract_tables():
                row_on_page = 0
                for cells in table:
                    cells = [one_line(c) if c is not None else None for c in cells] + [None] * 4
                    sno, st, name, amount = cells[:4]
                    if not (sno or "").isdigit():
                        continue  # title and header rows
                    if st:
                        state = st
                    row_on_page += 1
                    rows.append({"raw_sno": int(sno), "raw_state": state, "raw_state_cell": st or None,
                                 "raw_name": name, "raw_amount_cr": amount, "page": pg.page_number,
                                 "row_on_page": row_on_page})
    return pd.DataFrame(rows)


def norm_state(s) -> str:
    t = re.sub(r"[^A-Z ]", " ", str(s or "").upper().replace("&", " AND "))
    t = re.sub(r"\s+", " ", t).strip()
    fix = {"DADAR AND NAGAR HAVELI": "DADRA AND NAGAR HAVELI", "ANDAMAN AND NIKOBAR": "ANDAMAN AND NICOBAR ISLANDS",
           "ARUNACHAL PRADESH": "ARUNACHAL PRADESH", "ORISSA": "ODISHA", "UTTRAKHAND": "UTTARAKHAND"}
    t = fix.get(t, t)
    return locate.STATE_ALIASES.get(t, t) or t


def locate_row(r) -> dict:
    """The list has no address or PIN: the town or district is read from the
    unit name (comma parts, last first; then single words), within the listed
    state, and always reported as that district's centre."""
    st = norm_state(r.raw_state)
    state_arg = locate.nice(st)
    name = r.raw_name
    notes, tried = [], []
    hand = HAND_PLACE.get(name)
    parts = [x.strip(" .-") for x in re.split(r"[,;/]|\s[-–]\s", name) if x.strip(" .-")]
    words = re.findall(r"[A-Za-z]{4,}", name)[::-1]
    queries = ([(hand, ("district", "city", "post_office"), "hand reading")] if hand else []) + \
              [(x, ("district", "city", "post_office"), "name part") for x in reversed(parts)] + \
              [(w, ("district", "city"), "name word") for w in words]
    for q, kinds, how in queries:
        if how != "hand reading":
            q = re.sub(r"[\d#()'.&]+", " ", q)
            q = " ".join(w for w in q.split() if normalise_name(w) and all(
                x not in GENERIC and x not in STOP and x not in LOCAL_ABBREV for x in normalise_name(w).split()))
        q = q.strip(" .-:")
        if len(q) < 3 or q.lower() in tried:
            continue
        tried.append(q.lower())
        res = locate.resolve(q, state=state_arg)
        if res.status == "ok" and res.kind in kinds and res.district:
            if res.state and norm_state(res.state) != st and st not in norm_state(res.state):
                notes.append(f"'{q}' resolved to {res.state}, list state is {r.raw_state}")
                continue
            d = locate.resolve(f"{res.district} district", state=res.state)
            if not (d.status == "ok" and d.kind == "district"):
                d = locate.resolve(res.district, state=res.state)
            if not (d.status == "ok" and d.kind == "district"):
                notes.append(f"'{q}' resolved, but its district {res.district} has no district centre")
                continue
            return {"district": d.district, "state_pin": d.state, "lat": d.lat, "lon": d.lon,
                    "position_precision_km": d.precision_km, "position_note": "district centre",
                    "position_detail": f"{how} '{q}' ({res.kind} match: {res.how}); position: {d.how}",
                    "locate_notes": "; ".join(notes) or None}
    notes.append("no district resolved from the name" + (f" (tried: {', '.join(tried)})" if tried else ""))
    return {"district": None, "state_pin": st, "lat": None, "lon": None, "position_precision_km": None,
            "position_note": None, "position_detail": None, "locate_notes": "; ".join(notes)}


def build_list(src: Path, retrieved: str, src_sha: str) -> tuple[pd.DataFrame, dict]:
    df = parse_pdf(src)
    loc = pd.DataFrame([locate_row(r) for r in df.itertuples()], index=df.index)
    df = pd.concat([df, loc], axis=1)
    df["list_uid"] = df["raw_sno"].map(lambda n: f"mohfw_trauma-{n:03d}")
    df["name"] = df["raw_name"]
    df["address"] = None  # the list gives none
    df["pin"] = None
    df["state"] = df.apply(lambda r: locate.nice(r.state_pin) if r.state_pin else r.raw_state, axis=1)
    df["district"] = df["district"].map(lambda d: locate.nice(d) if d else None)
    df["amount_allocated_cr"] = pd.to_numeric(df["raw_amount_cr"], errors="coerce")
    df["service_line"] = SERVICE_LINE
    df["what"] = WHAT
    df["status"] = "undated"
    df["list_date"] = LIST_DATE
    df["status_detail"] = ("The list records funds allocated under the MoHFW trauma care scheme (the programme page "
                           "gives the scheme's validity as up to March 2020). It carries no per-facility date, level "
                           "or operating status. File dated 8 Feb 2023.")
    df["retrieved_utc"] = retrieved
    df["source_url"] = OFFICIAL_URL
    df["citation"] = df.apply(lambda r: (f"{SOURCE_NAME} (MoHFW, 8 Feb 2023), page {r.page}, S. No. {r.raw_sno}; "
                                         f"{OFFICIAL_URL} (Internet Archive capture 15 Dec 2023), "
                                         f"retrieved {retrieved[:10]}"), axis=1)
    df["source_file"] = f"external/{SOURCE_ID}/{PDF_FILE}"
    df["source_sha256"] = src_sha
    cols = ["list_uid", "name", "address", "pin", "district", "state", "lat", "lon", "position_precision_km",
            "position_note", "position_detail", "locate_notes", "service_line", "what", "status", "list_date",
            "status_detail", "amount_allocated_cr", "citation", "source_url", "retrieved_utc", "page",
            "row_on_page", "raw_sno", "raw_state", "raw_state_cell", "raw_name", "raw_amount_cr", "state_pin",
            "source_file", "source_sha256"]
    df = df[cols].reset_index(drop=True)
    snos = df.raw_sno.tolist()
    stats = {"rows": len(df), "sno_contiguous_1_to_n": snos == list(range(1, len(df) + 1)),
             "states": int(df.raw_state.nunique()),
             "amount_missing": int(df.amount_allocated_cr.isna().sum()),
             "amount_values_cr": df.amount_allocated_cr.value_counts().sort_index().to_dict(),
             "position_note": df.position_note.fillna("not located").value_counts().to_dict(),
             "not_located": df[df.position_note.isna()][["list_uid", "name", "raw_state"]].to_dict("records")}
    return df, stats


# ---------------------------------------------------------------------------
# Link
# ---------------------------------------------------------------------------

def tokens(name) -> list[str]:
    out = []
    for w in normalise_name(name).split():
        out.extend(LOCAL_ABBREV.get(w, w).split())
    return [w for w in out if w not in STOP and len(w) > 1]


PLACE_ALIASES = {"thiruvananthapuram": "trivandrum", "trivandrum": "thiruvananthapuram", "bengaluru": "bangalore",
                 "bangalore": "bengaluru", "kochi": "cochin", "cochin": "kochi", "gurugram": "gurgaon",
                 "gurgaon": "gurugram", "mumbai": "bombay", "kolkata": "calcutta", "prayagraj": "allahabad",
                 "allahabad": "prayagraj", "visakhapatnam": "vizag", "kozhikode": "calicut", "calicut": "kozhikode",
                 "mysuru": "mysore", "mysore": "mysuru", "vadodara": "baroda", "baroda": "vadodara",
                 "belgaum": "belagavi", "belagavi": "belgaum", "hubli": "hubballi", "hubballi": "hubli",
                 "tumkur": "tumakuru", "tumakuru": "tumkur", "davangere": "devangiri", "gulbarga": "kalaburagi"}


def place_tokens(*names) -> set:
    out = {"india"}
    for n in names:
        if n is not None and not (isinstance(n, float) and math.isnan(n)):
            out |= set(normalise_name(n).split())
    out |= {PLACE_ALIASES[t] for t in list(out) if t in PLACE_ALIASES}
    return out - set(GENERIC)


def same_place(city, district) -> bool:
    a, b = normalise_name(city), normalise_name(district)
    if not a or not b:
        return False
    a, b = PLACE_ALIASES.get(a, a), PLACE_ALIASES.get(b, b)
    return a == b or fuzz.token_set_ratio(a, b) >= 85


def strip_place(toks: list[str], place: set) -> list[str]:
    kept = [t for t in toks if t not in place]
    return kept if kept else toks


def soft_jaccard(a: list[str], b: list[str], idf: dict, default_idf: float) -> tuple[float, list]:
    """IDF-weighted soft Jaccard on token sets, in [0, 1]; tokens pair greedily by
    rapidfuzz ratio (at least SOFT_MIN). score = overlap / (W_a + W_b - overlap)."""
    A, B = sorted(set(a)), sorted(set(b))
    if not A or not B:
        return 0.0, []
    w = lambda t: idf.get(t, default_idf)  # noqa: E731
    pairs = []
    for x in A:
        for y in B:
            s = 100.0 if x == y else (fuzz.ratio(x, y) if min(len(x), len(y)) >= 4 else 0.0)
            if s >= SOFT_MIN:
                pairs.append((s, x, y))
    pairs.sort(reverse=True)
    used_a, used_b, overlap, matched = set(), set(), 0.0, []
    for s, x, y in pairs:
        if x in used_a or y in used_b:
            continue
        used_a.add(x)
        used_b.add(y)
        overlap += (s / 100.0) * (w(x) + w(y)) / 2
        matched.append(x if x == y else f"{x}~{y}")
    wa, wb = sum(w(t) for t in A), sum(w(t) for t in B)
    den = wa + wb - overlap
    return (float(min(1.0, max(0.0, overlap / den))) if den > 0 else 0.0), matched


def haversine_km(lat1, lon1, lat2, lon2):
    if any(v is None or (isinstance(v, float) and math.isnan(v)) for v in (lat1, lon1, lat2, lon2)):
        return None
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(h))


def duplicate_clusters(dups: pd.DataFrame) -> dict:
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in zip(dups.facility_id_a, dups.facility_id_b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)
    return {x: find(x) for x in parent}


def link(lst: pd.DataFrame, fac: pd.DataFrame, pinx: pd.DataFrame, dups: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    f = fac.copy()
    nm = f["name"].fillna("")
    rej = (f["facility_type"].isin(REJECT_TYPES) | nm.str.contains(REJECT_ALWAYS)
           | (nm.str.contains(REJECT_NAME) & ~nm.str.contains(KEEP_NAME)))
    pool = f[~rej].copy()
    stats = {"our_records": len(f), "rejected_ayush_dental_vet": int(rej.sum()), "pool": len(pool)}

    docs = [set(tokens(n)) for n in f["name"]] + [set(tokens(n)) for n in lst["name"]]
    df_all, df_ours = {}, {}
    for i, d in enumerate(docs):
        for t in d:
            df_all[t] = df_all.get(t, 0) + 1
            if i < len(f):
                df_ours[t] = df_ours.get(t, 0) + 1
    N = len(docs)
    idf = {t: math.log((N + 1) / (c + 1)) + 1.0 for t, c in df_all.items()}
    default_idf = math.log(N + 1) + 1.0

    pd_map = {str(p): set(zip(g.state_pin, g.district_pin)) for p, g in pinx.groupby("pin")}
    pool["districts"] = pool["pin"].map(lambda p: pd_map.get(str(p), set()) if pd.notna(p) else set())
    pool["toks"] = pool["name"].map(tokens)
    by_dist = {}
    for i, ds in zip(pool.index, pool["districts"]):
        for d in ds:
            by_dist.setdefault(d, []).append(i)
    clusters = duplicate_clusters(dups)

    rows, place_token_blocks = [], 0
    for r in lst.itertuples():
        if not (r.district and r.state_pin):
            continue
        ltoks = tokens(r.name)
        dist_tokens = set(normalise_name(r.district).split())
        # rare: at most RARE_MAX_DF of our names carry it, 3+ letters, not a unit-type word.
        # The district's own word is allowed (the list names units by their town).
        rare = {t for t in ltoks if df_ours.get(t, 0) <= RARE_MAX_DF and len(t) >= 3 and t not in GENERIC
                and not t.isdigit()}
        keys = [k for k in by_dist if k[0] == r.state_pin and k[1].upper() == r.district.upper()]
        cand = {}
        for k in keys:
            for i in by_dist[k]:
                otoks = pool.at[i, "toks"]
                shared = rare & set(otoks)
                if not shared:
                    shared = {t for t in rare for u in otoks if len(t) >= 4 and len(u) >= 4
                              and fuzz.ratio(t, u) >= SOFT_MIN}
                if shared:
                    cand.setdefault(i, set()).add("same_district_rare_token:" + ",".join(sorted(shared)))
                    if shared <= dist_tokens | {PLACE_ALIASES.get(t, t) for t in dist_tokens}:
                        place_token_blocks += 1
        for i, blocks in cand.items():
            o = pool.loc[i]
            city = o["city_raw"] if same_place(o["city_raw"], r.district) else None
            place = place_tokens(r.district, r.state, r.state_pin, city, o["state_raw"])
            score, matched = soft_jaccard(strip_place(ltoks, place), strip_place(o["toks"], place),
                                          idf, default_idf)
            km = haversine_km(r.lat, r.lon, o["lat"], o["lon"]) if bool(o["coord_valid"]) else None
            rows.append({
                "list_uid": r.list_uid, "facility_id": o["facility_id"],
                "dup_cluster": clusters.get(o["facility_id"], o["facility_id"]),
                "block": " | ".join(sorted(blocks)), "same_pin": False,
                "score": round(score, 4), "matched_tokens": ", ".join(matched),
                "km_apart": None if km is None else round(km, 2),
                "list_position_note": r.position_note, "list_position_precision_km": r.position_precision_km,
                "list_name": r.name, "list_address": r.address, "list_pin": r.pin, "list_district": r.district,
                "list_state": r.state, "our_name": o["name"], "our_city": o["city_raw"], "our_state": o["state_raw"],
                "our_pin": o["pin"], "our_type": o["facility_type"], "our_operator": o["operator_type"],
                "our_coord_check": o["coord_check"], "our_row_status": o["row_status"],
            })
    links = pd.DataFrame(rows)
    if links.empty:
        return links, stats

    links = links.sort_values(["score", "list_uid", "facility_id"], ascending=[False, True, True]).reset_index(drop=True)
    links["rank_in_list_row"] = links.groupby("list_uid")["score"].rank(method="first", ascending=False).astype(int)
    taken_list, taken_rec, winner = {}, {}, []
    for x in links.itertuples():
        lc = taken_list.get(x.list_uid)
        rc = taken_rec.get(x.dup_cluster)
        ok = (lc is None or lc == x.dup_cluster) and (rc is None or rc == x.list_uid)
        if ok and x.score >= AUTO_MIN:
            taken_list[x.list_uid] = x.dup_cluster
            taken_rec[x.dup_cluster] = x.list_uid
        winner.append(ok)
    links["one_to_one_ok"] = winner
    links["band"] = np.where((links.score >= AUTO_MIN) & links.one_to_one_ok, "auto",
                             np.where(links.score >= MIDDLE_MIN, "middle", "low"))
    links["band_note"] = None
    links.loc[(links.score >= AUTO_MIN) & ~links.one_to_one_ok, "band_note"] = "lost one-to-one contest"
    links["link_version"] = LINK_VERSION
    stats.update({"candidates": len(links), "bands": links.band.value_counts().to_dict(),
                  "list_rows_blockable": int(lst.district.notna().sum()),
                  "list_rows_with_candidate": int(links.list_uid.nunique()),
                  "list_rows_with_auto": int(links[links.band == "auto"].list_uid.nunique()),
                  "list_rows_with_auto_or_middle": int(links[links.band != "low"].list_uid.nunique()),
                  "pairs_blocked_only_on_place_word": place_token_blocks,
                  "idf_docs": N, "rare_max_df": RARE_MAX_DF})
    return links, stats


def check_sheet(links: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Every auto and middle pair (sampled per band if over the cap), then the best
    low pairs as a boundary probe if room remains; shuffled so band is not read from order."""
    rng = np.random.default_rng(SEED)
    a, m = links[links.band == "auto"], links[links.band == "middle"]
    sampling = {"cap": CHECK_CAP, "auto_total": len(a), "middle_total": len(m)}
    if len(a) + len(m) > CHECK_CAP:
        share = len(a) / (len(a) + len(m))
        na = min(len(a), max(int(round(CHECK_CAP * share)), min(len(a), 60)))
        a = a.sample(n=na, random_state=SEED) if len(a) > na else a
        m = m.sample(n=min(CHECK_CAP - len(a), len(m)), random_state=SEED)
        sampling["sampled"] = True
        sampling["sampling_rule"] = ("auto keeps its share of the cap (at least 60 or all); middle fills the rest; "
                                     f"simple random sample per band, seed {SEED}")
    else:
        sampling["sampled"] = False
    room = CHECK_CAP - len(a) - len(m)
    low = links[links.band == "low"].sort_values("score", ascending=False)
    lo = low.head(max(0, min(LOW_PROBE, room)))
    # second probe: every other low pair whose record is public (upgraded or renamed
    # government units score low on names: "District Hospital, Chamba" is now a
    # medical college), so readers can see what the score misses
    pub = low[(low.our_operator == "public") & ~low.index.isin(lo.index)]
    pub = pub.head(max(0, CHECK_CAP - len(a) - len(m) - len(lo)))
    lo = pd.concat([lo, pub])
    sampling.update({"auto_in_sheet": len(a), "middle_in_sheet": len(m), "low_probe_in_sheet": len(lo),
                     "low_probe_rule": f"the {LOW_PROBE} highest-scoring low pairs, plus every other low pair "
                                       "whose record's operator is public",
                     "low_total": int((links.band == "low").sum())})
    s = pd.concat([a, m, lo])
    s = s.iloc[rng.permutation(len(s))]
    out = pd.DataFrame({
        "item_id": s.list_uid + "|" + s.facility_id,
        "list_name": s.list_name, "list_address": s.list_address, "list_pin": s.list_pin,
        "list_district": s.list_district, "our_facility_id": s.facility_id, "our_name": s.our_name,
        "our_address_or_city": s.our_city.fillna("") + ", " + s.our_state.fillna(""),
        "our_pin": s.our_pin, "km_apart": s.km_apart, "score": s.score, "band": s.band,
    })
    return out.reset_index(drop=True), sampling


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def lower_bound_ok(n: int, wrong: int, p0: float = 0.90, alpha: float = 0.05) -> bool:
    k = n - wrong
    tail = sum(math.comb(n, j) * p0 ** j * (1 - p0) ** (n - j) for j in range(k, n + 1))
    return tail <= alpha


def max_wrong(n: int) -> int | str:
    w = -1
    while w + 1 <= n and lower_bound_ok(n, w + 1):
        w += 1
    return w if w >= 0 else "none (too few pairs)"


def cp_lower(k: int, n: int, alpha: float = 0.05) -> float:
    """One-sided Clopper-Pearson lower bound of a binomial proportion k / n."""
    if n == 0 or k == 0:
        return 0.0
    return float(beta_dist.ppf(alpha, k, n - k + 1))


def apply_readers(links: pd.DataFrame, lst: pd.DataFrame, read: pd.DataFrame,
                  sheet_ids: set) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Join reader verdicts to the candidate pairs (as scripts/wp13_ext_ncg.py does):
    accept a band only if its one-sided 95% Clopper-Pearson lower bound of precision,
    same / (same + different), is at least ACCEPT_LB; inside accepted bands keep only
    reader-marked same pairs; give every list row a link status."""
    read = read.copy()
    read["verdict"] = read.verdict.astype(str).str.strip().str.lower()
    bad = sorted(set(read.verdict) - {"same", "different", "unsure"})
    if bad:
        raise SystemExit(f"reader file has unknown verdicts {bad}")
    if read.item_id.duplicated().any():
        raise SystemExit("reader file has duplicate item_id rows")
    if set(read.item_id) != sheet_ids:
        raise SystemExit("reader file items do not match the check sheet; rebuild the sheet and read again")
    links = links.copy()
    links["item_id"] = links.list_uid + "|" + links.facility_id
    missing = set(read.item_id) - set(links.item_id)
    if missing:
        raise SystemExit(f"{len(missing)} read items are not in the rebuilt candidates (inputs changed?)")
    rv = read.set_index("item_id")
    links["reader_verdict"] = links.item_id.map(rv.verdict)
    links["reader_note"] = links.item_id.map(rv.note)

    bands = []
    for bnd in ("auto", "middle", "low"):
        x = links[links.band == bnd]
        s, d, u = (int((x.reader_verdict == v).sum()) for v in ("same", "different", "unsure"))
        n = s + d
        bands.append({"band": bnd, "pairs": len(x), "read": s + d + u, "same": s, "different": d,
                      "unsure": u, "precision": (s / n) if n else float("nan"), "lower_95": cp_lower(s, n),
                      "lower_95_unsure_as_wrong": cp_lower(s, s + d + u),
                      "accepted": bool(n > 0 and cp_lower(s, n) >= ACCEPT_LB)})
    btab = pd.DataFrame(bands)
    # Sensitivity only (never used to accept): auto and middle pooled.
    am = links[links.band.isin(["auto", "middle"])]
    s_am, d_am = int((am.reader_verdict == "same").sum()), int((am.reader_verdict == "different").sum())
    pooled = {"pairs": len(am), "same": s_am, "different": d_am,
              "unsure": int((am.reader_verdict == "unsure").sum()),
              "precision": s_am / (s_am + d_am) if s_am + d_am else float("nan"),
              "lower_95": cp_lower(s_am, s_am + d_am)}
    ok_bands = set(btab[btab.accepted].band)
    links["accepted"] = links.band.isin(ok_bands) & (links.reader_verdict == "same")

    acc = links[links.accepted]
    fac_multi = int(acc.facility_id.duplicated().sum())
    lr = acc.groupby("list_uid").agg(n=("facility_id", "size"),
                                     clusters=("dup_cluster", lambda c: c.fillna("_none_").nunique()))
    list_multi = int(((lr.n > 1) & (lr.clusters > 1)).sum())
    if fac_multi or list_multi:
        raise SystemExit(f"accepted links break one-to-one: {fac_multi} records, {list_multi} list rows")

    same_any = set(links[links.reader_verdict == "same"].list_uid)
    unsure_any = set(links[links.reader_verdict == "unsure"].list_uid)
    linked = set(acc.list_uid)
    unread = links[links.reader_verdict.isna()]
    best_unread = unread.groupby("list_uid").score.max()
    n_unread = unread.groupby("list_uid").size()
    n_unread_nonprivate = unread[unread.our_operator.fillna("") != "private"].groupby("list_uid").size()
    ncand = links.groupby("list_uid").size()

    def status(u):
        if u in linked:
            return "linked"
        if u in same_any:
            return "in_dataset_unaccepted"
        if u in unsure_any:
            return "unsure"
        return "not_in_dataset"

    lst = lst.copy()
    lst["link_status"] = lst.list_uid.map(status)
    lst["n_candidates"] = lst.list_uid.map(ncand).fillna(0).astype(int)
    lst["n_unread_candidates"] = lst.list_uid.map(n_unread).fillna(0).astype(int)
    lst["best_unread_score"] = lst.list_uid.map(best_unread)
    lst["linked_facility_ids"] = lst.list_uid.map(acc.groupby("list_uid").facility_id.agg(list)).apply(
        lambda v: v if isinstance(v, list) else [])
    lst["link_version"] = LINK_VERSION

    low_read = links[(links.band == "low") & links.reader_verdict.notna()]
    low_by_op = (low_read.assign(op=low_read.our_operator.fillna("missing"))
                 .groupby("op").reader_verdict.value_counts().unstack(fill_value=0))
    unread_low_by_op = unread[unread.band == "low"].our_operator.fillna("missing").value_counts().to_dict()
    nid = lst[lst.link_status == "not_in_dataset"]
    stats = {
        "bands": btab.to_dict(orient="records"), "pooled_auto_middle_sensitivity": pooled,
        "accept_lb": ACCEPT_LB, "accepted_bands": sorted(ok_bands),
        "accepted_pairs": int(len(acc)), "list_rows_linked": int(len(linked)),
        "records_linked": int(acc.facility_id.nunique()),
        "list_rows_with_same_pair": int(len(same_any)),
        "link_status": lst.link_status.value_counts().to_dict(),
        "not_in_dataset_position": nid.position_note.value_counts().to_dict(),
        "not_in_dataset_precision_km": nid.position_precision_km.describe().round(2).to_dict() if len(nid) else {},
        "not_in_dataset_no_candidate": int((nid.n_candidates == 0).sum()),
        "not_in_dataset_unread_candidate": int((nid.n_unread_candidates > 0).sum()),
        "not_in_dataset_unread_nonprivate_candidate": int(nid.list_uid.isin(n_unread_nonprivate.index).sum()),
        "low_read_by_operator": {k: {c: int(v) for c, v in row.items()} for k, row in low_by_op.iterrows()},
        "unread_low_by_operator": {k: int(v) for k, v in unread_low_by_op.items()},
    }
    links = links.drop(columns=["item_id"])
    front = ["list_uid", "facility_id", "score", "band", "reader_verdict", "accepted"]
    links = links[front + [c for c in links.columns if c not in front]]
    return links, lst, stats


def reader_section(rstats: dict, lst: pd.DataFrame) -> str:
    L = ["\n## Reader verdicts and acceptance\n\n"
         f"Readers judged every pair in `data/labels/ext_link_check_{SOURCE_ID}.csv` "
         f"(`data/labels/ext_link_read_{SOURCE_ID}.csv`). Precision is same / (same + different); unsure pairs "
         "are left out of it, and a second column counts them as wrong. A band is accepted only if the one-sided "
         f"95% Clopper-Pearson lower bound is at least {ACCEPT_LB:.2f}. Inside an accepted band only pairs the "
         "reader marked same are kept.\n\n"
         "| band | pairs | read | same | different | unsure | precision | lower 95% | lower 95%, unsure as wrong "
         "| accepted |\n|---|---|---|---|---|---|---|---|---|---|\n"]
    for b in rstats["bands"]:
        prec = "n/a" if pd.isna(b["precision"]) else f"{b['precision']:.3f}"
        L.append(f"| {b['band']} | {b['pairs']} | {b['read']} | {b['same']} | {b['different']} | {b['unsure']} | "
                 f"{prec} | {b['lower_95']:.3f} | {b['lower_95_unsure_as_wrong']:.3f} | "
                 f"{'yes' if b['accepted'] else 'no'} |\n")
    auto = next(b for b in rstats["bands"] if b["band"] == "auto")
    mid = next(b for b in rstats["bands"] if b["band"] == "middle")
    pool = rstats["pooled_auto_middle_sensitivity"]
    if not rstats["accepted_bands"]:
        L.append(f"\n**No band qualifies, so this source produces no card facts.** The auto band was "
                 f"{auto['same']} of {auto['same'] + auto['different']} same, but {auto['pairs']} pairs is too "
                 f"few: even a perfect band needs 29 pairs for the lower bound to reach 0.90, and "
                 f"{auto['pairs']} perfect pairs give {auto['lower_95']:.3f}. The middle band is mixed "
                 f"({mid['same']} same, {mid['different']} different). Pooling auto and middle (a sensitivity "
                 f"check, not a declared rule) gives {pool['same']} same and {pool['different']} different, "
                 f"precision {pool['precision']:.3f}, lower bound {pool['lower_95']:.3f}: it fails too. No pooling "
                 "with another source was declared, so none is applied.\n")
    else:
        L.append(f"\nAccepted bands: {', '.join(rstats['accepted_bands'])}.\n")
    ls = rstats["link_status"]
    n = len(lst)
    L.append("\n## What the list adds\n\n| list rows | count | share |\n|---|---|---|\n")
    for key, label in (("linked", "linked (accepted link, card fact)"),
                       ("in_dataset_unaccepted", "in our data per reader, band not accepted (no card fact)"),
                       ("unsure", "reader unsure for a candidate, none same (excluded from the lane)"),
                       ("not_in_dataset", "not in our dataset (outside-nearby lane candidates)")):
        c = int(ls.get(key, 0))
        L.append(f"| {label} | {c} | {c / n:.1%} |\n")
    L.append(f"| all list rows | {n} | 100% |\n")
    nid = lst[lst.link_status == "not_in_dataset"]
    L.append("\nLocation quality of the not-in-dataset rows:\n\n| position | rows | precision radius km (median) "
             "| min | max |\n|---|---|---|---|---|\n")
    for note in ("PIN area", "district centre", "not located"):
        x = nid[nid.position_note == note]
        if len(x):
            L.append(f"| {note} | {len(x)} | {x.position_precision_km.median():.1f} | "
                     f"{x.position_precision_km.min():.1f} | {x.position_precision_km.max():.1f} |\n")
    L.append(f"\n- {rstats['accepted_pairs']} accepted links join {rstats['list_rows_linked']} list rows to "
             f"{rstats['records_linked']} of our records. Readers marked a candidate same for "
             f"{rstats['list_rows_with_same_pair']} list rows; with no accepted band those rows give no card fact, "
             "but they are kept out of the not-in-dataset lane.\n"
             if not rstats["accepted_bands"] else
             f"\n- {rstats['accepted_pairs']} accepted links join {rstats['list_rows_linked']} list rows to "
             f"{rstats['records_linked']} of our records; all are reader-marked same pairs from accepted bands.\n")
    lr = rstats["low_read_by_operator"]
    pub = lr.get("public", {})
    prv = lr.get("private", {})
    ul = rstats["unread_low_by_operator"]
    L.append(f"- Of the {len(nid)} not-in-dataset rows, {rstats['not_in_dataset_no_candidate']} had no candidate "
             f"at all and {rstats['not_in_dataset_unread_candidate']} still have unread low-band candidates "
             f"(score under {MIDDLE_MIN:.2f}). The low-band probe read every low pair whose record is public: "
             f"{pub.get('same', 0)} same, {pub.get('different', 0)} different, {pub.get('unsure', 0)} unsure, so "
             "renamed or upgraded government units do hide in the low band. It also read "
             f"{sum(prv.values())} private low pairs, all {prv.get('same', 0)} same. The unread low pairs are "
             f"{ul.get('private', 0)} private records and {ul.get('missing', 0)} with no operator type; "
             f"{rstats['not_in_dataset_unread_nonprivate_candidate']} not-in-dataset rows have an unread candidate "
             "that is not marked private. Recall is not measured, so \"not in this dataset\" means no candidate "
             "was confirmed, not that a match was ruled out.\n")
    L.append("- Every not-in-dataset row is placed at its district centre only. The lane must say \"position "
             "approximate (district centre)\", and its distance carries the district's radius as error.\n")
    return "".join(L)


def report(lst, lstats, links, kstats, sheet, sampling, src_meta, rsec: str = "") -> str:
    L = ["# MoHFW trauma care facilities: parsed, located, linked\n\n" if rsec else
         "# MoHFW trauma care facilities: parsed, located, candidate links\n\n",
         f"Built by `{SCRIPT}` on {utcnow()}. Source: {SOURCE_NAME} and funds allocated (MoHFW, file dated "
         f"8 Feb 2023), {OFFICIAL_URL}, fetched from the Internet Archive capture of 15 Dec 2023 "
         f"({ARCHIVE_URL}) on {src_meta['retrieved']}, sha256 `{src_meta['sha256']}`. Terms and provenance: "
         f"`data/external/{SOURCE_ID}/SOURCE.md`. "
         + ("Reader verdicts are applied below: a band is accepted only if its one-sided 95% lower bound of "
            "precision is at least 0.90, and only reader-marked same pairs in accepted bands are links."
            if rsec else
            "No link in this file is final: readers label the check sheet, and a band is accepted only if its "
            "one-sided 95% lower bound of precision is at least 0.90.")
         + "\n\n## Findings\n\n"]
    pn = lstats["position_note"]
    L.append(f"- **List:** {lstats['rows']} trauma care facilities in {lstats['states']} states and union "
             f"territories (S. No. 1 to {lstats['rows']}, contiguous: {lstats['sno_contiguous_1_to_n']}). Every row "
             "is a public hospital named with its town. The list gives no address, PIN, coordinates, level "
             "(I, II or III), date per facility or operating status, so every row has status `undated`.\n")
    L.append(f"- **Located:** {pn.get('district centre', 0)} to a district centre, {pn.get('not located', 0)} not "
             "located. No row can be placed more finely than its district.\n")
    if not links.empty:
        b = kstats["bands"]
        L.append(f"- **Candidates:** {kstats['candidates']} pairs for {kstats['list_rows_with_candidate']} of "
                 f"{kstats['list_rows_blockable']} located list rows (auto {b.get('auto', 0)}, middle "
                 f"{b.get('middle', 0)}, low {b.get('low', 0)}). {kstats['list_rows_with_auto']} list rows have a "
                 f"provisional auto pair; {kstats['list_rows_with_auto_or_middle']} have an auto or middle pair.\n")
        L.append(f"- **Check sheet:** {len(sheet)} pairs for readers (auto {sampling['auto_in_sheet']}, middle "
                 f"{sampling['middle_in_sheet']}, low probe {sampling['low_probe_in_sheet']}); "
                 f"{'sampled per band: ' + sampling.get('sampling_rule', '') if sampling['sampled'] else 'no sampling needed'}. "
                 f"Low probe: {sampling.get('low_probe_rule', '')}.\n")
        na, nam = int(b.get("auto", 0)), int(b.get("auto", 0)) + int(b.get("middle", 0))

        def verdict(n):
            w = max_wrong(n)
            return (f"passes only with at most {w} wrong" if isinstance(w, int)
                    else "cannot pass even if every pair is correct")
        L.append(f"- **Acceptance arithmetic** (exact binomial, one-sided 95%): with all read pairs correct the lower "
                 f"bound reaches 0.90 at 29 pairs; at 46 pairs one wrong pair is allowed. The auto band alone "
                 f"({na} pairs) {verdict(na)}. Auto and middle pooled ({nam} pairs) {verdict(nam)}"
                 + (". See the reader verdicts below.\n" if rsec else
                    ". So this list can produce card lines only if readers find the pooled band clean, or if its "
                    "links are pooled with another source's under one declared rule.\n"))
        L.append("- Our data is 88% private, and the list is all public hospitals, so few list rows can have a "
                 "match at all. A list row with no candidate is not evidence that the hospital is missing from our "
                 "data (recall is not measured), nor that it offers trauma care today.\n")
    L.append("\n## Method\n\n")
    L.append("1. Download: the official PDF URL on main.mohfw.gov.in no longer resolves (DNS) and the new site "
             "returns 404 at the same path, so the Internet Archive's capture of that exact URL was used; its "
             "recorded length (421,544 bytes) and Last-Modified (8 Feb 2023) are those of the original. The new "
             "site's robots.txt allows all paths. No form, login or search was used.\n")
    L.append("2. Parse (pdfplumber tables): one row per S. No.; the state cell is merged over its rows and carried "
             "forward, also across pages. Citation: page and S. No.\n")
    L.append("3. Locate (`vf.locate.resolve`, within the listed state): the town or district in the unit name, comma "
             "parts last first, then single words; a few names read by hand (`HAND_PLACE`). Always reported as the "
             "district centre with that district's precision radius.\n")
    L.append(f"4. Our pool: {kstats['pool']} of {kstats['our_records']} records; "
             f"{kstats['rejected_ayush_dental_vet']} AYUSH, dental or veterinary records rejected by type or name. "
             "Operator type is not used to filter (it is missing or wrong too often), so readers see private "
             "namesakes too.\n")
    L.append(f"5. Blocking: same PIN-directory district (our record's PIN through `silver/pin_crosswalk`) plus a rare "
             f"shared name token: a token in at most {RARE_MAX_DF} of our names, at least 3 letters, not a unit-type "
             "word (soft match at rapidfuzz ratio 85 for tokens of 4 letters or more). **Deviation from the NCG "
             "linker:** the district's own word may be the rare token, because this list names units by their town "
             "(\"District Hospital, Srikakulam\"); "
             f"{kstats.get('pairs_blocked_only_on_place_word', 0)} pairs were blocked only on such a place word. "
             "Same-PIN and 2 km blocks are not possible: the list has neither.\n")
    L.append(f"6. Score: IDF-weighted soft Jaccard on name tokens (abbreviations expanded as in `vf.public_list`, plus "
             f"`Hos.`, `Taluq`, `SD`), bounded to [0, 1], after dropping the pair's district, state and same-place "
             f"city words; IDF over {kstats.get('idf_docs', 'n/a')} names (ours plus the list's). So the score "
             "mostly compares unit types (\"district hospital\" against \"district headquarters hospital\") and "
             "any other distinctive words.\n")
    L.append(f"7. Bands (provisional, for readers): auto at score {AUTO_MIN} or more and the one-to-one winner; "
             f"middle at {MIDDLE_MIN} or more, or a high score that lost the one-to-one contest; low below. "
             "Records in one WP11 duplicate cluster may share a list row.\n")
    if not links.empty:
        L.append("\n## Score distribution\n\n| band | pairs | list rows | min score | max score |\n|---|---|---|---|---|\n")
        for bnd in ("auto", "middle", "low"):
            x = links[links.band == bnd]
            if len(x):
                L.append(f"| {bnd} | {len(x)} | {x.list_uid.nunique()} | {x.score.min():.2f} | {x.score.max():.2f} |\n")
        L.append("\n## Fifteen highest-scoring pairs (for orientation, not decisions)\n\n"
                 "| list name | list district | our name | our city | score | band | km apart |\n"
                 "|---|---|---|---|---|---|---|\n")
        for x in links.head(15).itertuples():
            L.append(f"| {x.list_name} | {x.list_district} | {x.our_name} | {x.our_city} | {x.score:.2f} | "
                     f"{x.band} | {x.km_apart} |\n")
    L.append(rsec)
    if lstats["not_located"]:
        L.append("\n## Not located\n\n")
        for r in lstats["not_located"]:
            L.append(f"- {r['list_uid']}: {r['name']} ({r['raw_state']})\n")
    L.append("\n## Limits\n\n")
    L.append("- The list records central funds allocated to upgrade a hospital's trauma care. It does not say the "
             "unit was built, is staffed, or works today, and operating status is not checked here. The card fact "
             "can say only that the hospital is on this MoHFW list of funded trauma care facilities, dated "
             "8 Feb 2023.\n")
    L.append("- Levels I to III are not in this file. The amount allocated tracks the level only loosely and is "
             "not used as a level.\n")
    L.append("- Positions are district centres, so `km_apart` mixes our record's error with the district's radius, "
             "and the nearby lane must say \"position approximate (district centre)\".\n")
    L.append("- MoHFW's copyright policy says its website content \"may not be reproduced partially or fully, "
             "without due permission\". Showing listed names on screen needs Hruday's decision.\n")
    return "".join(L)


def main() -> int:
    ext = paths.external_dir() / SOURCE_ID
    src = ext / PDF_FILE
    retrieved = (ext / "retrieved_utc.txt").read_text().strip()
    src_meta = {"retrieved": retrieved, "sha256": sha256(src), "file": f"external/{SOURCE_ID}/{PDF_FILE}"}
    silver, gold, labels = paths.silver_dir(), paths.gold_dir(), paths.labels_dir()
    out_list = silver / f"ext_{SOURCE_ID}.parquet"
    out_links = silver / f"ext_links_{SOURCE_ID}.parquet"
    out_sheet = labels / f"ext_link_check_{SOURCE_ID}.csv"
    read_path = labels / f"ext_link_read_{SOURCE_ID}.csv"
    rpath = paths.code_root() / "reports" / f"ext_{SOURCE_ID}.md"

    lst, lstats = build_list(src, retrieved, src_meta["sha256"])
    fac = pd.read_parquet(silver / "facilities.parquet")
    pinx = pd.read_parquet(silver / "pin_crosswalk.parquet", columns=["pin", "state_pin", "district_pin"])
    dups = pd.read_parquet(gold / "facility_duplicates.parquet")
    links, kstats = link(lst, fac, pinx, dups)
    sheet, sampling = check_sheet(links) if not links.empty else (pd.DataFrame(), {})

    readers = read_path.exists()
    rstats, rsec = {}, ""
    if readers:
        # The read sheet is frozen: verify the rebuild reproduces it, never overwrite it.
        frozen = pd.read_csv(out_sheet, dtype=str)
        if set(frozen.item_id) != set(sheet.item_id):
            raise SystemExit("rebuilt check sheet differs from the one readers judged; inputs changed")
        sampling = json.loads((out_sheet.parent / (out_sheet.name + ".meta.json")).read_text()).get(
            "sampling", sampling)
        read = pd.read_csv(read_path, dtype=str, keep_default_na=False)
        links, lst, rstats = apply_readers(links, lst, read, set(frozen.item_id))
        rsec = reader_section(rstats, lst)

    write_atomic(out_list, lambda p: lst.to_parquet(p, index=False))
    write_meta(out_list, len(lst), {
        "grain": "one row per row of the MoHFW list of 196 trauma care facilities",
        "columns": list(lst.columns), "source_url": OFFICIAL_URL, "retrieved_from": ARCHIVE_URL,
        "source_file": src_meta["file"], "source_sha256": src_meta["sha256"], "retrieved_utc": retrieved,
        "list_date": LIST_DATE, "status_rule": "every row 'undated': funding list, no per-facility date or status",
        "link_status_rule": ("linked: an accepted link; in_dataset_unaccepted: a reader marked a candidate same "
                             "but its band is not accepted; unsure: a reader marked a candidate unsure and none "
                             "same; not_in_dataset: none of these (the only rows for the outside-nearby lane)")
        if readers else None,
        "terms": "MoHFW copyright policy: no reproduction without permission; cite and acknowledge (SOURCE.md)",
        "use": "D016/D055: dated, cited card fact and outside-nearby lane only; never an input to grades, "
               "quadrants or ranking", "stats": lstats, "reader_stats": rstats or None})

    write_atomic(out_links, lambda p: links.to_parquet(p, index=False))
    write_meta(out_links, len(links), {
        "grain": ("one row per candidate pair (list_uid x our facility_id); reader verdict where read; "
                  "accepted marks the final links") if readers else
                 "one row per candidate pair (list_uid x our facility_id); provisional band, no decision",
        "columns": list(links.columns),
        "inputs": [f"silver/ext_{SOURCE_ID}.parquet", "silver/facilities.parquet", "silver/pin_crosswalk.parquet",
                   "gold/facility_duplicates.parquet"] + ([f"labels/{read_path.name}"] if readers else []),
        "bands": {"auto_min": AUTO_MIN, "middle_min": MIDDLE_MIN, "soft_min": SOFT_MIN},
        "acceptance_rule": "a band is accepted only if its one-sided 95% Clopper-Pearson lower bound of "
                           "precision, same / (same + different) from independent readers, is at least 0.90; "
                           "inside an accepted band only reader-marked same pairs are accepted (D048 item 5, D055)",
        "stats": kstats, "reader_stats": rstats or None})

    if not readers:
        write_atomic(out_sheet, lambda p: sheet.to_csv(p, index=False))
        write_meta(out_sheet, len(sheet), {
            "grain": f"one row per candidate pair given to readers; readers write labels/{read_path.name}",
            "sampling": sampling, "seed": SEED, "order": "shuffled with the seed, so band is not read from order"})

    body = report(lst, lstats, links, kstats, sheet, sampling, src_meta, rsec)
    write_atomic(rpath, lambda p: p.write_text(body))

    print(f"MoHFW trauma list: {lstats['rows']} rows, {lstats['states']} states, contiguous "
          f"{lstats['sno_contiguous_1_to_n']}; located {lstats['position_note']}")
    print(f"pool {kstats.get('pool')} (rejected {kstats.get('rejected_ayush_dental_vet')}); "
          f"candidates {kstats.get('candidates', 0)}; bands {kstats.get('bands', {})}; "
          f"list rows with auto {kstats.get('list_rows_with_auto')}, auto or middle "
          f"{kstats.get('list_rows_with_auto_or_middle')}")
    if readers:
        for b in rstats["bands"]:
            print(f"band {b['band']}: same {b['same']} different {b['different']} unsure {b['unsure']} "
                  f"lower95 {b['lower_95']:.3f} accepted {b['accepted']}")
        print(f"accepted links {rstats['accepted_pairs']} for {rstats['list_rows_linked']} list rows; "
              f"link status {rstats['link_status']}; not-in-dataset position {rstats['not_in_dataset_position']}")
    else:
        print(f"check sheet {len(sheet)} rows {sampling}")
    for p in (out_list, out_links) + (() if readers else (out_sheet,)) + (rpath,):
        print(f"wrote {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
