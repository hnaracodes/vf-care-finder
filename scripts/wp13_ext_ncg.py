"""WP13 / D055: National Cancer Grid member cancer centres, parsed, located and
linked (candidates only) to our facility records.

Reads:
  external/ncg/members_fragment_D83F0EAA-2B81-4E98-92B7-67AE3DA693FD.html
      the "Cancer Centres / Units" member list, as served to the public members
      page (https://www.ncgindia.org/hospitals-and-institutions/members) by its
      own GET request; downloaded once, read here as data, never executed
  silver/facilities.parquet, silver/pin_crosswalk.parquet,
  gold/facility_duplicates.parquet (WP11 duplicate pairs), silver/places.parquet
  (through vf.locate.resolve)

Writes (atomic, with .meta.json sidecars):
  silver/ext_ncg.parquet          one row per Indian member cancer centre on the list
  silver/ext_links_ncg.parquet    every candidate pair (list row x our record), score and band
  labels/ext_link_check_ncg.csv   pairs for independent readers (no decisions here)
  reports/ext_ncg.md              counts, method, limits

Linkage (reports/score_methodology_research.md, strict rules):
  blocking: same PIN; or same district plus a rare shared name token. The
  "within 2 km" block needs list coordinates; this list has none (its positions
  are PIN-area or district centres), so that block is not used.
  score: IDF-weighted soft Jaccard on name tokens, bounded to [0, 1].
  rejects: AYUSH, dental and veterinary records never enter the pool.
  one-to-one: a record links to at most one list row and a list row to at most
  one record, except records in the same WP11 duplicate cluster, which may share
  a list row. A pair that loses that contest drops to the middle band.
  bands here are provisional sorting for readers; a band is accepted only if
  its one-sided 95% lower bound of precision, from the readers' labels, is at
  least 0.90. No link is final in this script.

Usage:
  .venv/bin/python -I scripts/wp13_ext_ncg.py
"""
from __future__ import annotations

import hashlib
import html
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
from rapidfuzz import fuzz
from scipy.stats import beta as beta_dist

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from vf import locate, paths  # noqa: E402
from vf.public_list import GENERIC, normalise_name  # noqa: E402

SCRIPT = "scripts/wp13_ext_ncg.py"
SOURCE_ID = "ncg"
SOURCE_NAME = "National Cancer Grid member list"
PUBLISHER = "National Cancer Grid (coordinated by Tata Memorial Centre, Mumbai)"
PAGE_URL = "https://www.ncgindia.org/hospitals-and-institutions/members"
TYPE_ID = "D83F0EAA-2B81-4E98-92B7-67AE3DA693FD"  # "Cancer Centres / Units"
FRAGMENT_URL = f"https://www.ncgindia.org/hospitalsandinstitutions/_index?type_id={TYPE_ID}&country_id=&state_id="
FRAGMENT_FILE = f"members_fragment_{TYPE_ID}.html"
WHAT = "member cancer centre of the National Cancer Grid"
LINK_VERSION = "ncg_v1"

#: Provisional bands for readers (not acceptance): score >= AUTO_MIN and the
#: one-to-one winner is "auto"; MIDDLE_MIN <= score, or a high score that lost the
#: one-to-one contest, is "middle"; below is "low".
AUTO_MIN = 0.70
MIDDLE_MIN = 0.30
#: Soft token match: rapidfuzz ratio at least this (0 to 100).
SOFT_MIN = 85.0
#: A name token is rare when at most this many of our records' names carry it.
RARE_MAX_DF = 10
CHECK_CAP = 150
#: Up to this many of the best low-band pairs go to readers too, if under the cap.
LOW_PROBE = 15
SEED = 20261122
#: A band is accepted when its one-sided 95% lower bound of precision is at least this.
ACCEPT_LB = 0.90

STOP = frozenset("""and of the a an at in for pvt ltd limited private unit by trust society
foundation india co""".split())
#: AYUSH, dental and veterinary records are rejected for a hospital list.
REJECT_NAME = re.compile(
    r"\b(?:ayurved\w*|ayush|homoeo\w*|homeo\w*|unani|siddha|naturopath\w*|yoga|panchakarma|"
    r"dental|dentist\w*|dentofacial|orthodont\w*|endodont\w*|veterinar\w*|vet|animal|pets|"
    r"pet (?:clinic|hospital|care|multispeciality))\b", re.I)
#: A name that also names a non-dental, non-AYUSH service keeps the record
#: ("Gupta Maternity Hospital & Dental Clinic"). PET CT centres are not pet clinics.
KEEP_NAME = re.compile(
    r"\b(?:maternity|children|child|eye|ent|multi\w*|general|nursing home|cancer|onco\w*|heart|"
    r"orthopaedic|orthopedic|surgical|medical cent\w*|medical college)\b", re.I)
