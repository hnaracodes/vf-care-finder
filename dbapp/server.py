"""Databricks App server (WP8, D033).

FastAPI app that loads the bundle once at start and answers in process with the tested engine:
vf.rank (ranking, keyword fallback, simulation), vf.locate (the place resolver) and vf.services
(names, radii, defaults). There is no stub engine: if one of them cannot load, /api/health says why
and every other endpoint answers 503.

    GET /api/health                              what is loaded, from where
    GET /api/services                            the service lines (vf.services)
    GET /api/resolve?q=&state=&place_id=         place text to a vf.locate Resolution
    GET /api/answer?service=&q=&state=&text=     vf.rank.rank, or vf.rank.search for service=other + text,
                       &demo=&reveal=            plus the answer extras of app/design/SPEC.md 3.1 (enrich_answer)
    GET /api/sim?service=&q=&state=&demo=        the agent simulator's world and baseline counters (SPEC 3.2)
    GET /api/trip?service=&q=&state=             nearby post offices and their two trip targets (D032)
    GET /api/simulate?service=&q=&state=         vf.rank.simulate (D038); 404 until a probability table exists
    /                                            the prebuilt front end in dbapp/static/

Where the data comes from:
    1. dbapp/bundle/ (or $VF_BUNDLE_DIR) when it holds a manifest.json: the deployed case (WP12 output).
    2. otherwise the shared data folder through vf.paths (local development without a bundle).

Where the code comes from:
    the repo's src/vf in a checkout, dbapp/vf when deployed (only dbapp/ is uploaded).
    VF_USE_APP_VF=1 forces dbapp/vf locally, to check the copy before a deploy.

Run locally:   cd dbapp && ../.venv/bin/python server.py        (or: ../.venv/bin/uvicorn server:app)
"""
from __future__ import annotations

import dataclasses
import json
import logging
import math
import os
import re
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

APP_DIR = Path(__file__).resolve().parent
_REPO_SRC = APP_DIR.parent / "src"
if (_REPO_SRC / "vf" / "__init__.py").exists() and os.environ.get("VF_USE_APP_VF") != "1":
    _VF_FROM = "repo src/vf"
    # First on the path even when dbapp/ (which holds the deploy copy dbapp/vf) is already on it.
    while str(_REPO_SRC) in sys.path:
        sys.path.remove(str(_REPO_SRC))
    sys.path.insert(0, str(_REPO_SRC))
elif (APP_DIR / "vf" / "__init__.py").exists():
    _VF_FROM = "dbapp/vf"
    sys.path.insert(0, str(APP_DIR))
else:
    _VF_FROM = "installed package"

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from fastapi import FastAPI, Query  # noqa: E402
from fastapi.responses import HTMLResponse, JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from vf import locate, rank  # noqa: E402
from vf import services as S  # noqa: E402
from vf.geo import haversine_km  # noqa: E402

log = logging.getLogger("vf.server")

# The answer extras (SPEC 3.1) and the sim payload (SPEC 3.2) use these. Each is optional: a module
# that fails to import leaves its fields out and says why in /api/health extras_errors.
_IMPORT_ERRORS: dict[str, str] = {}
try:
    from vf import travel as T  # noqa: E402
except Exception as _e:  # pragma: no cover - depends on the deployed copy
    T = None
    _IMPORT_ERRORS["travel"] = f"{type(_e).__name__}: {_e}"
try:
    from vf import questions as Q  # noqa: E402
except Exception as _e:  # pragma: no cover
    Q = None
    _IMPORT_ERRORS["questions"] = f"{type(_e).__name__}: {_e}"
try:
    from vf import why_not as WN  # noqa: E402
except Exception as _e:  # pragma: no cover
    WN = None
    _IMPORT_ERRORS["why_not"] = f"{type(_e).__name__}: {_e}"
try:
    from vf import public_list as PL  # noqa: E402
except Exception as _e:  # pragma: no cover
    PL = None
    _IMPORT_ERRORS["public_list"] = f"{type(_e).__name__}: {_e}"

STATIC_DIR = APP_DIR / "static"
MAX_TRIP_POINTS = 3000
FALLBACK_ID = "other"


class ApiError(Exception):
    """An error the front end shows as is: the body is {"detail": "<plain sentence>", ...extra}."""

    def __init__(self, status: int, message: str, **extra: Any):
        super().__init__(message)
        self.status, self.message, self.extra = status, message, extra


def bundle_dir() -> Path:
    env = os.environ.get("VF_BUNDLE_DIR")
    return Path(env).expanduser().resolve() if env else APP_DIR / "bundle"


def has_bundle(d: Optional[Path] = None) -> bool:
    return ((d or bundle_dir()) / "manifest.json").exists()


def to_jsonable(obj: Any) -> Any:
    """JSON-safe copy: dataclasses, numpy and pandas scalars, NaN and inf as null."""
    if obj is None or isinstance(obj, (bool, str)):
        return obj
    if obj is pd.NA or obj is pd.NaT:
        return None
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, (int, np.integer)):
        return int(obj)
    if isinstance(obj, (float, np.floating)):
        f = float(obj)
        return f if math.isfinite(f) else None
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset, np.ndarray)):
        return [to_jsonable(v) for v in (obj.tolist() if isinstance(obj, np.ndarray) else obj)]
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_jsonable(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, Path):
        return str(obj)
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return str(obj)


# --------------------------------------------------------------------------------------------
# Loaded state
# --------------------------------------------------------------------------------------------

class State:
    def __init__(self) -> None:
        self.loaded = False
        self.lock = threading.Lock()
        self.error: Optional[str] = None

    def load(self) -> None:
        with self.lock:
            if self.loaded:
                return
            t0 = time.perf_counter()
            self.bundle = bundle_dir()
            self.mode = "bundle" if has_bundle(self.bundle) else "local"
            if self.mode == "bundle":
                self.manifest = json.loads((self.bundle / "manifest.json").read_text())
                self.data_root = str(self.bundle)
                self.index = rank.load_index(self.bundle)
                self.resolver = locate.Resolver(path=self.bundle / "places.parquet")
            else:
                from vf import paths
                self.manifest = None
                self.data_root = str(paths.data_dir())
                self.index = rank.load_index(None)
                self.resolver = locate.Resolver()
            self.services = [{"id": s.id, "label": s.label, "radius_km": float(s.radius_km),
                              "recipe_text": s.recipe_text, "graded": s.id in self.index.lines}
                             for s in S.SERVICES.values()]
            po = self.index.post_offices
            if po is not None and len(po):
                po = po.dropna(subset=["lat", "lon"]).drop_duplicates(subset=["lat", "lon"])
                self.po_lat = po["lat"].to_numpy(float)
                self.po_lon = po["lon"].to_numpy(float)
                self.po_pin = po["pin"].astype(str).to_numpy() if "pin" in po else np.full(len(po), "", object)
            else:
                self.po_lat = self.po_lon = np.array([], dtype=float)
                self.po_pin = np.array([], dtype=object)
            self.load_extras()
            self.load_ms = (time.perf_counter() - t0) * 1000
            self.loaded = True
            log.info("loaded in %.0f ms: mode=%s vf=%s", self.load_ms, self.mode, _VF_FROM)

    def load_extras(self) -> None:
        """The record info, phone book and outside lists, once (SPEC 8). A failure is kept in
        extras_errors and only the fields that need it are left out of answers."""
        self.extras_errors: dict[str, str] = dict(_IMPORT_ERRORS)
        self.infos: Optional[dict] = None
        self.book = None
        self.public = None
        self.outside = None
        self.extras_ms: dict[str, float] = {}

        def attempt(name: str, fn):
            t = time.perf_counter()
            try:
                out = fn()
            except Exception as e:
                self.extras_errors[name] = f"{type(e).__name__}: {e}"
                log.warning("extras %s failed: %s", name, self.extras_errors[name])
                out = None
            self.extras_ms[name] = round((time.perf_counter() - t) * 1000, 1)
            return out

        src = self.bundle if self.mode == "bundle" else None
        if Q is not None:
            self.infos = attempt("record_info", lambda: Q.load_record_info(src))
            # Phone numbers: the bundle's record_info.parquet phone columns when it has them (the
            # deployed case, no raw file there), else the raw file. call_info masks them; tel only on reveal.
            self.book = attempt("phone_book", lambda: Q.load_phone_book(src) if src is not None
                                else Q.load_phone_book())
        if PL is not None:
            self.public = attempt("public_list", lambda: PL.load(src))
            if hasattr(PL, "load_outside"):
                def _outside():
                    if src is not None and (src / getattr(PL, "CARD_FACTS_FILE", "-")).exists():
                        return PL.load_outside(src)
                    return PL.load_outside()
                self.outside = attempt("outside_lists", _outside)
        self.cache = _LRU(CACHE_SIZE)
        attempt("sim_world", lambda: _sim_world(self))  # the village points, built once
        if WN is not None and hasattr(WN, "_lookups"):
            attempt("why_not_lookups", lambda: WN._lookups(self.index))  # warm, so the first query is not slow

    def note_error(self, name: str, e: BaseException) -> None:
        """A per-request extras failure: logged and shown in /api/health, never a 500."""
        errs = getattr(self, "extras_errors", None)
        if errs is None:
            self.extras_errors = errs = {}
        errs[name] = f"{type(e).__name__}: {e}"
        log.warning("extras %s failed: %s", name, errs[name], exc_info=e)

    # ---- helpers -------------------------------------------------------------------------

    def service(self, sid: str) -> S.Service:
        if sid not in S.SERVICES:
            raise ApiError(400, "That service is not in the list. Pick one, or choose Something else and type it.",
                           services=list(S.SERVICES))
        return S.SERVICES[sid]

    def resolve(self, q: str, st: Optional[str], place_id: Optional[str]) -> locate.Resolution:
        if place_id:
            res = self.resolve_place_id(place_id, q)
            if res is not None:
                return res
            if not q.strip():
                return locate.Resolution("not_found", how="The picked place is not in the gazetteer.", query=q)
        return self.resolver.resolve(q, state=st or None)

    def resolve_place_id(self, place_id: str, query: str) -> Optional[locate.Resolution]:
        """A candidate the user picked after an ambiguous place. vf.locate has no public lookup by
        place_id yet, so this uses Resolver._ok, the same constructor resolve() uses for a match."""
        p = self.resolver.places
        hit = np.flatnonzero(p["place_id"].to_numpy() == place_id)
        if not len(hit):
            return None
        return self.resolver._ok(int(hit[0]), "You picked this place from the list.", query)


