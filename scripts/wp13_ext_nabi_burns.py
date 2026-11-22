"""WP13 / D055: NABI burn care providers, parsed, located and linked (candidates only).

Source: National Academy of Burns India (NABI), "List of Burn Care Providers in
India (XLS File 128 Burn Care Centres) updated till 5th Feb 2026", linked from
http://www.thenabi.org/bcp.php. See data/external/nabi_burns/SOURCE.md.

Reads (shared data dir):
  external/nabi_burns/burn_units.xlsx   untrusted download, parsed as XML data only
  silver/facilities.parquet, silver/pin_crosswalk.parquet, silver/places.parquet
  gold/facility_duplicates.parquet      WP11 duplicate pairs (one-to-one exception)

Writes (atomic, with .meta.json sidecars):
  silver/ext_nabi_burns.parquet         one row per list row (127), located
  silver/ext_links_nabi_burns.parquet   every blocked candidate pair, scored, banded, with
                                        reader_verdict and accepted once readers have read
  labels/ext_link_check_nabi_burns.csv  auto and middle band pairs for independent readers
  reports/ext_nabi_burns.md             counts and method

Linkage (reports/score_methodology_research.md, strict rules):
  * blocking: same PIN; or (list has coordinates: never, this list has none);
    or same district (or same town name) plus a rare shared name token
  * score: IDF-weighted soft Jaccard of name tokens, bounded to [0, 1]
  * AYUSH, dental and veterinary records are not candidates
  * one-to-one, except inside a WP11 duplicate cluster (our side) or a group of
    repeated rows of the same unit (list side)
  * bands are proposals; a band is accepted only if readers' checks give a
    one-sided 95% Clopper-Pearson lower bound of precision, same / (same +
    different), of at least 0.90. When labels/ext_link_read_nabi_burns.csv
    exists (readers write it; its items must equal the rebuilt check sheet),
    the verdicts are joined: inside an accepted band only reader-marked same
    pairs are links (`accepted`), and each list row gets `link_status` and
    `lane_eligible` (not in our dataset, not marked same or unsure by a reader,
    not a repeat row of such a unit, and placed).

Personal data: unit-head names, e-mail addresses, phone numbers and the name of
the person who filled the form are not copied into any output.

Usage:
  python -I scripts/wp13_ext_nabi_burns.py
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import sys
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from vf import geo, locate, paths  # noqa: E402
from vf import public_list as pl  # noqa: E402  (name normalisation only; read-only use)

SCRIPT = "scripts/wp13_ext_nabi_burns.py"
SOURCE_ID = "nabi_burns"
SOURCE_NAME = "NABI list of burn care providers"
PUBLISHER = "National Academy of Burns India (NABI)"
PAGE_URL = "http://www.thenabi.org/bcp.php"
FILE_URL = "http://www.thenabi.org/Burn%20units%20New%20till%2015%20sept%20-%20without%20phone%20numbers.xlsx"
LIST_DATE = date(2026, 2, 5)        # "updated till 5th Feb 2026" (page); xlsx Last-Modified 2026-02-05
FILE_NAME = "burn_units.xlsx"
SERVICE_LINE = "burns"
LINK_VERSION = "nabi_v1"
SEED = 20261122

#: Score bands (proposals; fixed before any pair was read).
AUTO_MIN = 0.60
MIDDLE_MIN = 0.35
CSV_CAP = 150
#: An auto pair may not be further apart than the row's position precision plus this.
FAR_KM = 25.0
#: A name token is "rare" when at most this many records in our pool use it.
RARE_DF = 30
#: Soft token match: rapidfuzz ratio at or above this (tokens of 4+ letters).
SOFT_MIN = 85

# Columns of the sheet. Personal-contact columns are listed so they can be dropped.
COLS = {
    "A": "sno", "B": "state", "C": "city", "D": "hospital_name", "E": "postal_address",
    "F": "funded_by", "G": "_unit_head_name", "H": "total_beds", "I": "_unit_email",
    "J": "n_doctors", "K": "n_trainees", "L": "opd", "M": "in_bed", "N": "burns_icu",
    "O": "icu_beds", "P": "non_icu_beds", "Q": "n_nurses", "R": "skin_bank", "S": "skin_bank_name",
    "T": "skin_bank_address", "U": "_skin_bank_contact", "V": "_skin_bank_contact2",
    "W": "lab", "X": "physiotherapy", "Y": "ventilator", "Z": "dialysis",
    "AA": "disaster_programme", "AB": "_form_filled_by", "AC": "_form_email",
    "AD": "dietician", "AE": "total_beds_2",
}
PERSONAL = [v for v in COLS.values() if v.startswith("_")]

STATE_FIX = {
    "andhra pradesh": "ANDHRA PRADESH", "assam": "ASSAM", "bihar": "BIHAR",
    "chattisgarh": "CHHATTISGARH", "chhattisgarh": "CHHATTISGARH", "delhi": "DELHI",
    "gujarat": "GUJARAT", "himachal pradesh": "HIMACHAL PRADESH", "j & k": "JAMMU AND KASHMIR",
    "jammu and kashmir": "JAMMU AND KASHMIR", "jharkhand": "JHARKHAND", "karnataka": "KARNATAKA",
    "kerala": "KERALA", "madhya pradesh": "MADHYA PRADESH", "maharashtra": "MAHARASHTRA",
    "maharastra": "MAHARASHTRA", "manipur": "MANIPUR", "odisha": "ODISHA", "puducherry": "PUDUCHERRY",
    "punjab": "PUNJAB", "rajasthan": "RAJASTHAN", "tamil nadu": "TAMIL NADU", "tamilnadu": "TAMIL NADU",
    "telangana": "TELANGANA", "up / delhi ncr": "UTTAR PRADESH", "uttar pradesh": "UTTAR PRADESH",
    "uttarakhand": "UTTARAKHAND", "west bengal": "WEST BENGAL",
}
#: Hand fixes, one per row they apply to, each with its reason (read on 22 Nov).
ROW_FIX = {
    # sheet row: (state, city, reason)
    15: ("MAHARASHTRA", "Aurangabad", "state and city columns swapped in the sheet"),
    41: ("ANDHRA PRADESH", "VISAKHAPATNAM", "state column says Hyderabad; the row repeats row 10 (Visakhapatnam)"),
    11: ("TELANGANA", "Hyderabad", "listed under Andhra Pradesh; the address says Telangana 500058"),
}
#: Town names the gazetteer finds in several districts; the district meant, by the address.
CITY_DISTRICT = {
    ("KARNATAKA", "mangalore"): "DAKSHINA KANNADA",
    ("CHHATTISGARH", "bhilai"): "DURG",
    ("MAHARASHTRA", "airoli, navi mumbai"): "THANE",
    ("ANDHRA PRADESH", "vijayawada"): "NTR",
    ("MAHARASHTRA", "wada"): "PALGHAR",      # "wada bhiwandi road"
    ("DELHI", "delhi"): None,   # nine districts; no single district centre is honest
}
OUTSIDE_INDIA_ROWS = {57: "Nepal Cleft and Burn Center, Kirtipur, Kathmandu: outside India"}

_PIN_RE = re.compile(r"(?<!\d)([1-8]\d{2})\s?(\d{3})(?!\d)")
_PHONE_RE = re.compile(r"^\+?[\d\s\-]{8,}$")
NO_NAME = re.compile(r"^\s*$")

EXCLUDE_NAME = re.compile(
    r"(?:\w*homoeo\w*|\w*homeopath\w*|\bhomeo\w*)|\b(?:ayurved\w*|ayush|unani|siddha|naturopath\w*|panchakarma|yoga|"
    r"dental|dentist\w*|dentistry|orthodont\w*|endodont\w*|veterinar\w*|vet|animal (?:care|hospital|clinic)|"
    r"pet (?:clinic|care|hospital)|pet multispeciality hospital|multispeciality pet hospital)\b")

#: Words that name a department, the list's own subject, or filler; dropped in
#: one scoring variant ("Department of Plastic Surgery, Gandhi Hospital").
DEPT_WORDS = frozenset("""
department dept deptt of the and burn burns plastic surgery surgeon reconstructive maxillofacial cosmetic
hand ward unit care attached to floor block building superspeciality speciality a pvt ltd private limited
india at
""".split())
STOP = frozenset("of the and a at in to for pvt ltd private limited india dr prof sri shri".split())
INSTITUTION = re.compile(
    r"\b(?:hospital|college|institute|medical|centre|center|clinic|nursing|aiims|foundation|trust|"
    r"sciences|jipmer|pgimer|skims|kgmu|sskm|ims|vims|vimsar|rims|gmc|nmch|pmch|jlnhrc|ucms|gtb)\b")


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


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------

def read_sheet(xlsx: Path) -> list[tuple[int, dict]]:
    """Rows of the first sheet as (sheet_row, {column letter: text}). Read as XML
    data from the zip; nothing in the file is executed."""
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    t = "{%s}t" % ns["m"]
    with zipfile.ZipFile(xlsx) as z:
        for info in z.infolist():
            if info.file_size > 20_000_000:
                raise ValueError(f"unexpectedly large member {info.filename}")
        ss = [''.join(x.text or '' for x in si.iter(t))
              for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", ns)]
        sheet = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
    out = []
    for r in sheet.find("m:sheetData", ns).findall("m:row", ns):
        d = {}
        for c in r.findall("m:c", ns):
            col = re.match(r"[A-Z]+", c.get("r")).group(0)
            v, typ = c.find("m:v", ns), c.get("t")
            if typ == "s" and v is not None:
                val = ss[int(v.text)]
            elif typ == "inlineStr":
                val = ''.join(x.text or '' for x in c.iter(t))
            else:
                val = v.text if v is not None else None
            d[col] = val
        out.append((int(r.get("r")), d))
    return out


def clean(s) -> str | None:
    if s is None or (isinstance(s, float) and math.isnan(s)):
        return None
    s = re.sub(r"\s+", " ", str(s)).strip(" ,;")
    return s or None


def yes_no(s) -> str | None:
    s = (clean(s) or "").lower()
    if not s:
        return None
    if s.startswith(("y", "ues")):
        return "yes"
    if s.startswith("no") or s in ("n", "nil", "0", "na", "n.a."):
        return "no"
    return None


def shifted(d: dict) -> bool:
    """One sheet row (128) has an extra phone-number cell at column I, so the
    values meant for columns I to AA sit one column to the right (an e-mail in
    the doctors column, the burns ICU answer under ICU beds)."""
    i, j = clean(d.get("I")) or "", clean(d.get("J")) or ""
    return bool(_PHONE_RE.match(i)) and "@" in j


def unshift(d: dict) -> dict:
    """Move the values of columns J to AA one column left, onto I to Z. Column
    I then holds the e-mail (personal, dropped anyway); the extra phone cell is
    discarded. AA is blanked (its shifted value is a person's name)."""
    letters = list(COLS)
    out = dict(d)
    a, b = letters.index("I"), letters.index("Z")
    for k in range(a, b + 1):
        out[letters[k]] = d.get(letters[k + 1])
    out["AA"] = None  # would take AB, a person's name; the disaster-programme answer is lost
    return out


_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE_IN_RE = re.compile(r"(?<![\w/])\+?\d[\d\s\-]{7,}\d(?![\w/])")
_PERSON_RE = re.compile(r"^\s*(?:dr|prof|mr|mrs|ms)\b\.?", re.I)


def scrub(s, names: bool = True):
    """Remove e-mail addresses, phone numbers and (``names``) a cell that is a
    person's name (starts with Dr, Prof, Mr, Mrs, Ms)."""
    s = clean(s)
    if s is None:
        return None
    if names and _PERSON_RE.match(s):
        return "[person name removed]"
    s = _EMAIL_RE.sub("[e-mail removed]", s)
    digits = lambda m: "[phone removed]" if len(re.sub(r"\D", "", m.group(0))) >= 9 else m.group(0)
    return _PHONE_IN_RE.sub(digits, s)


def find_pin(*texts) -> str | None:
    for t in texts:
        if not isinstance(t, str):
            continue
        for m in _PIN_RE.finditer(t):
            return m.group(1) + m.group(2)
    return None


def parse(xlsx: Path) -> pd.DataFrame:
    rows = read_sheet(xlsx)
    header = rows[0][1]
    assert (header.get("D") or "").startswith("Name of the Hospital"), header.get("D")
    recs = []
    for n, d in rows[1:]:
        note = []
        if shifted(d):
            d = unshift(d)
            note.append("values for columns I to Z sit one column right in the sheet (an extra phone cell); realigned, disaster-programme answer blanked")
        r = {"sheet_row": n}
        for col, name in COLS.items():
            if not name.startswith("_"):
                keep_names = name in ("hospital_name", "postal_address", "skin_bank_address")
                r["raw_" + name] = scrub(d.get(col), names=not keep_names)
        r["row_note"] = "; ".join(note) or None
        recs.append(r)
    df = pd.DataFrame(recs)
    df["list_uid"] = SOURCE_ID + ":r" + df["sheet_row"].astype(str)
    return df


def name_and_address(r) -> tuple[str | None, str | None]:
    """The hospital name as listed, and the postal address (column E, or the part
    of the name cell after the hospital name when E is empty)."""
    name = clean(r.raw_hospital_name)
    addr = clean(r.raw_postal_address)
    if name and not addr and "," in name:
        parts = [p.strip() for p in name.split(",")]
        # keep the leading parts up to and including the first institutional part
        k = next((i for i, p in enumerate(parts) if INSTITUTION.search(p.lower())), 0)
        lead, rest = ", ".join(parts[:k + 1]), ", ".join(p for p in parts[k + 1:] if p)
        if rest:
            return lead, rest
    return name, addr


# ---------------------------------------------------------------------------
# Locate
# ---------------------------------------------------------------------------

def district_centres(places: pd.DataFrame) -> dict:
    d = places[places["kind"] == "district"]
    return {(s, x): (la, lo, pk) for s, x, la, lo, pk in
            zip(d["state"], d["district"], d["lat"], d["lon"], d["precision_km"])}


def locate_row(r, pins_state: dict, xwalk: dict, centres: dict) -> dict:
    """PIN area when the row carries a PIN known to India Post (and in the row's
    state); else the centre of the district the town resolves to; else nothing."""
    out = {"pin": None, "district": None, "lat": None, "lon": None, "position_precision_km": None,
           "position_note": None, "locate_how": None, "districts_all": []}
    if r.sheet_row in OUTSIDE_INDIA_ROWS:
        out["locate_how"] = OUTSIDE_INDIA_ROWS[r.sheet_row]
        return out
    pin = find_pin(r.address, r.name, r.raw_postal_address)
    if pin and pin in pins_state:
        if r.state and pins_state[pin] != r.state:
            out["locate_how"] = f"PIN {pin} is in {pins_state[pin]}, not {r.state}; not used. "
            pin = None
    elif pin:
        out["locate_how"] = f"PIN {pin} not in the India Post directory; not used. "
        pin = None
    if pin:
        res = locate.resolve(pin)
        if res.status == "ok":
            out.update(pin=pin, district=res.district, lat=res.lat, lon=res.lon,
                       position_precision_km=res.precision_km, position_note="PIN area",
                       locate_how=(out["locate_how"] or "") + res.how,
                       districts_all=sorted(xwalk.get(pin, {f"{res.state}|{res.district}"})))
            return out
    city = clean(r.city)
    if not city:
        out["locate_how"] = (out["locate_how"] or "") + "No town given."
        return out
    key = (r.state, city.lower())
    if key in CITY_DISTRICT:
        dist = CITY_DISTRICT[key]
        if dist is None:
            out["locate_how"] = (out["locate_how"] or "") + f"Town {city!r} spans several districts; not placed."
            return out
        how = f"Town {city!r} matches several districts; {dist} by the address (hand table)."
    else:
        res = locate.resolve(city, r.state)
        if res.status != "ok":
            out["locate_how"] = (out["locate_how"] or "") + f"Town {city!r}: {res.status}. {res.how}"
            return out
        if r.state and res.state != r.state:
            out["locate_how"] = (out["locate_how"] or "") + f"Town {city!r} resolved outside {r.state}; not used."
            return out
        dist = res.district
        how = f"Town {city!r} -> {res.kind} in {res.district} district: {res.how}"
    c = centres.get((r.state, dist))
    if c is None:
        out["locate_how"] = (out["locate_how"] or "") + how + " No district centre in the gazetteer."
        return out
    out.update(district=dist, lat=float(c[0]), lon=float(c[1]), position_precision_km=float(c[2]),
               position_note="district centre", locate_how=(out["locate_how"] or "") + how,
               districts_all=[f"{r.state}|{dist}"])
    return out


# ---------------------------------------------------------------------------
# What the list says, in plain words
# ---------------------------------------------------------------------------

def what_text(r) -> str:
    s = "listed as a burn care unit"
    extra = []
    if r.burns_icu == "yes":
        extra.append("a burns ICU")
    if r.skin_bank_reported == "yes":
        extra.append("a skin bank")
    if extra:
        s += "; the unit reported " + " and ".join(extra) + " (self-reported on NABI's form)"
    return s


def card_text(r) -> str:
    return (f"Registry list ({SOURCE_NAME}, {PUBLISHER.split(' (')[0]}, updated {pl.fmt_date(LIST_DATE)}, "
            f"sheet row {r.sheet_row}): {r.what}. Outside data, not from this record.")


def skin_bank(raw_r, raw_s) -> str | None:
    v = yes_no(raw_r)
    if v:
        return v
    s = (clean(raw_r) or "").lower()
    if s in ("one", "inhouse", "in house"):
        return "yes"
    if s.isdigit() and int(s) > 0:
        return "yes"
    return None


# ---------------------------------------------------------------------------
# Link
# ---------------------------------------------------------------------------

#: Institution acronyms on the list (or in our names), expanded before scoring
#: so "AIIMS Rishikesh" can meet "All India Institute of Medical Sciences,
#: Rishikesh". Applied to both sides. Hand-written from general knowledge.
ACRONYMS = {
    "aiims": "all india institute medical sciences",
    "kgmu": "king george medical university",
    "skims": "sher i kashmir institute medical sciences",
    "pgimer": "post graduate institute medical education research",
    "jipmer": "jawaharlal institute post graduate medical education research",
    "pmch": "patna medical college hospital",
    "nmch": "nalanda medical college hospital",
    "kem": "king edward memorial",
    "sskm": "seth sukhlal karnani memorial",
    "ipgmer": "institute post graduate medical education research",
    "ipgme": "institute post graduate medical education",
    "jlnhrc": "jawaharlal nehru hospital research centre",
    "vimsar": "veer surendra sai institute medical sciences research",
    "rpgmc": "rajendra prasad government medical college",
    "ucms": "university college medical sciences",
    "gtb": "guru teg bahadur",
    "rml": "ram manohar lohia",
    "smsmc": "sawai man singh medical college",
    "kmc": "kasturba medical college",
    "dmch": "dayanand medical college hospital",
    "cmc": "christian medical college",
    "kles": "kle",
    "kle s": "kle",
}
#: Weight of a place-name token (the row's town, district or state, or our
#: record's town or state) relative to its IDF weight. Blocking already put the
#: two in the same place, so a shared town name says little about identity.
LOC_W = 0.3


def tokens(s) -> list[str]:
    if isinstance(s, str):
        s = re.sub(r"\b([A-Za-z])\.\s?(?=[A-Za-z]\b)", r"\1", s)  # K.E.M -> KEM
    n = pl.normalise_name(s)
    n = re.sub(r"\b\d+\w*\b", " ", n)
    n = n.replace("kle s ", "kle ")
    out = []
    for w in n.split():
        out.extend(ACRONYMS.get(w, w).split())
    return [w for w in out if w not in STOP and len(w) > 1]


def core(ts: list[str]) -> list[str]:
    """Distinctive tokens: generic words (``vf.public_list.GENERIC``) removed."""
    return [w for w in ts if w not in pl.GENERIC]


def place_tokens(*texts) -> set[str]:
    out = set()
    for t in texts:
        if isinstance(t, str) and t:
            out.update(tokens(t))
    return out - pl.GENERIC


def variants(name: str | None) -> list[list[str]]:
    """Token lists to score a list name by: the whole cell; each comma, slash,
    bracket or dash separated part that names an institution; and each of those
    without department words. The best variant counts."""
    if not name:
        return []
    out = []
    base = tokens(name)
    if base:
        out.append(base)
    parts = re.split(r"[,/()–—]|\s-\s", name)
    for p in parts:
        tp = tokens(p)
        if tp and INSTITUTION.search(" ".join(tp)) and tp != base:
            out.append(tp)
    more = []
    for v in out:
        dv = [w for w in v if w not in DEPT_WORDS]
        if dv and dv != v:
            more.append(dv)
    seen, res = set(), []
    for v in out + more:
        k = tuple(v)
        if k not in seen:
            seen.add(k)
            res.append(v)
    return res


class IDF:
    def __init__(self, docs: list[list[str]]):
        self.n = len(docs)
        self.df = Counter(w for d in docs for w in set(d))

    def __call__(self, w: str) -> float:
        return math.log((self.n + 1) / (self.df.get(w, 0) + 1)) + 1.0


def soft_jaccard(a: list[str], b: list[str], idf: IDF, places: set | None = None) -> float:
    """IDF-weighted soft Jaccard on token sets, in [0, 1]. Tokens pair one to
    one: exact matches first, then close spellings (rapidfuzz ratio >= SOFT_MIN,
    both 4+ letters) weighted by similarity."""
    from rapidfuzz import fuzz

    A, B = list(dict.fromkeys(a)), list(dict.fromkeys(b))
    if not A or not B:
        return 0.0
    places = places or set()
    wt = lambda w: idf(w) * (LOC_W if w in places else 1.0)
    wa, wb = {w: wt(w) for w in A}, {w: wt(w) for w in B}
    pairs = []
    for x in A:
        for y in B:
            if x == y:
                sim = 1.0
            elif len(x) >= 4 and len(y) >= 4:
                r = fuzz.ratio(x, y)
                sim = r / 100.0 if r >= SOFT_MIN else 0.0
            else:
                sim = 0.0
            if sim > 0:
                pairs.append((sim, x, y))
    pairs.sort(key=lambda t: (-t[0], t[1], t[2]))
    ua, ub, inter = set(), set(), 0.0
    for sim, x, y in pairs:
        if x in ua or y in ub:
            continue
        ua.add(x)
        ub.add(y)
        inter += sim * (wa[x] + wb[y]) / 2.0
    union = sum(wa.values()) + sum(wb.values()) - inter
    return float(min(1.0, max(0.0, inter / union))) if union > 0 else 0.0


def pair_score(list_variants: list[list[str]], ours: list[str], idf: IDF, places: set) -> float:
    """Best soft Jaccard over the list name's variants, on distinctive tokens
    (generic words removed; place names down-weighted). When either side has no
    distinctive token, the full token lists are compared instead."""
    best = 0.0
    oc = core(ours)
    for v in list_variants:
        vc = core(v)
        if vc and oc:
            sc = soft_jaccard(vc, oc, idf, places)
        else:
            sc = soft_jaccard(v, ours, idf, places)
        best = max(best, sc)
    return best


def dup_clusters(dups: pd.DataFrame, fac: pd.DataFrame) -> dict:
    """facility_id -> WP11 duplicate cluster id (connected components of the
    duplicate pairs); records with no duplicate are their own cluster."""
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in zip(dups["facility_id_a"], dups["facility_id_b"]):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    return {f: find(f) for f in fac["facility_id"]}


def list_groups(lst: pd.DataFrame) -> pd.Series:
    """Repeated rows of the same unit (same state, district and distinctive
    name core), so one hospital listed twice may link twice."""
    cores = lst["name"].map(lambda s: " ".join(sorted(set(core(tokens(s))) - DEPT_WORDS)) if s else "")
    key = lst["state"].fillna("") + "|" + lst["district"].fillna("") + "|" + cores
    first = {}
    out = []
    for uid, k, c in zip(lst["list_uid"], key, cores):
        if not c:
            out.append(uid)
            continue
        first.setdefault(k, uid)
        out.append(first[k])
    return pd.Series(out, index=lst.index)


def build_pool(fac: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    name_n = fac["name"].map(pl.normalise_name)
    excl = fac["facility_type"].eq("dentist") | name_n.str.contains(EXCLUDE_NAME)
    reasons = {"facility_type dentist": int(fac["facility_type"].eq("dentist").sum()),
               "AYUSH, dental or veterinary name": int((~fac["facility_type"].eq("dentist") &
                                                         name_n.str.contains(EXCLUDE_NAME)).sum())}
    return fac[~excl].copy(), reasons


def candidates(lst: pd.DataFrame, pool: pd.DataFrame, xwalk: dict) -> pd.DataFrame:
    pool = pool.copy()
    pool["toks"] = pool["name"].map(tokens)
    idf = IDF(list(pool["toks"]))
    pool["dkeys"] = pool["pin"].map(lambda p: xwalk.get(str(p), set()) if pd.notna(p) else set())
    pool["city_n"] = pool["city_raw"].map(lambda s: pl.normalise_name(s) if isinstance(s, str) else "")
    by_pin, by_dk, by_city = defaultdict(list), defaultdict(list), defaultdict(list)
    for i, p, dks, c, st in zip(pool.index, pool["pin"], pool["dkeys"], pool["city_n"], pool["state_raw"]):
        if pd.notna(p):
            by_pin[str(p)].append(i)
        for k in dks:
            by_dk[k].append(i)
        if c:
            by_city[c].append(i)

    def rare(w):
        return idf.df.get(w, 0) <= RARE_DF and w not in pl.GENERIC and w not in DEPT_WORDS and len(w) >= 3

    rows = []
    for r in lst.itertuples():
        if not r.name or not r.in_india:
            continue
        vs = variants(r.name)
        list_toks = set(w for v in vs for w in v)
        blocks = defaultdict(set)
        if r.pin:
            for i in by_pin.get(r.pin, []):
                blocks[i].add("same PIN")
        near = set()
        for k in r.districts_all or []:
            near.update(by_dk.get(k, []))
        city_n = pl.normalise_name(r.city) if r.city else ""
        city_hits = set(by_city.get(city_n, [])) if city_n else set()
        for i in near | city_hits:
            shared = [w for w in list_toks & set(pool.at[i, "toks"]) if rare(w)]
            if shared:
                how = "same district" if i in near else "same town name"
                blocks[i].add(f"{how} + rare token ({', '.join(sorted(shared))})")
        for i, why in blocks.items():
            our = pool.loc[i]
            places = place_tokens(r.city, r.district, r.state, our["city_raw"], our["state_raw"])
            sc = pair_score(vs, our["toks"], idf, places)
            km = None
            if r.lat is not None and pd.notna(our["lat"]) and pd.notna(r.lat):
                km = float(geo.haversine_km(r.lat, r.lon, our["lat"], our["lon"]))
            rows.append({
                "list_uid": r.list_uid, "list_group": r.list_group, "list_name": r.name,
                "list_address": r.address, "list_pin": r.pin, "list_district": r.district,
                "list_state": r.state, "list_city": r.city, "list_position_note": r.position_note,
                "facility_id": our["facility_id"], "our_name": our["name"],
                "our_city": our["city_raw"] if pd.notna(our["city_raw"]) else None,
                "our_state": our["state_raw"] if pd.notna(our["state_raw"]) else None,
                "our_pin": None if pd.isna(our["pin"]) else str(our["pin"]),
                "our_facility_type": our["facility_type"], "our_operator": our["operator_type"],
                "our_coord_check": our["coord_check"],
                "block": "; ".join(sorted(why)), "score": round(sc, 4), "km_apart": km,
            })
    return pd.DataFrame(rows)


def one_to_one(c: pd.DataFrame, cluster: dict) -> pd.DataFrame:
    """Greedy by score: a list group takes its best record (and that record's
    WP11 duplicates); a record goes to one list group only."""
    c = c.sort_values(["score", "list_uid", "facility_id"], ascending=[False, True, True]).copy()
    c["cluster"] = c["facility_id"].map(cluster)
    grp_cluster, fac_grp = {}, {}
    keep = []
    for g, f, cl in zip(c["list_group"], c["facility_id"], c["cluster"]):
        ok = (grp_cluster.get(g, cl) == cl) and (fac_grp.get(f, g) == g)
        if ok:
            grp_cluster.setdefault(g, cl)
            fac_grp.setdefault(f, g)
        keep.append(ok)
    c["one_to_one"] = keep
    c["rank_in_list_row"] = c.groupby("list_uid")["score"].rank(ascending=False, method="first").astype(int)
    return c


def band(score: float, o2o: bool, km, precision_km) -> str:
    """``auto``: one-to-one winner, score at least AUTO_MIN, and not further
    apart than the row's position precision plus FAR_KM (when both points are
    known). ``middle``: any other pair scoring at least MIDDLE_MIN."""
    far = (km is not None and not pd.isna(km) and
           km > (0 if precision_km is None or pd.isna(precision_km) else precision_km) + FAR_KM)
    if o2o and score >= AUTO_MIN and not far:
        return "auto"
    if score >= MIDDLE_MIN:
        return "middle"
    return "low"


# ---------------------------------------------------------------------------
# Readers: join verdicts, accept bands, classify list rows
# ---------------------------------------------------------------------------

VERDICTS = {"same", "different", "unsure"}
ACCEPT_LB, ALPHA = 0.90, 0.05


def cp_lower(k: int, n: int, alpha: float = ALPHA) -> float:
    """One-sided Clopper-Pearson lower bound of a binomial proportion k / n."""
    from scipy.stats import beta
    if n == 0 or k == 0:
        return 0.0
    return float(beta.ppf(alpha, k, n - k + 1))


def same_needed(n: int) -> int | None:
    """Smallest number of same verdicts out of n decided pairs that passes ACCEPT_LB."""
    for k in range(n + 1):
        if cp_lower(k, n) >= ACCEPT_LB:
            return k
    return None


def apply_readers(cand: pd.DataFrame, lst: pd.DataFrame, read_path: Path,
                  sheet_ids: set) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Accept a band only if the one-sided 95% Clopper-Pearson lower bound of
    precision, same / (same + different), is at least ACCEPT_LB; inside an
    accepted band keep only pairs the reader marked same. Give each list row a
    link status and say whether it may feed the not-in-dataset lane."""
    read = pd.read_csv(read_path, dtype=str, keep_default_na=False)
    read["verdict"] = read.verdict.str.strip().str.lower()
    bad = sorted(set(read.verdict) - VERDICTS)
    if bad:
        raise SystemExit(f"reader file has unknown verdicts {bad}")
    if read.item_id.duplicated().any():
        raise SystemExit("reader file has duplicate item_id rows")
    if set(read.item_id) != sheet_ids:
        raise SystemExit("reader file items do not match the rebuilt check sheet; inputs changed, read again")
    rv = read.set_index("item_id")
    cand = cand.copy()
    cand["reader_verdict"] = cand.item_id.map(rv.verdict)
    cand["reader_note"] = cand.item_id.map(rv.note)

    bands = []
    for b in ("auto", "middle", "low"):
        x = cand[cand.band == b]
        s, d, u = (int((x.reader_verdict == v).sum()) for v in ("same", "different", "unsure"))
        n = s + d
        bands.append({"band": b, "pairs": int(len(x)), "read": s + d + u, "same": s, "different": d, "unsure": u,
                      "precision": (s / n) if n else float("nan"), "lower_95": cp_lower(s, n),
                      "lower_95_unsure_as_wrong": cp_lower(s, s + d + u),
                      "same_needed_to_pass": same_needed(n) if n else None,
                      "accepted": bool(n > 0 and cp_lower(s, n) >= ACCEPT_LB)})
    btab = pd.DataFrame(bands)
    ok = set(btab[btab.accepted].band)
    cand["accepted"] = cand.band.isin(ok) & cand.reader_verdict.eq("same")

    acc = cand[cand.accepted]
    fac_multi = acc.groupby("facility_id").list_group.nunique()
    lr = acc.groupby("list_uid").agg(n=("facility_id", "size"), clusters=("cluster", "nunique"))
    if (fac_multi > 1).any() or ((lr.n > 1) & (lr.clusters > 1)).any():
        raise SystemExit("accepted links break one-to-one outside WP11 clusters and list groups")

    linked = set(acc.list_uid)
    same_any = set(cand[cand.reader_verdict == "same"].list_uid)
    unsure_any = set(cand[cand.reader_verdict == "unsure"].list_uid)
    grp = lst.set_index("list_uid").list_group
    found = linked | same_any | unsure_any
    held_groups = set(grp.reindex(list(found)).dropna())
    loose = loose_repeats(lst, found)
    unread = cand[cand.reader_verdict.isna()]
    best_unread = unread.groupby("list_uid").score.max()
    ncand = cand.groupby("list_uid").size()

    def status(r):
        if not r.in_india:
            return "outside_india"
        if r.name is None or pd.isna(r.name):
            return "unnamed"
        if r.list_uid in linked:
            return "linked"
        if r.list_uid in same_any:
            return "in_dataset_unaccepted"
        if r.list_uid in unsure_any:
            return "unsure"
        if r.list_group in held_groups or r.list_uid in loose:
            return "in_dataset_via_repeat_row"
        return "not_in_dataset"

    lst = lst.copy()
    lst["link_status"] = [status(r) for r in lst.itertuples()]
    lst["n_candidates"] = lst.list_uid.map(ncand).fillna(0).astype(int)
    lst["best_unread_score"] = lst.list_uid.map(best_unread)
    lst["linked_facility_ids"] = lst.list_uid.map(acc.groupby("list_uid").facility_id.agg(list)).apply(
        lambda v: v if isinstance(v, list) else [])
    lst["lane_eligible"] = lst.link_status.eq("not_in_dataset") & lst.lat.notna()
    lst["repeat_of"] = lst.list_uid.map(loose)
    lst["link_version"] = LINK_VERSION

    nid = lst[lst.link_status == "not_in_dataset"]
    stats = {
        "bands": btab.to_dict(orient="records"), "accept_lb": ACCEPT_LB, "accepted_bands": sorted(ok),
        "accepted_pairs": int(len(acc)), "list_rows_linked": len(linked),
        "records_linked": int(acc.facility_id.nunique()),
        "link_status": lst.link_status.value_counts().to_dict(),
        "not_in_dataset": int(len(nid)), "lane_eligible": int(lst.lane_eligible.sum()),
        "loose_repeats": {u: v for u, v in loose.items()},
        "auto_wrong": [f"{r.list_name} -> {r.our_name}: {r.reader_note}" for r in
                       cand[(cand.band == "auto") & (cand.reader_verdict == "different")].itertuples()],
        "not_in_dataset_only_different": int(((nid.n_candidates > 0) & nid.best_unread_score.isna()).sum()),
        "not_in_dataset_position": nid.position_note.fillna("not located").value_counts().to_dict(),
        "not_in_dataset_no_candidate": int((nid.n_candidates == 0).sum()),
        "not_in_dataset_unread_candidate": int(nid.best_unread_score.notna().sum()),
        "not_in_dataset_unread_ge_030": int((nid.best_unread_score >= 0.30).sum()),
        "not_in_dataset_unread_ge_020": int((nid.best_unread_score >= 0.20).sum()),
    }
    front = ["list_uid", "facility_id", "score", "band", "reader_verdict", "accepted"]
    cand = cand[front + [c for c in cand.columns if c not in front]]
    return cand, lst, stats


def loose_repeats(lst: pd.DataFrame, found: set) -> dict:
    """Rows that may repeat a unit a reader found in our data, by a looser test
    than ``list_groups``: same state and district, and at least one shared
    distinctive name word (generic, department and place words removed).
    Used only to keep such rows out of the not-in-dataset lane, never to link.
    Returns list_uid -> the found row's list_uid."""
    named = lst[lst.in_india & lst.name.notna()]
    f = named[named.list_uid.isin(found)]
    out = {}
    for b in named[~named.list_uid.isin(found)].itertuples():
        for a in f[(f.state == b.state) & (f.district == b.district)].itertuples():
            pt = place_tokens(a.city, a.district, a.state, b.city, b.district)
            ta = set(core(tokens(a.name))) - DEPT_WORDS - pt
            tb = set(core(tokens(b.name))) - DEPT_WORDS - pt
            if ta & tb:
                out[b.list_uid] = a.list_uid
                break
    return out


def reader_section(rs: dict, lst: pd.DataFrame) -> str:
    L = ["\n## Reader verdicts and acceptance\n\n"
         f"Readers judged every pair in `data/labels/ext_link_check_{SOURCE_ID}.csv` and wrote "
         f"`data/labels/ext_link_read_{SOURCE_ID}.csv`. Precision is same / (same + different); unsure pairs are "
         "left out of it, and a second column counts them as wrong. A band is accepted only if the one-sided 95% "
         f"Clopper-Pearson lower bound is at least {ACCEPT_LB:.2f}; inside an accepted band only pairs the "
         "reader marked same are links. Low-band pairs were not read.\n\n"
         "| band | pairs | read | same | different | unsure | precision | lower 95% | lower 95%, unsure as wrong "
         "| same needed to pass | accepted |\n|---|---|---|---|---|---|---|---|---|---|---|\n"]
    for b in rs["bands"]:
        prec = "n/a" if pd.isna(b["precision"]) else f"{b['precision']:.3f}"
        need = "n/a" if b["same_needed_to_pass"] is None or pd.isna(b["same_needed_to_pass"]) else \
            f"{int(b['same_needed_to_pass'])} of {b['same'] + b['different']}"
        lb = f"{b['lower_95']:.3f}" if b["read"] else "n/a"
        lbs = f"{b['lower_95_unsure_as_wrong']:.3f}" if b["read"] else "n/a"
        L.append(f"| {b['band']} | {b['pairs']} | {b['read']} | {b['same']} | {b['different']} | {b['unsure']} | "
                 f"{prec} | {lb} | {lbs} | {need} | {'yes' if b['accepted'] else 'no'} |\n")
    ls = rs["link_status"]
    india = lst[lst.in_india]
    L.append("\n## What the list adds\n\n| Indian list rows | count | share |\n|---|---|---|\n")
    for key, label in (("linked", "linked (accepted link, card fact)"),
                       ("in_dataset_unaccepted", "in our data per reader, band not accepted (no card fact)"),
                       ("unsure", "reader unsure for a candidate (kept out of the lane)"),
                       ("in_dataset_via_repeat_row", "may repeat a row the reader found in our data: same "
                                                     "district and a shared distinctive name word (kept out "
                                                     "of the lane)"),
                       ("unnamed", "no hospital name in the sheet (cannot be linked; kept out of the lane)"),
                       ("not_in_dataset", "not in our dataset (no accepted link, no reader-marked same pair)")):
        c = int(ls.get(key, 0))
        L.append(f"| {label} | {c} | {c / len(india):.1%} |\n")
    L.append(f"| all Indian rows | {len(india)} | 100% |\n")
    if ls.get("outside_india"):
        L.append(f"\nOne further row is outside India (Kathmandu) and is not counted.\n")
    nid = lst[lst.link_status == "not_in_dataset"]
    L.append("\nLocation quality of the not-in-dataset rows:\n\n| position | rows | precision radius km (median) "
             "| max |\n|---|---|---|---|\n")
    for note in ("PIN area", "district centre"):
        x = nid[nid.position_note == note]
        if len(x):
            L.append(f"| {note} | {len(x)} | {x.position_precision_km.median():.1f} | "
                     f"{x.position_precision_km.max():.1f} |\n")
    x = nid[nid.lat.isna()]
    if len(x):
        L.append(f"| not located | {len(x)} | n/a | n/a |\n")

    L.append(f"\n- **No band qualifies, so this source gives no card facts.** " if not rs["accepted_bands"] else
             f"\n- {rs['accepted_pairs']} accepted links join {rs['list_rows_linked']} list rows to "
             f"{rs['records_linked']} of our records ({', '.join(rs['accepted_bands'])} band). ")
    if not rs["accepted_bands"]:
        a = next(b for b in rs["bands"] if b["band"] == "auto")
        m = next(b for b in rs["bands"] if b["band"] == "middle")
        n_a = a["same"] + a["different"]
        L.append(f"The auto band was {a['same']} same of {n_a} read (precision {a['precision']:.3f}), but with "
                 f"{n_a} pairs the one-sided 95% lower bound is {a['lower_95']:.3f}; passing needed "
                 f"{int(a['same_needed_to_pass'])} of {n_a}"
                 f"{', that is no wrong pair at all' if int(a['same_needed_to_pass']) == n_a else ''}. Wrong auto "
                 f"pairs: {'; '.join(rs['auto_wrong']) or 'none'}. The middle band was {m['same']} of {m['same'] + m['different']} "
                 f"(lower bound {m['lower_95']:.3f}). The thresholds and the acceptance rule were fixed before "
                 "reading and are not moved after it.\n")
    L.append(f"- {ls.get('in_dataset_unaccepted', 0)} list rows have a pair the reader marked same, so the unit is "
             "very likely in our data; it gets no card fact because its band failed, and it is kept out of the "
             "not-in-dataset lane.\n")
    for u, v in rs["loose_repeats"].items():
        a_, b_ = (lst.set_index("list_uid").loc[k] for k in (u, v))
        L.append(f"- Sheet row {a_.sheet_row} ({a_['name']}, {a_.district}) has no reader-marked pair of its own, "
                 f"but it shares the district and a distinctive name word with row {b_.sheet_row} ({b_['name']}), "
                 "which the reader found in our data. The list-side grouping missed it, so it is kept out of the "
                 "lane rather than shown as not in our dataset.\n")
    L.append(f"- {rs['not_in_dataset']} rows are not in our dataset by this definition; {rs['lane_eligible']} of "
             "them are placed and may feed the nearby lane. "
             f"{rs['not_in_dataset_no_candidate']} had no candidate at all, "
             f"{rs['not_in_dataset_only_different']} had only candidates the reader marked different, and "
             f"{rs['not_in_dataset_unread_candidate']} had only unread low-band candidates (score under "
             f"{MIDDLE_MIN}); {rs['not_in_dataset_unread_ge_030']} of those have one at 0.30 or more and "
             f"{rs['not_in_dataset_unread_ge_020']} at 0.20 or more. Low pairs were not read, so recall is not "
             "measured: a few of these rows may be in our data under another name. `best_unread_score` in "
             f"`ext_{SOURCE_ID}.parquet` carries this per row, and `lane_eligible` marks the rows the lane may use.\n")
    return "".join(L)


# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    t0 = utcnow()
    ext = paths.external_dir() / SOURCE_ID
    xlsx = ext / FILE_NAME
    silver, gold, labels = paths.silver_dir(), paths.gold_dir(), paths.labels_dir()

    raw = parse(xlsx)
    raw[["name", "address"]] = raw.apply(lambda r: pd.Series(name_and_address(r)), axis=1)
    raw["state"] = raw["raw_state"].map(lambda s: STATE_FIX.get((clean(s) or "").lower()))
    raw["city"] = raw["raw_city"].map(clean)
    raw["fix_note"] = None
    for n, (st, city, why) in ROW_FIX.items():
        m = raw["sheet_row"].eq(n)
        raw.loc[m, ["state", "city", "fix_note"]] = [st, city, why]
    raw["in_india"] = ~raw["sheet_row"].isin(OUTSIDE_INDIA_ROWS)
    raw = raw.astype(object).where(raw.notna(), None)
    raw["sheet_row"] = raw["sheet_row"].astype(int)
    raw["in_india"] = raw["in_india"].astype(bool)

    places = pd.read_parquet(silver / "places.parquet")
    pins_state = dict(zip(places.loc[places.kind == "pin", "pin"].astype(str),
                          places.loc[places.kind == "pin", "state"]))
    pc = pd.read_parquet(silver / "pin_crosswalk.parquet")
    xwalk = {p: set(g["state_pin"] + "|" + g["district_pin"]) for p, g in pc.groupby("pin")}
    centres = district_centres(places)
    loc = pd.DataFrame([locate_row(r, pins_state, xwalk, centres) for r in raw.itertuples()], index=raw.index)
    lst = pd.concat([raw, loc], axis=1)

    lst["burns_icu"] = lst["raw_burns_icu"].map(yes_no)
    lst["skin_bank_reported"] = [skin_bank(a, b) for a, b in zip(lst["raw_skin_bank"], lst["raw_skin_bank_name"])]
    lst["service_line"] = SERVICE_LINE
    lst["what"] = lst.apply(what_text, axis=1)
    lst["status"] = "undated"
    lst["list_date"] = LIST_DATE.isoformat()
    lst["row_date"] = None
    lst["citation"] = lst.apply(lambda r: f"{PUBLISHER}, List of Burn Care Providers in India (XLS, updated "
                                          f"{pl.fmt_date(LIST_DATE)}), sheet row {r.sheet_row} (s no "
                                          f"{r.raw_sno or 'blank'}), {PAGE_URL}", axis=1)
    lst["text"] = lst.apply(card_text, axis=1)
    lst["source_id"], lst["source_name"], lst["publisher"] = SOURCE_ID, SOURCE_NAME, PUBLISHER
    lst["list_group"] = list_groups(lst)
    lst.loc[lst["name"].isna(), "row_note"] = lst.loc[lst["name"].isna(), "row_note"].fillna(
        "no hospital name in the sheet")

    front = ["list_uid", "source_id", "source_name", "publisher", "sheet_row", "name", "address", "pin",
             "district", "state", "city", "lat", "lon", "position_precision_km", "position_note", "locate_how",
             "service_line", "what", "status", "list_date", "row_date", "citation", "text", "burns_icu",
             "skin_bank_reported", "in_india", "list_group", "row_note", "fix_note", "districts_all"]
    lst = lst[front + [c for c in lst.columns if c.startswith("raw_")]]
    for c in ("lat", "lon", "position_precision_km"):
        lst[c] = pd.to_numeric(lst[c], errors="coerce")
    assert not set(PERSONAL) & set(lst.columns)

    # ---- link
    fac = pd.read_parquet(silver / "facilities.parquet")
    pool, excl = build_pool(fac)
    dups = pd.read_parquet(gold / "facility_duplicates.parquet")
    cluster = dup_clusters(dups, fac)
    cand = candidates(lst, pool, xwalk)
    cand = one_to_one(cand, cluster)
    prec = cand["list_uid"].map(lst.set_index("list_uid")["position_precision_km"])
    cand["list_position_precision_km"] = prec
    cand["band"] = [band(s, o, k, p) for s, o, k, p in zip(cand["score"], cand["one_to_one"], cand["km_apart"], prec)]
    cand["item_id"] = [f"nabi_{u.split(':r')[1]}_{f[:8]}" for u, f in zip(cand["list_uid"], cand["facility_id"])]
    cand["link_version"] = LINK_VERSION
    cand = cand.sort_values(["list_uid", "score"], ascending=[True, False]).reset_index(drop=True)

    # ---- reader file
    pick = cand[cand["band"].isin(["auto", "middle"])].copy()
    sampling = {"cap": CSV_CAP, "n_auto": int((pick.band == "auto").sum()),
                "n_middle": int((pick.band == "middle").sum()), "sampled": False}
    if len(pick) > CSV_CAP:
        rng = np.random.default_rng(SEED)
        n_auto = int((pick.band == "auto").sum())
        k_auto = min(n_auto, CSV_CAP // 2) if (pick.band == "middle").sum() > CSV_CAP // 2 else n_auto
        k_mid = CSV_CAP - k_auto
        parts = []
        for b, k in (("auto", k_auto), ("middle", k_mid)):
            g = pick[pick.band == b]
            idx = rng.choice(len(g), size=min(k, len(g)), replace=False)
            parts.append(g.iloc[sorted(idx)])
        pick = pd.concat(parts)
        sampling.update(sampled=True, kept_auto=int(k_auto), kept_middle=int(min(k_mid, sampling["n_middle"])),
                        seed=SEED, method="simple random sample within band, numpy default_rng")
    out_csv = pd.DataFrame({
        "item_id": pick["item_id"], "list_name": pick["list_name"], "list_address": pick["list_address"],
        "list_pin": pick["list_pin"], "list_district": pick["list_district"],
        "our_facility_id": pick["facility_id"], "our_name": pick["our_name"],
        "our_address_or_city": [", ".join(x for x in (c, s) if x) for c, s in zip(pick["our_city"], pick["our_state"])],
        "our_pin": pick["our_pin"],
        "km_apart": pick["km_apart"].map(lambda v: "" if pd.isna(v) else f"{v:.1f}"),
        "score": pick["score"].map(lambda v: f"{v:.3f}"), "band": pick["band"],
    })
    # shuffle so readers do not see band order or score order by position
    out_csv = out_csv.sample(frac=1.0, random_state=SEED).reset_index(drop=True)

    # ---- readers (only once their file exists; the check sheet must be unchanged)
    read_path = labels / f"ext_link_read_{SOURCE_ID}.csv"
    rstats = None
    if read_path.exists():
        cand, lst, rstats = apply_readers(cand, lst, read_path, set(out_csv["item_id"]))

    # ---- write
    s_path = silver / f"ext_{SOURCE_ID}.parquet"
    write_atomic(s_path, lambda p: lst.to_parquet(p, index=False))
    write_meta(s_path, len(lst), {
        "grain": "one row per row of the NABI sheet (header excluded)",
        "source_url": FILE_URL, "page_url": PAGE_URL, "list_date": LIST_DATE.isoformat(),
        "source_sha256": sha256(xlsx), "columns": list(lst.columns),
        "dropped_personal_columns": [c.lstrip("_") for c in PERSONAL],
        "position_notes": lst["position_note"].value_counts(dropna=False).to_dict(),
        "status_values": lst["status"].value_counts().to_dict(),
        "link_status": rstats["link_status"] if rstats else None,
        "lane_eligible": "link_status not_in_dataset (no accepted link, no reader-marked same or unsure pair, no "
                         "repeat row of such a unit) and a position" if rstats else None,
        "started_at_utc": t0,
    })
    l_path = silver / f"ext_links_{SOURCE_ID}.parquet"
    write_atomic(l_path, lambda p: cand.drop(columns=["cluster"]).to_parquet(p, index=False))
    write_meta(l_path, len(cand), {
        "grain": "one row per blocked candidate pair (NABI list row x our facility_id)",
        "bands": {"auto": f"one-to-one winner and score >= {AUTO_MIN}",
                  "middle": f"score >= {MIDDLE_MIN} and not auto", "low": f"score < {MIDDLE_MIN}"},
        "band_counts": cand["band"].value_counts().to_dict(),
        "blocking": "same PIN; or same district (any PIN-crosswalk district of our record's PIN) or same town "
                    f"name, plus a shared name token used by at most {RARE_DF} pool records and not generic",
        "score": f"IDF-weighted soft Jaccard of name tokens (soft match rapidfuzz ratio >= {SOFT_MIN}), max over "
                 "list-name variants, bounded [0,1]",
        "pool_exclusions": excl, "pool_size": int(len(pool)),
        "final_links_decided": rstats is not None,
        "acceptance_rule": "a band is used only if readers' checks give a one-sided 95% Clopper-Pearson lower bound "
                           "of precision, same / (same + different), >= 0.90; inside it only reader-marked same pairs",
        "accepted_column": "true only for a reader-marked same pair in an accepted band",
        "reader_file": f"labels/ext_link_read_{SOURCE_ID}.csv" if rstats else None,
        "reader_bands": rstats["bands"] if rstats else None,
        "accepted_bands": rstats["accepted_bands"] if rstats else None,
        "accepted_pairs": rstats["accepted_pairs"] if rstats else None,
        "inputs": ["silver/ext_nabi_burns.parquet", "silver/facilities.parquet", "silver/pin_crosswalk.parquet",
                   "silver/places.parquet", "gold/facility_duplicates.parquet"],
    })
    c_path = labels / f"ext_link_check_{SOURCE_ID}.csv"
    write_atomic(c_path, lambda p: out_csv.to_csv(p, index=False))
    write_meta(c_path, len(out_csv), {
        "grain": "one row per candidate pair for independent readers; rows shuffled",
        "sampling": sampling, "readers_write": f"labels/ext_link_read_{SOURCE_ID}.csv",
        "columns": list(out_csv.columns),
    })

    summ = summary(lst, cand, out_csv, pool, excl, sampling)
    rpath = paths.code_root() / "reports" / f"ext_{SOURCE_ID}.md"
    write_atomic(rpath, lambda p: p.write_text(report(lst, cand, summ, rstats)))
    print(json.dumps(summ, indent=1, default=str))
    if rstats:
        print(json.dumps({k: v for k, v in rstats.items()}, indent=1, default=str))
    print(f"wrote {s_path}\nwrote {l_path}\nwrote {c_path}\nwrote {rpath}")
    return 0


def summary(lst, cand, out_csv, pool, excl, sampling) -> dict:
    in_india = lst[lst.in_india]
    named = in_india[in_india.name.notna()]
    o2o = cand[cand.one_to_one]
    return {
        "sheet_rows": int(len(lst)), "in_india": int(len(in_india)), "named_in_india": int(len(named)),
        "list_groups_named_in_india": int(named.list_group.nunique()),
        "with_pin": int(lst.pin.notna().sum()),
        "position_note": lst.position_note.value_counts(dropna=False).to_dict(),
        "not_located": int(lst.lat.isna().sum()),
        "burns_icu_reported": int((lst.burns_icu == "yes").sum()),
        "skin_bank_reported": int((lst.skin_bank_reported == "yes").sum()),
        "pool": int(len(pool)), "pool_excluded": excl,
        "candidate_pairs": int(len(cand)), "list_rows_with_a_candidate": int(cand.list_uid.nunique()),
        "bands": cand.band.value_counts().to_dict(),
        "list_rows_with_auto": int(cand[cand.band == "auto"].list_uid.nunique()),
        "list_rows_auto_or_middle": int(cand[cand.band.isin(["auto", "middle"])].list_uid.nunique()),
        "one_to_one_pairs": int(len(o2o)),
        "csv_rows": int(len(out_csv)), "csv_sampling": sampling,
        "repeated_units": repeated_units(lst),
        "not_placed": [f"row {r.sheet_row}: {(r.name or '')[:40]}, {r.city or r.raw_city or ''}"
                       for r in lst[lst.lat.isna()].itertuples()],
        "blocks": cand.block.map(lambda s: "same PIN" if "same PIN" in s else
                                 ("same district" if "same district" in s else "same town")).value_counts().to_dict(),
    }


def repeated_units(lst) -> list[str]:
    g = lst[lst.name.notna()].groupby("list_group")
    out = []
    for k, d in g:
        if len(d) > 1:
            out.append(f"{d.iloc[0]['name'][:40]}, {d.iloc[0]['city']}: rows "
                       + ", ".join(str(x) for x in d.sheet_row))
    return out


def report(lst, cand, s, rstats=None) -> str:
    L = []
    L.append("# NABI burn care providers: parsed, located, linked\n\n" if rstats else
             "# NABI burn care providers: parsed, located, candidate links\n\n")
    L.append(f"Built by `{SCRIPT}` on {utcnow()}. Source: {PUBLISHER}, \"List of Burn Care Providers in India "
             f"(XLS File 128 Burn Care Centres) updated till 5th Feb 2026\", linked from {PAGE_URL}. Download "
             "record, terms and checksum in `data/external/nabi_burns/SOURCE.md`. Service line: burns.\n")
    L.append("\n## Findings\n\n")
    L.append(f"- **The sheet has {s['sheet_rows']} rows, not 128.** Serial numbers skip 17 and 73 and repeat 16. "
             f"{s['in_india']} rows are in India (one is a Kathmandu, Nepal centre), and {s['named_in_india']} of those "
             f"name a hospital. Some units appear more than once ({'; '.join(s['repeated_units'])}), leaving "
             f"{s['list_groups_named_in_india']} distinct units by the list-side grouping (same district and the "
             "same distinctive name words; looser repeats such as row 62 against rows 64 and 127 for Hamidia "
             "Hospital are not grouped).\n")
    L.append(f"- **Positions are approximate.** The list has no coordinates. {s['position_note'].get('PIN area', 0)} "
             f"rows are placed at their PIN area and {s['position_note'].get('district centre', 0)} at a district "
             f"centre; {s['not_located']} are not placed ({'; '.join(s['not_placed'])}). Two rows with no hospital name "
             "are placed at their district but cannot be linked.\n")
    L.append(f"- **What a row says.** Every row is a burn care unit on NABI's list; {s['burns_icu_reported']} rows "
             f"report a burns ICU and {s['skin_bank_reported']} a skin bank. These are answers to NABI's form, "
             "self-reported and undated per row; the list as a whole is dated 5 Feb 2026, so every row has "
             "status `undated`.\n")
    if rstats:
        a = next(b for b in rstats["bands"] if b["band"] == "auto")
        L.append(f"- **No link is accepted, so NABI gives no card facts.** Readers marked {a['same']} of "
                 f"{a['same'] + a['different']} auto-band pairs the same unit; that is a one-sided 95% lower bound "
                 f"of {a['lower_95']:.3f}, under the 0.90 rule. The middle band is lower. "
                 if not rstats["accepted_bands"] else
                 f"- **Links accepted:** {rstats['accepted_pairs']} pairs for {rstats['list_rows_linked']} list rows "
                 f"({', '.join(rstats['accepted_bands'])} band). ")
        nloose = len(rstats["loose_repeats"])
        L.append(f"{rstats['not_in_dataset'] + nloose} named Indian rows have no accepted link and no "
                 f"reader-marked same pair; {nloose} of them {'likely repeats' if nloose == 1 else 'likely repeat'} a row that has one, leaving "
                 f"{rstats['not_in_dataset']} not in our dataset. {rstats['lane_eligible']} of those are placed "
                 "and may feed the nearby lane. See the reader section.\n")
    L.append(f"- **Candidate links.** {s['candidate_pairs']} blocked pairs for "
             f"{s['list_rows_with_a_candidate']} list rows; bands: auto {s['bands'].get('auto', 0)}, middle {s['bands'].get('middle', 0)}, "
             f"low {s['bands'].get('low', 0)}. "
             f"{s['list_rows_with_auto']} list rows have an auto-band pair and {s['list_rows_auto_or_middle']} "
             f"have an auto or middle pair. {s['csv_rows']} pairs go to readers"
             f"{' (sampled, see below)' if s['csv_sampling'].get('sampled') else ' (all auto and middle pairs; no sampling)'}.\n")
    if not rstats:
        L.append("- No link is final. A band is used on cards only if the readers' checks give a one-sided 95% "
                 "lower bound of precision of at least 0.90 (D048, D055).\n")

    L.append("\n## Method\n\n")
    L.append("1. Parse: the xlsx is read as XML from the zip (no spreadsheet software, nothing executed). One row "
             "per sheet row; `list_uid` is `nabi_burns:r<sheet row>` because the serial numbers are not unique.\n")
    L.append("2. Personal data dropped: unit-head names, unit and form e-mails, skin-bank phone and contact cells, "
             "and the form filler's name are not copied. Raw non-personal columns are kept as `raw_*`.\n")
    L.append("3. Hand fixes (one per affected row, each with its reason in `fix_note`): swapped state and city "
             "(row 15), a state cell reading Hyderabad on a Visakhapatnam row (row 41), Apollo DRDO Hyderabad "
             "listed under Andhra Pradesh with a Telangana address (row 11). Row 128 has an extra phone cell at column I, "
             "so its values for columns I to Z sit one column right; it is realigned.\n")
    L.append("4. Locate: a 6-digit PIN found in the address or name cell, known to India Post and in the row's "
             "state, gives the PIN area (`vf.locate.resolve`, position_note `PIN area`). Otherwise the town is "
             "resolved with the state and the row is placed at that district's centre (`district centre`). "
             "Towns the gazetteer finds in several districts use a small hand table read from the addresses "
             "(Mangalore, Bhilai, Airoli, Vijayawada, Wada); Delhi rows without a PIN are not placed, because the "
             "town spans several districts.\n")
    ex = s["pool_excluded"]
    L.append(f"5. Pool: our {s['pool']:,} records, after removing {ex['facility_type dentist']} with facility "
             f"type dentist and {ex['AYUSH, dental or veterinary name']:,} more whose name says AYUSH (ayurveda, "
             "homoeopathy, unani, siddha, naturopathy, yoga), dental or veterinary.\n")
    L.append(f"6. Blocking: same PIN; or the same district (every district our record's PIN falls in, from "
             f"`silver/pin_crosswalk`) or the same town name, plus a shared name token used by at most {RARE_DF} "
             "pool records that is not a generic or department word. The 2 km rule does not apply: the list "
             "has no coordinates.\n")
    L.append(f"7. Score: IDF-weighted soft Jaccard of name tokens, bounded to [0, 1]. Names are normalised with "
             f"`vf.public_list.normalise_name`, dotted and listed acronyms are expanded (AIIMS, KGMU, SKIMS, PMCH, "
             f"KEM, RPGMC and others; the table is in the script), and generic words (`vf.public_list.GENERIC`: "
             f"hospital, medical, college, government ...) are removed, falling back to all tokens when a side has "
             f"none left. Close spellings match at rapidfuzz ratio {SOFT_MIN} or more, weighted by similarity. "
             f"Place-name tokens (the row's town, district and state, our record's town and state) count at "
             f"{LOC_W} of their IDF weight, because blocking already put the pair in one place. The best score over "
             "variants of the list's name cell counts (whole cell, each institutional part, each without "
             "department words such as \"Department of Plastic Surgery\").\n")
    L.append(f"8. One-to-one: greedy by score; a list unit takes its best record and that record's WP11 duplicates; "
             f"a record goes to one list unit. Repeated rows of the same unit share a group and may link together.\n")
    L.append(f"9. Bands: `auto` = one-to-one winner with score at least {AUTO_MIN} and, when both points are known, "
             f"no further apart than the row's position precision plus {FAR_KM:.0f} km; `middle` = any other pair "
             f"scoring at least {MIDDLE_MIN}; `low` below that. Readers get every auto and middle pair, shuffled "
             f"(cap {CSV_CAP}).\n")
    L.append("10. How the scorer was set (disclosed so readers can weigh it): the band thresholds were written "
             "before any pair was read. A first pass scored all tokens; reading its auto and middle pairs showed "
             "generic and place words carrying false matches (Nalanda Medical College against Patna Medical "
             "College, auto at 0.64) and acronyms missing true ones (AIIMS Rishikesh, 0.23). The scorer was "
             "revised once (generic words out, place words down-weighted, acronyms expanded, the distance gate, "
             "and electro-homoeopathic names caught by the AYUSH filter), and these are its results. The readers' "
             "check is independent of that tuning, so its precision is the number that decides each band.\n")

    L.append("\n## Blocks behind the candidate pairs\n\n")
    L.append("| block | pairs |\n|---|---|\n")
    for k, v in s["blocks"].items():
        L.append(f"| {k} | {v} |\n")
    L.append("\n## Band counts\n\n| band | pairs | list rows |\n|---|---|---|\n")
    for b in ("auto", "middle", "low"):
        g = cand[cand.band == b]
        L.append(f"| {b} | {len(g)} | {g.list_uid.nunique()} |\n")

    if rstats:
        L.append(reader_section(rstats, lst))
    L.append("\n## Rows not placed or not linkable\n\n")
    for r in lst[lst.lat.isna() | lst.name.isna()].itertuples():
        L.append(f"- sheet row {r.sheet_row}: {r.name or '(no name)'}, {r.city or ''}, {r.state or ''}: "
                 f"{r.row_note or r.locate_how}\n")

    L.append("\n## Limits\n\n")
    L.append("- The list is compiled from NABI members' form answers. It says itself that some centres are "
             "missing. Being on it is outside evidence that a unit described itself as a burn care provider to "
             "a professional body; it is not an accreditation or a government licence, and not being on it says "
             "nothing about a hospital.\n")
    L.append("- No row has its own date. The list date (5 Feb 2026) is the only date; a row could be years old.\n")
    L.append("- Positions are PIN areas or district centres, not addresses. A district centre can be tens of "
             "kilometres from the unit; `position_precision_km` says how far.\n")
    L.append("- Terms: the page says \"All contents and pictures are private properties of NABI. All rights "
             "reserved\". The file is publicly linked with no login. It is used here for research and cited per "
             "row; the raw file is not committed or republished. Whether facility names and the citation may be "
             "shown in the public demo is Hruday's call.\n")
    return "".join(L)


if __name__ == "__main__":
    raise SystemExit(main())
