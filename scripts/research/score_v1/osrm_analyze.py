import numpy as np, pandas as pd, json, sys
from scipy import ndimage
from scipy.stats import spearmanr
from skimage.graph import MCP_Geometric
S = sys.argv[1]; D = f"{S}/dl_osrm"
po = pd.read_parquet(f"{S}/out/po_nearest_tt_snapped.parquet")
fac = pd.read_parquet(f"{S}/out/osrm_fac.parquet")
meta = json.load(open(f"{S}/out/osrm_pairs_meta.json"))
Fm = np.load(f"{S}/out/friction_india_masked.npy"); ocean = Fm >= 1.0
_, (ir, ic) = ndimage.distance_transform_edt(ocean, return_indices=True)
LAT0, LON0, PX, H = 37.5, 68.0, 1/120, 927.67
def rc(lat, lon):
    r = int((LAT0 - lat) / PX); c = int((lon - LON0) / PX); return ir[r, c], ic[r, c]
def hav(la1, lo1, la2, lo2):
    la1, lo1, la2, lo2 = map(np.radians, (la1, lo1, la2, lo2))
    a = np.sin((la2-la1)/2)**2 + np.cos(la1)*np.cos(la2)*np.sin((lo2-lo1)/2)**2
    return 2*6371.0088*np.arcsin(np.sqrt(a))
rows = []; orders = []
for m in meta:
    t = json.load(open(f"{D}/table_{m['k']:02d}.json"))
    if t.get("code") != "Ok": print("bad", m["k"], t.get("code")); continue
    for si, pi in enumerate(m["src_po_idx"]):
        la, lo = po.lat.values[pi], po.lon.values[pi]
        r0, c0 = rc(la, lo); h = int(3.0 / PX)
        r1, c1 = max(r0-h, 0), max(c0-h, 0); crop = Fm[r1:r0+h, c1:c0+h] * H
        cum, _ = MCP_Geometric(crop, sampling=(1.0, np.cos(np.radians(la)))).find_costs([(r0-r1, c0-c1)])
        rec = []
        for dj, fj in enumerate(m["fac_idx"]):
            fl, fo = fac.lat.values[fj], fac.lon.values[fj]
            fr, fc = rc(fl, fo)
            mapt = cum[fr-r1, fc-c1] if (0 <= fr-r1 < cum.shape[0] and 0 <= fc-c1 < cum.shape[1]) else np.nan
            dur = t["durations"][si][dj]; dist = t["distances"][si][dj]
            snap_s = t["sources"][si]["distance"]; snap_d = t["destinations"][dj]["distance"]
            km = hav(la, lo, fl, fo)
            rows.append(dict(anchor=m["k"], src=pi, fac=fj, km=km, road_km=(dist or np.nan)/1000, osrm_min=(dur or np.nan)/60, map_min=mapt, snap_m=max(snap_s, snap_d), state=po.state_pin.values[pi]))
            rec.append((km, (dur or np.nan)/60, mapt))
        rec = np.array(rec, float)
        if np.isfinite(rec).all(): orders.append(rec)
R = pd.DataFrame(rows)
R = R[(R.km > 1) & R.road_km.notna() & (R.snap_m < 2000)]
R["circuity"] = R.road_km / R.km
R["osrm_min_per_km"] = R.osrm_min / R.km; R["map_min_per_km"] = R.map_min / R.km
R["map_over_osrm"] = R.map_min / R.osrm_min
pd.set_option("display.width", 200)
print("pairs:", len(R), "anchors:", R.anchor.nunique(), "sources:", R.src.nunique())
print(R[["km","road_km","circuity","osrm_min","osrm_min_per_km","map_min_per_km","map_over_osrm"]].describe(percentiles=[.1,.25,.5,.75,.9]).round(3).to_string())
R["band"] = pd.cut(R.km, [1, 10, 25, 50, 100, 300]).astype(str)
print(R.groupby("band").agg(n=("km","size"), circuity_med=("circuity","median"), circ_p90=("circuity", lambda x: x.quantile(.9)), osrm_kmh=("osrm_min_per_km", lambda x: 60/x.median()), map_over_osrm=("map_over_osrm","median")).round(3).to_string())
lr = np.log(R.osrm_min) - np.log(R.km)
print("log(osrm_min/km) sd: %.3f ; log(map/osrm) sd: %.3f; corr(log map, log osrm): %.3f" % (lr.std(), np.log(R.map_over_osrm).std(), np.corrcoef(np.log(R.map_min), np.log(R.osrm_min))[0,1]))
X = np.c_[np.ones(len(R)), np.log(R.km)]; b, *_ = np.linalg.lstsq(X, np.log(R.osrm_min), rcond=None)
print("fit log(osrm_min) = %.3f + %.3f log(km), resid sd %.3f" % (b[0], b[1], (np.log(R.osrm_min) - X@b).std()))
b2, *_ = np.linalg.lstsq(X, np.log(R.road_km), rcond=None)
print("fit log(road_km) = %.3f + %.3f log(km), resid sd %.3f" % (b2[0], b2[1], (np.log(R.road_km) - X@b2).std()))
# ordering agreement over each source's 8 destinations
top1_osrm = np.mean([np.argmin(o[:,0]) == np.argmin(o[:,1]) for o in orders])
top1_map = np.mean([np.argmin(o[:,2]) == np.argmin(o[:,1]) for o in orders])
rho_km = np.median([spearmanr(o[:,0], o[:,1])[0] for o in orders]); rho_map = np.median([spearmanr(o[:,2], o[:,1])[0] for o in orders])
# top-3 set overlap
def ov(a, b): return len(set(np.argsort(a)[:3]) & set(np.argsort(b)[:3])) / 3
print(f"sources with complete rows: {len(orders)}; nearest-by-straight-line == nearest-by-OSRM-time: {top1_osrm:.2f}; nearest-by-MAP == nearest-by-OSRM: {top1_map:.2f}")
print(f"median Spearman(straight km, OSRM min): {rho_km:.3f}; median Spearman(MAP min, OSRM min): {rho_map:.3f}")
print(f"top-3 overlap straight vs OSRM: {np.mean([ov(o[:,0], o[:,1]) for o in orders]):.2f}; MAP vs OSRM: {np.mean([ov(o[:,2], o[:,1]) for o in orders]):.2f}")
print(R.groupby("state").agg(n=("km","size"), circ=("circuity","median"), osrm_min_per_km=("osrm_min_per_km","median"), map_over_osrm=("map_over_osrm","median")).round(2).to_string())
R.drop(columns=["band"]).to_csv(f"{S}/out/osrm_pairs.csv", index=False)
