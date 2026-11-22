import numpy as np, pandas as pd, sys
from scipy import ndimage
from scipy.spatial import cKDTree
from skimage.graph import MCP_Geometric
S = sys.argv[1]
Fm = np.load(f"{S}/out/friction_india_masked.npy")
ocean = Fm >= 1.0
LAT0, LON0, PX, H = 37.5, 68.0, 1/120, 927.67
_, (ir, ic) = ndimage.distance_transform_edt(ocean, return_indices=True)
def rc(lat, lon):
    r = ((LAT0 - np.asarray(lat)) / PX).astype(int); c = ((np.asarray(lon) - LON0) / PX).astype(int)
    return ir[r, c], ic[r, c]  # snapped to nearest land pixel
fac = pd.read_parquet(f"{S}/out/fac_grades.parquet")
fac = fac[fac.coord_valid & fac.lat.between(6, 37.5) & fac.lon.between(68, 98)].copy()
po = pd.read_parquet(f"{S}/out/po.parquet")
pop = pd.read_parquet(f"{S}/out/pop.parquet")
npo = po.groupby("district_key").size().rename("n_po_here")
po = po.join(npo, on="district_key").merge(pop[["district_key", "pop_2011"]], on="district_key", how="left")
po["w"] = po.pop_2011 / po.n_po_here
print("POs with pop weight:", po.w.notna().mean().round(3), "total pop represented (M):", round(po.w.sum() / 1e6, 1))
po["w"] = po.w.fillna(po.w.median())
fr, fc = rc(fac.lat, fac.lon); pr, pc = rc(po.lat, po.lon)
def xyz(lat, lon):
    la, lo = np.radians(lat), np.radians(lon)
    return np.c_[np.cos(la)*np.cos(lo), np.cos(la)*np.sin(lo), np.sin(la)]
P = xyz(po.lat.values, po.lon.values)
cost = Fm * H
RAD = dict(emoc=50, trauma=50, dialysis=50, ct=75, icu=75, nicu=75, mri=100, cath_lab=100, neurosurgery=150, burns=200, cardiac_surgery=250, oncology=250)
rows = []
allratio = []
for s, rad in RAD.items():
    sel = (fac[s] >= 2).values
    m = MCP_Geometric(cost, sampling=(1.0, np.cos(np.radians(21.5))))
    cum, _ = m.find_costs(list(zip(fr[sel], fc[sel])))
    tt = cum[pr, pc]
    d, _ = cKDTree(xyz(fac.lat.values[sel], fac.lon.values[sel])).query(P)
    km = 2 * 6371.0088 * np.arcsin(np.clip(d / 2, 0, 1))
    po[s + "_min"] = tt; po[s + "_km"] = km
    w = po.w.values
    def share(mask): return float((w * mask).sum() / w.sum())
    rows.append(dict(service=s, radius_km=rad, seeds=int(sel.sum()),
        pop_within_radius=share(km <= rad), pop_within_60min=share(tt <= 60), pop_within_120min=share(tt <= 120), pop_within_240min=share(tt <= 240),
        wmedian_km=float(np.median(np.repeat(km, 1))), median_min=float(np.median(tt)), p90_min=float(np.quantile(tt, 0.9)),
        in_radius_but_over_120=share((km <= rad) & (tt > 120)), out_radius_but_under_120=share((km > rad) & (tt <= 120))))
    ok = (km > 2) & np.isfinite(tt) & (tt < 2000)
    allratio.append(pd.DataFrame(dict(service=s, km=km[ok], tt=tt[ok], state=po.state_pin.values[ok], w=w[ok])))
T = pd.DataFrame(rows)
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 30)
print(T.round(3).to_string(index=False))
R = pd.concat(allratio)
R["min_per_km"] = R.tt / R.km
print("\nmin per straight-line km (PO to nearest g2+ facility, all services pooled):")
print(R.min_per_km.describe(percentiles=[.1, .25, .5, .75, .9]).round(3))
# fit log t = a + b log km
X = np.c_[np.ones(len(R)), np.log(R.km)]
beta, *_ = np.linalg.lstsq(X, np.log(R.tt), rcond=None)
res = np.log(R.tt) - X @ beta
print("fit log(min) = %.3f + %.3f log(km); resid sd %.3f" % (beta[0], beta[1], res.std()))
# linear speed model: t = km * k ; by km band
R["band"] = pd.cut(R.km, [2, 10, 25, 50, 100, 200, 400, 2000])
print(R.groupby("band", observed=True).min_per_km.describe(percentiles=[.1, .5, .9])[["count", "10%", "50%", "90%"]].round(2))
st = R.groupby("state").agg(n=("km", "size"), med_min_per_km=("min_per_km", "median"), p90=("min_per_km", lambda x: x.quantile(.9))).sort_values("med_min_per_km")
print(st.round(2).to_string())
po.to_parquet(f"{S}/out/po_nearest_tt_snapped.parquet")
R.to_parquet(f"{S}/out/ratio_pairs.parquet")
