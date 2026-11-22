"""Location resolver (WP2 Part B, D029).

``resolve(query, state=None)`` turns what a user types as a place into a point,
a precision radius and a plain-language label, or into a short list of
candidates when the name is ambiguous. It reads only ``silver/places.parquet``
(built by ``scripts/wp2_places.py``).

Order:

1. Coordinates: two numbers, in either order, when one fits India's latitude
   range and the other its longitude range. A point outside India's box,
   outside India's outline by more than ``BORDER_SLACK_KM`` (Natural Earth,
   see ``india_border_km``), or, north of ``DISTANCE_RULE_NORTH_OF``, more
   than ``OUTSIDE_KM`` from every Indian post office and PIN point, is
   ``outside_india``.
2. PIN: a 6-digit token ("302001", "302 001"). An unknown PIN is said in
   ``how`` and the rest of the text is resolved instead.
3. Name. First an exact match on the whole text as typed ("New Delhi", "North
   Goa", "East District" are district names). Then the state ("Aurangabad,
   Bihar", "Hamirpur UP") and an optional district after a comma ("Singhwara,
   Darbhanga", "Singhwara, Darbhanga, Bihar") are parsed off the end, unless
   that would leave only a bare word such as North or New: exact match on
   district, then city, then post office name (with the old-name aliases in
   ``vf.geo.CITY_ALIASES``); then fuzzy, districts and cities before offices,
   within the given state first and nationally after.

When the best candidates of a step lie in different districts and score within
``AMBIGUOUS_MARGIN`` points of each other, the answer is ``ambiguous`` with up
to ``MAX_CANDIDATES`` candidates. It never picks silently. Two more cases come
back as a choice (``ambiguous``, even with one candidate) rather than ``ok``:

* a fuzzy match where only part of the text matched a name and the other words
  match nothing ("Hyderabad, Sindh"); a district name inside a longer village
  name does not win over a close spelling of an office ("Rampur Chourai")
* a fuzzy match found only outside the state the user gave or typed.

The state filter applies to every name step. A PIN or a coordinate pair is
never filtered out by the state: it is resolved, and a mismatch is said in ``how``.
"""
from __future__ import annotations

import base64
import re
import zlib
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from rapidfuzz import fuzz, process

from . import geo, paths

#: Minimum fuzzy score (0 to 100, vf.geo.name_score) for a name match.
FUZZY_THRESHOLD = 85.0
#: Candidates in different districts within this many points are ambiguous.
AMBIGUOUS_MARGIN = 3.0
MAX_CANDIDATES = 5
#: A typed point farther than this from every post office and PIN point is not in India.
OUTSIDE_KM = 50.0
#: A typed point outside India's outline by more than this is outside India. The outline is
#: simplified to about 0.5 km; Indian post office points on the coast and border sit up to a
#: few km outside it, the nearest foreign cities checked (Biratnagar, Rajshahi, Kasur) about 7.
BORDER_SLACK_KM = 3.0
#: India's outline includes Pakistan- and China-administered Kashmir. North of this latitude a point
#: inside the outline must also be within OUTSIDE_KM of an Indian post office or PIN point. Elsewhere
#: the distance rule is not applied: islands such as Kavaratti and Minicoy are far from any usable point.
DISTANCE_RULE_NORTH_OF = 32.5
#: Nearest PIN points that vote on the district of a typed point.
DISTRICT_VOTE_K = 5
#: A district or city name this close to the query competes with an exact
#: post-office-only match (the answer becomes ambiguous, never a silent pick).
CROSS_TIER_MIN = 90.0
#: Offices pre-selected by plain edit ratio before the full name_score rescoring.
FUZZY_PRESELECT = 40
#: City centre for a district named after its town (D046 fix): the town's head
#: post office point, with a precision from the spread of the town's PIN areas
#: (same district, same first four PIN digits as the head office) at this
#: quantile of their distance from it, and never below CITY_MIN_PRECISION_KM.
CITY_PIN_PREFIX = 4
CITY_PRECISION_Q = 0.75
CITY_MIN_PRECISION_KM = 3.0
#: The town point is used only when its precision is under this: a looser "city"
#: (Barmer, about 204 km across) is no tighter in practice, so the district wording stays.
CITY_MAX_PRECISION_KM = 15.0
#: A typed word that asks for the district itself, not its town.
_DISTRICT_WORD = re.compile(r"\b(DISTRICT|DISTT|DIST|ZILA|JILA)\b")

KIND_ORDER = {"district": 0, "city": 1, "post_office": 2, "pin": 3}

#: Words dropped from a query before matching. "nagar" is kept: it is part of
#: many names (Gautam Buddha Nagar, Ahmednagar).
_FILLER = re.compile(
    r"\b(DISTRICT|DISTT|DIST|CITY|TOWN|VILLAGE|VILL|PIN ?CODE|PINCODE|PIN|POSTAL CODE|NEAR|INDIA|"
    r"TEHSIL|TALUK|TALUKA|BLOCK|HOSPITAL|HOSPITALS|CLINIC|NURSING HOME|ME|MY|HERE|NEARBY|AROUND)\b"
)

#: State abbreviations and old names, in vf.geo.norm_name form, to PIN-directory spelling.
STATE_ALIASES: dict[str, str] = {
    "UP": "UTTAR PRADESH", "U P": "UTTAR PRADESH", "MP": "MADHYA PRADESH", "M P": "MADHYA PRADESH",
    "HP": "HIMACHAL PRADESH", "H P": "HIMACHAL PRADESH", "AP": "ANDHRA PRADESH", "A P": "ANDHRA PRADESH",
    "TN": "TAMIL NADU", "WB": "WEST BENGAL", "MH": "MAHARASHTRA", "RJ": "RAJASTHAN", "GJ": "GUJARAT",
    "KA": "KARNATAKA", "KL": "KERALA", "TS": "TELANGANA", "TG": "TELANGANA", "JK": "JAMMU AND KASHMIR",
    "J AND K": "JAMMU AND KASHMIR", "JAMMU KASHMIR": "JAMMU AND KASHMIR", "HR": "HARYANA", "PB": "PUNJAB",
    "BR": "BIHAR", "CG": "CHHATTISGARH", "JH": "JHARKHAND", "UK": "UTTARAKHAND",
    "ORISSA": "ODISHA", "PONDICHERRY": "PUDUCHERRY", "UTTARANCHAL": "UTTARAKHAND",
    "NCT OF DELHI": "DELHI", "NEW DELHI": None, "MAHARASTRA": "MAHARASHTRA", "CHATTISGARH": "CHHATTISGARH",
    "TAMILNADU": "TAMIL NADU", "ANDAMAN": "ANDAMAN AND NICOBAR ISLANDS",
    "DADRA AND NAGAR HAVELI": "THE DADRA AND NAGAR HAVELI AND DAMAN AND DIU",
    "DAMAN AND DIU": "THE DADRA AND NAGAR HAVELI AND DAMAN AND DIU",
}

_NUM = r"[-+]?\d{1,3}(?:\.\d+)?"
_COORD = re.compile(
    rf"^\s*\(?\s*({_NUM})\s*°?\s*([NSEW])?\s*[,;/\s]\s*({_NUM})\s*°?\s*([NSEW])?\s*\)?\s*$", re.I
)
_PIN = re.compile(r"(?<!\d)(\d{3})\s?(\d{3})(?!\d)")
_SMALL_WORDS = {"And", "Of", "The"}
#: Words that do not name a place on their own. Text cut down to only these keeps its longer form
#: ("North Goa" is not "North" in Goa; "East District" is not "East").
_BARE = frozenset(geo.GENERIC_TOKENS | {"NEW", "OLD", "GREATER"})


def _is_bare(q: str) -> bool:
    toks = q.split()
    return bool(toks) and all(t in _BARE for t in toks)


@dataclass
class Resolution:
    status: str                       # ok, ambiguous, not_found, outside_india
    lat: Optional[float] = None
    lon: Optional[float] = None
    precision_km: Optional[float] = None
    kind: Optional[str] = None        # coords, pin, district, city, post_office
    label: str = ""
    how: str = ""
    candidates: list = field(default_factory=list)
    state: Optional[str] = None
    district: Optional[str] = None
    place_id: Optional[str] = None
    query: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def nice(s: Optional[str]) -> str:
    """Display form of a PIN-directory name: 'UTTAR PRADESH' -> 'Uttar Pradesh'."""
    if not s:
        return ""
    words = str(s).strip().title().split()
    if words and words[0] == "The" and len(words) > 1:
        words = words[1:]
    return " ".join(w.lower() if (i and w in _SMALL_WORDS) else w for i, w in enumerate(words))


def _spelling(a: str, b: str) -> float:
    """vf.geo.name_score without containment: plain, word-order-free and space-free edit ratios."""
    if not a or not b:
        return 0.0
    return float(max(fuzz.ratio(a, b), fuzz.token_sort_ratio(a, b),
                     fuzz.ratio(a.replace(" ", ""), b.replace(" ", ""))))


def _clean_query(text: str) -> str:
    return geo.norm_name(_FILLER.sub(" ", geo.norm_name(text)))