STATE = State()


def state() -> State:
    if not STATE.loaded:
        try:
            STATE.load()
        except Exception as e:
            STATE.error = f"{type(e).__name__}: {e}"
            log.exception("load failed")
            raise ApiError(503, f"The app's data did not load: {STATE.error}") from e
    return STATE


# --------------------------------------------------------------------------------------------
# Answers and trips
# --------------------------------------------------------------------------------------------

def do_answer(s: State, service: str, res: Any, text: Optional[str]) -> dict:
    if service == FALLBACK_ID:
        if not text or not text.strip():
            raise ApiError(400, "For 'Something else', type the care needed.")
        if not keyword_words(text):
            raise ApiError(400, f"We could not match \"{text.strip()[:40]}\" to any record. Type at least one word "
                                "of 3 or more letters (for example \"snake bite\"), or pick one of the listed services.")
        ans = rank.search(s.index, text.strip(), res, k=3)
    else:
        s.service(service)
        ans = rank.rank(s.index, service, res, k=3)
    return to_jsonable(ans)


#: Words that carry no meaning for the keyword search; a query made only of these (or of words under
#: 3 letters) is refused rather than answered with a confident match on "a" or "the".
KEYWORD_STOPWORDS = frozenset("""a an and are at be by can do for from has have in is it its me my near need no
nor not of on or our the to too was we what where which who with you your any some this that there here care
hospital hospitals clinic facility facilities treatment service services centre center""".split())

#: Free text that names one of the graded services: the answer offers the checked answer for it
#: ("Did you mean Dialysis?"). Whole words or phrases, lower case.
SERVICE_WORDS: dict[str, tuple[str, ...]] = {
    "dialysis": ("dialysis", "haemodialysis", "hemodialysis", "kidney dialysis"),
    "emoc": ("c-section", "c section", "csection", "caesarean", "cesarean", "caesarian", "lscs"),
    "cath_lab": ("heart attack", "angioplasty", "angiography", "cath lab", "cathlab", "stemi", "stent"),
    "cardiac_surgery": ("heart surgery", "cardiac surgery", "bypass surgery", "cabg", "open heart"),
    "mri": ("mri", "mri scan"),
    "ct": ("ct scan", "ct-scan", "cat scan", "ct"),
    "burns": ("burn", "burns", "burn unit", "burn care"),
    "oncology": ("cancer", "chemo", "chemotherapy", "radiotherapy", "oncology"),
    "nicu": ("nicu", "newborn icu", "neonatal icu", "neonatal intensive care"),
    "icu": ("icu", "intensive care"),
    "trauma": ("trauma", "road accident", "accident"),
    "neurosurgery": ("neurosurgery", "brain surgery", "spine surgery"),
}


def keyword_words(text: Optional[str]) -> list[str]:
    """The words of a free-text query that can match anything: 3 or more letters, not a stopword."""
    return [w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) >= 3 and w not in KEYWORD_STOPWORDS]


def did_you_mean(text: Optional[str]) -> Optional[dict]:
    """The graded service the free text names, if exactly one does."""
    t = " " + re.sub(r"[^a-z0-9-]+", " ", (text or "").lower()).strip() + " "
    hits = [sid for sid, words in SERVICE_WORDS.items() if any(f" {w} " in t for w in words)]
    hits = [h for h in dict.fromkeys(hits) if h in S.SERVICES]
    if len(hits) != 1:
        return None
    return {"service": hits[0], "label": S.SERVICES[hits[0]].label}


_NEG_CUE = re.compile(r"\b(excluding|exclude[sd]?|except|other than|not|no|non|without|unavailable|nor)\b[^.;:()]*$")


def negated_match(text: Optional[str], query: Optional[str]) -> bool:
    """True when the query's words appear in this text only inside a negation: "Animal bites (Excluding
    Snake Bite)" for "snake bite". The cue must come before the word, in the same clause or bracket."""
    low = (text or "").lower()
    words = keyword_words(query)
    if not low or not words:
        return False
    # the whole phrase first ("snake bite"): "Animal bites (Excluding Snake Bite)" names bites, but
    # excludes the snake bites asked about
    phrase = list(re.finditer(r"\b" + r"\W+".join(re.escape(w) for w in words), low)) if len(words) > 1 else []
    hits = phrase or [m for w in words for m in re.finditer(rf"\b{re.escape(w)}", low)]
    if not hits:
        return False
    return all(_NEG_CUE.search(low[max(0, m.start() - 60):m.start()]) for m in hits)


def _targets(ans: dict) -> list[dict]:
    """The facilities a trip may end at: the cards, or the nearest reliable one beyond the radius."""
    cards = list(ans.get("results") or [])
    if not cards and ans.get("beyond_radius"):
        cards = [ans["beyond_radius"]]
    return [c for c in cards if c.get("lat") is not None and c.get("lon") is not None]


