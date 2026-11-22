"""Reach for the ranker: travel-time estimate + clinical window -> reach in [0, 1].

numpy + scipy only (both already in dbapp/requirements.txt). Raster: MAP 2019 motorized friction
surface (Weiss et al. 2020, CC BY 4.0), India crop 68-98E, 6-37.5N, 1/120 deg, ocean masked,
log-quantized to uint8 (255 = impassable), 5.2 MB npz (under the 10 MB Apps file limit).
"""
from __future__ import annotations
import math
import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import dijkstra
from scipy.special import ndtr

LAT0, LON0, PX, H_M = 37.5, 68.0, 1 / 120, 927.67

# window_min: one-way travel budget; sigma: log-scale softness (0.3 emergency, 0.5 elective)
WINDOWS = {
    "emoc": (120, 0.3), "trauma": (120, 0.3), "icu": (120, 0.3), "nicu": (120, 0.3), "ct": (120, 0.3),
    "cath_lab": (90, 0.3), "neurosurgery": (150, 0.3), "burns": (240, 0.3),
    "dialysis": (60, 0.5), "mri": (180, 0.5), "cardiac_surgery": (240, 0.5), "oncology": (240, 0.5),
}


def load_friction(path):
    z = np.load(path)
    q, lo, hi = z["q"], float(z["lo"]), float(z["hi"])
    f = (lo * np.exp(q.astype(np.float32) / 254 * math.log(hi / lo))).astype(np.float32)
    f[q == 255] = 1.0  # min per metre: effectively impassable (sea)
    return f


def _rc(lat, lon):
    return int((LAT0 - lat) / PX), int((lon - LON0) / PX)


def travel_minutes(F, lat, lon, cand_lat, cand_lon, max_min=480.0, half_deg=None):
    """Least-cost free-flow minutes from (lat, lon) to each candidate. NaN if outside crop or > max_min."""
    if half_deg is None:  # 120 km/h ceiling bounds how far max_min can reach
        half_deg = min(4.5, max_min / 60 * 120 / 111 + 0.1)
    r0, c0 = _rc(lat, lon)
    h = int(half_deg / PX)
    r1, c1 = max(r0 - h, 0), max(c0 - h, 0)
    crop = F[r1:min(r0 + h, F.shape[0]), c1:min(c0 + h, F.shape[1])] * H_M  # minutes per pixel height
    n, m = crop.shape
    idx = np.arange(n * m).reshape(n, m)
    cosl = math.cos(math.radians(lat))
    src, dst, w = [], [], []
    for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
        a = idx[0:n - dr, max(0, -dc):m - max(0, dc)]
        b = idx[dr:n, max(0, dc):m - max(0, -dc)]
        ww = (crop.ravel()[a.ravel()] + crop.ravel()[b.ravel()]) / 2 * math.hypot(dr, dc * cosl)
        src += [a.ravel(), b.ravel()]; dst += [b.ravel(), a.ravel()]; w += [ww, ww]
    G = coo_matrix((np.concatenate(w), (np.concatenate(src), np.concatenate(dst))), shape=(n * m, n * m)).tocsr()
    d = dijkstra(G, indices=(r0 - r1) * m + (c0 - c1), limit=max_min).reshape(n, m)
    out = np.full(len(cand_lat), np.nan)
    for i, (la, lo) in enumerate(zip(cand_lat, cand_lon)):
        r, c = _rc(la, lo)
        r, c = r - r1, c - c1
        if 0 <= r < n and 0 <= c < m and np.isfinite(d[r, c]):
            out[i] = d[r, c]
    return out


def fallback_minutes(km):
    """Straight-line km -> free-flow minutes. Fit on 934 India PO->facility pairs vs OSRM (log resid sd 0.26)."""
    return 2.30 * np.power(np.maximum(km, 0.5), 0.842)


def reach(minutes, service):
    """P(trip fits the window) under lognormal error of the estimate. 0.5 exactly at the window."""
    T, s = WINDOWS[service]
    t = np.asarray(minutes, float)
    r = ndtr((math.log(T) - np.log(np.maximum(np.nan_to_num(t, nan=1e9), 1.0))) / s)
    return r  # NaN (beyond the search limit or off-raster) -> 0


def time_range(minutes, sigma=0.26):
    """68% range for display, rounded to 5 minutes."""
    lo, hi = minutes * math.exp(-sigma), minutes * math.exp(sigma)
    return 5 * round(lo / 5), 5 * round(hi / 5)