class Resolver:
    """Loads the gazetteer once; ``resolve`` answers in milliseconds."""

    def __init__(self, places: pd.DataFrame | None = None, path: Path | None = None):
        from sklearn.neighbors import BallTree

        if places is None:
            places = pd.read_parquet(path or paths.silver_dir() / "places.parquet")
        p = places.reset_index(drop=True).copy()
        p["group"] = p["state"] + "|" + p["district"]
        self._district_norm = p["district"].map(geo.norm_name).to_numpy(dtype=object)
        p["note"] = p["note"].fillna("").astype(str)
        self.places = p
        self.pins = p[p["kind"] == "pin"].set_index("pin")

        self.states = sorted(p["state"].unique())
        self._state_forms: dict[str, Optional[str]] = {}
        for s in self.states:
            n = geo.norm_name(s)
            self._state_forms[n] = s
            if n.startswith("THE "):
                self._state_forms[n[4:]] = s
        for alias, target in STATE_ALIASES.items():
            if target is None:
                self._state_forms.pop(alias, None)
            elif target in self.states:
                self._state_forms.setdefault(alias, target)
        self._district_forms = set(p.loc[p["kind"] == "district", "name_norm"])

        # exact lookup tables: name_norm -> row positions, per kind
        self._exact: dict[str, dict[str, np.ndarray]] = {}
        for kind in ("district", "city", "post_office"):
            sub = p[p["kind"] == kind]
            self._exact[kind] = {k: v.to_numpy() for k, v in sub.groupby("name_norm").groups.items()}
        # fuzzy choice lists: distinct names per kind
        self._names = {k: list(v.keys()) for k, v in self._exact.items()}
        self._big = self._names["district"] + self._names["city"]
        # point tree for the outside-India check and the district vote
        pts = p[p["kind"].isin(["pin", "post_office"])]
        self._pts_tree = BallTree(np.radians(pts[["lat", "lon"]].to_numpy()), metric="haversine")
        self._pin_rows = p[p["kind"] == "pin"].reset_index(drop=True)
        self._pin_tree = BallTree(np.radians(self._pin_rows[["lat", "lon"]].to_numpy()), metric="haversine")
        self._city_points = self._build_city_points(p)

    def _build_city_points(self, p: pd.DataFrame) -> dict:
        """``group -> {lat, lon, precision_km, pin, office}`` for districts named after their town:
        a post office of the district carries the district's name (or an old name of it in
        vf.geo.CITY_ALIASES). The point is that office's PIN point (the lowest such PIN, the head
        office's), and the precision the spread of the town's PIN areas. Used only when it is
        tighter than the district centre."""
        old_names: dict[str, list] = {}
        for old, new in geo.CITY_ALIASES.items():
            for n in new:
                old_names.setdefault(n, []).append(old)
        dist = p[p["kind"] == "district"]
        wanted = set(dist["name_norm"]) | {o for n in dist["name_norm"] for o in old_names.get(n, [])}
        offices = p[(p["kind"] == "post_office") & p["pin"].notna() & p["name_norm"].isin(wanted)]
        heads = {}  # (group, name) -> (lowest pin, office name)
        for g, n, pin, name in zip(offices["group"], offices["name_norm"], offices["pin"].astype(str), offices["name"]):
            if (g, n) not in heads or pin < heads[(g, n)][0]:
                heads[(g, n)] = (pin, str(name))
        pins = p[(p["kind"] == "pin") & p["pin"].notna()]
        pin_pt = pins.drop_duplicates("pin").set_index("pin")[["lat", "lon", "precision_km"]]
        pins_by_group = {g: sub for g, sub in pins.groupby("group")}
        out = {}
        for r in dist.itertuples(index=False):
            hit = None
            for name in [r.name_norm, *old_names.get(r.name_norm, [])]:
                if (r.group, name) in heads:
                    hit = heads[(r.group, name)]
                    break
            if hit is None:
                continue
            head = hit[0]
            if head not in pin_pt.index:
                continue
            lat0, lon0 = float(pin_pt.at[head, "lat"]), float(pin_pt.at[head, "lon"])
            town = pins_by_group.get(r.group)
            if town is not None:
                town = town[town["pin"].astype(str).str[:CITY_PIN_PREFIX] == head[:CITY_PIN_PREFIX]]
            if town is None or len(town) < 2:
                # one PIN area: as good as that PIN's own spread
                spread, n_pins = float(pin_pt.at[head, "precision_km"]), 1
            else:
                km = geo.haversine_km(lat0, lon0, town["lat"].to_numpy(float), town["lon"].to_numpy(float))
                spread, n_pins = float(np.quantile(km, CITY_PRECISION_Q)), int(len(town))
            prec = max(CITY_MIN_PRECISION_KM, spread)
            if not prec < float(r.precision_km) or not prec < CITY_MAX_PRECISION_KM:
                continue
            out[r.group] = {"lat": lat0, "lon": lon0, "precision_km": prec, "pin": head,
                            "office": hit[1], "n_pins": n_pins}
        return out

    def _wants_city(self, query: str) -> bool:
        return not _DISTRICT_WORD.search(geo.norm_name(query or ""))

    def city_point(self, i: int) -> Optional[dict]:
        """The town point for district row ``i``, or None (``_build_city_points``)."""
        r = self._row(i)
        return self._city_points.get(r["group"]) if r["kind"] == "district" else None

    # ------------------------------------------------------------------ parsing

    def parse_state(self, text: str) -> tuple[str, Optional[str]]:
        """Strip a trailing state name or abbreviation; returns (rest, state).
        A query that is only a state name is left alone."""
        n = geo.norm_name(text)
        parts = n.split()
        for k in range(min(len(parts) - 1, 7), 0, -1):
            tail = " ".join(parts[-k:])
            if tail in self._state_forms:
                rest = " ".join(parts[:-k])
                if rest:
                    return rest, self._state_forms[tail]
        return n, None

    def normalize_state(self, state: Optional[str]) -> Optional[str]:
        if not state:
            return None
        n = geo.norm_name(state)
        if n in self._state_forms:
            return self._state_forms[n]
        best = process.extractOne(n, list(self._state_forms), scorer=fuzz.ratio, score_cutoff=85)
        return self._state_forms[best[0]] if best else None

    # ------------------------------------------------------------------ helpers

    def _row(self, i: int) -> pd.Series:
        return self.places.iloc[int(i)]

    def _label(self, r: pd.Series) -> str:
        st, di = nice(r["state"]), nice(r["district"])
        if r["kind"] == "pin":
            note = r["note"] or ""
            where = f"mostly in {di}" if "mostly in" in note else di
            return f"PIN {r['pin']}, {where}, {st}"
        if r["kind"] == "district":
            return f"Centre of {di} district, {st}, about {2 * r['precision_km']:.0f} km across"
        if r["kind"] == "city":
            return f"{r['name']}, {di} district, {st}"
        return f"{r['name']} (post office area, PIN {r['pin']}), {di} district, {st}"

    def _city_label(self, r: pd.Series, c: dict) -> str:
        town = nice(r["name"]) if str(r["name"]).isupper() else str(r["name"])
        return (f"{town} city (centre at the head post office, PIN {c['pin']}), {nice(r['district'])} district, "
                f"{nice(r['state'])}, about {2 * c['precision_km']:.0f} km across")

    def _cand(self, i: int, score: float, query: Optional[str] = None) -> dict:
        r = self._row(i)
        out = {"place_id": r["place_id"], "kind": r["kind"], "name": r["name"], "state": r["state"],
               "district": r["district"], "lat": float(r["lat"]), "lon": float(r["lon"]),
               "precision_km": float(r["precision_km"]), "label": self._label(r), "score": float(score)}
        c = self.city_point(i) if self._wants_city(query or "") else None
        if c is not None:
            out.update(lat=c["lat"], lon=c["lon"], precision_km=float(c["precision_km"]), label=self._city_label(r, c))
        return out

    def _ok(self, i: int, how: str, query: str) -> Resolution:
        r = self._row(i)
        c = self.city_point(i) if self._wants_city(query) else None
        if c is not None:
            # a district named after its town: a user typing "Jaipur" means the city (D046)
            how = how.replace(" This is the district centre, not an exact location.", "")
            how += (f" The district is named after its town, so the point is the town's head post office "
                    f"({c['office']}, PIN {c['pin']}), not the district centre; type "
                    f"\"{nice(r['district'])} district\" for the district centre.")
            return Resolution("ok", c["lat"], c["lon"], float(c["precision_km"]), r["kind"], self._city_label(r, c),
                              how, [], r["state"], r["district"], r["place_id"], query)
        return Resolution("ok", float(r["lat"]), float(r["lon"]), float(r["precision_km"]), r["kind"],
                          self._label(r), how, [], r["state"], r["district"], r["place_id"], query)

    def _filter(self, idx, state: Optional[str], district: Optional[str]) -> np.ndarray:
        idx = np.asarray(idx, dtype=int)
        if state is not None:
            idx = idx[self.places["state"].to_numpy()[idx] == state]
        if district is not None:
            idx = idx[self._district_norm[idx] == district]
        return idx

    def _decide(self, scored: list[tuple[int, float]], step: str, query: str, choose: bool = False,
                choose_how: str = "", prefer_specific: bool = False) -> Optional[Resolution]:
        """Pick one place or return ambiguous. ``scored``: (row, score) pairs. ``choose`` returns the
        candidates as a choice even when they all lie in one district."""
        if not scored:
            return None
        p = self.places
        best: dict[int, float] = {}
        for i, s in scored:
            best[int(i)] = max(s, best.get(int(i), s))
        sign = -1 if prefer_specific else 1
        order = sorted(best.items(), key=lambda t: (-t[1], sign * KIND_ORDER[p["kind"].iat[t[0]]],
                                                    -p["n_offices"].iat[t[0]], p["place_id"].iat[t[0]]))
        top = order[0][1]
        close = [t for t in order if t[1] >= top - AMBIGUOUS_MARGIN]
        groups: dict[str, tuple[int, float]] = {}
        for i, s in close:
            groups.setdefault(p["group"].iat[i], (i, s))
        if len(groups) == 1 and not choose:
            i, s = order[0]
            r = p.iloc[i]
            how = f"Matched the {r['kind'].replace('_', ' ')} name {r['name']!r} {step}"
            how += f" (score {s:.0f} of 100)." if s < 100 else "."
            if r["kind"] == "district":
                how += " This is the district centre, not an exact location."
            elif r["kind"] == "post_office" and r["note"]:
                how += f" Note: {r['note']}."
            return self._ok(i, how, query)
        cands = [self._cand(i, s, query) for i, s in list(groups.values())[:MAX_CANDIDATES]]
        n = len(groups)
        more = f" Showing {MAX_CANDIDATES} of {n}." if n > MAX_CANDIDATES else ""
        if n > 1:
            c = cands[-1]
            example = (f"{c['name'].title()}, {nice(c['state'])}" if c["kind"] == "district" else
                       f"{c['name']}, {nice(c['district'])}")
            how = (f"{n} places in different districts match the name ({step}); pick one, or add the state, or "
                   f"the district after a comma (\"{example}\").{more}")
        else:
            how = f"One place matches ({step}), but not well enough to use without asking; pick it if it is right."
        if choose_how:
            how = choose_how + " " + how
        label = f"{n} places match" if n > 1 else "1 possible match"
        return Resolution("ambiguous", how=how, candidates=cands, label=label, query=query)

    # ------------------------------------------------------------------ steps

    def _coords(self, text: str, state: Optional[str]) -> Optional[Resolution]:
        m = _COORD.match(text)
        if not m:
            return None
        a, ha, b, hb = m.group(1), (m.group(2) or "").upper(), m.group(3), (m.group(4) or "").upper()
        if "." not in a + b and not (ha or hb):
            return None  # two bare integers are not coordinates
        x, y = float(a), float(b)
        if ha in "SW" and ha:
            x = -x
        if hb in "SW" and hb:
            y = -y
        decimals = min(len(a.split(".")[1]) if "." in a else 0, len(b.split(".")[1]) if "." in b else 0)
        lat_ok = lambda v: 6.0 <= v <= 38.0   # noqa: E731  vf.io.INDIA_BBOX
        lon_ok = lambda v: 68.0 <= v <= 98.0  # noqa: E731
        swapped = False
        if ha in ("E", "W") or hb in ("N", "S"):
            x, y, swapped = y, x, True
        elif not (lat_ok(x) and lon_ok(y)) and lat_ok(y) and lon_ok(x):
            x, y, swapped = y, x, True
        if not (abs(x) <= 90 and abs(y) <= 180):
            return None
        out = dict(kind="coords", label=f"{x:.4f}, {y:.4f}", query=text)
        if not (lat_ok(x) and lon_ok(y)):
            return Resolution("outside_india", x, y, **out,
                              how="These coordinates are outside India. Check the numbers, or type a PIN or a place name.")
        border_km = float(india_border_km(x, y)[0])
        if border_km > BORDER_SLACK_KM:
            return Resolution("outside_india", x, y, **out,
                              how=(f"This point is outside India's border, about {border_km:.0f} km from it (in a "
                                   "neighbouring country or at sea). Check the numbers, or type a PIN or a place name."))
        d, _ = self._pts_tree.query(np.radians([[x, y]]), k=1)
        near_km = float(d[0, 0] * geo.EARTH_RADIUS_KM)
        if near_km > OUTSIDE_KM and x > DISTANCE_RULE_NORTH_OF:
            return Resolution("outside_india", x, y, **out,
                              how=(f"This point is {near_km:.0f} km from the nearest Indian post office, so it is "
                                   "probably outside India or at sea. Check the numbers."))
        _, ix = self._pin_tree.query(np.radians([[x, y]]), k=DISTRICT_VOTE_K)
        groups = self._pin_rows["group"].to_numpy()[ix[0]]
        vals, counts = np.unique(groups, return_counts=True)
        best = max(range(len(vals)), key=lambda k: (counts[k], -list(groups).index(vals[k])))
        st, di = vals[best].split("|", 1)
        prec = max(0.05, 111.0 * 10 ** (-decimals))
        how = (f"Read as latitude {x:.4f}, longitude {y:.4f}" + (" (the numbers were in longitude, latitude order)"
               if swapped else "") + f". District from the {DISTRICT_VOTE_K} nearest PIN areas.")
        if border_km > 0:
            how += f" The point is {border_km:.1f} km outside India's mapped border or coast; check it is in India."
        if state and st != state:
            how += f" Note: this point is in {nice(st)}, not {nice(state)}."
        return Resolution("ok", x, y, prec, "coords", f"{x:.4f}, {y:.4f}, in {nice(di)} district, {nice(st)}",
                          how, [], st, di, None, text)

    def _exact_step(self, q: str, state: Optional[str], district: Optional[str], where: str,
                    query: str) -> Optional[Resolution]:
        """Exact match on district, city, post office, each with the old and alternative names in
        vf.geo.CITY_ALIASES ("Gurgaon" is the old name of Gurugram district, and also three village offices)."""
        if not q:
            return None
        qkey = geo.place_key(q)
        literal_d, literal_o = [q], [k for k in dict.fromkeys([qkey, q]) if k]
        aliases = [k for k in dict.fromkeys([*geo.CITY_ALIASES.get(q, ()), *geo.CITY_ALIASES.get(qkey, ())])
                   if k not in (q, qkey)]
        for kind in ("district", "city", "post_office"):
            literal = self._filter([i for k in (literal_d if kind == "district" else literal_o)
                                    for i in self._exact[kind].get(k, ())], state, district)
            via_alias = self._filter([i for k in aliases for i in self._exact[kind].get(k, ())], state, district)
            idx = np.concatenate([literal, via_alias]).astype(int)
            if not len(idx):
                continue
            alias = "" if len(literal) else " (old or alternative name)"
            scored = [(int(i), 100.0) for i in idx]
            step = f"exactly{alias}{where}"
            if kind == "post_office" and not alias:
                # an office-only hit must not hide a district or city the user more likely meant
                # ("Hydrabad" is a village office in Unnao and a misspelling of Hyderabad)
                mine = {self.places["group"].iat[i] for i, _ in scored}
                rival = [(i, sp) for i, _, sp in self._fuzzy(q, qkey, ("district", "city"), self._big, state, district)
                         if sp >= CROSS_TIER_MIN and self.places["group"].iat[i] not in mine]
                if rival:
                    best = max(s for _, s in rival)
                    scored += [(i, 100.0) for i, s in rival if s >= best - AMBIGUOUS_MARGIN]
                    step = f"exactly{where} as a post office, and closely as a district or city name"
            return self._decide(scored, step, query)
        return None

    def _unused(self, q: str, i: int) -> list[str]:
        """Words of the query that the place at row ``i`` does not explain: not in its name, its
        district or its state, and not a bare word."""
        r = self.places.iloc[int(i)]
        known = (set(str(r["name_norm"]).split()) | set(geo.norm_name(r["name"]).split())
                 | set(str(self._district_norm[int(i)]).split()) | set(geo.norm_name(r["state"]).split()))
        return [t for t in q.split() if t not in known and t not in _BARE and len(t) >= 3]

    def _span_hits(self, qkey: str, state, district) -> list[tuple[int, float, float]]:
        """Offices whose name is exactly a run of the query's words ("Daudnagar" in "Daudnagar
        Aurangabad"), scored as containment."""
        toks = qkey.split()[:8]
        out = []
        for n in range(len(toks) - 1, 0, -1):
            for a in range(len(toks) - n + 1):
                span = toks[a:a + n]
                if not any(len(t) >= 4 and t not in geo.GENERIC_TOKENS for t in span):
                    continue
                for i in self._filter(self._exact["post_office"].get(" ".join(span), ()), state, district):
                    out.append((int(i), geo.CONTAINMENT_SCORE, 0.0))
        return out

    def _fuzzy_step(self, q: str, qkey: str, scope, district, query: str, w: str, choose: bool,
                    choose_how: str) -> Optional[Resolution]:
        """Districts and cities first, then offices. A hit only by containment that leaves words of the
        text unexplained ("Rampur" inside "Rampur Chourai") does not count as a match: a close spelling of
        an office competes with it, and if nothing explains the whole text the answer is a choice."""
        step = f"approximately{w}"
        big = self._fuzzy(q, qkey, ("district", "city"), self._big, scope, district)
        full = [(i, s) for i, s, sp in big if sp >= FUZZY_THRESHOLD or not self._unused(q, i)]
        if full:
            return self._decide(full, step, query, choose=choose, choose_how=choose_how)
        offices = self._fuzzy(q, qkey, ("post_office",), self._names["post_office"], scope, district)
        if len(qkey.split()) > 1:
            offices += self._span_hits(qkey, scope, district)
        o_full = [(i, s) for i, s, sp in offices if sp >= FUZZY_THRESHOLD or not self._unused(q, i)]
        if o_full:
            return self._decide(o_full, step, query, choose=choose, choose_how=choose_how, prefer_specific=True)
        pool = [(i, s) for i, s, _ in big + offices]
        if not pool:
            return None
        top = max(pool, key=lambda t: t[1])[0]
        matched = self.places["name"].iat[top]
        matched = matched.title() if matched.isupper() else matched
        unused = " ".join(self._unused(q, top)).title()
        how = (f"Only part of the text matches a place name ({matched!r}); {unused!r} matches nothing"
               + (f" in {nice(scope)}" if scope else "") + ".")
        how = (choose_how + " " + how) if choose_how else how
        return self._decide(pool, step, query, choose=True, choose_how=how, prefer_specific=True)

    def _names_step(self, text: str, state: Optional[str], query: str, prefix: str = "") -> Resolution:
        note = ""

        def pre(res: Resolution) -> Resolution:
            res.how = prefix + note + res.how
            return res

        def where_of(st, di) -> str:
            return (f" in {nice(st)}" if st else "") + (f", {di.title()} district" if di else "")

        # 1. the whole text exactly as typed, before any state or filler word is taken off
        whole = geo.norm_name(text)
        res = self._exact_step(whole, state, None, where_of(state, None), query)
        if res is not None:
            return pre(res)

        # 2. a state after the last comma, then a district after the new last comma
        #    ("Singhwara, Darbhanga, Bihar"), then a state at the end without a comma ("Rampur UP")
        parts = [x for x in text.split(",") if x.strip()]
        parsed_state = None
        if len(parts) >= 2 and geo.norm_name(parts[-1]) in self._state_forms:
            parsed_state, parts = self._state_forms[geo.norm_name(parts[-1])], parts[:-1]
        district, rest = None, None
        if len(parts) >= 2:
            tail = _clean_query(parts[-1])
            if tail in self._district_forms:
                head, s2 = self.parse_state(", ".join(parts[:-1]))
                if _clean_query(head):
                    district, rest, parsed_state = tail, head, parsed_state or s2
        if rest is None:
            rest, s2 = self.parse_state(", ".join(parts))
            if s2 and _is_bare(_clean_query(rest)):
                # "North Tripura" is a district, not "North" in Tripura: keep the words, use the state
                rest = geo.norm_name(", ".join(parts))
            parsed_state = parsed_state or s2
        if parsed_state:
            if state and parsed_state != state:
                note = f"The text names {nice(parsed_state)}; using it instead of {nice(state)}. "
            state = parsed_state
        q = _clean_query(rest)
        if _is_bare(q):
            q = geo.norm_name(rest)   # "North District": "District" is part of the name here
        if not q:
            return Resolution("not_found", how=prefix + "No place name found in the text.", query=query)
        qkey = geo.place_key(q)
        where = where_of(state, district)

        res = self._exact_step(q, state, district, where, query)
        if res is not None:
            return pre(res)

        # 3. fuzzy: within the state first, then nationally. A national match for a text that named
        #    or picked a state is offered as a choice, never used silently.
        scopes = [state, None] if state else [None]
        for scope in scopes:
            w = (f" in {nice(scope)}" if scope else " across India") + (f", {district.title()} district" if district else "")
            outside = scope is None and state is not None
            res = self._fuzzy_step(q, qkey, scope, district, query, w, choose=outside,
                                   choose_how=(f"Nothing close enough was found in {nice(state)}; these are the "
                                               "closest matches elsewhere.") if outside else "")
            if res is not None:
                return pre(res)
        return Resolution("not_found", how=prefix + note + f"No district, city or post office{where} matches "
                          f"{rest.title()!r}. Try a PIN, a nearby town, or coordinates.", query=query)

    def _fuzzy(self, q: str, qkey: str, kinds, choices, state, district) -> list[tuple[int, float, float]]:
        """(row, score, spelling) for names scoring at least FUZZY_THRESHOLD with vf.geo.name_score.
        ``spelling`` is the same score without containment credit, so "Rampur Baijapur" scores 90
        against "Rampur" but has a low spelling score."""
        if "post_office" in kinds:
            pre = process.extract(qkey, choices, scorer=fuzz.ratio, limit=FUZZY_PRESELECT,
                                  score_cutoff=FUZZY_THRESHOLD - 15)
            names = [c[0] for c in pre]
        else:
            names = choices
        out = []
        for name in names:
            s = max(geo.name_score(q, name)[0], geo.name_score(qkey, name)[0])
            if s < FUZZY_THRESHOLD:
                continue
            sp = max(_spelling(q, name), _spelling(qkey, name))
            rows = []
            for kind in kinds:
                rows.extend(self._exact[kind].get(name, ()))
            for i in self._filter(rows, state, district):
                out.append((int(i), float(s), float(sp)))
        return out

    # ------------------------------------------------------------------ entry

    def resolve(self, query: str, state: Optional[str] = None) -> Resolution:
        text = (query or "").strip()
        st = self.normalize_state(state)
        if not text:
            return Resolution("not_found", how="Type a PIN, a place name, or coordinates.", query=query or "")
        c = self._coords(text, st)
        if c is not None:
            return c
        prefix = ""
        m = _PIN.search(text)
        if m:
            pin = m.group(1) + m.group(2)
            rest = (text[: m.start()] + " " + text[m.end():]).strip(" ,;")
            if pin in self.pins.index:
                r = self.pins.loc[pin]
                i = int(self.places.index[self.places["place_id"] == r["place_id"]][0])
                how = f"Matched PIN {pin} in the India Post directory: the middle of its post offices"
                how += f" ({r['note']})." if r["note"] else "."
                if rest and _clean_query(rest):
                    how += f" The rest of the text ({rest!r}) was not used."
                if st and r["state"] != st:
                    how += f" Note: this PIN is in {nice(r['state'])}, not {nice(st)}."
                return self._ok(i, how, query)
            prefix = f"PIN {pin} is not in the India Post directory. "
            if not _clean_query(rest):
                return Resolution("not_found", how=prefix + "Check the number, or type a place name.", query=query)
            text = rest
        return self._names_step(text, st, query, prefix)