def build_trip(s: State, service: str, res: Any, ans: dict) -> dict:
    """Nearby post offices, each with its nearest recommended result and its nearest facility that
    claims the service (D032). Shape: app/src/types.ts `Trip`."""
    svc = s.service(service)
    radius = float(svc.radius_km)
    lat, lon = float(res.lat), float(res.lon)
    out: dict[str, Any] = {"status": "ok", "service": service, "service_label": svc.label, "radius_km": radius,
                           "center": {"lat": lat, "lon": lon}, "facilities": [], "points": [], "pins": [],
                           "nearest_recommended": [], "nearest_claim": [], "extra_km": [], "summary": {},
                           "notes": ["Straight-line km, not travel time.",
                                     "Each dot is a post office area; offices sharing one point are drawn once."]}
    if service not in s.index.lines:
        return {**out, "status": "unavailable", "reason": "This service has no grades loaded."}
    if not len(s.po_lat):
        return {**out, "status": "unavailable", "reason": "No post office points are loaded."}
    ld = s.index.lines[service]
    f_lat, f_lon = np.asarray(s.index.lat, float), np.asarray(s.index.lon, float)
    f_ids, f_names = s.index.fac["facility_id"].to_numpy(), s.index.fac["name"].to_numpy()
    grade, blocked = np.asarray(ld.grade, int), np.asarray(ld.blocked, bool)

    dpo = haversine_km(lat, lon, s.po_lat, s.po_lon)
    sel = np.flatnonzero(dpo <= radius)
    sel = sel[np.argsort(dpo[sel], kind="stable")][:MAX_TRIP_POINTS]
    plat, plon = s.po_lat[sel], s.po_lon[sel]

    # facility list: the recommended results first, then every claim target that is not one of them
    rec = _targets(ans)
    facilities: list[dict] = []
    pos_of: dict[str, int] = {}
    for r, c in enumerate(rec, start=1):
        pos_of[c["facility_id"]] = len(facilities)
        facilities.append({"facility_id": c["facility_id"], "name": c.get("name"), "lat": float(c["lat"]),
                           "lon": float(c["lon"]), "kind": "recommended", "rank": r, "supported": True,
                           "unsupported_reason": None})
    claim = np.flatnonzero(np.isfinite(f_lat) & np.isfinite(f_lon) & (grade >= 1))
    if len(claim):
        claim = claim[haversine_km(lat, lon, f_lat[claim], f_lon[claim]) <= S.REACH_FACTOR * radius]
    claim_supported = (grade[claim] >= 2) & ~blocked[claim]
    # Why a claim target is not supported: it only claims the service (grade 1), or its evidence is
    # held back by a blocking warning (grade 2 or more, blocked). The counter names the two apart.
    claim_held_back = (grade[claim] >= 2) & blocked[claim]
    claim_fidx = []
    for p, sup, held in zip(claim, claim_supported, claim_held_back):
        fid = f_ids[p]
        if fid not in pos_of:
            pos_of[fid] = len(facilities)
            facilities.append({"facility_id": fid, "name": f_names[p], "lat": float(f_lat[p]),
                               "lon": float(f_lon[p]), "kind": "recommended" if sup else "claim_only",
                               "rank": None, "supported": bool(sup),
                               "unsupported_reason": None if sup else ("held_back" if held else "claim_only")})
        claim_fidx.append(pos_of[fid])
    claim_fidx = np.asarray(claim_fidx, dtype=int)
    out["facilities"] = facilities

    n = len(sel)
    if rec:
        drec = np.stack([haversine_km(plat, plon, c["lat"], c["lon"]) for c in rec], axis=1)
        rec_t, rec_km = drec.argmin(axis=1), drec.min(axis=1)
    else:
        rec_t, rec_km = np.full(n, -1), np.full(n, np.nan)
    extra = np.full(n, np.nan)
    reroute = np.zeros(n, bool)
    unsupported = np.zeros(n, bool)
    held_back = np.zeros(n, bool)
    cl_t = np.full(n, -1)
    if len(claim) and n:
        dcl = np.stack([haversine_km(plat, plon, f_lat[p], f_lon[p]) for p in claim], axis=1)
        k = dcl.argmin(axis=1)
        cl_t, cl_km = claim_fidx[k], dcl.min(axis=1)
        unsupported = ~claim_supported[k]
        held_back = claim_held_back[k]
        extra[~unsupported] = 0.0
        reroute = unsupported & (rec_t >= 0)
        for j in np.flatnonzero(reroute):
            c, p = rec[rec_t[j]], claim[k[j]]
            leg = float(haversine_km(f_lat[p], f_lon[p], c["lat"], c["lon"]))
            extra[j] = cl_km[j] + leg - rec_km[j]
    out["points"] = [[float(a), float(b)] for a, b in zip(plon, plat)]
    out["pins"] = [str(x) for x in s.po_pin[sel]]
    out["nearest_recommended"] = [int(x) for x in rec_t]
    out["nearest_claim"] = [int(x) for x in cl_t]
    out["extra_km"] = [None if np.isnan(x) else float(x) for x in extra]
    out["summary"] = {
        "n_points": int(n),
        "n_first_unsupported": int(unsupported.sum()),
        "n_first_claim_only": int((unsupported & ~held_back).sum()),
        "n_first_held_back": int((unsupported & held_back).sum()),
        "n_rerouted": int(reroute.sum()),
        "extra_km_mean": float(np.mean(extra[reroute])) if reroute.any() else None,
        "n_no_recommended_target": int((rec_t < 0).sum()),
        "n_no_claim_target": int((cl_t < 0).sum()),
        "recommended_beyond_radius": bool(not ans.get("results") and rec),
    }
    if not rec:
        out["notes"].append("No recommended facility to travel to.")
    elif not ans.get("results"):
        out["notes"].append(f"No recommended facility within {radius:.0f} km: trips go to the nearest one beyond it.")
    return out


# --------------------------------------------------------------------------------------------
# Shared per-query core: the answer, the gates, the sim facility set, the why-not strip
# --------------------------------------------------------------------------------------------

CACHE_SIZE = 64
SIM_VERSION = "sim_v1"
SIM_VILLAGE_KM = 50.0
SIM_MAX_VILLAGES = 3000
SIM_MAX_FACILITIES = 2500
#: An exact latitude shared by this many post office rows or more is an artefact (15.59, 15.5934).
ARTEFACT_LAT_MIN_ROWS = 100
LABEL_KM = 250.0
LABEL_PO_KM = 60.0
LABEL_PO_CELL_KM = 6.0
LABEL_MAX = 120
#: The browser's sim uses this radius (SPEC 7.2); the server's counters use it too, so both agree.
SIM_EARTH_R = 6371.0088
SHORTENER_HOSTS = ("goo.gl", "maps.app.goo.gl", "bit.ly", "tinyurl.com", "tinyurl", "g.co", "t.co")
STATUS_RANK = {"evidence": 0, "held_back": 1, "claim_only": 2}


class _LRU:
    """A small thread-safe LRU for per-query results."""

    def __init__(self, n: int):
        from collections import OrderedDict
        self.n, self.d, self.lock = n, OrderedDict(), threading.Lock()

    def get(self, key):
        with self.lock:
            if key in self.d:
                self.d.move_to_end(key)
                return self.d[key]
        return None

    def put(self, key, value) -> None:
        with self.lock:
            self.d[key] = value
            self.d.move_to_end(key)
            while len(self.d) > self.n:
                self.d.popitem(last=False)


def demo_on(param: Optional[str]) -> bool:
    """Masking: VF_DEMO_MODE=1 (env) or demo=1 (request). The request can only turn it on."""
    env = os.environ.get("VF_DEMO_MODE", "").strip().lower() in ("1", "true", "yes", "on")
    return env or (param or "").strip().lower() in ("1", "true", "yes", "on")


def _flag(param: Optional[str]) -> bool:
    return (param or "").strip().lower() in ("1", "true", "yes", "on")


def _res_key(res: Any) -> tuple:
    return tuple(getattr(res, k, None) for k in ("status", "place_id", "lat", "lon", "precision_km", "state",
                                                  "district", "label", "kind"))


def nice_name(s: Optional[str]) -> str:
    """'UTTAR PRADESH' -> 'Uttar Pradesh', 'JAMMU AND KASHMIR' -> 'Jammu and Kashmir'."""
    if not s:
        return ""
    try:
        return locate.nice(s)
    except Exception:
        small = {"and", "of", "the", "in"}
        words = str(s).strip().title().split()
        return " ".join(w.lower() if (i and w.lower() in small) else w for i, w in enumerate(words))


