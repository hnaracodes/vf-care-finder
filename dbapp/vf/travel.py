"""Estimated drive time from straight-line distance (score spec v1-final, D048).

    minutes = state factor x 2.30 x km ** 0.842

Fitted to 934 OSRM free-flow car trips from 30 anchor post offices (score
research, Nov 21): leave-one-anchor-out log error SD 0.26, and the 95% band
``minutes x exp(+-0.52)`` covered 94.6% of held-out trips. The state factor
(``reference/terrain_multiplier.csv``) is the median ratio of MAP friction-surface
minutes to the formula in that state; it cut the fallback's disagreement with MAP
in hill and northeast states from 12.6% to 7.4% (emoc). The factor is the
origin's state, for every facility, so for one origin minutes rise strictly with
km and "nearest by minutes" is "nearest by km".

Islands (Andaman and Nicobar, Lakshadweep) have no factor: sea travel is not
modelled, so no minutes are given there and callers fall back to km.

Everything here is free-flow road time from a straight line: real trips take
longer (traffic, waiting, finding transport). Never show these as real trip times.

    from vf import travel
    travel.travel_minutes(30, "BIHAR")          # 41.1
    travel.band(41.1)                           # (24.4, 69.1)
    travel.window_km(120, "RAJASTHAN")          # 98.9: straight-line km of a 120-minute window
"""
from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Mapping, Optional

import numpy as np

#: Fit constants (D048): minutes = factor x A x km ** B.
A_MIN = 2.30
B_EXP = 0.842
#: Leave-one-anchor-out SD of log(OSRM minutes / fitted minutes).
LOG_SD = 0.26
#: Half-width of the 95% band on the log scale (2 x LOG_SD).
BAND_LOG = 0.52
#: Share of held-out OSRM trips inside the band.
BAND_COVERAGE = 0.946
N_TRIPS = 934
N_ANCHORS = 30

TRAVEL_VERSION = "travel_v1 (2.30 x km^0.842, state factor)"
SEA_NOTE = "sea travel not modelled"
FREE_FLOW_NOTE = "estimated drive time from straight-line distance, free-flow; real trips take longer"

MODEL_TEXT = (
    f"Estimated drive time = state terrain factor x {A_MIN:.2f} x km^{B_EXP} minutes, from the straight-line "
    f"km. Fitted to {N_TRIPS} free-flow car trips (OSRM) from {N_ANCHORS} post offices. Error: leave-one-anchor-out "
    f"log SD {LOG_SD}; the shown range (x e^-{BAND_LOG} to x e^+{BAND_LOG}) held {BAND_COVERAGE:.1%} of held-out trips. "
    "The factor is the origin state's (reference/terrain_multiplier.csv). Free-flow means no traffic and no waiting: "
    "real trips take longer. Islands: sea travel is not modelled, so no minutes are shown there."
)

#: The factor table as shipped (reference/terrain_multiplier.csv, Nov 21). The
#: CSV is read when present; this copy keeps the app working without it.
_FACTORS_BUILTIN: dict[str, Optional[float]] = {
    "ANDAMAN AND NICOBAR ISLANDS": None, "ANDHRA PRADESH": 0.96, "ARUNACHAL PRADESH": 3.08, "ASSAM": 1.11,
    "BIHAR": 1.02, "CHANDIGARH": 0.8, "CHHATTISGARH": 1.24, "DELHI": 0.8, "GOA": 0.8, "GUJARAT": 0.98,
    "HARYANA": 0.86, "HIMACHAL PRADESH": 1.06, "JAMMU AND KASHMIR": 0.85, "JHARKHAND": 1.07, "KARNATAKA": 0.93,
    "KERALA": 0.83, "LADAKH": 1.39, "LAKSHADWEEP": None, "MADHYA PRADESH": 1.12, "MAHARASHTRA": 0.96,
    "MANIPUR": 1.32, "MEGHALAYA": 1.4, "MIZORAM": 1.29, "NAGALAND": 1.16, "ODISHA": 1.11, "PUDUCHERRY": 0.86,
    "PUNJAB": 0.93, "RAJASTHAN": 1.09, "SIKKIM": 0.94, "TAMIL NADU": 0.85, "TELANGANA": 1.03,
    "THE DADRA AND NAGAR HAVELI AND DAMAN AND DIU": 0.85, "TRIPURA": 1.3, "UTTAR PRADESH": 1.01,
    "UTTARAKHAND": 1.1, "WEST BENGAL": 1.04,
}

