import numpy as np, pandas as pd, time, sys
from scipy import ndimage
from scipy.spatial import cKDTree
from skimage.graph import MCP_Geometric
S = sys.argv[1]
F = np.load(f"{S}/out/friction_india.npy")
LAT0, LON0, PX, H = 37.5, 68.0, 1/120, 927.67
# ocean mask: constant 0.00316 components touching the south or west edge
const = np.isclose(F, 0.00316, atol=1e-7)
lab, n = ndimage.label(const)
edge = set(np.unique(lab[-1, :])) | set(np.unique(lab[:, 0])) | set(np.unique(lab[:, -1]))
edge.discard(0)
ocean = np.isin(lab, list(edge))
print("ocean px", ocean.sum(), f"{ocean.mean():.1%}", "other const px", (const & ~ocean).sum())
Fm = F.copy(); Fm[ocean] = 1.0  # 1 min per metre: effectively impassable
np.save(f"{S}/out/friction_india_masked.npy", Fm)
fac = pd.read_parquet(f"{S}/out/fac_grades.parquet")
po = pd.read_parquet(f"{S}/out/po.parquet")
fac = fac[fac.coord_valid & fac.lat.between(6, 37.5) & fac.lon.between(68, 98)].copy()
def rc(lat, lon):
    return ((LAT0 - np.asarray(lat)) / PX).astype(int), ((np.asarray(lon) - LON0) / PX).astype(int)
fr, fc = rc(fac.lat, fac.lon); pr, pc = rc(po.lat, po.lon)
print("POs on ocean px:", ocean[pr, pc].sum(), "facilities on ocean px:", ocean[fr, fc].sum())
def xyz(lat, lon):
    la, lo = np.radians(lat), np.radians(lon)
    return np.c_[np.cos(la)*np.cos(lo), np.cos(la)*np.sin(lo), np.sin(la)]
P = xyz(po.lat.values, po.lon.values)
cost = Fm * H
res = {}
for s in ["emoc","nicu","dialysis","icu","cardiac_surgery","cath_lab","mri","ct","trauma","burns","neurosurgery","oncology"]:
    sel = (fac[s] >= 2).values
    t = time.perf_counter()
    m = MCP_Geometric(cost, sampling=(1.0, np.cos(np.radians(21.5))))
    cum, _ = m.find_costs(list(zip(fr[sel], fc[sel])))
    dt = time.perf_counter() - t
    tt = cum[pr, pc]
    tree = cKDTree(xyz(fac.lat.values[sel], fac.lon.values[sel]))
    d, _ = tree.query(P)
    km = 2 * 6371.0088 * np.arcsin(np.clip(d / 2, 0, 1))
    res[s + "_min"] = tt; res[s + "_km"] = km
    print(f"{s:16s} seeds {sel.sum():5d}  full-India MCP {dt:5.1f}s  median PO: {np.median(km):6.1f} km, {np.median(tt):6.1f} min")
out = po[["po_id","lat","lon","district_key","state_pin"]].copy()
for k, v in res.items(): out[k] = v
out.to_parquet(f"{S}/out/po_nearest_tt.parquet")