def _hav(lat1, lon1, lat2, lon2, r: float = SIM_EARTH_R):
    """Haversine km (broadcasting), with the radius the browser uses (SPEC 7.2)."""
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi = p2 - p1
    dl = np.radians(lon2) - np.radians(lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * r * np.arcsin(np.sqrt(np.minimum(a, 1.0)))


def _window_of(s: State, service: str, ans: dict, res: Any) -> Optional[dict]:
    """The query's window: the answer's (v1), else the line's window from vf.services (v0)."""
    w = dict(ans.get("window") or {})
    if w.get("minutes") is not None:
        return w
    if service not in getattr(S, "WINDOWS", {}):
        return None
    sw = S.WINDOWS[service]
    out = dict(sw.as_dict())
    f = None
    if T is not None:
        try:
            f = T.origin_factor(rank._location_dict(res), s.index.state_at).value
        except Exception:
            f = None
    W = float(sw.minutes)
    out.update(minutes=W, allowance=float(sw.allowance), factor=f, origin_loose=False,
               reach_minutes=float(getattr(S, "REACH_FACTOR_V1", 3.0)) * W, v0_fallback=True)
    if f is not None and T is not None:
        out["km_equivalent"] = round(float(T.window_km(W, factor=f)), 1)
        out["reach_km"] = round(float(T.window_km(out["reach_minutes"], factor=f)), 1)
    return out


def _card_positions(s: State, ans: dict) -> list[tuple[int, int]]:
    """(position, card_rank) for the cards: results 1..k, or beyond_radius as 1 when there are none."""
    out = []
    for r, c in enumerate(ans.get("results") or [], start=1):
        p = s.index.id_to_pos.get(c.get("facility_id"))
        if p is not None:
            out.append((int(p), r))
    if not out and ans.get("beyond_radius"):
        p = s.index.id_to_pos.get(ans["beyond_radius"].get("facility_id"))
        if p is not None:
            out.append((int(p), 1))
    return out


def _sim_set(s: State, service: str, res: Any, ans: dict) -> dict:
    """The sim's facilities (SPEC 3.2): rank.gate_masks in_reach (the funnel's claim_within_reach),
    one record per duplicate group, nearest first, at most SIM_MAX_FACILITIES.

    Duplicate groups keep one record: a card if one is in the group, else the record with the best
    status (evidence, held back, claim only), then the group's preferred record (not in idx.dup_drop),
    then the nearest. This is "minus idx.dup_drop" whenever the preferred record is in the set, and it
    never drops a place's only listing with evidence."""
    idx = s.index
    lat, lon = float(res.lat), float(res.lon)
    d = rank._distances(idx, lat, lon)
    g = rank.gate_masks(idx, service, d, res)
    ld = idx.lines[service]
    grade = np.asarray(ld.grade, int)
    blocked = np.asarray(ld.blocked, bool)
    rec_mask = np.asarray(g["rec_mask"], bool)
    cand = np.flatnonzero(np.asarray(g["in_reach"], bool))
    cand = cand[np.argsort(d[cand], kind="stable")]
    status = np.where(rec_mask, "evidence", np.where((grade >= 2) & blocked, "held_back", "claim_only"))
    cards = dict(_card_positions(s, ans))
    dup_drop = getattr(idx, "dup_drop", set()) or set()
    canon = getattr(idx, "dup_canon", {}) or {}
    groups: dict[int, list[int]] = {}
    for p in cand:
        p = int(p)
        groups.setdefault(canon.get(p, -1 - p), []).append(p)
    keep_set = set()
    for gkey, members in groups.items():
        if len(members) == 1:
            keep_set.add(members[0])
            continue
        best = min(members, key=lambda p: (0 if p in cards else 1, STATUS_RANK[str(status[p])],
                                           1 if p in dup_drop else 0, float(d[p]), p))
        keep_set.add(best)
    keep = [int(p) for p in cand if int(p) in keep_set]
    n_listing = int(len(cand))
    n_merged = n_listing - len(keep)
    merged_rec = int(rec_mask[cand].sum()) - int(rec_mask[np.asarray(keep, dtype=np.int64)].sum())
    n_capped = max(0, len(keep) - SIM_MAX_FACILITIES)
    if n_capped:
        extra = [p for p in keep[SIM_MAX_FACILITIES:] if p in cards]
        keep = keep[: SIM_MAX_FACILITIES - len(extra)] + extra
    return {"d": d, "g": g, "pos": np.asarray(keep, dtype=np.int64), "status": status, "grade": grade,
            "cards": cards, "n_listing": n_listing, "n_merged": n_merged, "n_merged_evidence": merged_rec,
            "n_capped": n_capped}


def _core(s: State, service: str, res: Any, text: Optional[str], demo: bool) -> dict:
    """One query's shared work, cached: the raw answer, the window, the sim facility set and the
    why-not strip with letters. /api/answer and /api/sim both read it, so their cards, letters and
    sets agree."""
    key = ("core", service, (text or "").strip().lower() if service == FALLBACK_ID else None, _res_key(res), demo)
    cache = getattr(s, "cache", None)
    if cache is None:
        cache = s.cache = _LRU(CACHE_SIZE)
    if getattr(res, "status", None) != "ok":  # ambiguous, not found, outside India: cheap, never cached
        return _core_build(s, service, res, text, demo)
    hit = cache.get(key)
    if hit is not None:
        return hit
    with _key_lock(key):  # /api/answer and /api/sim arrive together: compute once
        hit = cache.get(key)
        if hit is not None:
            return hit
        core = _core_build(s, service, res, text, demo)
        cache.put(key, core)
        return core


_KEY_LOCKS: dict = {}
_KEY_LOCKS_GUARD = threading.Lock()


def _key_lock(key) -> threading.Lock:
    with _KEY_LOCKS_GUARD:
        if len(_KEY_LOCKS) > 4 * CACHE_SIZE:
            for k in [k for k, v in _KEY_LOCKS.items() if not v.locked()]:
                _KEY_LOCKS.pop(k, None)
        return _KEY_LOCKS.setdefault(key, threading.Lock())


def _core_build(s: State, service: str, res: Any, text: Optional[str], demo: bool) -> dict:
    ans = do_answer(s, service, res, text)
    core: dict[str, Any] = {"ans": ans, "service": service, "window": None, "set": None, "strip": None,
                            "funnel": None}
    graded = service != FALLBACK_ID and service in s.index.lines
    located = getattr(res, "status", None) == "ok" and res.lat is not None and res.lon is not None
    if graded and located:
        try:
            core["window"] = _window_of(s, service, ans, res)
        except Exception as e:
            s.note_error("answer.window", e)
        try:
            core["set"] = _sim_set(s, service, res, ans)
        except Exception as e:
            s.note_error("sim.facilities", e)
        if WN is not None:
            try:
                strip = WN.why_not_strip(s.index, service, res, ans, mask_names=demo)
                for i, it in enumerate(strip.get("items") or []):
                    it["letter"] = _letter(i)
                core["strip"] = strip
            except Exception as e:
                s.note_error("answer.why_not", e)
            try:
                f = WN.funnel(s.index, service, res, answer_dict=ans, max_ids=0, detail_steps=(), mask_names=demo)
                core["funnel"] = {"steps": [{k: st[k] for k in ("step", "label", "n", "n_distinct") if k in st}
                                            for st in f.get("steps") or []],
                                  "consistency": f.get("consistency")}
            except Exception as e:
                s.note_error("answer.funnel", e)
    return to_jsonable_keep(core)


def to_jsonable_keep(core: dict) -> dict:
    """JSON-safe copy of the parts that go on the wire; numpy arrays in the sim set stay arrays."""
    out = dict(core)
    for k in ("ans", "strip", "funnel", "window"):
        if out.get(k) is not None:
            out[k] = to_jsonable(out[k])
    return out


def _letter(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


# --------------------------------------------------------------------------------------------
# /api/answer extras (SPEC 3.1)
# --------------------------------------------------------------------------------------------

def _official_domain(url: Optional[str]) -> Optional[str]:
    """The host of the record's official website without "www.", or None for link shorteners and maps."""
    from urllib.parse import urlsplit

    if not url or not isinstance(url, str) or not url.strip():
        return None
    u = url.strip()
    if "://" not in u:
        u = "http://" + u
    try:
        parts = urlsplit(u)
    except ValueError:
        return None
    host = (parts.hostname or "").lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    if not host or "." not in host:
        return None
    path = (parts.path or "").lower()
    if host in SHORTENER_HOSTS or host.endswith(".goo.gl") or host.startswith("maps.google."):
        return None
    if (host.startswith("google.") or ".google." in host) and path.startswith("/maps"):
        return None
    return host


def _requirement_names(line: Optional[str]) -> list[str]:
    """Plain requirement names to key quotes by: questions.REQUIREMENT_PHRASE keys and the line's recipe names."""
    names = set(getattr(Q, "REQUIREMENT_PHRASE", {}) or {}) if Q is not None else set()
    try:
        from vf import recipes
        rule = recipes.GRADE_RULES.get(line) if line else None
        if rule is not None:
            names.update(getattr(rule, "supported_names", ()) or ())
            names.update(getattr(rule, "corroborated_names", ()) or ())
    except Exception:
        pass
    return sorted(names, key=len, reverse=True)


def _req_key(req: Optional[str], names: list[str]) -> Optional[str]:
    """The plain name a recipe requirement string starts with: "caesarean section (WHO 2009 ...)" gives
    "caesarean section". The name sharing the longest prefix wins: a whole name ending at a word boundary,
    or a common prefix of at least 15 characters ("dialysis unit, machines or haemodialysis named ..."
    gives "dialysis unit, machines or nephrologist", not "dialysis")."""
    if not req:
        return None
    r = str(req).strip()
    rl = r.lower()
    best, best_n = None, 0
    for n in names:
        nl = n.lower()
        if rl.startswith(nl) and (len(rl) == len(nl) or not rl[len(nl)].isalnum()):
            score = len(nl) + 0.5
        else:
            k = 0
            for a, b in zip(rl, nl):
                if a != b:
                    break
                k += 1
            score = k if k >= 15 else 0
        if score > best_n:
            best, best_n = n, score
    if best is not None:
        return best
    base = re.split(r"\s*[(;]", r)[0].strip(" ,")
    return base or None


def _marks(text: str, rx) -> list[list[int]]:
    if rx is None or not text:
        return []
    low = text.lower()
    hay = low if len(low) == len(text) else text
    flags = 0 if hay is low else re.IGNORECASE
    pat = rx if not flags else re.compile(rx.pattern, flags)
    return [[m.start(), m.end()] for m in pat.finditer(hay) if m.end() > m.start()]


def _quotes(card: dict, line: Optional[str], names: list[str], rx) -> list[dict]:
    """card.evidence merged by normalised quote text: the first item's order, the union of keys."""
    out: list[dict] = []
    by_norm: dict[str, dict] = {}
    for e in card.get("evidence") or []:
        q = e.get("quote")
        if not q:
            continue
        text = " ".join(str(Q.mask_numbers(q) if Q is not None and hasattr(Q, "mask_numbers") else q).split())
        norm = re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
        reqs = [x for x in (e.get("requirements") or [e.get("requirement")]) if x]
        keys = [k for k in (_req_key(x, names) for x in reqs) if k]
        if norm in by_norm:
            row = by_norm[norm]
            for k in keys:
                if k not in row["requirement_keys"]:
                    row["requirement_keys"].append(k)
            continue
        row = {"text": text, "field": e.get("field"), "requirement_keys": list(dict.fromkeys(keys)),
               "marks": _marks(text, rx)}
        by_norm[norm] = row
        out.append(row)
    return out


def _missing_phrases(card: dict, names: list[str]) -> list[str]:
    phrase = getattr(Q, "REQUIREMENT_PHRASE", {}) if Q is not None else {}
    try:
        raw = Q._missing_requirements(card) if Q is not None else list(card.get("missing") or [])
    except Exception:
        raw = list(card.get("missing") or [])
    out = []
    for m in raw:
        k = _req_key(m, names) or m
        p = phrase.get(k, k)
        if p and p not in out:
            out.append(p)
    return out


def _time(card: dict, loose: bool) -> Optional[dict]:
    m = card.get("minutes")
    if m is None or T is None:
        return None
    lo, hi = card.get("minutes_low"), card.get("minutes_high")
    rng = f"{T.fmt_minutes(lo)} to {T.fmt_minutes(hi)}" if lo is not None and hi is not None else None
    short = rng if (loose and rng) else "about " + T.fmt_minutes(m)
    return {"short": short, "range": rng or short, "loose": bool(loose)}


def _call(s: State, fid: str, reveal: bool, demo: bool) -> Optional[dict]:
    """The Call button: the number always masked; tel only when reveal=1 is asked and demo is off."""
    if Q is None or s.book is None:
        return None
    masked = Q.call_info(fid, s.book, reveal=False)
    tel = None
    if reveal and not demo and masked.get("has_phone"):
        tel = Q.call_info(fid, s.book, reveal=True).get("tel")
    return {"has_phone": bool(masked.get("has_phone")), "phone": masked.get("phone"), "tel": tel,
            "note": masked.get("note")}


def _enrich_card(s: State, c: dict, *, service: str, line: Optional[str], text: Optional[str], origin_state: str,
                 loose: bool, demo: bool, reveal: bool, names: list[str], rx, sim_index: dict) -> None:
    fid = c.get("facility_id")

    def put(field: str, fn) -> None:
        try:
            c[field] = fn()
        except Exception as e:
            c.pop(field, None)
            s.note_error(f"card.{field}", e)

    name = str(c.get("name") or "")
    head, _, rest = name.partition(",")
    c["name_short"] = head.strip() or name
    c["name_rest"] = rest.strip() or None
    put("time", lambda: _time(c, loose))

    def state_disp():
        st = None
        if c.get("lat") is not None and c.get("lon") is not None:
            st = s.index.state_at(float(c["lat"]), float(c["lon"]))
        return nice_name(st or c.get("state")) or None
    put("state_display", state_disp)
    if c.get("state_display") and origin_state:
        c["crosses_state"] = c["state_display"] != origin_state
    else:
        c["crosses_state"] = False
    put("quotes", lambda: _quotes(c, line, names, rx))
    if service == FALLBACK_ID and isinstance(c.get("quotes"), list):
        for q in c["quotes"]:
            q["negated"] = negated_match(q.get("text"), text)
        c["match_negated"] = bool(c["quotes"]) and bool(c["quotes"][0].get("negated"))
    put("missing_phrases", lambda: _missing_phrases(c, names))
    if Q is not None:
        def qs():
            rec = s.infos.get(fid) if s.infos else None
            if service == FALLBACK_ID:
                got = Q.questions_for(c, "search", rec, service_text=text, max_questions=3)
            else:
                got = Q.questions_for(c, service, rec, max_questions=3)
            return [{"question": x.get("question"), "why": x.get("why")} for x in got][:3]
        put("questions", qs)
        if s.book is not None:
            put("call", lambda: _call(s, fid, reveal, demo))
    put("official_domain", lambda: _official_domain((c.get("sources") or {}).get("official_website")))
    if s.public is not None:
        put("public_list", lambda: to_jsonable(s.public.line(fid)) if service == "emoc" else None)
    elif "public_list" in c:
        c["public_list"] = None if service != "emoc" else c["public_list"]
    c["sim_index"] = sim_index.get(fid)


def _why_not_minutes(s: State, ans: dict) -> None:
    """Why-not rows get the same estimated drive the cards use (minutes from straight-line km and the
    origin's terrain factor), so the strip speaks in minutes like the cards. Rows with an approximate
    position (no km) get none. For a loose origin the minutes are from the middle of the place, as the
    cards' point minutes are."""
    if T is None:
        return
    try:
        f = (ans.get("window") or {}).get("factor")
        if f is None:
            return
        for it in (ans.get("why_not") or {}).get("items") or []:
            km = it.get("distance_km")
            if km is None:
                continue
            m = T.travel_minutes(float(km), factor=float(f))
            if m is not None:
                it["minutes"] = round(float(m), 1)
                it["time_short"] = "about " + T.fmt_minutes(m)
    except Exception as e:
        s.note_error("answer.why_not_minutes", e)


def _mask_unverified(s: State, ans: dict) -> None:
    """Demo mode: unverified_nearby names are masked like the why-not strip (the UI does not show them,
    but the payload must not carry them either)."""
    lst = ans.get("unverified_nearby") or []
    if not lst:
        return
    try:
        fids = s.index.fac["facility_id"].astype(str).to_numpy()
        pos_of = getattr(s, "_fid_pos", None)
        if pos_of is None or len(pos_of) != len(fids):
            pos_of = {f: i for i, f in enumerate(fids)}
            s._fid_pos = pos_of
        for c in lst:
            if not isinstance(c, dict):
                continue
            p = pos_of.get(str(c.get("facility_id")))
            masked = WN.masked_name(s.index, p) if (WN is not None and p is not None) else "Facility (name hidden)"
            old = str(c.get("name") or "")
            c["name"] = masked
            c["name_masked"] = True
            for k in ("name_short", "name_rest"):
                c.pop(k, None)
            if old and len(old) >= 3:
                rx = re.compile(re.escape(old), re.I)

                def scrub(o):
                    if isinstance(o, str):
                        return rx.sub(masked, o)
                    if isinstance(o, list):
                        return [scrub(x) for x in o]
                    if isinstance(o, dict):
                        return {k: (v if k == "facility_id" else scrub(v)) for k, v in o.items()}
                    return o
                c.update(scrub({k: v for k, v in c.items() if k != "name"}))
    except Exception as e:
        s.note_error("answer.mask_unverified", e)
        for c in lst:
            if isinstance(c, dict):
                c["name"] = "Facility (name hidden)"
                c["name_masked"] = True


def _why_not_extras(s: State, ans: dict, line: Optional[str], *, demo: bool, reveal: bool) -> None:
    """Why-not rows carry the same outside-list lines the cards carry (a LaQshya certificate on a public
    district hospital the rule did not pick), and public hospitals get the same masked Call button, so
    "call and ask" can be done. Nothing here changes which rows are listed or their order."""
    items = (ans.get("why_not") or {}).get("items") or []
    if not items:
        return
    from datetime import date
    for it in items:
        fid = str(it.get("facility_id") or "")
        if not fid:
            continue
        try:
            lines = []
            if s.outside is not None and hasattr(s.outside, "lines_for"):
                lines = list(s.outside.lines_for(fid, line, as_of=date.today()) or [])
            if line == "emoc" and s.public is not None:
                pl = s.public.line(fid)
                if pl and isinstance(pl, dict) and pl.get("text") and not any(
                        (x.get("text") or "").strip().lower() == pl["text"].strip().lower() for x in lines):
                    lines.insert(0, pl)
            it["outside_lines"] = to_jsonable(lines)
        except Exception as e:
            s.note_error("why_not.outside_lines", e)
        public = (str(it.get("operator") or "").lower() in ("public", "government")
                  or it.get("reason_code") == "public_record_silent")
        if public and Q is not None and s.book is not None:
            try:
                it["call"] = _call(s, fid, reveal, demo)
            except Exception as e:
                s.note_error("why_not.call", e)


def enrich_answer(s: State, service: str, res: Any, core: dict, *, text: Optional[str] = None,
                  demo: bool = False, reveal: bool = False) -> dict:
    """/api/answer = do_answer plus the SPEC 3.1 fields. Everything already there keeps its name and value
    (further_away loses the beyond_radius card, which is shown once). A failing part leaves its own fields
    out and is listed in /api/health extras_errors; this never raises."""
    import copy

    ans = copy.deepcopy(core["ans"])
    ans["demo_masked"] = bool(demo)
    try:
        loc = ans.get("location") or {}
        if ans.get("status") not in ("ok", "no_reliable_option") or loc.get("lat") is None:
            return ans
        win = core.get("window")
        line = service if service != FALLBACK_ID else None
        # window texts
        if win is not None:
            w = dict(ans.get("window") or {}) or dict(win)
            try:
                if T is not None:
                    w["hours_text"] = T.fmt_hours(w["minutes"])
                    if w.get("reach_minutes") is not None:
                        w["reach_text"] = T.fmt_minutes(w["reach_minutes"])
            except Exception as e:
                s.note_error("answer.window_text", e)
            ans["window"] = w
        loose = bool((ans.get("window") or {}).get("origin_loose", False))
        # origin
        origin_state = ""
        try:
            origin_state = nice_name(loc.get("state")) or nice_name(s.index.state_at(float(loc["lat"]), float(loc["lon"])))
            prec = loc.get("precision_km")
            kme = (ans.get("window") or {}).get("km_equivalent")
            ans["origin"] = {"short_name": rank._place_name(loc), "label": loc.get("label"), "precision_km": prec,
                             "loose": loose,
                             "too_coarse": bool(prec is not None and kme is not None and float(prec) > float(kme)),
                             "state_display": origin_state}
        except Exception as e:
            s.note_error("answer.origin", e)
        # further away: the beyond_radius card is listed once
        br = ans.get("beyond_radius")
        if br and isinstance(ans.get("further_away"), list):
            ans["further_away"] = [c for c in ans["further_away"] if c.get("facility_id") != br.get("facility_id")]
        # why not, funnel
        sim_status: dict[str, str] = {}
        sim_index: dict[str, int] = {}
        st = core.get("set")
        if st is not None:
            fids = s.index.fac["facility_id"].to_numpy()
            for i, p in enumerate(st["pos"]):
                sim_index[str(fids[p])] = i
                sim_status[str(fids[p])] = str(st["status"][p])
        if core.get("strip") is not None:
            strip = copy.deepcopy(core["strip"])
            for it in strip.get("items") or []:
                it["in_sim"] = sim_status.get(str(it.get("facility_id"))) in ("claim_only", "held_back")
            ans["why_not"] = strip
            _why_not_minutes(s, ans)
        if demo:
            _mask_unverified(s, ans)
        if core.get("funnel") is not None:
            ans["funnel"] = copy.deepcopy(core["funnel"])
        # outside lists (public_list v2), then the per-card fields
        if PL is not None and hasattr(PL, "annotate_answer") and s.outside is not None:
            try:
                PL.annotate_answer(ans, service, loc.get("lat"), loc.get("lon"), loc.get("state"), outside=s.outside)
                ans = to_jsonable(ans)
            except Exception as e:
                s.note_error("answer.outside_lists", e)
        _why_not_extras(s, ans, line, demo=demo, reveal=reveal)
        if service == FALLBACK_ID:
            ans["did_you_mean"] = did_you_mean(text)
        names = _requirement_names(line)
        rx = None
        if WN is not None and line is not None:
            try:
                rx = WN.literal_pattern(line)
            except Exception as e:
                s.note_error("answer.marks", e)
        br = ans.get("beyond_radius")
        cards = list(ans.get("results") or []) + ([br] if br else []) + list(ans.get("further_away") or [])
        for c in cards:
            if isinstance(c, dict) and c.get("facility_id"):
                _enrich_card(s, c, service=service, line=line, text=text, origin_state=origin_state, loose=loose,
                             demo=demo, reveal=reveal, names=names, rx=rx, sim_index=sim_index)
    except Exception as e:  # never a 500 because of an extra
        s.note_error("answer.enrich", e)
    return ans


# --------------------------------------------------------------------------------------------
# /api/sim (SPEC 3.2, 3.3, 7.3)
# --------------------------------------------------------------------------------------------

def _sim_world(s: State) -> dict:
    """Village points for every query, built once: post offices without placeholders and artefact
    latitudes, with usable coordinates, one per (lat, lon), each with its state's terrain factor."""
    w = getattr(s, "_sim_world_cache", None)
    if w is not None:
        return w
    po = s.index.post_offices
    if po is None or not len(po):
        w = {"lat": np.array([]), "lon": np.array([]), "pin": np.array([], dtype=object),
             "factor": np.array([]), "n_artefact": 0, "n_placeholder": 0}
        s._sim_world_cache = w
        return w
    po = po.dropna(subset=["lat", "lon"])
    n_ph = 0
    if "placeholder" in po:
        ph = po["placeholder"].fillna(False).astype(bool)
        n_ph = int(ph.sum())
        po = po[~ph]
    vc = po["lat"].value_counts()
    bad = vc[vc >= ARTEFACT_LAT_MIN_ROWS].index
    art = po["lat"].isin(bad)
    n_art = int(art.sum())
    # 5 decimals (about 1 m) is what the payload carries, so points are deduplicated at that precision
    po = po[~art].assign(lat=po["lat"].round(5), lon=po["lon"].round(5)).drop_duplicates(subset=["lat", "lon"])
    lat = po["lat"].to_numpy(float)
    lon = po["lon"].to_numpy(float)
    pin = po["pin"].astype(str).to_numpy() if "pin" in po else np.full(len(po), "", object)
    factor = np.full(len(po), np.nan)
    if T is not None and len(po):
        states = _states_at(s, lat, lon)
        fac_of: dict = {}
        for i, st in enumerate(states):
            if st not in fac_of:
                v = T.state_factor(st).value
                fac_of[st] = np.nan if v is None else float(v)
            factor[i] = fac_of[st]
    w = {"lat": lat, "lon": lon, "pin": pin, "factor": factor, "n_artefact": n_art, "n_placeholder": n_ph,
         "artefact_lats": [float(x) for x in bad]}
    s._sim_world_cache = w
    return w


def _states_at(s: State, lat: np.ndarray, lon: np.ndarray) -> list:
    """idx.state_at for many points at once: the state of the nearest PIN point (a k-d tree on unit
    vectors gives the same nearest point as the haversine argmin)."""
    idx = s.index
    if idx.pin_lat is None or not len(idx.pin_lat):
        return [None] * len(lat)
    plat, plon = np.asarray(idx.pin_lat, float), np.asarray(idx.pin_lon, float)
    ok = np.isfinite(plat) & np.isfinite(plon)
    pst = np.asarray(idx.pin_state)[ok]

    def xyz(a, b):
        a, b = np.radians(a), np.radians(b)
        return np.c_[np.cos(a) * np.cos(b), np.cos(a) * np.sin(b), np.sin(a)]
    try:
        from scipy.spatial import cKDTree
        _, j = cKDTree(xyz(plat[ok], plon[ok])).query(xyz(lat, lon))
        return [str(pst[k]) for k in j]
    except ImportError:  # pragma: no cover
        return [idx.state_at(float(a), float(b)) for a, b in zip(lat, lon)]


def sim_summary(v_lat, v_lon, v_fac, f_lat, f_lon, status, W: float, a: float, b: float) -> dict:
    """The SPEC 7.3 counters on the payload's own numbers (the browser runs the same rules).

    MIN[v][j] = factor[v] * a * KM[v][j]^b, KM haversine with R 6371.0088.
    within: nearestEv(v) <= W. avg_minutes: mean finite nearestEv, 1 decimal. first(v): argmin KM over
    all facilities (ties to the lower i). first_no_evidence: first(v) has no evidence. extra_minutes_mean:
    over those with a finite nearestEv, MIN[v][first] + factor[v]*a*km(first, j*)^b - nearestEv(v), where
    j* is the evidence facility nearest to first(v), 1 decimal."""
    v_lat, v_lon, v_fac = (np.asarray(x, float) for x in (v_lat, v_lon, v_fac))
    f_lat, f_lon = np.asarray(f_lat, float), np.asarray(f_lon, float)
    status = np.asarray(status, dtype=object)
    V, F = len(v_lat), len(f_lat)
    ev = status == "evidence"
    out = {"n_villages": int(V), "within": 0, "avg_minutes": None, "n_no_evidence_in_reach": int(V),
           "first_no_evidence": 0, "first_claim_only": 0, "first_held_back": 0, "extra_minutes_mean": None}
    if V == 0 or F == 0:
        return out
    near_ev = np.full(V, np.inf)
    first = np.zeros(V, dtype=np.int64)
    first_km = np.zeros(V)
    ev_idx = np.flatnonzero(ev)
    step = max(1, 2_000_000 // max(F, 1))
    for s0 in range(0, V, step):
        sl = slice(s0, min(V, s0 + step))
        km = _hav(v_lat[sl, None], v_lon[sl, None], f_lat[None, :], f_lon[None, :])
        first[sl] = np.argmin(km, axis=1)
        first_km[sl] = km[np.arange(km.shape[0]), first[sl]]
        if len(ev_idx):
            mins = v_fac[sl, None] * a * np.power(km[:, ev_idx], b)
            near_ev[sl] = mins.min(axis=1)
    fin = np.isfinite(near_ev)
    out["within"] = int((near_ev <= W).sum())
    out["n_no_evidence_in_reach"] = int((~fin).sum())
    if fin.any():
        out["avg_minutes"] = round(float(near_ev[fin].mean()), 1)
    no_ev = ~ev[first]
    out["first_no_evidence"] = int(no_ev.sum())
    out["first_claim_only"] = int((no_ev & (status[first] == "claim_only")).sum())
    out["first_held_back"] = int((no_ev & (status[first] == "held_back")).sum())
    sel = np.flatnonzero(no_ev & fin)
    if len(sel) and len(ev_idx):
        uf = np.unique(first[sel])
        kmf = _hav(f_lat[uf, None], f_lon[uf, None], f_lat[None, ev_idx], f_lon[None, ev_idx])
        jstar_km = dict(zip(uf.tolist(), kmf[np.arange(len(uf)), np.argmin(kmf, axis=1)].tolist()))
        leg2 = np.array([jstar_km[int(first[v])] for v in sel])
        m_first = v_fac[sel] * a * np.power(first_km[sel], b)
        extra = m_first + v_fac[sel] * a * np.power(leg2, b) - near_ev[sel]
        out["extra_minutes_mean"] = round(float(extra.mean()), 1)
    return out


def _context_labels(s: State, res: Any) -> list[dict]:
    """Map labels near the origin from the gazetteer: districts and cities within LABEL_KM (prio 1 for
    the origin's district, 2 otherwise), and post offices within LABEL_PO_KM thinned to one per
    LABEL_PO_CELL_KM grid cell by the most offices (prio 3). At most LABEL_MAX, by prio then distance."""
    p = s.resolver.places
    lat0, lon0 = float(res.lat), float(res.lon)
    kind = p["kind"].to_numpy()
    plat, plon = p["lat"].to_numpy(float), p["lon"].to_numpy(float)
    m = np.isin(kind, ("district", "city", "post_office")) & np.isfinite(plat) & np.isfinite(plon)
    m &= np.abs(plat - lat0) < LABEL_KM / 100.0
    pos = np.flatnonzero(m)
    dk = haversine_km(lat0, lon0, plat[pos], plon[pos])
    dist_o = (getattr(res, "district", None) or "").strip().upper()
    names = p["name"].to_numpy()
    dists = p["district"].to_numpy() if "district" in p else np.full(len(p), "", object)
    nof = p["n_offices"].to_numpy() if "n_offices" in p else np.zeros(len(p))
    out = []
    big = pos[(dk <= LABEL_KM) & np.isin(kind[pos], ("district", "city"))]
    dbig = dk[(dk <= LABEL_KM) & np.isin(kind[pos], ("district", "city"))]
    for q, dd in zip(big, dbig):
        k = str(kind[q])
        prio = 1 if (k == "district" and dist_o and str(dists[q]).strip().upper() == dist_o) else 2
        out.append({"name": nice_name(str(names[q])), "kind": k, "lat": round(float(plat[q]), 5),
                    "lon": round(float(plon[q]), 5), "prio": prio, "_d": float(dd)})
    pom = (dk <= LABEL_PO_KM) & (kind[pos] == "post_office")
    po_pos, po_d = pos[pom], dk[pom]
    if len(po_pos):
        cy = np.floor((plat[po_pos] - lat0) * 111.2 / LABEL_PO_CELL_KM).astype(int)
        cx = np.floor((plon[po_pos] - lon0) * 111.2 * math.cos(math.radians(lat0)) / LABEL_PO_CELL_KM).astype(int)
        best: dict = {}
        for q, dd, a, b in zip(po_pos, po_d, cy, cx):
            cur = best.get((a, b))
            key = (-float(nof[q]), float(dd))
            if cur is None or key < cur[0]:
                best[(a, b)] = (key, q, dd)
        for _, q, dd in best.values():
            out.append({"name": nice_name(str(names[q])), "kind": "post_office", "lat": round(float(plat[q]), 5),
                        "lon": round(float(plon[q]), 5), "prio": 3, "_d": float(dd)})
    out.sort(key=lambda r: (r["prio"], r["_d"], r["name"]))
    seen, labels = set(), []
    for r in out:
        k = (r["name"].lower(), r["kind"])
        if k in seen:
            continue
        seen.add(k)
        r.pop("_d")
        labels.append(r)
        if len(labels) >= LABEL_MAX:
            break
    return labels


def _sim_unavailable(service: str, reason: str, **extra: Any) -> dict:
    label = S.SERVICES[service].label if service in S.SERVICES else None
    return {"status": "unavailable", "version": SIM_VERSION, "service": service, "service_label": label,
            "reason": reason, **extra}


def build_sim(s: State, service: str, res: Any, core: dict, demo: bool) -> dict:
    """The /api/sim payload (SPEC 3.3): villages, facilities with their evidence status, the window,
    the drive-time model, the counters by the SPEC 7.3 rules, and map labels."""
    idx = s.index
    if T is None:
        return _sim_unavailable(service, "The drive-time model is not loaded.")
    win = core.get("window")
    st = core.get("set")
    if win is None or st is None:
        return _sim_unavailable(service, "The trip view could not be built for this place.")
    origin_factor = win.get("factor")
    if origin_factor is None or win.get("sea_travel_not_modelled"):
        return _sim_unavailable(service, "Drive times are not modelled for island origins.")
    ans = core["ans"]
    loc = ans.get("location") or rank._location_dict(res)
    lat0, lon0 = float(res.lat), float(res.lon)
    W, A = float(win["minutes"]), float(win.get("allowance") or 0.0)
    a, b = float(T.A_MIN), float(T.B_EXP)

    # villages
    world = _sim_world(s)
    dv = haversine_km(lat0, lon0, world["lat"], world["lon"]) if len(world["lat"]) else np.array([])
    vsel = np.flatnonzero(dv <= SIM_VILLAGE_KM)
    vsel = vsel[np.argsort(dv[vsel], kind="stable")][:SIM_MAX_VILLAGES]
    v_lat = np.round(world["lat"][vsel], 5)
    v_lon = np.round(world["lon"][vsel], 5)
    v_fac = world["factor"][vsel].copy()
    v_fac[~np.isfinite(v_fac)] = float(origin_factor)
    v_fac = np.round(v_fac, 4)

    # facilities
    fac = idx.fac
    pos = st["pos"]
    d = st["d"]
    status = st["status"]
    grade = st["grade"]
    approx = np.asarray(idx.approx, bool) if getattr(idx, "approx", None) is not None else np.zeros(len(fac), bool)
    cards = st["cards"]
    letters = {str(it.get("facility_id")): it.get("letter") for it in ((core.get("strip") or {}).get("items") or [])}
    fids = fac["facility_id"].to_numpy()
    names = fac["name"].to_numpy()
    ops = fac["operator_type"].to_numpy() if "operator_type" in fac else np.full(len(fac), None, object)
    facilities = []
    for i, p in enumerate(pos):
        p = int(p)
        fid = str(fids[p])
        masked = bool(demo and p not in cards and WN is not None)
        nm = WN.masked_name(idx, p) if masked else rank._str_or_none(names[p]) or "(no name)"
        op = rank._str_or_none(ops[p])
        facilities.append({
            "i": i, "facility_id": fid, "name": nm,
            "lat": round(float(idx.lat[p]), 5), "lon": round(float(idx.lon[p]), 5),
            "status": str(status[p]), "strong": bool(status[p] == "evidence" and grade[p] >= 3),
            "grade": int(grade[p]), "approximate": bool(approx[p]), "operator": op,
            "km_from_origin": round(float(d[p]), 1), "card_rank": cards.get(p),
            "why_not_letter": letters.get(fid), "name_masked": masked})
    f_lat = np.array([f["lat"] for f in facilities], float)
    f_lon = np.array([f["lon"] for f in facilities], float)
    f_status = np.array([f["status"] for f in facilities], dtype=object)
    summary = sim_summary(v_lat, v_lon, v_fac, f_lat, f_lon, f_status, W, a, b)
    summary.update({"n_facilities": len(facilities), "n_evidence": int((f_status == "evidence").sum()),
                    "n_claim_only": int((f_status == "claim_only").sum()),
                    "n_held_back": int((f_status == "held_back").sum()),
                    "n_listing_records": int(st["n_listing"]), "n_merged_duplicates": int(st["n_merged"])})
    basis = str(win.get("basis") or "")
    mm = re.match(r"^(.*?[.!?])(\s|$)", basis)
    km_eq = win.get("km_equivalent")
    reach_m = win.get("reach_minutes") or float(getattr(S, "REACH_FACTOR_V1", 3.0)) * W
    out = {
        "status": "ok", "version": SIM_VERSION, "service": service, "service_label": S.SERVICES[service].label,
        "service_phrase": rank._label_lc(service),
        "origin": {"lat": lat0, "lon": lon0, "short_name": rank._place_name(loc), "label": loc.get("label"),
                   "precision_km": loc.get("precision_km"),
                   "state_display": nice_name(loc.get("state")) or nice_name(idx.state_at(lat0, lon0)),
                   "factor": float(origin_factor)},
        "window": {"minutes": W, "allowance": A, "hours_text": T.fmt_hours(W),
                   "km_equivalent": km_eq if km_eq is not None else round(float(T.window_km(W, factor=origin_factor)), 1),
                   "reach_km": win.get("reach_km") if win.get("reach_km") is not None
                   else round(float(T.window_km(reach_m, factor=origin_factor)), 1),
                   "reach_minutes": float(reach_m), "basis_short": (mm.group(1) if mm else basis) or None},
        "travel": {"a": a, "b": b, "text": T.MODEL_TEXT},
        "villages": {"n": int(len(vsel)), "radius_km": SIM_VILLAGE_KM, "lon": v_lon.tolist(), "lat": v_lat.tolist(),
                     "pin": [str(x) for x in world["pin"][vsel]], "factor": v_fac.tolist()},
        "facilities": facilities,
        "summary": summary,
        "context": {"labels": []},
        "notes": ["Straight-line paths; times from the drive-time estimate, free-flow.",
                  "Each village is a post office area; offices sharing one point are one village."],
    }
    try:
        out["context"]["labels"] = _context_labels(s, res)
    except Exception as e:
        s.note_error("sim.labels", e)
    return out


SIM_LABELS = {"nearest_claim":"Nearest facility that claims it", "nearest_evidence": "Nearest with evidence",
              "ranked": "Our ranking"}


def simulate_available(s: State, service: Optional[str] = None) -> bool:
    lines = [service] if service else list(s.index.lines)
    return any(s.index.is_probability(line) for line in lines)


# --------------------------------------------------------------------------------------------
# App
# --------------------------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        state()
    except ApiError:  # keep serving /api/health so the failure is visible
        pass
    yield


app = FastAPI(title="Find care you can trust", version="0.3", lifespan=lifespan)


@app.exception_handler(ApiError)
async def _api_error(request, exc: ApiError) -> JSONResponse:
    # detail is a plain sentence the front end shows as is (app/src/api.ts reads body.detail)
    return JSONResponse({"detail": exc.message, **to_jsonable(exc.extra)}, status_code=exc.status)


def _timed(payload: dict, t0: float) -> JSONResponse:
    ms = (time.perf_counter() - t0) * 1000
    payload.setdefault("timing_ms", round(ms, 1))
    return JSONResponse(payload, headers={"X-Elapsed-Ms": f"{ms:.1f}"})


def _resolve_or_400(s: State, q: Optional[str], st: Optional[str], place_id: Optional[str]) -> locate.Resolution:
    if not (q and q.strip()) and not place_id:
        raise ApiError(400, "Type a place: a PIN, city, district, village or coordinates.")
    return s.resolve(q or "", st, place_id)


@app.get("/api/health")
def api_health() -> JSONResponse:
    try:
        s = state()
    except ApiError as e:
        return JSONResponse({"ok": False, "vf_from": _VF_FROM, "error": e.message}, status_code=503)
    idx = s.index
    return JSONResponse(to_jsonable({
        "ok": True, "mode": s.mode, "data_root": s.data_root, "vf_from": _VF_FROM,
        "n_facilities": len(idx.fac), "graded_lines": list(idx.graded_lines),
        "recipe_version": idx.recipe_version, "grades_method": idx.grades_method,
        "flags_source": idx.flags_source, "n_places": len(s.resolver.places), "n_trip_points": int(len(s.po_lat)),
        "probability_lines": [l for l in idx.lines if idx.is_probability(l)],
        "simulate_available": simulate_available(s), "search_available": True,
        "static": STATIC_DIR.exists(), "load_ms": round(s.load_ms, 1), "manifest": s.manifest,
        "extras": {"record_info": getattr(s, "infos", None) is not None,
                   "phone_book": getattr(s, "book", None) is not None,
                   "public_list": getattr(s, "public", None) is not None,
                   "outside_lists": getattr(s, "outside", None) is not None,
                   "why_not": WN is not None, "travel": T is not None,
                   "load_ms": getattr(s, "extras_ms", {})},
        "extras_errors": dict(getattr(s, "extras_errors", {}) or {}),
        "sim_version": SIM_VERSION,
    }))


@app.get("/api/services")
def api_services() -> JSONResponse:
    s = state()
    return JSONResponse(to_jsonable({
        "services": s.services,
        "fallback": {"id": FALLBACK_ID, "label": "Something else", "needs_text": True},
        "simulate_available": simulate_available(s),
    }))


@app.get("/api/resolve")
def api_resolve(q: str = Query("", description="PIN, city, district, village or coordinates"),
                state_: Optional[str] = Query(None, alias="state"),
                place_id: Optional[str] = Query(None, description="a candidate picked after an ambiguous place")
                ) -> JSONResponse:
    t0 = time.perf_counter()
    s = state()
    return _timed(to_jsonable(_resolve_or_400(s, q, state_, place_id)), t0)


@app.get("/api/answer")
def api_answer(service: str = Query(""), q: str = Query(""),
               state_: Optional[str] = Query(None, alias="state"),
               text: Optional[str] = Query(None, description="free text for 'Something else'"),
               place_id: Optional[str] = Query(None),
               demo: Optional[str] = Query(None, description="1 masks names and phone links (can only turn masking on)"),
               reveal: Optional[str] = Query(None, description="1 adds a tel: link to Call (never in demo mode)")
               ) -> JSONResponse:
    t0 = time.perf_counter()
    s = state()
    if not service and not text:
        raise ApiError(400, "Pick the care needed.")
    service = service or FALLBACK_ID
    if service != FALLBACK_ID:
        s.service(service)
    res = _resolve_or_400(s, q, state_, place_id)
    if service == FALLBACK_ID and (not text or not text.strip()):
        raise ApiError(400, "For 'Something else', type the care needed.")
    dm = demo_on(demo)
    core = _core(s, service, res, text, dm)
    return _timed(enrich_answer(s, service, res, core, text=text, demo=dm, reveal=_flag(reveal)), t0)


@app.get("/api/sim")
def api_sim(service: str = Query(...), q: str = Query(""),
            state_: Optional[str] = Query(None, alias="state"),
            place_id: Optional[str] = Query(None),
            demo: Optional[str] = Query(None)) -> JSONResponse:
    """The agent simulator's world for one query (SPEC 3.2): villages, facilities with their evidence
    status, the window, the drive-time model, the baseline counters and map labels."""
    t0 = time.perf_counter()
    s = state()
    if service == FALLBACK_ID:
        return _timed(_sim_unavailable(service, "The trip view needs one of the listed services."), t0)
    s.service(service)
    res = _resolve_or_400(s, q, state_, place_id)
    if res.status != "ok" or res.lat is None or res.lon is None:
        return _timed(_sim_unavailable(service, "Pick a single place first.", location=to_jsonable(res)), t0)
    if service not in s.index.lines:
        return _timed(_sim_unavailable(service, "This service has no grades loaded."), t0)
    dm = demo_on(demo)
    key = ("sim", service, _res_key(res), dm)
    hit = s.cache.get(key)
    if hit is None:
        try:
            core = _core(s, service, res, None, dm)
            hit = build_sim(s, service, res, core, dm)
        except ApiError:
            raise
        except Exception as e:
            s.note_error("sim.build", e)
            return _timed(_sim_unavailable(service, "The trip view could not be built for this place."), t0)
        hit = to_jsonable(hit)
        s.cache.put(key, hit)
    return _timed(dict(hit), t0)


@app.get("/api/trip")
def api_trip(service: str = Query(...), q: str = Query(""),
             state_: Optional[str] = Query(None, alias="state"),
             place_id: Optional[str] = Query(None)) -> JSONResponse:
    t0 = time.perf_counter()
    s = state()
    if service == FALLBACK_ID:
        return _timed({"status": "unavailable", "service": service,
                       "reason": "The trip view needs one of the listed services."}, t0)
    radius = float(s.service(service).radius_km)
    res = _resolve_or_400(s, q, state_, place_id)
    if res.status != "ok":
        return _timed({"status": "unavailable", "service": service, "radius_km": radius,
                       "reason": "Pick a single place first.", "location": to_jsonable(res),
                       "facilities": [], "points": [], "nearest_recommended": [], "nearest_claim": []}, t0)
    ans = do_answer(s, service, res, None)
    return _timed(build_trip(s, service, res, ans), t0)


@app.get("/api/simulate")
def api_simulate(service: str = Query(...), q: str = Query(""),
                 state_: Optional[str] = Query(None, alias="state"),
                 place_id: Optional[str] = Query(None),
                 n_origins: Optional[int] = Query(None, ge=1, le=MAX_TRIP_POINTS)) -> JSONResponse:
    t0 = time.perf_counter()
    s = state()
    if service != FALLBACK_ID:
        s.service(service)
    if service == FALLBACK_ID or not simulate_available(s, service):
        raise ApiError(404, "Simulation is not available yet for this service: it needs calibrated "
                            "probabilities (gold/service_probability.parquet, WP13). No probabilities are made up.")
    res = _resolve_or_400(s, q, state_, place_id)
    if res.status != "ok":
        raise ApiError(409, "Pick a single place first.", location=to_jsonable(res))
    raw = to_jsonable(rank.simulate(s.index, service, res, policy=None, n_origins=n_origins or S.SIM_DEFAULT_ORIGINS))
    pols = raw.pop("policies", {}) or {}
    policies = [{"policy": name, "label": SIM_LABELS.get(name, name), "expected_km": p.get("expected_km_mean"), **p}
                for name, p in pols.items()]
    return _timed({"status": "ok", **raw, "location": to_jsonable(res), "policies": policies,
                   "notes": ["Expected straight-line km until reaching a facility that offers the service, "
                             "when each facility offers it with its estimated probability."]}, t0)


if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
else:
    @app.get("/", response_class=HTMLResponse)
    def index_placeholder() -> str:
        return ("<!DOCTYPE html><html><head><meta charset='utf-8'><title>Find care you can trust</title></head><body>"
                "<p>The front end is not built yet (dbapp/static is missing). The API is live: "
                "<a href='/api/health'>/api/health</a>, <a href='/api/services'>/api/services</a>.</p>"
                "</body></html>")


if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    port = int(os.environ.get("DATABRICKS_APP_PORT") or os.environ.get("PORT") or 8000)
    host = "0.0.0.0" if "DATABRICKS_APP_PORT" in os.environ else "127.0.0.1"
    uvicorn.run(app, host=host, port=port)
