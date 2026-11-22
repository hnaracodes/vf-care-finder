"""Databricks App server (WP8, D033).

FastAPI app that loads the bundle once at start and answers in process with the tested engine:
vf.rank (ranking, keyword fallback, simulation), vf.locate (the place resolver) and vf.services
(names, radii, defaults). There is no stub engine: if one of them cannot load, /api/health says why
and every other endpoint answers 503.

    GET /api/health                              what is loaded, from where
    GET /api/services                            the service lines (vf.services)
    GET /api/resolve?q=&state=&place_id=         place text to a vf.locate Resolution
    GET /api/answer?service=&q=&state=&text=     vf.rank.rank, or vf.rank.search for service=other + text
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
            self.load_ms = (time.perf_counter() - t0) * 1000
            self.loaded = True
            log.info("loaded in %.0f ms: mode=%s vf=%s", self.load_ms, self.mode, _VF_FROM)

    # ---- helpers -------------------------------------------------------------------------

    def service(self, sid: str) -> S.Service:
        if sid not in S.SERVICES:
            raise ApiError(400, f"Unknown service '{sid}'. Pick one of the listed services, or 'other' with text.",
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
        ans = rank.search(s.index, text.strip(), res, k=3)
    else:
        s.service(service)
        ans = rank.rank(s.index, service, res, k=3)
    return to_jsonable(ans)


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
                           "lon": float(c["lon"]), "kind": "recommended", "rank": r, "supported": True})
    claim = np.flatnonzero(np.isfinite(f_lat) & np.isfinite(f_lon) & (grade >= 1))
    if len(claim):
        claim = claim[haversine_km(lat, lon, f_lat[claim], f_lon[claim]) <= S.REACH_FACTOR * radius]
    claim_supported = (grade[claim] >= 2) & ~blocked[claim]
    claim_fidx = []
    for p, sup in zip(claim, claim_supported):
        fid = f_ids[p]
        if fid not in pos_of:
            pos_of[fid] = len(facilities)
            facilities.append({"facility_id": fid, "name": f_names[p], "lat": float(f_lat[p]),
                               "lon": float(f_lon[p]), "kind": "recommended" if sup else "claim_only",
                               "rank": None, "supported": bool(sup)})
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
    cl_t = np.full(n, -1)
    if len(claim) and n:
        dcl = np.stack([haversine_km(plat, plon, f_lat[p], f_lon[p]) for p in claim], axis=1)
        k = dcl.argmin(axis=1)
        cl_t, cl_km = claim_fidx[k], dcl.min(axis=1)
        unsupported = ~claim_supported[k]
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


SIM_LABELS = {"nearest_claim": "Nearest facility that claims it", "nearest_evidence": "Nearest with evidence",
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
               place_id: Optional[str] = Query(None)) -> JSONResponse:
    t0 = time.perf_counter()
    s = state()
    if not service and not text:
        raise ApiError(400, "Pick the care needed.")
    service = service or FALLBACK_ID
    if service != FALLBACK_ID:
        s.service(service)
    res = _resolve_or_400(s, q, state_, place_id)
    return _timed(do_answer(s, service, res, text), t0)


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