REJECT_TYPES = frozenset({"dentist"})
_PIN = re.compile(r"(?<!\d)([1-8]\d{2})\s?(\d{3})(?!\d)")
_TAGS = re.compile(r"<[^>]+>")


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


def clean(s: str) -> str:
    s = html.unescape(_TAGS.sub(" ", s or "")).replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------

_HEAD = re.compile(r'<th[^>]*state-id="([^"]*)"[^>]*>\s*<div[^>]*>(.*?)</div>', re.S)
_ROW = re.compile(r'<tr>\s*<td class="pe-lg-5">(.*?)</td>\s*<td class="w-50">(.*?)</td>\s*'
                  r'<td class="w-15[^"]*">(.*?)</td>', re.S)
INDIAN_SECTIONS_END = "Bangladesh"  # foreign sections follow the Indian states and "National"


def parse_fragment(text: str) -> pd.DataFrame:
    events = [(m.start(), "head", m) for m in _HEAD.finditer(text)] + \
             [(m.start(), "row", m) for m in _ROW.finditer(text)]
    events.sort(key=lambda e: e[0])
    rows, section, section_id, n_in_section, section_no = [], None, None, 0, 0
    for _, kind, m in events:
        if kind == "head":
            section, section_id = clean(m.group(2)), m.group(1)
            section_no += 1
            n_in_section = 0
            continue
        n_in_section += 1
        links = re.findall(r'href="([^"]+)"', m.group(3))
        rows.append({"raw_section": section, "raw_section_id": section_id, "section_no": section_no,
                     "row_in_section": n_in_section, "raw_name": clean(m.group(1)),
                     "raw_address": clean(m.group(2)), "raw_website": links[0] if links else None,
                     "raw_links": links})
    df = pd.DataFrame(rows)
    df["list_row"] = np.arange(1, len(df) + 1)
    return df


FOREIGN = {"Bangladesh", "Bhutan", "Indonesia", "Kenya", "Malaysia", "Nepal", "Nigeria", "Rwanda",
           "South Africa", "USA", "Zambia"}


def pins_in(addr: str) -> list[str]:
    return [a + b for a, b in _PIN.findall(addr or "")]


def norm_state(s) -> str:
    t = re.sub(r"[^A-Z ]", " ", str(s or "").upper().replace("&", " AND "))
    t = re.sub(r"\s+", " ", t).strip()
    return locate.STATE_ALIASES.get(t, t) or t


def locate_row(r) -> dict:
    """PIN first (must resolve and sit in the listed state, unless the list's
    section is 'National'); else the district named in the address."""
    st = norm_state(r.raw_section)
    national = r.raw_section.strip().lower() == "national"
    notes = []
    for p in reversed(pins_in(r.raw_address)):
        res = locate.resolve(p)
        if res.status != "ok" or res.kind != "pin":
            notes.append(f"PIN {p} not in the India Post directory")
            continue
        if not national and res.state and norm_state(res.state) != st:
            notes.append(f"PIN {p} is in {res.state}, list section is {r.raw_section}")
            continue
        return {"pin": p, "district": res.district, "state_pin": res.state, "lat": res.lat, "lon": res.lon,
                "position_precision_km": res.precision_km, "position_note": "PIN area",
                "position_detail": res.how, "locate_notes": "; ".join(notes) or None}
    # district centre: comma parts of the address (last first; a part may name a
    # district, a district-named city or a post office), then single address words
    # and name words (districts and cities only); always reported as the district centre
    parts = [x.strip(" .-") for x in re.split(r"[,;\n.]|\s[-\u2013]\s", r.raw_address) if x.strip(" .-")]
    words = [w for w in re.findall(r"[A-Za-z]{4,}", r.raw_address)][::-1] + re.findall(r"[A-Za-z]{4,}", r.raw_name)[::-1]
    tried = []
    for q, kinds in [(x, ("district", "city", "post_office")) for x in reversed(parts)] + \
                    [(w, ("district", "city")) for w in words]:
        q = re.sub(r"(?i)\b(dist(rict)?|distt|ta|tal|taluka|po|p\s?o|india|pin|-)\b\.?", " ", q)
        q = re.sub(r"[\d#()']+", " ", q).strip(" .-:")
        if len(q) < 3 or norm_state(q) == st or q.lower() in tried:
            continue
        tried.append(q.lower())
        res = locate.resolve(q, state=None if national else st.title())
        if res.status == "ok" and res.kind in kinds and res.district:
            d = locate.resolve(f"{res.district} district", state=res.state)
            if not (d.status == "ok" and d.kind == "district"):
                continue
            return {"pin": None, "district": d.district, "state_pin": d.state, "lat": d.lat,
                    "lon": d.lon, "position_precision_km": d.precision_km, "position_note": "district centre",
                    "position_detail": f"address or name text '{q}' ({res.kind} match): {d.how}",
                    "locate_notes": "; ".join(notes) or None}
    notes.append("no PIN or district resolved" + (f" (tried: {', '.join(tried)})" if tried else ""))
    return {"pin": None, "district": None, "state_pin": None if national else st, "lat": None, "lon": None,
            "position_precision_km": None, "position_note": None, "position_detail": None,
            "locate_notes": "; ".join(notes)}