@lru_cache(maxsize=1)
def default_resolver() -> Resolver:
    return Resolver()


def resolve(query: str, state: Optional[str] = None) -> Resolution:
    """Resolve a typed place with the gazetteer at ``silver/places.parquet``."""
    return default_resolver().resolve(query, state)


# ---------------------------------------------------------------------------
# scoring the hand-written cases (data/labels/resolver_cases.csv)
# ---------------------------------------------------------------------------

def _groups(s: str) -> set[str]:
    return {x.strip().upper() for x in str(s or "").split(";") if x.strip()}


def score_case(res: Resolution, expected_status: str, expected_kind: str = "",
               expected_districts: str = "") -> tuple[bool, str]:
    """Hit or miss for one case, and why.

    * ok: status ok, the resolved ``STATE|DISTRICT`` is one of the expected
      (``;``-separated), and the kind matches when one is given.
    * ambiguous: status ambiguous and every expected district is a candidate.
    * not_found, outside_india: the status matches.
    """
    want = _groups(expected_districts)
    if res.status != expected_status:
        got = f"{res.state}|{res.district}" if res.status == "ok" else \
            "; ".join(f"{c['state']}|{c['district']}" for c in res.candidates)
        return False, f"status {res.status} ({got or '-'}), expected {expected_status}"
    if expected_status == "ok":
        got = f"{res.state}|{res.district}".upper()
        if want and got not in want:
            return False, f"resolved to {got}, expected {' or '.join(sorted(want))}"
        if expected_kind and res.kind != expected_kind:
            return False, f"kind {res.kind}, expected {expected_kind}"
    if expected_status == "ambiguous":
        got = {f"{c['state']}|{c['district']}".upper() for c in res.candidates}
        missing = want - got
        if missing:
            return False, f"candidates miss {', '.join(sorted(missing))}"
    return True, ""