#: Spellings that differ from the PIN directory's (the table's) state names.
_STATE_ALIASES = {
    "ORISSA": "ODISHA", "PONDICHERRY": "PUDUCHERRY", "UTTARANCHAL": "UTTARAKHAND", "NCT OF DELHI": "DELHI",
    "NEW DELHI": "DELHI", "JAMMU & KASHMIR": "JAMMU AND KASHMIR", "ANDAMAN & NICOBAR ISLANDS": "ANDAMAN AND NICOBAR ISLANDS",
    "DADRA AND NAGAR HAVELI AND DAMAN AND DIU": "THE DADRA AND NAGAR HAVELI AND DAMAN AND DIU",
    "DADRA AND NAGAR HAVELI": "THE DADRA AND NAGAR HAVELI AND DAMAN AND DIU",
    "DAMAN AND DIU": "THE DADRA AND NAGAR HAVELI AND DAMAN AND DIU",
}


def _csv_path() -> Optional[Path]:
    here = Path(__file__).resolve()
    for p in (here.parents[2] / "reference" / "terrain_multiplier.csv",  # src/vf -> repo root
              here.parents[1] / "bundle" / "terrain_multiplier.csv",    # dbapp/vf -> dbapp/bundle
              here.parent / "terrain_multiplier.csv"):
        if p.exists():
            return p
    return None


def read_factor_table(path=None) -> dict[str, Optional[float]]:
    """State -> factor (None for islands, where sea travel is not modelled)."""
    out: dict[str, Optional[float]] = {}
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            v = (r.get("multiplier") or "").strip()
            out[r["state"].strip().upper()] = float(v) if v else None
    return out


@lru_cache(maxsize=1)
def factor_table() -> dict[str, Optional[float]]:
    p = _csv_path()
    if p is None:
        return dict(_FACTORS_BUILTIN)
    try:
        return read_factor_table(p)
    except Exception:  # a half-written file: the shipped copy is the same table
        return dict(_FACTORS_BUILTIN)


def norm_state(state: Optional[str]) -> Optional[str]:
    if state is None:
        return None
    s = " ".join(str(state).replace("&", "AND").upper().split())
    if not s or s in ("NULL", "NAN", "NONE"):
        return None
    if s in factor_table():
        return s
    if s in _STATE_ALIASES:
        return _STATE_ALIASES[s]
    if "THE " + s in factor_table():
        return "THE " + s
    return None


@dataclass(frozen=True)
class Factor:
    """The terrain factor for one origin. ``value`` is None for islands
    (``sea``), where no minutes are estimated."""
    state: Optional[str]
    value: Optional[float]
    source: str  # "state table" | "island: sea travel not modelled" | "state unknown: no factor (1.00)" | "off"

    @property
    def sea(self) -> bool:
        return self.value is None

    @property
    def text(self) -> str:
        if self.sea:
            return f"{_nice(self.state)}: {SEA_NOTE}"
        if self.source == "state table":
            return f"terrain factor {self.value:.2f} ({_nice(self.state)})"
        return f"terrain factor {self.value:.2f} ({self.source})"


def _nice(s: Optional[str]) -> str:
    if not s:
        return "state unknown"
    words = s.title().split()
    if words and words[0] == "The" and len(words) > 1:
        words = words[1:]
    return " ".join(w.lower() if i and w in ("And", "Of") else w for i, w in enumerate(words))


def state_factor(state: Optional[str], *, terrain: bool = True) -> Factor:
    """The origin's factor. ``terrain=False`` gives 1.00 everywhere (sensitivity
    check), except that islands still get no minutes."""
    s = norm_state(state)
    tab = factor_table()
    if s is not None and s in tab and tab[s] is None:
        return Factor(s, None, f"island: {SEA_NOTE}")
    if not terrain:
        return Factor(s, 1.0, "off")
    if s is not None and tab.get(s) is not None:
        return Factor(s, float(tab[s]), "state table")
    return Factor(s, 1.0, "state unknown: no factor (1.00)")


def travel_minutes(km, state=None, *, factor: Optional[float] = None, terrain: bool = True):
    """Estimated free-flow drive minutes for straight-line ``km`` (a number or
    an array) from an origin in ``state``. None (or NaN in an array) where sea
    travel is not modelled. ``factor`` overrides the state lookup."""
    f = factor if factor is not None else state_factor(state, terrain=terrain).value
    if f is None:
        if np.ndim(km):
            return np.full(np.shape(km), np.nan)
        return None
    k = np.maximum(np.asarray(km, dtype=float), 0.0)
    t = f * A_MIN * np.power(k, B_EXP)
    if np.ndim(km):
        return t
    t = float(t)
    return None if math.isnan(t) else t