def build_list(src: Path, retrieved: str, src_sha: str) -> tuple[pd.DataFrame, dict]:
    raw = parse_fragment(src.read_text(encoding="utf-8"))
    n_all = len(raw)
    foreign = raw[raw.raw_section.isin(FOREIGN)]
    df = raw[~raw.raw_section.isin(FOREIGN)].copy()
    loc = pd.DataFrame([locate_row(r) for r in df.itertuples()], index=df.index)
    df = pd.concat([df, loc], axis=1)
    df["list_uid"] = df.apply(lambda r: "ncg-" + hashlib.sha1(
        f"{r.raw_name}|{r.raw_address}".encode()).hexdigest()[:10], axis=1)
    df["name"] = df["raw_name"]
    df["address"] = df["raw_address"]
    df["state"] = df.apply(lambda r: locate.nice(r.state_pin) if r.state_pin else r.raw_section, axis=1)
    df["district"] = df["district"].map(lambda d: locate.nice(d) if d else None)
    df["what"] = WHAT
    df["status"] = "undated"
    df["list_date"] = None
    df["status_detail"] = (f"The list carries no date or validity; it is the member list as served on "
                           f"{retrieved[:10]}. Membership is not an accreditation of a service.")
    df["retrieved_utc"] = retrieved
    df["source_url"] = PAGE_URL
    df["citation"] = df.apply(lambda r: (f"{SOURCE_NAME}, Cancer Centres / Units, section '{r.raw_section}', "
                                         f"row {r.row_in_section} (list row {r.list_row}); {PAGE_URL}, "
                                         f"retrieved {retrieved[:10]}"), axis=1)
    df["source_file"] = f"external/ncg/{FRAGMENT_FILE}"
    df["source_sha256"] = src_sha
    cols = ["list_uid", "name", "address", "pin", "district", "state", "lat", "lon", "position_precision_km",
            "position_note", "position_detail", "locate_notes", "what", "status", "list_date", "status_detail",
            "citation", "source_url", "retrieved_utc", "list_row", "raw_section", "row_in_section",
            "raw_section_id", "raw_name", "raw_address", "raw_website", "raw_links", "state_pin", "source_file",
            "source_sha256"]
    df = df[cols].reset_index(drop=True)
    stats = {"list_rows_all_countries": n_all, "foreign_rows_dropped": len(foreign),
             "foreign_sections": sorted(foreign.raw_section.unique()), "indian_rows": len(df),
             "duplicate_list_uid": int(df.list_uid.duplicated().sum()),
             "position_note": df.position_note.fillna("not located").value_counts().to_dict()}
    return df, stats


# ---------------------------------------------------------------------------
# Link
# ---------------------------------------------------------------------------

def tokens(name) -> list[str]:
    return [w for w in normalise_name(name).split() if w not in STOP and len(w) > 1]


def place_tokens(*names) -> set:
    """Words of the pair's district, state and city names, plus 'india': dropped
    from both names before scoring, so "Jehangir Hospital, Pune, Maharashtra,
    India" scores against "Jehangir Hospital" on the hospital's own words."""
    out = {"india"}
    for n in names:
        if n is not None and not (isinstance(n, float) and math.isnan(n)):
            out |= set(normalise_name(n).split())
    out |= {PLACE_ALIASES[t] for t in list(out) if t in PLACE_ALIASES}
    return out - set(GENERIC)