def load_cases(path: Path | None = None) -> pd.DataFrame:
    return pd.read_csv(path or paths.labels_dir() / "resolver_cases.csv", dtype=str, keep_default_na=False)


# ---------------------------------------------------------------------------
# India's outline, for the outside-India check
# ---------------------------------------------------------------------------

def _outline_edges() -> np.ndarray:
    """Edges (lon1, lat1, lon2, lat2) of India's outline, decoded once."""
    global _EDGES
    if _EDGES is None:
        raw = zlib.decompress(base64.b85decode("".join(_INDIA_OUTLINE_B85))).decode()
        parts = []
        for ring in raw.split(";"):
            xy = np.cumsum(np.array(ring.split(","), dtype=np.int64).reshape(-1, 2), axis=0) / 1000.0
            parts.append(np.hstack([xy[:-1], xy[1:]]))
        _EDGES = np.vstack(parts)
    return _EDGES


def india_border_km(lat, lon, chunk: int = 500) -> np.ndarray:
    """0 for points inside India's outline, otherwise the distance in km to the outline (flat-earth
    approximation, fine at these distances). NaN in gives NaN out.

    The outline is Natural Earth 1:10m Admin 0 Countries, India point of view
    (``ne_10m_admin_0_countries_ind``, v5.1.1, public domain), all 35 rings, simplified with
    Douglas-Peucker at 0.005 degrees (about 0.5 km) and rounded to 0.001 degrees: 4,112 points.
    """
    lat = np.atleast_1d(np.asarray(lat, dtype="float64"))
    lon = np.atleast_1d(np.asarray(lon, dtype="float64"))
    out = np.full(len(lat), np.nan)
    ok = np.isfinite(lat) & np.isfinite(lon)
    x1, y1, x2, y2 = _outline_edges().T
    dy = np.where(y2 == y1, 1e-12, y2 - y1)
    idx = np.flatnonzero(ok)
    inside = np.zeros(len(lat), dtype=bool)
    for s in range(0, len(idx), chunk):
        j = idx[s:s + chunk]
        la, lo = lat[j, None], lon[j, None]
        cross = ((y1 > la) != (y2 > la)) & (lo < (x2 - x1) * (la - y1) / dy + x1)
        inside[j] = (cross.sum(axis=1) % 2) == 1
    out[ok & inside] = 0.0
    for j in np.flatnonzero(ok & ~inside):
        k = np.cos(np.radians(lat[j]))
        px, py = lon[j] * k, lat[j]
        ax, bx = x1 * k, x2 * k
        ex, ey = bx - ax, y2 - y1
        t = np.clip(((px - ax) * ex + (py - y1) * ey) / np.maximum(ex * ex + ey * ey, 1e-18), 0.0, 1.0)
        out[j] = float(np.min(np.hypot(ax + t * ex - px, y1 + t * ey - py))) * (np.pi / 180.0) * geo.EARTH_RADIUS_KM
    return out


