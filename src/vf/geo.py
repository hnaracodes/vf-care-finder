"""Geography helpers shared across work packages: name normalization and
district name matching between the India Post PIN directory and NFHS-5.

WP2 uses these to build ``silver/district_crosswalk.parquet``. Anything that
needs to compare district or state names from different sources should go
through ``norm_name`` / ``norm_state`` so every package normalizes the same way.

Matching rules (see scripts/wp2_geography_nfhs.py for the full pipeline):

* exact: equal after ``norm_state`` and ``norm_name``
* fuzzy: within one state, best ``name_score`` at or above a threshold, with a
  margin over the runner-up, assigned one-to-one (each NFHS district can be
  claimed by only one PIN-directory district through fuzzy matching)

Coordinate helpers (second half of this module):

* ``post_office_coords``: clean PIN-directory coordinates, including the repair
  of rows whose latitude and longitude are swapped (WP2).
* ``placeholder_points``: offices stacked on one point with offices of other
  PINs, a placeholder rather than their location (WP5 ``coord_placeholder``).
* ``PinReference`` and ``check_facility_locations``: where each PIN is, and the
  check of facility coordinates against their own PIN, with the city text as
  the tie-breaker (WP1).
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Iterable, Mapping, Optional

import numpy as np
import pandas as pd
from rapidfuzz import fuzz

from .io import INDIA_BBOX, to_float

#: State names that differ between sources, keyed by their ``norm_name`` form
#: and mapped to the PIN directory's spelling (without a leading "THE").
STATE_ALIASES: dict[str, str] = {
    "MAHARASTRA": "MAHARASHTRA",
    "NCT OF DELHI": "DELHI",
    "THE DADRA AND NAGAR HAVELI AND DAMAN AND DIU": "DADRA AND NAGAR HAVELI AND DAMAN AND DIU",
}

#: NFHS district names appear to be cut off at this many characters
#: ("Sri Potti Sriramulu Nello", "North Twenty Four Pargana").
NFHS_NAME_LIMIT = 25

#: Score given when every token of the shorter name appears in the longer one
#: ("KHARGONE" in "KHARGONE WEST NIMAR"). Below 100 so true spelling matches win.
CONTAINMENT_SCORE = 90.0

#: Tokens too common to justify a containment match on their own.
GENERIC_TOKENS = frozenset(
    {
        "AND", "THE", "DISTRICT", "DIST", "NORTH", "SOUTH", "EAST", "WEST",
        "CENTRAL", "UPPER", "LOWER", "RURAL", "URBAN", "NAGAR", "CITY",
    }
)

_NON_ALNUM = re.compile(r"[^A-Z0-9 ]+")
_WS = re.compile(r"\s+")


def norm_name(s) -> str:
    """Uppercase, ``&`` to AND, punctuation to spaces, whitespace collapsed.

    >>> norm_name("Khandwa (East Nimar) ")
    'KHANDWA EAST NIMAR'
    """
    if s is None:
        return ""
    s = str(s).upper().replace("&", " AND ")
    s = _NON_ALNUM.sub(" ", s)
    return _WS.sub(" ", s).strip()


def norm_state(s) -> str:
    """``norm_name`` plus the known cross-source state spellings."""
    n = norm_name(s)
    return STATE_ALIASES.get(n, n)


def district_key(state_ut, district_name) -> str:
    """Canonical NFHS district id: ``"<state_ut>|<district_name>"``.

    Leading and trailing whitespace is stripped from both parts (704 of the 706
    raw NFHS district names carry a trailing space); spelling and case are kept
    exactly as in the NFHS file.
    """
    return f"{str(state_ut).strip()}|{str(district_name).strip()}"


def looks_truncated(raw_name, limit: int = NFHS_NAME_LIMIT) -> bool:
    """True when a raw NFHS name is long enough to have been cut off."""
    return len(str(raw_name).strip()) >= limit


def _contained(a: str, b: str) -> bool:
    """Every token of the shorter name is in the longer, and the shorter name
    has at least one distinctive token (4+ characters, not generic)."""
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb or ta == tb:
        return False
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    if not short <= long_:
        return False
    return any(len(t) >= 4 and t not in GENERIC_TOKENS for t in short)


def name_score(pin_name: str, nfhs_name: str, nfhs_truncated: bool = False) -> tuple[float, str]:
    """Similarity (0 to 100) between two already-normalized names.

    Returns ``(score, how)``, the best of:

    * ``ratio``: plain edit-distance ratio
    * ``token_sort``: word order ignored ("DINAJPUR UTTAR" vs "UTTAR DINAJPUR")
    * ``no_space``: spaces ignored ("KUSHI NAGAR" vs "KUSHINAGAR")
    * ``prefix``: the PIN name cut to the NFHS name's length, only when the NFHS
      name looks truncated
    * ``contained``: ``CONTAINMENT_SCORE`` when one name's tokens all appear in
      the other ("EAST NIMAR" vs "KHANDWA EAST NIMAR")
    """
    a, b = pin_name, nfhs_name
    if not a or not b:
        return 0.0, "ratio"
    scores = {
        "ratio": fuzz.ratio(a, b),
        "token_sort": fuzz.token_sort_ratio(a, b),
        "no_space": fuzz.ratio(a.replace(" ", ""), b.replace(" ", "")),
    }
    if nfhs_truncated and len(a) > len(b):
        scores["prefix"] = fuzz.ratio(a[: len(b)], b)
    if _contained(a, b):
        scores["contained"] = CONTAINMENT_SCORE
    how = max(scores, key=lambda k: (scores[k], k == "ratio"))
    return float(scores[how]), how


def match_within_state(
    pin_names: Iterable[str],
    candidates: Mapping[str, bool],
    threshold: float,
    margin: float,
) -> list[dict]:
    """Greedy one-to-one fuzzy matching inside one state.

    ``pin_names``: normalized PIN-directory district names still unmatched.
    ``candidates``: normalized NFHS district name -> looks_truncated flag, for
    the NFHS districts in the same state not already claimed.

    Pairs are taken in descending score. A pair is accepted when its score is
    at least ``threshold`` and beats both runners-up (the PIN name's next best
    free candidate, and the NFHS name's next best free PIN name) by at least
    ``margin``. A PIN name whose best free pair fails the margin is left
    unmatched as ambiguous rather than falling through to a worse candidate.

    Returns one dict per PIN name: ``pin_name``, ``nfhs_name`` (accepted match
    or None), ``score``, ``how``, ``best_nfhs_name``, ``best_score``,
    ``runner_up_score``, ``status`` (``accepted``, ``below_threshold``,
    ``ambiguous``, or ``no_candidates``).
    """
    pins = list(dict.fromkeys(pin_names))
    cands = dict(candidates)
    pair = {}
    for p in pins:
        for n, trunc in cands.items():
            pair[(p, n)] = name_score(p, n, trunc)

    out = {
        p: {
            "pin_name": p, "nfhs_name": None, "score": None, "how": None,
            "best_nfhs_name": None, "best_score": None, "runner_up_score": None,
            "status": "no_candidates" if not cands else "below_threshold",
        }
        for p in pins
    }
    # best candidate per PIN name, for the review file, regardless of outcome
    for p in pins:
        ranked = sorted(((s, n) for (pp, n), (s, _) in pair.items() if pp == p), reverse=True)
        if ranked:
            out[p]["best_nfhs_name"], out[p]["best_score"] = ranked[0][1], ranked[0][0]

    done_pins: set[str] = set()
    claimed: set[str] = set()
    order = sorted(pair.items(), key=lambda kv: (-kv[1][0], kv[0]))
    for (p, n), (s, how) in order:
        if p in done_pins or n in claimed:
            continue
        if s < threshold:
            break
        ru_pin = max(
            (pair[(p, n2)][0] for n2 in cands if n2 != n and n2 not in claimed), default=0.0
        )
        ru_nfhs = max(
            (pair[(p2, n)][0] for p2 in pins if p2 != p and p2 not in done_pins), default=0.0
        )
        runner_up = max(ru_pin, ru_nfhs)
        rec = out[p]
        rec["runner_up_score"] = runner_up
        done_pins.add(p)
        if s - runner_up >= margin:
            rec.update(nfhs_name=n, score=s, how=how, status="accepted")
            claimed.add(n)
        else:
            rec.update(status="ambiguous")
    return [out[p] for p in pins]


def best_candidate(pin_name: str, candidates: Mapping[str, bool]) -> tuple[Optional[str], Optional[float]]:
    """Highest-scoring candidate name and its score, or (None, None)."""
    best = (None, None)
    for n, trunc in candidates.items():
        s, _ = name_score(pin_name, n, trunc)
        if best[1] is None or s > best[1]:
            best = (n, s)
    return best


# ---------------------------------------------------------------------------
# Coordinates
# ---------------------------------------------------------------------------

EARTH_RADIUS_KM = 6371.0

#: A post office whose latitude and longitude are swapped in the raw file is
#: swapped back only when the corrected point lies within SWAP_REPAIR_KM of its
#: PIN-directory district's centre and within SWAP_NEIGHBOUR_KM of a correctly
#: geocoded office of the same district (it lands among its district's offices,
#: not in the sea off Goa). Otherwise it stays null.
SWAP_REPAIR_KM = 100.0
SWAP_NEIGHBOUR_KM = 10.0
#: Head and sub offices a district needs before its centre is their median
#: rather than the median of all its offices. Branch offices are often geocoded
#: to a same-named village in another district (WP5 uses the same rule).
CENTRE_MIN_TOWN_OFFICES = 3
#: An office is "placed" (trusted to locate a PIN or name a place) when it lies
#: within this distance of its district centre.
PLACED_OFFICE_KM = 100.0
#: A facility farther than this from its own PIN's location is not in that PIN
#: area: 99% of placed post offices lie within 98 km of their PIN's location.
#: Beyond it a conflict the city text cannot settle sets the location to null.
FACILITY_PIN_KM = 100.0
#: Beyond this distance from its PIN's location (and outside the PIN's district)
#: a facility's point is checked against the city text. Between this and
#: FACILITY_PIN_KM it is moved to the PIN only when the city text names the
#: PIN's place and not the point's, and kept otherwise: 12.5% of placed post
#: offices lie more than 40 km from their own PIN's location, so distance alone
#: is not evidence of a wrong point.
FACILITY_PIN_CHECK_KM = 40.0
#: Radius around a point whose placed head and sub offices name the place.
PLACE_NAMES_KM = 25.0
#: Nearest placed head and sub offices that vote on the district of a point.
DISTRICT_VOTE_K = 5

#: A point shared by at least this many geocoded post offices from at least
#: PLACEHOLDER_MIN_PINS different PINs is a placeholder, usually the district
#: town, not where those offices are (Bihar's Samastipur: 358 of its 376 offices,
#: from 35 PINs, on one point). See ``placeholder_points``.
PLACEHOLDER_MIN_OFFICES = 10
PLACEHOLDER_MIN_PINS = 3

#: Words dropped from office, division and city names before comparing them.
_PLACE_NOISE = re.compile(
    r"\b(B ?O|S ?O|H ?O|G ?P ?O|DIVISION|REGION|CIRCLE|HEAD OFFICE|SUB OFFICE|CITY|TOWN|"
    r"EAST|WEST|NORTH|SOUTH)\b"
)
#: City spellings in the facility pages mapped to the PIN directory's names.
#: Both spellings are tried, so an alias never removes a match.
CITY_ALIASES: dict[str, tuple[str, ...]] = {
    "BANGALORE": ("BENGALURU",), "BOMBAY": ("MUMBAI",), "CALCUTTA": ("KOLKATA",), "MADRAS": ("CHENNAI",),
    "GURGAON": ("GURUGRAM",), "TRIVANDRUM": ("THIRUVANANTHAPURAM",), "POONA": ("PUNE",),
    "BARODA": ("VADODARA",), "MYSORE": ("MYSURU",), "MANGALORE": ("MANGALURU",), "BELGAUM": ("BELAGAVI",),
    "HUBLI": ("HUBBALLI", "DHARWAD"), "CALICUT": ("KOZHIKODE",), "COCHIN": ("ERNAKULAM",),
    "KOCHI": ("ERNAKULAM",), "VIZAG": ("VISAKHAPATNAM",), "PONDICHERRY": ("PUDUCHERRY",),
    "ALLAHABAD": ("PRAYAGRAJ",), "BENARES": ("VARANASI",), "BANARAS": ("VARANASI",),
    "TRICHY": ("TIRUCHIRAPPALLI",), "TIRUCHIRAPALLI": ("TIRUCHIRAPPALLI",), "TUMKUR": ("TUMAKURU",),
    "BELLARY": ("BALLARI",), "GULBARGA": ("KALABURAGI",), "BIJAPUR": ("VIJAYAPURA",),
    "SHIMOGA": ("SHIVAMOGGA",), "CHIKMAGALUR": ("CHIKKAMAGALURU",), "DAVANGERE": ("DAVANAGERE",),
    "TUTICORIN": ("THOOTHUKUDI", "TUTICORIN"), "FAIZABAD": ("AYODHYA",), "MEWAT": ("NUH",),
    "NOIDA": ("GAUTAM BUDDHA NAGAR",), "GREATER NOIDA": ("GAUTAM BUDDHA NAGAR",),
    "MOHALI": ("S A S NAGAR",), "NAVI MUMBAI": ("THANE",), "SECUNDERABAD": ("HYDERABAD",),
    "NEW DELHI": ("DELHI",), "VIJAYAWADA": ("KRISHNA", "NTR"), "GUWAHATI": ("KAMRUP METRO",),
}
#: City values that name no place.
_CITY_STOP = frozenset({"", "NA", "NULL", "NONE", "INDIA"})


def haversine_km(lat1, lon1, lat2, lon2) -> np.ndarray:
    """Great-circle distance in km between degree coordinates, vectorized. NaN in gives NaN out."""
    lat1, lon1, lat2, lon2 = (np.radians(np.asarray(x, dtype="float64")) for x in (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def in_india_bbox(lat, lon) -> np.ndarray:
    """Vectorized ``vf.io.in_india``: True inside the loose India box, False for NaN."""
    b = INDIA_BBOX
    lat = np.asarray(lat, dtype="float64")
    lon = np.asarray(lon, dtype="float64")
    with np.errstate(invalid="ignore"):
        return (lat >= b["lat_min"]) & (lat <= b["lat_max"]) & (lon >= b["lon_min"]) & (lon <= b["lon_max"])


def pin_district_group(state, district) -> pd.Series:
    """``"STATE|DISTRICT"`` as spelled in the PIN directory (stripped), None when either part is 'NA'."""
    s = pd.Series(state).astype(object).fillna("NA").astype(str).str.strip()
    d = pd.Series(district).astype(object).fillna("NA").astype(str).str.strip()
    key = s + "|" + d
    return key.where((s.str.upper() != "NA") & (d.str.upper() != "NA"))


def group_centres(lat, lon, is_town, group, min_town: int = CENTRE_MIN_TOWN_OFFICES) -> pd.DataFrame:
    """Median point of each group of post offices.

    The median of the group's head and sub offices (``is_town``) when it has at
    least ``min_town`` geocoded ones, else the median of all its geocoded
    offices. Rows with a missing coordinate or group are ignored. Returns a frame
    indexed by group with ``lat``, ``lon`` and ``basis`` (``head_sub`` or ``all``).
    """
    df = pd.DataFrame({
        "lat": np.asarray(lat, dtype="float64"),
        "lon": np.asarray(lon, dtype="float64"),
        "town": np.asarray(is_town, dtype=bool),
        "g": np.asarray(group, dtype=object),
    })
    df = df[df["lat"].notna() & df["lon"].notna() & df["g"].notna()]
    out = df.groupby("g")[["lat", "lon"]].median()
    out["basis"] = "all"
    t = df[df["town"]]
    n_town = t.groupby("g").size().reindex(out.index, fill_value=0)
    use = n_town.index[n_town >= min_town]
    if len(use):
        out.loc[use, ["lat", "lon"]] = t.groupby("g")[["lat", "lon"]].median().loc[use].to_numpy()
        out.loc[use, "basis"] = "head_sub"
    out.index.name = "group"
    return out


def post_office_coords(raw: pd.DataFrame, repair_km: float = SWAP_REPAIR_KM,
                       neighbour_km: float = SWAP_NEIGHBOUR_KM) -> tuple[pd.DataFrame, dict]:
    """Clean coordinates for every row of the raw PIN directory (``vf.io.read_pincodes_raw``).

    Returns a frame on the raw index with ``lat``, ``lon`` (null unless valid),
    ``coord_valid`` and ``coord_swap_repaired``, plus a stats dict.

    A coordinate is valid when both parts are numeric and inside India's box. A
    row whose two numbers fall inside the box only when exchanged has latitude
    and longitude swapped in the raw file. It is swapped back when the corrected
    point lies within ``repair_km`` of its PIN-directory district's centre
    (``group_centres`` over the valid rows) and within ``neighbour_km`` of a valid
    office of the same district. It stays null otherwise: many swapped Goa rows
    land in the sea, and a row with district 'NA' has no centre.
    """
    from sklearn.neighbors import BallTree

    lat = pd.Series(raw["latitude"].map(to_float), index=raw.index, dtype="float64")
    lon = pd.Series(raw["longitude"].map(to_float), index=raw.index, dtype="float64")
    numeric = lat.notna() & lon.notna()
    valid = pd.Series(in_india_bbox(lat, lon), index=raw.index)
    swapped = numeric & ~valid & pd.Series(in_india_bbox(lon, lat), index=raw.index)
    group = pin_district_group(raw["statename"], raw["district"]).set_axis(raw.index)
    is_town = raw["officetype"].astype(str).str.strip() != "BO"
    centres = group_centres(lat.where(valid), lon.where(valid), is_town, group)

    sw = swapped[swapped].index
    c = centres.reindex(group.loc[sw].to_numpy())
    d = pd.Series(haversine_km(lon.loc[sw], lat.loc[sw], c["lat"], c["lon"]), index=sw)
    # does the corrected point land within neighbour_km of a valid office of its own district?
    vidx = valid[valid].index
    tree = BallTree(np.radians(np.column_stack([lat.loc[vidx], lon.loc[vidx]])), metric="haversine")
    hits = tree.query_radius(np.radians(np.column_stack([lon.loc[sw], lat.loc[sw]])),
                             r=neighbour_km / EARTH_RADIUS_KM) if len(sw) else []
    vgroup = group.loc[vidx].to_numpy(dtype=object)
    among = pd.Series([g is not None and g == g and bool((vgroup[h] == g).any())
                       for g, h in zip(group.loc[sw].to_numpy(dtype=object), hits)], index=sw, dtype=bool)
    fixed = d.index[(d <= repair_km) & among]

    out = pd.DataFrame({"lat": lat.where(valid), "lon": lon.where(valid)}, index=raw.index)
    out.loc[fixed, "lat"] = lon.loc[fixed]
    out.loc[fixed, "lon"] = lat.loc[fixed]
    out["coord_swap_repaired"] = out.index.isin(fixed)
    out["coord_valid"] = (valid | out["coord_swap_repaired"]).astype(bool)

    def by_group(idx) -> dict:
        vc = group.loc[idx].fillna("NA|NA").value_counts()
        return {str(k): int(v) for k, v in vc.items()}

    left = sw.difference(fixed)
    stats = {
        "coord_swapped_detected": int(len(sw)),
        "coord_swapped_repaired": int(len(fixed)),
        "coord_swapped_left_null": int(len(left)),
        "coord_swapped_left_null_no_centre": int(d.loc[left].isna().sum()),
        "coord_swapped_left_null_far_from_centre": int((d.loc[left] > repair_km).sum()),
        "coord_swapped_left_null_no_neighbour": int(((d.loc[left] <= repair_km) & ~among.loc[left]).sum()),
        "swap_repair_rule": (
            f"lat and lon exchanged back when the corrected point is within {repair_km:.0f} km of the centre "
            "of the office's PIN-directory district (median of its valid head and sub offices when it has "
            f"{CENTRE_MIN_TOWN_OFFICES} or more, else of all its valid offices) and within {neighbour_km:.0f} km "
            "of a valid office of the same district; otherwise left null"
        ),
        "coord_swapped_repaired_by_district": by_group(fixed),
        "coord_swapped_left_null_by_district": by_group(left),
        "coord_swapped_repaired_km_to_centre_median": float(d.loc[fixed].median()) if len(fixed) else None,
    }
    return out[["lat", "lon", "coord_valid", "coord_swap_repaired"]], stats


def placeholder_points(lat, lon, pin, min_offices: int = PLACEHOLDER_MIN_OFFICES,
                       min_pins: int = PLACEHOLDER_MIN_PINS) -> pd.DataFrame:
    """Post offices that sit on a shared placeholder point.

    Inputs are aligned sequences over the offices to compare (pass the geocoded
    ones): ``lat``, ``lon`` and ``pin``. Returns a frame on the same positions
    (RangeIndex) with ``n_at_point`` (offices on exactly the same point, itself
    included), ``pins_at_point`` (distinct PINs among them) and ``placeholder``
    (at least ``min_offices`` offices from at least ``min_pins`` PINs). Different
    PINs are different delivery areas, so a point standing for several of them
    is not where they are. Rows with a missing coordinate get 0, 0, False.
    """
    df = pd.DataFrame({
        "lat": np.asarray(lat, dtype="float64"),
        "lon": np.asarray(lon, dtype="float64"),
        "pin": np.asarray(pin, dtype=object),
    })
    ok = df["lat"].notna() & df["lon"].notna()
    g = df[ok].groupby(["lat", "lon"])["pin"]
    out = pd.DataFrame(index=df.index)
    out["n_at_point"] = g.transform("size").reindex(df.index).fillna(0).astype("int64")
    out["pins_at_point"] = g.transform("nunique").reindex(df.index).fillna(0).astype("int64")
    out["placeholder"] = (out["n_at_point"] >= min_offices) & (out["pins_at_point"] >= min_pins)
    return out


def place_key(s) -> str:
    """Comparable form of a place, office or division name: ``norm_name`` without
    office-type and direction words ("Agra Cantt S.O" -> "AGRA CANTT")."""
    n = _PLACE_NOISE.sub(" ", norm_name(s))
    return re.sub(r"\s+", " ", n).strip()


def city_keys(city) -> set[str]:
    """``place_key`` of a city value plus its known aliases; empty when it names no place."""
    k = place_key(city)
    if len(k) < 3 or k in _CITY_STOP:
        return set()
    return {k, *CITY_ALIASES.get(k, ())}


def names_match(keys: Iterable[str], names: Iterable[str]) -> bool:
    """True when any key equals a name, appears in it as whole words (keys of 4+
    characters), contains it as whole words (names of 4+ characters), or is
    within edit-distance ratio 88 of it (both 5+ characters)."""
    keys = [k for k in keys if k]
    if not keys:
        return False
    for n in names:
        if not n:
            continue
        for k in keys:
            if k == n:
                return True
            if len(k) >= 4 and re.search(rf"\b{re.escape(k)}\b", n):
                return True
            if len(n) >= 4 and re.search(rf"\b{re.escape(n)}\b", k):
                return True
            if min(len(k), len(n)) >= 5 and fuzz.ratio(k, n) >= 88:
                return True
    return False


class PinReference:
    """Where each PIN is, and which places a point or a PIN belongs to.

    Built from the raw PIN directory and its cleaned coordinates
    (``post_office_coords``). District centres follow ``group_centres``. An office
    is *placed* when it lies within ``PLACED_OFFICE_KM`` of its district centre;
    only placed offices locate PINs and name places, so a PIN whose own offices
    are geocoded to another state does not drag its location with them.

    A PIN's location (``pins``) is the median of its placed head and sub
    offices (basis ``head_sub``), else of its placed offices of any type
    (``branch``), else the centre of its main district (``district_centre``).
    Its main district is the one most of its offices belong to.
    """

    def __init__(self, raw: pd.DataFrame, coords: pd.DataFrame,
                 placed_km: float = PLACED_OFFICE_KM):
        from sklearn.neighbors import BallTree

        po = pd.DataFrame({
            "pin": raw["pincode"].astype(str).str.strip().to_numpy(),
            "town": (raw["officetype"].astype(str).str.strip() != "BO").to_numpy(),
            "group": pin_district_group(raw["statename"], raw["district"]).to_numpy(),
            "office": raw["officename"].map(place_key).to_numpy(),
            "district": raw["district"].map(place_key).to_numpy(),
            "division": raw["divisionname"].map(place_key).to_numpy(),
            "lat": coords["lat"].to_numpy(dtype="float64"),
            "lon": coords["lon"].to_numpy(dtype="float64"),
        })
        self.centres = group_centres(po["lat"], po["lon"], po["town"], po["group"])
        c = self.centres.reindex(po["group"].to_numpy())
        po["centre_km"] = haversine_km(po["lat"], po["lon"], c["lat"], c["lon"])
        po["placed"] = po["centre_km"] <= placed_km
        self.placed_km = placed_km

        main = (po.dropna(subset=["group"]).groupby(["pin", "group"]).size().reset_index(name="n")
                .sort_values(["pin", "n", "group"], ascending=[True, False, True])
                .drop_duplicates("pin").set_index("pin")["group"])
        pins = pd.DataFrame(index=pd.Index(sorted(po["pin"].unique()), name="pin"))
        pins["district"] = main.reindex(pins.index)
        pins["lat"] = np.nan
        pins["lon"] = np.nan
        pins["basis"] = None
        placed = po[po["placed"]]
        for basis, src in (("head_sub", placed[placed["town"]]), ("branch", placed)):
            med = src.groupby("pin")[["lat", "lon"]].median()
            m = pins["lat"].isna() & pins.index.isin(med.index)
            pins.loc[m, ["lat", "lon"]] = med.reindex(pins.index[m]).to_numpy()
            pins.loc[m, "basis"] = basis
        m = pins["lat"].isna() & pins["district"].notna()
        dc = self.centres.reindex(pins.loc[m, "district"].to_numpy())
        pins.loc[m, ["lat", "lon"]] = dc[["lat", "lon"]].to_numpy()
        pins.loc[m & pins["lat"].notna(), "basis"] = "district_centre"
        self.pins = pins
        self.offices = po

        # a PIN's own names come from the text of all its offices, geocoded or not
        own = po.groupby("pin")[["office", "district", "division"]].agg(lambda s: set(s.dropna()))
        self._pin_names = {p: set().union(r.office, r.district, r.division) for p, r in own.iterrows()}
        town = po[po["placed"] & po["town"]].reset_index(drop=True)
        self._town = town
        self._tree = BallTree(np.radians(town[["lat", "lon"]].to_numpy()), metric="haversine")

    def location(self, pins) -> pd.DataFrame:
        """``lat``, ``lon``, ``basis``, ``district`` for each PIN (NaN/None when unknown), aligned to ``pins``."""
        return self.pins.reindex(pd.Index(pins, dtype=object))

    def _nearby(self, lat: float, lon: float, km: float) -> pd.DataFrame:
        idx = self._tree.query_radius(np.radians([[lat, lon]]), r=km / EARTH_RADIUS_KM)[0]
        if len(idx) == 0:
            idx = self._tree.query(np.radians([[lat, lon]]), k=1)[1][0]
        return self._town.iloc[idx]

    def names_near(self, lat: float, lon: float, km: float = PLACE_NAMES_KM) -> set[str]:
        """Office, district and division names of the placed head and sub offices
        within ``km`` of a point (the nearest one when none is that close)."""
        sub = self._nearby(lat, lon, km)
        return set(sub["office"]) | set(sub["district"]) | set(sub["division"])

    def names_of_pin(self, pin) -> set[str]:
        """Office, district and division names of all of a PIN's offices."""
        return set(self._pin_names.get(pin, set()))

    def district_at(self, lat: float, lon: float, k: int = DISTRICT_VOTE_K) -> Optional[str]:
        """Majority PIN-directory district (``STATE|DISTRICT``) of the ``k`` nearest
        placed head and sub offices; ties go to the nearer office."""
        idx = self._tree.query(np.radians([[lat, lon]]), k=k)[1][0]
        votes = Counter(g for g in self._town["group"].to_numpy()[idx] if isinstance(g, str))
        return votes.most_common(1)[0][0] if votes else None


#: Values of ``coord_check`` written by ``check_facility_locations``.
#: "farther" means more than FACILITY_PIN_CHECK_KM (40 km) from the PIN's location.
COORD_CHECKS: dict[str, str] = {
    "ok": "within FACILITY_PIN_CHECK_KM (40 km) of its PIN's location",
    "ok_same_district": "farther, but in the PIN's main district by the nearest placed head and sub offices",
    "moved_to_pin": "farther and outside the PIN's district; the city text names the PIN's place and not the "
                    "point's, so lat/lon are the PIN's location",
    "kept_city_matches_coords": "farther and outside the PIN's district; the city text names the point's place "
                                "and not the PIN's, so the PIN is wrong",
    "kept_unresolved": "40 to 100 km (FACILITY_PIN_CHECK_KM to FACILITY_PIN_KM) and outside the PIN's district; the "
                       "city text names both places or neither, so the point is kept",
    "excluded_unresolved": "more than FACILITY_PIN_KM (100 km) and outside the PIN's district; the city text names "
                           "both places or neither, so lat/lon are set to null",
    "no_pin_check": "coordinates kept unchecked: no PIN, or a PIN the directory cannot locate",
    "missing": "no usable coordinates in the scraped row (missing, malformed or outside India)",
}


def check_facility_locations(lat, lon, pin, city, ref: PinReference,
                             far_km: float = FACILITY_PIN_KM,
                             check_km: float = FACILITY_PIN_CHECK_KM) -> pd.DataFrame:
    """Check facility coordinates against their own PIN; settle conflicts with the city text.

    Inputs are aligned sequences: scraped ``lat``, ``lon`` (NaN when missing or
    outside India), ``pin`` (6-digit string or None) and ``city`` (raw text).
    Returns a frame on the same positions with ``coord_check`` (see
    ``COORD_CHECKS``), ``coord_pin_km`` (km from the scraped point to the PIN's
    location, null without both), ``lat``, ``lon`` (the location to use, null
    when excluded or missing).

    A point more than ``check_km`` from its PIN's location and outside the PIN's
    main district is a conflict. The city text settles it the same way at any
    distance: names only the PIN's place, moved to the PIN; names only the
    point's place, kept. When it names both or neither the point is kept up to
    ``far_km`` (one PIN's post offices can spread that far) and set to null
    beyond.
    """
    lat = np.asarray(lat, dtype="float64")
    lon = np.asarray(lon, dtype="float64")
    pins = pd.Series(list(pin), dtype=object)
    cities = list(city)
    loc = ref.location(pins.where(pins.notna(), None).tolist())
    plat, plon = loc["lat"].to_numpy(dtype="float64"), loc["lon"].to_numpy(dtype="float64")
    d = haversine_km(lat, lon, plat, plon)
    n = len(lat)
    check = np.full(n, "ok", dtype=object)
    out_lat, out_lon = lat.copy(), lon.copy()

    has_xy = ~np.isnan(lat) & ~np.isnan(lon)
    has_pin = ~np.isnan(plat)
    check[~has_xy] = "missing"
    check[has_xy & ~has_pin] = "no_pin_check"
    for i in np.flatnonzero(has_xy & has_pin & (d > check_km)):
        if ref.district_at(lat[i], lon[i]) == loc["district"].iloc[i]:
            check[i] = "ok_same_district"
            continue
        keys = city_keys(cities[i])
        on_pin = names_match(keys, ref.names_of_pin(pins.iloc[i]) | ref.names_near(plat[i], plon[i]))
        on_xy = names_match(keys, ref.names_near(lat[i], lon[i]))
        if on_pin and not on_xy:
            check[i] = "moved_to_pin"
            out_lat[i], out_lon[i] = plat[i], plon[i]
        elif on_xy and not on_pin:
            check[i] = "kept_city_matches_coords"
        elif d[i] <= far_km:
            check[i] = "kept_unresolved"
        else:
            check[i] = "excluded_unresolved"
            out_lat[i] = out_lon[i] = np.nan
    return pd.DataFrame({
        "coord_check": check,
        "coord_pin_km": np.where(has_xy & has_pin, d, np.nan),
        "lat": out_lat,
        "lon": out_lon,
    })