#: Old and new city names, so the alias is dropped too.
PLACE_ALIASES = {"thiruvananthapuram": "trivandrum", "trivandrum": "thiruvananthapuram", "bengaluru": "bangalore",
                 "bangalore": "bengaluru", "kochi": "cochin", "cochin": "kochi", "ernakulam": "cochin",
                 "gurugram": "gurgaon", "gurgaon": "gurugram", "mumbai": "bombay", "chennai": "madras",
                 "kolkata": "calcutta", "prayagraj": "allahabad", "allahabad": "prayagraj",
                 "visakhapatnam": "vizag", "visakhapatanam": "vizag", "kozhikode": "calicut", "calicut": "kozhikode",
                 "mysuru": "mysore", "mysore": "mysuru", "vadodara": "baroda", "puducherry": "pondicherry"}


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
    """IDF-weighted soft Jaccard on token sets, in [0, 1]. Tokens pair greedily by
    rapidfuzz ratio (at least SOFT_MIN); a pair adds sim x mean(idf) to the overlap.
    score = overlap / (W_a + W_b - overlap)."""
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
    """facility_id -> cluster label (union-find over WP11 duplicate pairs)."""
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
    f["norm"] = f["name"].map(normalise_name)
    nm = f["name"].fillna("")
    rej = f["facility_type"].isin(REJECT_TYPES) | (nm.str.contains(REJECT_NAME) & ~nm.str.contains(KEEP_NAME))
    pool = f[~rej].copy()
    stats = {"our_records": len(f), "rejected_ayush_dental_vet": int(rej.sum()), "pool": len(pool)}

    # IDF over our records' names plus the list names (document = one name)
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

    # our districts per PIN (every district the PIN's offices fall in)
    pd_map = {str(p): set(zip(g.state_pin, g.district_pin)) for p, g in pinx.groupby("pin")}
    pool["districts"] = pool["pin"].map(lambda p: pd_map.get(str(p), set()) if pd.notna(p) else set())
    pool["toks"] = pool["name"].map(tokens)
    by_pin = pool.groupby("pin").groups
    by_dist = {}
    for i, ds in zip(pool.index, pool["districts"]):
        for d in ds:
            by_dist.setdefault(d, []).append(i)
    clusters = duplicate_clusters(dups)

    rows = []
    for r in lst.itertuples():
        ltoks = tokens(r.name)
        dist_tokens = set(normalise_name(r.district or "").split())
        rare = {t for t in ltoks if df_ours.get(t, 0) <= RARE_MAX_DF and len(t) >= 4 and t not in GENERIC
                and t not in dist_tokens and not t.isdigit()}
        cand = {}
        if r.pin and r.pin in by_pin:
            for i in by_pin[r.pin]:
                cand.setdefault(i, set()).add("same_pin")
        if r.district and r.state_pin:
            keys = [k for k in by_dist if k[0] == r.state_pin and k[1].upper() == r.district.upper()]
            for k in keys:
                for i in by_dist[k]:
                    shared = rare & set(pool.at[i, "toks"])
                    if not shared:
                        # soft: a rare list token close to one of ours
                        shared = {t for t in rare for u in pool.at[i, "toks"]
                                  if len(u) >= 4 and fuzz.ratio(t, u) >= SOFT_MIN}
                    if shared:
                        cand.setdefault(i, set()).add("same_district_rare_token:" + ",".join(sorted(shared)))
        for i, blocks in cand.items():
            o = pool.loc[i]
            # our city's words are dropped only when the city is the list row's district
            # (a different city in our name is a conflict and must count against the pair)
            city = o["city_raw"] if same_place(o["city_raw"], r.district) else None
            place = place_tokens(r.district, r.state, r.state_pin, city, o["state_raw"])
            score, matched = soft_jaccard(strip_place(ltoks, place), strip_place(o["toks"], place),
                                          idf, default_idf)
            km = haversine_km(r.lat, r.lon, o["lat"], o["lon"]) if bool(o["coord_valid"]) else None
            rows.append({
                "list_uid": r.list_uid, "facility_id": o["facility_id"],
                "dup_cluster": clusters.get(o["facility_id"], o["facility_id"]),
                "block": " | ".join(sorted(blocks)), "same_pin": "same_pin" in blocks,
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

    # one-to-one, greedy by score; records in one WP11 duplicate cluster may share a list row
    links = links.sort_values(["score", "same_pin"], ascending=[False, False]).reset_index(drop=True)
    links["rank_in_list_row"] = links.groupby("list_uid")["score"].rank(method="first", ascending=False).astype(int)
    taken_list, taken_rec = {}, {}
    winner = []
    for x in links.itertuples():
        lc = taken_list.get(x.list_uid)  # cluster already holding this list row
        rc = taken_rec.get(x.dup_cluster)  # list row already holding this cluster
        ok = (lc is None or lc == x.dup_cluster) and (rc is None or rc == x.list_uid)
        if ok and x.score >= AUTO_MIN:
            taken_list[x.list_uid] = x.dup_cluster
            taken_rec[x.dup_cluster] = x.list_uid
        winner.append(ok)
    links["one_to_one_ok"] = winner
    links["band"] = np.where((links.score >= AUTO_MIN) & links.one_to_one_ok, "auto",
                             np.where(links.score >= MIDDLE_MIN, "middle",
                                      np.where(links.score >= AUTO_MIN, "middle", "low")))
    links.loc[(links.score >= AUTO_MIN) & ~links.one_to_one_ok, "band_note"] = "lost one-to-one contest"
    links["link_version"] = LINK_VERSION
    stats.update({"candidates": len(links), "bands": links.band.value_counts().to_dict(),
                  "list_rows_with_candidate": int(links.list_uid.nunique()),
                  "list_rows_with_auto": int(links[links.band == "auto"].list_uid.nunique()),
                  "blocks": {"same_pin": int(links.same_pin.sum()),
                             "district_rare_token_only": int((~links.same_pin).sum())},
                  "idf_docs": N, "rare_max_df": RARE_MAX_DF})
    return links, stats


def check_sheet(links: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Pairs for readers: every auto and middle pair (sampled per band if over the
    cap), then the best low pairs as a boundary probe if room remains. Rows are
    shuffled so the band is not read from the order."""
    rng = np.random.default_rng(SEED)
    a, m = links[links.band == "auto"], links[links.band == "middle"]
    sampling = {"cap": CHECK_CAP, "auto_total": len(a), "middle_total": len(m)}
    if len(a) + len(m) > CHECK_CAP:
        share = len(a) / (len(a) + len(m))
        na = min(len(a), max(int(round(CHECK_CAP * share)), min(len(a), 60)))
        nm = CHECK_CAP - na
        a = a.sample(n=na, random_state=SEED) if len(a) > na else a
        m = m.sample(n=min(nm, len(m)), random_state=SEED)
        sampling["sampled"] = True
    else:
        sampling["sampled"] = False
    room = CHECK_CAP - len(a) - len(m)
    lo = links[links.band == "low"].sort_values("score", ascending=False).head(max(0, min(LOW_PROBE, room)))
    sampling.update({"auto_in_sheet": len(a), "middle_in_sheet": len(m), "low_probe_in_sheet": len(lo),
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
    """Clopper-Pearson one-sided: the lower bound is at least p0 when
    P(X >= n - wrong | p = p0) <= alpha."""
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
    """Join reader verdicts to the candidate pairs, accept bands whose one-sided
    95% Clopper-Pearson lower bound of precision (same / (same + different)) is
    at least ACCEPT_LB, keep only reader-marked same pairs inside accepted bands,
    and give every list row a link status."""
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
        lb = cp_lower(s, n)
        # Sensitivity: unsure counted as wrong.
        lb_strict = cp_lower(s, s + d + u)
        bands.append({"band": bnd, "pairs": len(x), "read": s + d + u, "same": s, "different": d,
                      "unsure": u, "precision": (s / n) if n else float("nan"), "lower_95": lb,
                      "lower_95_unsure_as_wrong": lb_strict, "accepted": bool(n > 0 and lb >= ACCEPT_LB)})
    btab = pd.DataFrame(bands)
    ok_bands = set(btab[btab.accepted].band)
    links["accepted"] = links.band.isin(ok_bands) & (links.reader_verdict == "same")

    acc = links[links.accepted]
    # One-to-one check on accepted links (WP11 duplicate clusters may share a list row).
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
    lst["best_unread_score"] = lst.list_uid.map(best_unread)
    lst["linked_facility_ids"] = lst.list_uid.map(acc.groupby("list_uid").facility_id.agg(list)).apply(
        lambda v: v if isinstance(v, list) else [])
    lst["link_version"] = LINK_VERSION

    lowest_same_low = links[(links.band == "low") & (links.reader_verdict == "same")].score.min()
    nid = lst[lst.link_status == "not_in_dataset"]
    stats = {
        "bands": btab.to_dict(orient="records"), "accept_lb": ACCEPT_LB, "accepted_bands": sorted(ok_bands),
        "accepted_pairs": int(len(acc)), "list_rows_linked": int(len(linked)),
        "records_linked": int(acc.facility_id.nunique()),
        "link_status": lst.link_status.value_counts().to_dict(),
        "not_in_dataset_position": nid.position_note.value_counts().to_dict(),
        "not_in_dataset_precision_km": nid.position_precision_km.describe().round(2).to_dict() if len(nid) else {},
        "not_in_dataset_no_candidate": int((nid.n_candidates == 0).sum()),
        "not_in_dataset_unread_candidate": int(nid.best_unread_score.notna().sum()),
        "lowest_read_same_score_in_low": None if pd.isna(lowest_same_low) else float(lowest_same_low),
        "not_in_dataset_unread_at_or_above_that": int((nid.best_unread_score >= lowest_same_low).sum())
        if not pd.isna(lowest_same_low) else 0,
    }
    links = links.drop(columns=["item_id"])
    front = ["list_uid", "facility_id", "score", "band", "reader_verdict", "accepted"]
    links = links[front + [c for c in links.columns if c not in front]]
    return links, lst, stats


def reader_section(rstats: dict, lst: pd.DataFrame, links: pd.DataFrame) -> str:
    L = ["\n## Reader verdicts and acceptance\n\n"
         f"Readers judged every pair in `data/labels/ext_link_check_ncg.csv` "
         f"(`data/labels/ext_link_read_ncg.csv`). Precision is same / (same + different); unsure pairs are left "
         f"out of it, and a second column counts them as wrong. A band is accepted only if the one-sided 95% "
         f"Clopper-Pearson lower bound is at least {ACCEPT_LB:.2f}. Inside an accepted band only pairs the "
         "reader marked same are kept.\n\n"
         "| band | pairs | read | same | different | unsure | precision | lower 95% | lower 95%, unsure as wrong "
         "| accepted |\n|---|---|---|---|---|---|---|---|---|---|\n"]
    for b in rstats["bands"]:
        prec = "n/a" if pd.isna(b["precision"]) else f"{b['precision']:.3f}"
        L.append(f"| {b['band']} | {b['pairs']} | {b['read']} | {b['same']} | {b['different']} | {b['unsure']} | "
                 f"{prec} | {b['lower_95']:.3f} | {b['lower_95_unsure_as_wrong']:.3f} | "
                 f"{'yes' if b['accepted'] else 'no'} |\n")
    ls = rstats["link_status"]
    n = len(lst)
    L.append("\n## What the list adds\n\n| list rows | count | share |\n|---|---|---|\n")
    for key, label in (("linked", "linked (accepted link, card fact)"),
                       ("in_dataset_unaccepted", "in our data per reader, band not accepted (no card fact)"),
                       ("unsure", "reader unsure for a candidate (excluded from the lane)"),
                       ("not_in_dataset", "not in our dataset (nearby-lane candidates)")):
        c = int(ls.get(key, 0))
        L.append(f"| {label} | {c} | {c / n:.1%} |\n")
    L.append(f"| all Indian list rows | {n} | 100% |\n")
    nid = lst[lst.link_status == "not_in_dataset"]
    pos = rstats["not_in_dataset_position"]
    L.append("\nLocation quality of the not-in-dataset rows:\n\n| position | rows | precision radius km "
             "(median) | max |\n|---|---|---|---|\n")
    for note in ("PIN area", "district centre", "not located"):
        x = nid[nid.position_note == note]
        if len(x):
            L.append(f"| {note} | {len(x)} | {x.position_precision_km.median():.1f} | "
                     f"{x.position_precision_km.max():.1f} |\n")
    L.append(f"\n- {rstats['accepted_pairs']} accepted links join {rstats['list_rows_linked']} list rows to "
             f"{rstats['records_linked']} of our records; all are reader-marked same pairs from the "
             f"{', '.join(rstats['accepted_bands']) or 'no'} band.\n")
    low = rstats["lowest_read_same_score_in_low"]
    L.append(f"- Of the {len(nid)} not-in-dataset rows, {rstats['not_in_dataset_no_candidate']} had no candidate "
             f"at all and {rstats['not_in_dataset_unread_candidate']} had only unread low-band candidates "
             "(score under 0.30). ")
    if low is not None:
        lb = next(b for b in rstats["bands"] if b["band"] == "low")
        L.append(f"The low-band probe (the {lb['read']} best low pairs) found {lb['same']} same pairs, the lowest "
                 f"at score {low:.2f}; {rstats['not_in_dataset_unread_at_or_above_that']} not-in-dataset rows "
                 "have an unread candidate at or above that score. Unread pairs below it may still hide a few "
                 "matches under another name, so recall is not measured. `best_unread_score` in "
                 "`ext_ncg.parquet` carries this per row.\n")
    else:
        L.append("\n")
    L.append("- Middle band: readers marked many pairs same but also many different, so the band fails and "
             "its same pairs give no card fact. Those list rows are still kept out of the not-in-dataset lane.\n")
    return "".join(L)


def report(lst, lstats, links, kstats, sheet, sampling, src_meta, rsec: str = "") -> str:
    L = ["# National Cancer Grid member list: parsed, located, candidate links\n\n",
         f"Built by `{SCRIPT}` on {utcnow()}. Source: {SOURCE_NAME}, \"Cancer Centres / Units\", {PAGE_URL} "
         f"(served by {FRAGMENT_URL}), retrieved {src_meta['retrieved']}, sha256 `{src_meta['sha256']}`. "
         + ("Reader verdicts are applied below: a band is accepted only if its one-sided 95% lower bound of "
            "precision is at least 0.90, and only reader-marked same pairs in accepted bands are links."
            if rsec else
            "No link in this file is final: readers label the check sheet, and a band is accepted only if its "
            "one-sided 95% lower bound of precision is at least 0.90.")
         + "\n\n## Findings\n\n"]
    pn = lstats["position_note"]
    L.append(f"- **List:** {lstats['indian_rows']} Indian member cancer centres or units "
             f"({lstats['list_rows_all_countries']} rows including {lstats['foreign_rows_dropped']} outside India, "
             "dropped). The list is undated and carries no validity; every row has status `undated`.\n")
    L.append(f"- **Located:** {pn.get('PIN area', 0)} to a PIN area, {pn.get('district centre', 0)} to a district "
             f"centre, {pn.get('not located', 0)} not located. The list gives no coordinates.\n")
    if not links.empty:
        b = kstats["bands"]
        L.append(f"- **Candidates:** {kstats['candidates']} pairs for {kstats['list_rows_with_candidate']} list rows "
                 f"(auto {b.get('auto', 0)}, middle {b.get('middle', 0)}, low {b.get('low', 0)}). "
                 f"{kstats['list_rows_with_auto']} list rows have a provisional auto pair.\n")
        L.append(f"- **Check sheet:** {len(sheet)} pairs for readers (auto {sampling['auto_in_sheet']}, middle "
                 f"{sampling['middle_in_sheet']}, low probe {sampling['low_probe_in_sheet']}); "
                 f"{'sampled per band' if sampling['sampled'] else 'no sampling needed'}.\n")
        na = int(b.get("auto", 0))
        L.append(f"- Acceptance arithmetic (exact binomial, one-sided 95%): with all read pairs correct the lower "
                 f"bound reaches 0.90 at 29 pairs; at 46 pairs one wrong pair is allowed. If readers judge all {na} "
                 f"auto pairs, the band passes with at most {max_wrong(na)} wrong.\n")
    L.append("\n## Method\n\n")
    L.append("1. Download: the public members page loads its table from its own GET request; that response for "
             "type \"Cancer Centres / Units\" was saved once to `data/external/ncg/` and parsed as text. robots.txt "
             "allows all paths. No form, login or search was used.\n")
    L.append("2. Parse: one row per table row, with the section heading (state, \"National\", or a foreign "
             "country) and the row number inside the section as the citation.\n")
    L.append("3. Locate (`vf.locate.resolve`): the last 6-digit PIN in the address that is in the India Post "
             "directory and in the listed state gives the PIN-area centre and its precision; otherwise an "
             "address part that resolves to a district or a district-named city gives the district centre.\n")
    L.append(f"4. Our pool: {kstats['pool']} of {kstats['our_records']} records; "
             f"{kstats['rejected_ayush_dental_vet']} AYUSH, dental or veterinary records rejected by type or name.\n")
    L.append(f"5. Blocking: same PIN, or the same PIN-directory district plus a rare shared name token (a token in "
             f"at most {RARE_MAX_DF} of our names, at least 4 letters, not a generic unit word, not the district's "
             "own name; soft match at rapidfuzz ratio 85). The 2 km block is not used: the list has no coordinates.\n")
    L.append(f"6. Score: IDF-weighted soft Jaccard on name tokens (abbreviations expanded as in `vf.public_list`), "
             f"bounded to [0, 1]; IDF over {kstats.get('idf_docs', 'n/a')} names (ours plus the list's).\n")
    L.append(f"7. Bands (provisional, for readers): auto at score {AUTO_MIN} or more and the one-to-one winner; "
             f"middle at {MIDDLE_MIN} or more, or a high score that lost the one-to-one contest; low below. "
             "One-to-one is greedy by score; records in one WP11 duplicate cluster may share a list row.\n")
    if not links.empty:
        L.append("\n## Score distribution\n\n| band | pairs | same PIN | min score | max score |\n|---|---|---|---|---|\n")
        for bnd in ("auto", "middle", "low"):
            x = links[links.band == bnd]
            if len(x):
                L.append(f"| {bnd} | {len(x)} | {int(x.same_pin.sum())} | {x.score.min():.2f} | {x.score.max():.2f} |\n")
        L.append("\n## Ten highest-scoring pairs (for orientation, not decisions)\n\n"
                 "| list name | our name | score | band | km apart |\n|---|---|---|---|---|\n")
        for x in links.head(10).itertuples():
            L.append(f"| {x.list_name} | {x.our_name} | {x.score:.2f} | {x.band} | {x.km_apart} |\n")
    L.append(rsec)
    L.append("\n## Limits\n\n")
    L.append("- Membership of the National Cancer Grid is a network membership, not an accreditation. The card "
             "fact can say the centre is on the member list; it cannot say which cancer treatments it offers.\n")
    L.append("- The list is undated. A member that left the network may still be listed, and a new member may be "
             "missing.\n")
    L.append("- Positions are PIN-area or district centres, so `km_apart` mixes our record's error with the "
             "list's precision radius.\n")
    L.append("- A list row with no candidate is not evidence that the centre is missing from our data. Recall is "
             "not measured.\n")
    return "".join(L)


def main() -> int:
    ext = paths.external_dir() / SOURCE_ID
    src = ext / FRAGMENT_FILE
    retrieved = (ext / "retrieved_utc.txt").read_text().strip()
    src_meta = {"retrieved": retrieved, "sha256": sha256(src), "file": f"external/ncg/{FRAGMENT_FILE}"}
    silver, gold, labels = paths.silver_dir(), paths.gold_dir(), paths.labels_dir()
    out_list = silver / "ext_ncg.parquet"
    out_links = silver / "ext_links_ncg.parquet"
    out_sheet = labels / "ext_link_check_ncg.csv"
    read_path = labels / f"ext_link_read_{SOURCE_ID}.csv"
    rpath = paths.code_root() / "reports" / "ext_ncg.md"

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
        sampling = json.loads((out_sheet.parent / (out_sheet.name + ".meta.json")).read_text()).get("sampling", sampling)
        read = pd.read_csv(read_path, dtype=str, keep_default_na=False)
        links, lst, rstats = apply_readers(links, lst, read, set(frozen.item_id))
        rsec = reader_section(rstats, lst, links)

    write_atomic(out_list, lambda p: lst.to_parquet(p, index=False))
    write_meta(out_list, len(lst), {
        "grain": "one row per Indian row of the NCG 'Cancer Centres / Units' member list",
        "columns": list(lst.columns), "source_url": PAGE_URL, "fragment_url": FRAGMENT_URL,
        "source_file": src_meta["file"], "source_sha256": src_meta["sha256"], "retrieved_utc": retrieved,
        "list_date": None, "status_rule": "every row 'undated': the list carries no date or validity",
        "link_status_rule": ("linked: an accepted link; in_dataset_unaccepted: a reader marked a candidate same "
                             "but its band is not accepted; unsure: a reader marked a candidate unsure and none "
                             "same; not_in_dataset: none of these (the only rows for the outside-nearby lane)")
        if readers else None,
        "use": "D016/D055: dated, cited card fact and outside-nearby lane only; never an input to grades, "
               "quadrants or ranking", "stats": lstats, "reader_stats": rstats or None})

    write_atomic(out_links, lambda p: links.to_parquet(p, index=False))
    write_meta(out_links, len(links), {
        "grain": ("one row per candidate pair (NCG list_uid x our facility_id); reader verdict where read; "
                  "accepted marks the final links") if readers else
                 "one row per candidate pair (NCG list_uid x our facility_id); provisional band, no decision",
        "columns": list(links.columns),
        "inputs": ["silver/ext_ncg.parquet", "silver/facilities.parquet", "silver/pin_crosswalk.parquet",
                   "gold/facility_duplicates.parquet"] + ([f"labels/{read_path.name}"] if readers else []),
        "bands": {"auto_min": AUTO_MIN, "middle_min": MIDDLE_MIN, "soft_min": SOFT_MIN},
        "acceptance_rule": "a band is accepted only if its one-sided 95% Clopper-Pearson lower bound of "
                           "precision, same / (same + different) from independent readers, is at least 0.90; "
                           "inside an accepted band only reader-marked same pairs are accepted (D048 item 5, D055)",
        "stats": kstats, "reader_stats": rstats or None})

    if not readers:
        write_atomic(out_sheet, lambda p: sheet.to_csv(p, index=False))
        write_meta(out_sheet, len(sheet), {
            "grain": "one row per candidate pair given to readers; readers write labels/ext_link_read_ncg.csv",
            "sampling": sampling, "seed": SEED, "order": "shuffled with the seed, so band is not read from order"})

    body = report(lst, lstats, links, kstats, sheet, sampling, src_meta, rsec)
    write_atomic(rpath, lambda p: p.write_text(body))

    print(f"NCG list: {lstats['indian_rows']} Indian rows ({lstats['foreign_rows_dropped']} foreign dropped); "
          f"located {lstats['position_note']}")
    print(f"pool {kstats.get('pool')} (rejected {kstats.get('rejected_ayush_dental_vet')}); "
          f"candidates {kstats.get('candidates', 0)}; bands {kstats.get('bands', {})}")
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