_EDGES: Optional[np.ndarray] = None
#: India's outline (see india_border_km): per ring, "lon,lat" in thousandths of a degree, first point
#: absolute and the rest as differences, rings joined by ";", zlib-compressed and base85-encoded.
_INDIA_OUTLINE_B85 = (
    "c-mE+2ae>(4n!{`0ZO03-2Yyij2Be(0{`#0Dk%+ZFvwo}E&FP%?RoWe-MwC=wd)@9Dzjew+Rt^D6~8=Jo7Y{(b;k$ZdOmdit<P)gcjqy8JO4P|"
    "^M!uh=d<P2*Xw!a&|~%$zt;CUZ;FTY`26PAdLCNNul;;tHU1x;iGSKxpL%kcyggnriXIhTj<3Y)&zkD_+g-HgyT<y~b-uG>)kUAs^3HOfSi{+0"
    "zs_ps(xUaH9veDKzc<g<_Xban#l^p#W4dFHvF5Gco_DQyZSlL$Dr<c5soKJ**BCmA_G-^>yz#s;w%N-SyN?&e@y^21V;gny@_5m_;$P4D;!o${"
    "@%eKuO@|#{njCig+a1rJFUMD}8H;Q@zLY1<zb-8$)_opz#fhFBp1(TlJlj6oI*&ZpcRtYc`?#SV&*_(ry<P3mcZv=n?yj^ryI6Zjz<F={UDdM3"
    "IdNUE?)WT{P3pvpo_n4aJKg89dk9;6{QMF^dESp03_Ywok_4Ug`Qtbno9FVLrd3tWDvsrxPAv62=6pZo9C4|L=sDWg=lOEAXRZ9VA$*O^#1oGk"
    "ovWI<uh{LblBy#(=Q!f*uTta4w@&hiLV4p}&H~V?dudho8a|*n^bj-cb83+i;PYo4p5-?LAnrAmH@Y^eXM`dxe>L78hp@ViPk)YMo_P`IqW<n2"
    ";-gcGEr!xmWJbr;IF56T55HSFqImDQ6P;G&v~-KwMYZ}j{SsHbbh+KiI42e$by6Z3x`rOaY2W(tl3a&S7oYo)yBmRPHab?fiHyf1LeI`C-%AFd"
    "OOgh<cpjn4eIyI#p|S9zvhnD3op(=}r*oo1M?T_@O<#&PmkX1!s@@cbGthvu`B3H~NkuZWbT&GYE;|?!6jmn$`ut5rJhZ;jF`_!>a#_4$fm>^m"
    "mFw2jSIdqwkK@BY#_xLQ{wvjNTI<l>8g}HYQp$KXfLw=coW(9KY$7e?!K6yiu5&EM<cI7XrHv2xCgb7f9a8_Zc$vkWXDzvS_3C)pI7=#3Du?Mq"
    "y9Zm}Qj_BSoOKoiEN<#7T&6V6>imP0{(P{<o|ZHu>|B!sb;;6sXAeWIV;mt&PwZV*riM#;!-j;RhzBi9UkRsmjyc3}UAo^TIXo{p=d)#1!d1Mn"
    "*7Ln{66aLHL~1Kt_DuFEHXso_t32<CRfR*UNN)`-?5zAe-##<cFz%q+TZP$@cZ&725SLQ%X~!YhQZ^K4Ue?PHMvgd?XW|cv)IrYSI+|c7Q$qIc"
    "iMczs{G#cht4|#5c?tgY>}bUI&J~5d-?G##{w7qtaKiTPTU#i&#PBLZ-k|3(p8uUMoM7Sv4<X4_UJ4aCXVfHY9UFQMsd6~>NF|nV&Z36^4v9o4"
    ")R=N*Rd2G%IA<{99zvsZ^Y4s@pMO0HdX^_Ckb{-d7i@m9yO&qpc;t?{#ZqOA&n-?paq6n#z{3+rDvnTf;-o4oJ>x&I<s${svLdG#GQ7htEbf-G"
    "(?M26GG{Tzu(bGUlh%cYix-Bg?jeg@<F>Pj!;NECIHEVolq_lYlo<*UzK%mUk1r2CCLH;?>;&S&BV_fQdg-!1=jq`+rR^bcRhNjVK6|uh2|us1"
    "m)?ZLv^b6Pp>hJ0^MJKNn!-YA!xFS3-|}hFi?g)d@wm%3ib!M4UyKOnEK||+H)jhS7x4~-lXd5sLWRx>LjgxDB|g*Sc$ytonSb2hu<LVZ6#st2"
    "^WE6NO@I+nI6|J99J)+IC~wh%mV`8{>y!n0Qf_5;IhXUk5cVA=M0&KsW}UZ9sZ@E2yxe*9IS~6N3`)f}+CRq`9E};WK*Ui?#<)sjLu${bE5Z}5"
    "MAtCo=FWl@rgUxPyn#SyNc+!6id=0)l)_TI&xJb+q2+cJ706tdxY_fNE(g&<HTC+b>lz!Ge&Us(0tk2r@b$`ghGa{qBp<r^xWH>;)i`1qzglf|"
    "jtI$c#kEX4Kvg6vWsy0IzbrV*^GhQU>4ci+cUwADd`*qBcx}Q%S>(GhJc}jA%ECBv%X0IIt_tyC2xNV+_aziaez9u_uY@{0@5Wq)0Lqb~0M3zK"
    "#fgN?ugt-gLWbHNy^WWL4p!Y+oI#wll9hUOLcM|vjCgVAKvT}8yB{cRCEjWJ#FUvmMs8h-=HnW5yj`*S`AWRCOCI9@QGLSXZiO$;6BqB;?VRI~"
    "dBgpVaNXzhYg6`cHXg#-WaLBEx6J8A*5<2#W62RH^C()H?b8f}2u(pv&x5v1i~={AAo=+6XmL&EVnuv@&h<U3JHKDDaYMFLYlz@qYhPiSl=3M?"
    "k-@E6$a$9H>4NPbzuaNb8ZpQtD=`(*A1R##oSRrhZawTe@eBubM55Nqe+>0I-;Q%SCwKWw&!!`z&4h3{(Rm`)h)`^X>crcY#O~F%%MMvnoLKSb"
    "U+NHA5lcHVS!AFjdFyf{-HCA(LMkkq&IrhVB;z4Rk<!c2%sBt_bjSaln|>Y86T5aN^{?#cL~}wU-<QNQ{?REzBl%WFkN1k8)A{I<35cjCBzRy^"
    "tE|G2yr!(_sFuv-*&X>n982VZzv!)nxlues-W2DeeAp2;izXyn_5qc2XBvPgPQq+Y8ipXYy$RE9v`NobO7pOpu>T92N^-O=XT^h<lO(-j-3yka"
    "6Bd%+6{VTlsPKkZhmz3-u<+_uo2+Qan_}xG?YXPnR4Q&P&S^^}|Bc<PLaP3P9PqukI{Cgw{!ds7=Dg_~r?HrceCR-%@3(Y3Z|qhFB)NGJfMs{v"
    "oYbLHAq|~__Gbyshx6J{8lnP{<46>n^U`5lL(*MX(jr5d&PkWdS1dTrV*w=?2~dpp=oULDM?9!VMg<o|Y&Yc_^h)GG>v*_>SanZSC{|q`XJ_?V"
    "&O@E6dOOMjL+06(4nyHeVGD9zXJ1BtNK=cVAKj!uvQE0(tFg7>HB2FNp{fcT;tJ~gK~!FHm`n2N<y_p?VoB)5XB^=UIM+mbl(H_uFc}Q__V{|-"
    "`cjw>0)$YHG&jdm2+~`!P-m42xnB8J<hMrNs+<(KLs}+<sNM1hHg)4j6Itr(!ybT}8pqPDP~3-{Aecq*!2^RygX9F_c64(S04+g+_j2Ufl<qs?"
    "!vaeHB}<F*e2rbol1xAiwiI5Lh%=!L;XIKgZQc4nREFWY1fYzp#;HjtDoL;|I7E^~N1O~GsGvm|Y|(=q#tfS=312aISuxz547`VddXVG(IPfGL"
    ")va%f6etCL!hjF=tkclea9jmjHF!=1jEg@-Ajrke57V(F18BpH)*n9RlTak+#Ybq+@UOz}B9*xeiZ!GX%;(%Ove@``!}Lf8od0->1<+&TI2UkO"
    "B%Q>;&g2w2l1YbcBD~_djBZ4J8a`LcR`6ZPclh-4VP3C|`LwP8wQVDA`r|fmZnHt65uaXC#O2c|pA!I4QI5-n>%h3?z&P|aDXV>a2&itn>sbkH"
    "cDphQ!Ll8AoIZie8c(d_iy8=@JGftI4m>&#ZvqEAfMP(!X1pKVRgH531s5{%ha};Z#NXW-DMY@4H%FdfFlrVcw5bdTRn>xzjlq;xR}BQiRptE="
    "=qfn`Ua3a@$Un}D0neHcL{upL7@=mk=q@}+&nij-6qCyf6k#tw6M=M)Y6adVyd=KV3{(K-Rg8G!%1a<YrIH&zp+7~3yNvtjc;v;c#=JGQ{OIhL"
    "^7EAymWT;B-VjWs>jV_UB$&M>^@wyw;q?hI6?rQ0TO?T_HAR3^U^y7IiKNV2_<8Dy3aT=!#-;e)0_C|9IqOy+fk_jC*s`V}a150>zm>fn$Vnv@"
    "kXY6B-Ra`?tX56JzHPG$MyNWvttmqi;)!}uEEwR!B|TUM#@+TUEN-aFz8nM8W;08XOnx)*3Yq%;WCg-{RH#2?to15_mv?cUC(OAZL4A4<g4Te4"
    "Mur<;UfA6!c(nip3r>!z77lO=g&E-%=Znm%q$kmh|DvxWk=Yx=-=(4h#9pgVW+%WbL)xfS#i`Bu#TYW8+s=7iE=~!W6B33)Yq)L~Rs;b0itK8-"
    "^<4RRMH(`ZR5WQn;TOwBPFp}{=$VRPq&hXgHa^pIK5xu=#!9O4;}Bbg;`&Heid;#afQ6cJ_4N=o+C0~iH&#HPDv57|l~X48JeulA9;VX3^13k7"
    "98ICiz#_`1)@5T{>Mw#WGyN`sY^QWuoL5z_kU&0k$5=q{WYT@2e0{$oy***HMrK%miDMI^eEXOE9WwRv6G5DmJ;J*7cfj4YC<KPmD#`nzhz8<9"
    "w2|jnKf#fjC{iIXa=5{B?8dsC)u8C2-x}^4_EUiZ8uBZ^tpQCw($f=TM#7BnPxieyj5<?#C$*32K=P4ZbdcZy5-c>PD!OQroSpj8IdR8ZvLV2q"
    "lM;>L!zd=y%Td#|7=RHG`}h}Cl?y_o_=LpYb%TZx97<7LBX;ax2^VX3?&LU4>m8(G(-&H?sD#^G5p_f-Y1A>>SBGu5S(Rj+l@TJYV$>FAMKrfu"
    "wX#`2j(h(!$oJQNHR7feakk4LajY-YNwOz|N74Hv!45??h(gh~$`~z&RC<POzUAEl^OAFc_;_j6w?`i2_;XT+kkDHd30yN=R|!hTEc8QZ6uutk"
    "p`&#<JlrG2f1>05)p?sT5ubC`W0iRs-2mj)s`aTN%q0+aq4Y&}<M{kV2@JTS99211==Tf4Ua&Z2f8tO%6snu^JCf=TJ9w`EGX&}^W>U|~ny>;W"
    ")hZ_EmnJhT@a5pQfRh-$e}SiL%v|S^sRORSI<;S@Uqtx8j1(Z%MG|3zR`IzLyXw%tV_k@6Z<6+!qO!gq_?$%t>&ol~9bH0JiZG|=OQyezvT$Uf"
    ";o&X>NbL(h!FQX1xC{))Q0?3&IZ^R_xMJ*BS%I`-^Q9WjOX4BB7A`2_tO@q5I;ieeswT&F>-$xHy)~r&FYACDoH~q+m*B5bGMxq^dK^Pm-$jqg"
    "LQ!}Wen@3$MOOuKN%5n|WU4g{SYtWcFb!eL-{=vyQ!E_5!LS<6Ws{<2HlkPF25<17uagmhW8LUXNvu;qkZNm+gmsWZ&}m`GBNDT!sMr}_6UC&c"
    "(EU*2eJN5P-SeyakLJh)lWt}P(1J_Ii3h!=F?`rbQOZ#sY<%W>f)kw7;uZ}3H>;J+d|Ho4LBX~L+HZj)A}^q9DffMFSBba~6XGp8F<L1<c1l%M"
    "_a=FG^?PN~P#<uzBl!;_Vy7l_c*ClaZ!TbPL?W#_&Y>jOC|N{SZVajv6O0Nz8f*4sGT#ZeOC_u~uA7*5A*PZ?8xH&n@$lUZk_zN1%?DY)N+q!K"
    "yF7*a%a<}JQwnjDwr5aUjh1zYV@<))NbjpiR(CqdCKTr^8-g7WcR=L=E25rK5*w&y+%0E-_qy!P<>#s4TqTJ+hI6oRM3*K5k@Y)bi&%GA2+NO4"
    "Gt$WkwQL3zhYD2!b;LI*RMI6PnZXOdsirr)*R~_TiY<c>#ak!|2W`@u;keRu+tR-M^*@OCOQg|+XwPXk++2l4{PAaCj85$f1+KOo%ettGaWaGs"
    "&a2B3A&8W!v>)P8cCr;6txBNa(uoK3yMu0+)%?3U5<wxXLD>~)9GeJ!5GQdK*-VUzvn^9GVv|@{0`8H>vq**2;_M8{pmDc)JH@hkDLjyzX1W0S"
    "voY|7vvK-G=pwnBVH8TwgBq#{NDu@TIEb>|fE5?NWSK&6T9KKVC|OsnTqg3`ewR_en$$RpuQk!~R$<5hAwkCk!zulG%(}Uv>@k%apSySrjt$2r"
    "c}lSoDOX8|-(q{0>H>(km+$-e=w|v6R=a*MOeF#tju)w(-LHefxE@tU>IZh-!3umG5=)?bISQ(mS*;L8p3H>s!2ltP2&+C5LM;x|-dvf$BjuH!"
    "!SUjdGGv`Ugm1V8koi4aPM0OIh|PtMX&AY<E>NAg8F*7Q{~oDeAH~N~II45J1aIXAg@FsBDY&NFYSbMICJ58^kESWBuAA(B@H9o%aGOLwEbB&$"
    "9NmaPgrnwj-Kat_GeDt%_xvv48kmf)Ylfvctr$<zS9NLvpA&>)ds#2SFQTjB>0+47rwj>gyrYa?5iOqcrd@OhHcb8{tQfLz;Y%+E^y~VnFh;xv"
    ";Drlb9D8A_Ev?$TT-bD7BcoG0{#G*R0N7dSSnbwEwH$9kupci}M#)s>ZbJhf<%U1<l^R#l45uh6I49@CE2{#r9)hOv)uDSyhR-sfS3yMy6&z@h"
    "4waE1KiHkG_Wc<{Z!sN0b5@L*@Sjtr5>(&q<77JETt6o<`!7&<6YP5-h*Pgrw1L&Hxa#5nh#+dX3X^u42s#4o$?r%yUGAwq^}SQ4LFq(giFejZ"
    "5qp1<seLENN+77Pu|y1~<=mFhO2K|hj`r0v3VA3X4<sD{&!XILuxiG4+`W<o*e$p5iv}R-T#rtGLXx4=Dj$`*x(b^tl=$kh&!yP1GlF|&QoCnL"
    "90yc0nyGeI$fPZGdt5HwNz8QI8xK}4y$sA)l9811<6hU9B|n9?CMZy6kxNA5tKCZ0%@Y7qo>Ga{M{yFQN=Oogv6VxQDxfoA`6dPHP-QWl0O7+?"
    "HWa=pc*U=F3#_GD)Sx2*Fg_#wckH()G0)HT+y+qt)c)wZNL>P!BbBvt-_$+_1rm_v`oj^G)Kbb(^fUy#s?JfLKMfeF%Q}u(hDE1I55+;}m*n||"
    "zKk`=mm(eiK?&st9bJeY;W&6Y8taCA+?}$YFA_C=^Yu}Hn7eXjhq#}QfKb4qUzl|&oWL%;mjf)ra1~D=aAkW--0Bv@BIZB*j;Qx$$yiQ``pE0Q"
    "t=S#&Q5#HsNDx*Z88uCp;fF6f(l4*=Z|-!Z|B_9jlE0mvbWw8(^(xi3Yx{*6m9)YoO*J1>SO+FWptZv#P$Rq3zn6u6yW_Z)E_ngg-S)@c_MjqB"
    "pRd)XLQ)XsHv=?LQuDfKDm56e%)^irNz?9Mv>CP$7rbCISO9A{@Ip;3AzByL+nb1MKss`}kcvOas=~$9VX;2)w!KTESXz6gkp;H${;@lXZ3Q|4"
    "{27?2P}kZ_qmE;SD4HfJN&^YbHnZTZO(Fxt-95(tk)0^tenE%qd=q~zPG>0PFEmy(nOkFCL~b<uG%?@;IlrJqB=Tm^Wst9t1DMUGR9I-$8(UOx"
    "IYnxYaHh^g3`0{C?b-|v2S=NVP7r<wP85Vz5klib)gCqXc-b_F{VyDDJMnp>x<$Ppg)*%HMoIxe|Eym%N^VL~WMrC`6DqSLM*mYSfwv|AyKvy$"
    "VH)}!{6cZVW#id=74%4_MoAV1)}7>NCd1{<V0eF#oGHfBq;3N(8wx%&y`kJnZ`{JylL=bJ2lN(@(eUT(_oz7L+LAzD=sa-_52zbX23i6HME=`$"
    "@-V0$g$%p@4WKHXvA-syp!1Tt0zgo_F=H=2`#`SED3$oVip3duW>@fEC%q((vk_u9TdF}*Pp;e*q&UCf2B+}J-4#4z<D#Hl&XAh<#gBB?^+I4@"
    "`3{luBYZ5IovG*Q@-`J%D5B}v^%Lu7Z#a$j`Jz({W_iUCFuAOClYH3fkXRtYkjX`N2MAXPS&~Yj6iV?oVU-R?z<HKnF0)eDb9>Ootp68xC?#;*"
    "ATZw>?4^h?5bZT}S+Bg@t@s~sMwnpRpUzRk?cYo4Wx5Z9r;bP1a;%lwZ`ldSOa2YnSJ$h=YhBqJH9kGhnm-@wu5YX#Z!ijmP&1Uf$#O>-(b*f("
    "RCn9ZYo5ZQb|a+8*|RrYRbT=<8wf7ShB?&VC@rl|^0?aL>MBh>m82zaN`9L@NjY5iR8|SK>PN7hE$sCx^{PzMRS=+a!N;&@09KEqUNt5mqDnz4"
    "F464|b^~M+?p^|B><{=XAy{3;l?$}g@CUYPT8_&?DwQiZc1g5~wS=0&_$*>UQ^S|&FFiw-GF;`EA65VWuypG}YNXaU<W7TJ5$2|twBoO?$M7)V"
    "c;}=Rr19#8s`iCWm^{$a*n&~*Vl8W!1ec=<dxTO}40jV~8%$k0lb#;;-T-Rb*8@`pwYi9DlwKaK3K6I4R$a^h5(ea5YbiZTlm#l7$!&$U0}QJZ"
    "?<bXt`XhZQV`V38lg4gm0>m^5Ok6yxV8oV1Y%fG?$Q<tb#qZg4Q!^W|<LGU<br#edsBB!OK)rN5ONuTxTM~<sn_>_m(5UWiRy^QcyIb1Lz=NPw"
    "A+O7Il^UH<9<{Ql7ep<J#eu~8%Reg7gPOu@W{5#w_<Qwo2a>EQy;6c;%ImAD2DL?IZXg2XPjk>~7LAe}Jal9d-|JvjRTIkVGPK1e>PW1P9xgVZ"
    "o0){F+Lf0nrc7AinI^14EStE)uAP{G>{3;eWqO9CTl|lU(s!rJ%2nI`^-o}MB<nQAHZyr%Q}h-f062jSbu;ZyrACv}h7YDq8ZwLNIQ;^aew$^!"
    "{@4)k!_?n?tBgfV#zMBDJYi9-e2edHY^%v;zHQ|0nI-BU`Mv%SKoBx0eNT!#k?RH7Dl*{9q=2|MDfso;x0v==n!7w|`%6BrGnu%wQq+}H%p_|`"
    "VrE<$B%wi&X;Kp8UPVwc@F`8TeS{mRy$0*0-pn7s&p=OF)v!=^;ooqq>&t*xYDI|yCe#S^L=jE6$l!}YfID4CRG&;VX2R9CqiIQzMFJHO&g0T="
    "aoM!W$6`h36o|0U(+P}hxsOavcF>PLswtsG724OO;uc$g*r%pTSxHFc#8Aq>nt0*r>}*s+s(NEr<YOZ0-D+gzd?z$&dDQ+z{N_Z)8ADeoE{nL3"
    "yMH#)arIqItJ5w4luS5l%6O%!nK+rJTvu{v%5=YIG>T30W4qJ3uBI-0?ek&w>o+Jv3#Atwod3$~y*Nos!k@W25f2sBi30Gb>Y*aUMGfq`tLr_N"
    "Tb6DcX!m!{Fe3OWsgN&oXD2i>2bOf?rDe@MklJ}sAI?gcu$j3;bN!6(EoE|B9p6LrZ?*nK2pdW{wD6KX%W)XxgV4X%Ge{;R1P|ZHtbRH|hb}%9"
    "xlBVm#F{FJhRu}#E<#30%9QQ-l2qBjg1nWCS8En|XqTZ{(~~!DNHVZxc_A1XJai}*9csHv%41rvNKdmJY^wh_+QQLj{93%c)vqh9IHJDIv~tgG"
    "8DC%TG-~w1tZO~V=?<36s+iP9y6f>=U?B^Y<>cJjZ+1(qu1$^CU9+_CJMwDAo<cBp_J9#^1liQwko6H2sc}0OUt~*;H_ktFQ3U@GC!4~ukwHvw"
    "zNn-c2Yix~m#Fmn)rMAfWIUN#*9_*I#d`1n$+c46Rz8+<wrXUz!j+z<eFfxnAwv!wAF{vT*0A7mwf9ceV!yJ!O<Rlcf2dBdZOmgSq`!$(4p40;"
    "$g~3yAC=3qN0zOwqcfG+`kUqFq=HZsb+5X)OTeJ=23gf<kL^u%fj4XT$O?J=N>=MPDut$S^VL9$ZjTqCyl>!fx}p{RAn*R)K%((WMjQ=!HU<=?"
    "@z>$Jzc;IF(6Yk2ji+W^3Jcndc7Ba$OeNZ`jyRaUO~USG#nFEr%r5N&MV{6C$;ck2S%MF^IjoJx6me6V&_u~x80AVG!UfJn_0ADcB`0O~$^WW9"
    "6s5f22%9$k=tm~nY9R1-H603wD#gnCUKNRG#h5e{yE(>>CP1}DSLgYq+JmZZnJ9`1*-!i1FN%}byX{u7pz2^Q>gy(#D@u5QSky-WZu8);=QsAj"
    "+;SlZ(x@j3CbJ}0&9gVQxyq{1E6#v<H4CsZz1>4;)>cF8mc!mNV+W~BSOwYV;heE4>0xPIc~DELUQtTgRiAIk&vFb8rTsgi{`_I7Q$FqAD5b{&"
    "o&Br+y|pKkW_QP`(X<&?bg99-$nk!^w_R-x)tva&ueUDlec8@=Aj<h#|7m&qF<e0U+Sh(Cq4`XiA-4|=B1x0MU&&`P?JCAb$X9mtf>))z`#UdN"
    "ClEOTnwD((UVe=ubO>mKhJ!XD$h5ZPXl9?f5?&t<62e_lqL+Yt9bx4cKHojLfF=~x?E0>mRaT`Gjb=DVCds*~DT)1med5K*Z4+LCcb}Ym{bnUx"
    "S~X$BP*3~l*&761c_}UqRCv6uDrN>_ZS>$Dm9MhiT0VL?{19_y)^B=Ti2v)ZyKIQ<aPC$%i^wR-8jvh~U;>a@QHlf6RudbZuU}C_C=OXk<8+^u"
    "PfrTAl`^$%zGzf2E_$<-jjCXnb;)kBh_aB6I-+pY1mPyPuAcS!Iwfg9!M)P0lYtX$>;-4p)$Ed+6^FEduq&$DQu`OFX~FuUm%8qnP&K`5!DZ_G"
    "Yj6n!AXe8qX*N^&f-2Jmj1Cz$d8TRLdVD!R0+xAXPm&S>7WUkTViw;!$*qWc2tt`#)ix!@X~1fGA`r#*%4oE*zWQ{B=-YSZ8MiozRTaW>4!tD-"
    "Ukzf=CyLlYQ^pf;@Bxd`I6WrqEml}5?_XxdY=;=c8q6~rAUI=WNijTn7!{CM*I^i-RygS`1&?V)IfP=;nz(>{-pu2oK#P>StHGmnibqsLNyDu;"
    "iVV8CRBD$8SweoNTF1`KPmOgb#X~=yIE^hl3~v^q9jt^EnCaB?t);?2oKQ5Yqy7>9?Edji>2NHHl^UVbAn;#W@>VIo%Dl>j#>FivnL>be9CJG>"
    "+6oOZi3LN_G+r|3=V8Vnq`yqyAT;PolXe_x1dI|{3QSYYjztzY3dFCT*`NxkJ&xfNTUivQqr_&_MdF||yMt)1t<6TDO}B?qXQ{6K#8S@lnL|>T"
    "3Rgmyh#|V5FYE@<@0v`M6fnzNLp%myI7x9dl$zP<s7O`gr2~p~{CcIGD;n?R?grn9+YRO?#jgu&N<gRRLR*Mhj6%!#P%dU+0AGp^@9VBQkP&@{"
    "DUDU#6IhEmGFwqtvR%_@_6t*kS7iCRBV3%aC(!9|>BrT|9m5rwkg6d-f|jQmu}3l0Kgr?mqII?BkO;v_dY51#6V%BsU69UtAcZ(Ri6ZT*fKsz3"
    "g{8d;zxw4t71`g_zZ4!;Uj2358)=`O@MHg5;kzvJ_!w|T?(6A+orXkD%YrM#EF*!GJgx{jsp+OWy(F4ma@9Y1b$TuV$tw$~b-7cMguhbdc?_Xy"
    "6wb~nL`A2&U_aS{F#6LZVbKA=xoMM!T=kL`6k$J;FknG0-P5zKszQe_IyuRyXw+I<+{G|hkNXOGyzEJY#gAs$sV^on2K82HxSHTqtXbgL(oqJ5"
    "0+vf=TUO}*xDg<L$JH46@L{J2*kWpqIQ)(Dj*u{vq(W$We?4%WQD<_2NhmdZN^I4T#+Z1h5M7>c%+E0oGCi^{x7d<U3r7D~H3os#S3-bI2Q{L`"
    "Y1=f-McZTl@q8J7RkPo#Q;<_e+Kl7?f-Pb}TdC|(r=Jf4mAr^3z8gGMW^#GN*kG35plGfYO>MS|Jk_%zEk(@PYfZx%Ws+<n<vp78aFV*nVi3ql"
    "Pq&#AjXiEvxT_E=JbckHG_dIkV`?l<e5(fF!RRX$ow!&Pn&XR`kvz+NWu&PpC1u!XD)jaU(InB7$5YJ|jPClap~QSKnKFn6b~%z~rcHp!5IMB!"
    ">1rLSq9zAio{8K{|0vmbs89j9@%W|2@J%vz+IwVeA!_9;!<nG6Ol38Hpg0x{Ez&KU$Gk|nCJp?%8~v<w8DbkD96j_0J@VJ(Nfg1e&zy6<c%CqM"
    "o*=_N4azzTa%3YokC<wKS9^f2$Kw<`FvdVr=21<i*b;kM?b8@VS!7Fw(aq96)S1uEO>Lm$w9|(8%;GxYq`;+|n^JddbEulE8GgRNrCGBf1W-Y`"
    "oY%+_GS#w)u)!({)UajI679qgDD!JHeW^x<+j%|6&bVHqJkv#!p@B~xv@xZna)>V5dhh`aQ(}x%BJ1YPmFD)6=g;ilBtEyK{PpajaQjiKzp3AF"
    "%^tHBX+Xb0RKB*#o_&-3*V&tG0oyZpF<3ciMeArkqSnsdmp8lJumQ^PO?G#qut$DrrfvOUrtO=>Qknl)pw4EqO3mp&mnZma&ognSxcV20-!Xr4"
    "ze~=)bgERw2RyWw2Ciq}S+`SBL$Sr|X%{TY4`C~w4f|c83LFGK>a6&;pEux-vEGsDd;`wrEmlB@jw2TpL0rN5Z?!4l;@RVC=@MyST@1B+;v*5A"
    "KSh$|{$_ZLh>L=POT$*Kho<`T`G2cUO{yK!^nU8npb+c)^9Y0ato3@F5llwSIuQub;~{OTr*gL^2Q&J!ibVhuWhLVw=b<a!SxQ#Wa-H>?TOD>M"
    "fGEA_n9lQxM^Mao;7!+z7mfvu2Wn3Fd?`cU@b>!e>5o>b(F{R+{P(sUF16PA{)W&)2REIt{(E~>Hq<-OSk3u4^seZP#(B~kHst}PXWH2?ti0Jx"
    "PR#I4SY(_r8fe}oloqmdMKo{r=rQHCW!oA7dHe&WdA?tHu)S9<Qxlv*lIbT^KLhM1>iY6&y&9)<T|E&$&t{*ozZJf{&G(xXH*#*&_!)|Cx^PSO"
    "L)`|?07l}*6-Z9?#&$sN7FW@g*9KT+@X4J^a6mpBQQlPB+MY-)>|$rnNixHV`Xu_w8X*}+-=64qUO0|fUGJ-)VnJD>mqVN-5F~hF0XB<L>P)l5"
    "x6i`&;|gpR2Rz0FfzKENZ%;1Zibs~!vzG9?)0ZFqz^vOb{{Ty70N#J?9+>j$hBxWQ36*xEeP#V2;s1sMG-s8bOh&;-sd@#3JDZCcPCI}OSGb!i"
    "a8i8t_+uPRo%&F3cf>YW`HGFs^X3zCdKvn1&#a}jq}_{ZvykBCd7R*_anwiuA}jH{^@;?JY&K&oA=A#Y>Ny6nD#^&3v%LkM(;U-|0MrqDf^%RD"
    "<Qv~WI~Pj_?YQ;Vsh^y4$L(&6-rx8PCUKf5>oGi_zbY&ghPb=&<}tlcS{D2Tsk)johTw^q1j<ij&{&t8LEX>F=Mv}B)RY89v*q{Ak%|HWu*Q@|"
    "<k%R1k<9_w<Zf~7ap33qVPp;O&IFk1ABk<hzar2bIY+)ShIgN9e>6V+os<b@9vOWE{hJpW=z|OoKVPGm$Zog<T1*IKgow4gPuAUB9elqb4@_8x"
    "SqCfGoilM}O9ScNMbExVChzlW3G)2Q;_-N3QY|1Cbi-2g@Vxc}eAMbrRM5pvJkFZ~5%UKzI0Mg0PIrqL>?EtA^Lu*OI_Yd5S#*Y%wmb&FvZjo3"
    "B+aTOx{z1ailYP$-xC-Slk9O|!eUNS@{fX{wG5H@I#~1PueduoTLDn|ZU|?2d|W!W`7sMM2>2sKIL>KF?8>p+{-G!9H$hx|XSc)7Y-epGS=sdB"
    "cG#IeO2e3dG8|2zQ%VYUjiR@VWnYBNBMR1^?&WWgJZVIMyyhQh!7#YXMFCOtx}8CWgZ>c^M7PQ5<~;eU`t{G7_<PkH8OP6dd9xW{?r#bcW~|e;"
    "*WlIQVWontIMH-N-jhEJ#gh!8J|$W~r?x@J^9Z!sJ*j=9T=3{>w!rGAR$x%glq1_v2vQ1c6c6}vZH<{dMWv0-6m=PR)vkJWgQ9XtS)nxRA7Mea"
    "zk&!+Sx0s0)FpSYh5Aj+smvB>9_7vH!Z`h*g%JZXa7uac=uQ@w?NH7!D;_+uzA*tkhamoDJ{7v6Nt5V+wDRYV<}sy=pZGGbq-<o<$XBIxQB#&r"
    "psh5uhOKGpYMbl(X*Cnxp<)ZAqW|`eV74B!ZS-np#NarzLn{%BIPM>q5yDV8smXryGd0#TDERXeAd0J#HG7d=O>fUPb(5mp?Pi|tz7~+8DIazo"
    "KEt|{m)i79ER#NWY~09+Qzof03MncxB^DPm%gYwG3m7PwZ|+m6A9VZuNSFO9M<+YwMD(jh!Qtd7&kG6(5&ij%CpzxtBohL?s(ygL?Na!Xttd*&"
    "#{LhCY-P;"
)