def band(minutes: Optional[float]) -> tuple[Optional[float], Optional[float]]:
    """The 95% band (minutes x e^-0.52, minutes x e^+0.52)."""
    if minutes is None or (isinstance(minutes, float) and math.isnan(minutes)):
        return None, None
    return float(minutes) * math.exp(-BAND_LOG), float(minutes) * math.exp(BAND_LOG)


def window_km(window_min: float, state=None, *, factor: Optional[float] = None, terrain: bool = True) -> Optional[float]:
    """Straight-line km whose estimate equals ``window_min``: (W / (2.30 f))^(1/0.842)."""
    f = factor if factor is not None else state_factor(state, terrain=terrain).value
    if f is None:
        return None
    return float((float(window_min) / (A_MIN * f)) ** (1.0 / B_EXP))


def origin_is_loose(origin_precision_km: Optional[float], window_min: float, factor: Optional[float]) -> bool:
    """D048: the origin is too loose for one number when its precision is more
    than a quarter of the window's straight-line equivalent."""
    if origin_precision_km is None or factor is None:
        return False
    k = window_km(window_min, factor=factor)
    return k is not None and float(origin_precision_km) > k / 4.0


def minutes_range(km: float, factor: Optional[float], origin_precision_km: Optional[float] = None,
                  loose: bool = False) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """(point, low, high) minutes. The range is the 95% band; for a loose origin
    it also spans the origin's precision (km - p to km + p)."""
    if factor is None or km is None or (isinstance(km, float) and math.isnan(km)):
        return None, None, None
    t = travel_minutes(km, factor=factor)
    if loose and origin_precision_km:
        lo_km, hi_km = max(float(km) - float(origin_precision_km), 0.0), float(km) + float(origin_precision_km)
        lo = travel_minutes(lo_km, factor=factor) * math.exp(-BAND_LOG)
        hi = travel_minutes(hi_km, factor=factor) * math.exp(BAND_LOG)
        return t, lo, hi
    lo, hi = band(t)
    return t, lo, hi


def fmt_minutes(m: float) -> str:
    """'8 min', '25 min', '1 h 35 min', '4 h'. Rounded to 1 min under 10, else to 5."""
    m = float(m)
    r = int(round(m)) if m < 10 else int(5 * round(m / 5))
    r = max(r, 1)
    if r < 60:
        return f"{r} min"
    h, mm = divmod(r, 60)
    return f"{h} h" if mm == 0 else f"{h} h {mm} min"


def fmt_hours(window_min: float) -> str:
    """'2 hours', '1.5 hours', '1 hour', '90 minutes' style for a window."""
    h = float(window_min) / 60.0
    if abs(h - 1.0) < 1e-9:
        return "1 hour"
    if abs(h - round(h)) < 1e-9:
        return f"{int(round(h))} hours"
    return f"{h:.1f} hours"


def minutes_label(point: Optional[float], low: Optional[float], high: Optional[float], *, loose: bool = False,
                  place: Optional[str] = None, sea: bool = False, approximate: bool = False,
                  span_km: Optional[float] = None) -> str:
    """The card's drive-time line. ``span_km``: the searched place is wider than the
    window reaches, so a range would run from minutes to hours; say so instead."""
    if sea or point is None:
        return f"drive time not estimated: {SEA_NOTE}"
    if span_km is not None:
        where = place or "the place you searched"
        s = (f"drive time unclear: {where} spans about {float(span_km):.0f} km, so it depends on where you start; "
             "search a PIN code or town for an estimate")
        if approximate:
            s += " (the facility's position is approximate)"
        return s
    if loose and low is not None and high is not None:
        where = place or "the place you searched"
        s = (f"about {fmt_minutes(low)} to {fmt_minutes(high)} estimated drive, free-flow, depends on where in "
             f"{where} you start; real trips take longer")
    else:
        s = f"about {fmt_minutes(point)} estimated drive, free-flow; real trips take longer"
    if approximate:
        s += " (the facility's position is approximate)"
    return s


def origin_factor(res: Mapping | object, state_lookup=None, *, terrain: bool = True) -> Factor:
    """The factor for a resolution: its ``state`` when the table knows it,
    else ``state_lookup(lat, lon)`` (nearest PIN point's state), else 1.00."""
    get = (lambda k: res.get(k)) if isinstance(res, Mapping) else (lambda k: getattr(res, k, None))
    s = norm_state(get("state"))
    if s is None and state_lookup is not None and get("lat") is not None and get("lon") is not None:
        try:
            s = norm_state(state_lookup(float(get("lat")), float(get("lon"))))
        except Exception:
            s = None
    return state_factor(s, terrain=terrain)
