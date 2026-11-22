import sys, time, numpy as np, pandas as pd
S = sys.argv[1]; sys.path.insert(0, f"{S}/code")
import reach as R
t = time.perf_counter(); F = R.load_friction(f"{S}/out/friction_u8.npz"); print(f"load {1000*(time.perf_counter()-t):.0f} ms, {F.nbytes/1e6:.0f} MB in RAM")
fac = pd.read_parquet(f"{S}/out/fac_grades.parquet")
fac = fac[fac.coord_valid & fac.lat.between(6, 37.5) & fac.lon.between(68, 98)]
def hav(la1, lo1, la2, lo2):
    la1, lo1, la2, lo2 = map(np.radians, (la1, lo1, la2, lo2))
    a = np.sin((la2-la1)/2)**2 + np.cos(la1)*np.cos(la2)*np.sin((lo2-lo1)/2)**2
    return 2*6371.0088*np.arcsin(np.sqrt(a))
cases = [("dialysis", "Jaipur", 26.912, 75.787), ("emoc", "Lucknow", 26.847, 80.947), ("dialysis", "rural Bihar (Kishanganj area)", 26.10, 87.95), ("trauma", "NH44 near Sagar MP", 23.84, 78.74), ("emoc", "Leh, Ladakh", 34.16, 77.58), ("cath_lab", "Barmer, Rajasthan", 25.75, 71.39)]
for svc, name, la, lo in cases:
    c = fac[fac[svc] >= 2]
    km = hav(la, lo, c.lat.values, c.lon.values)
    near = np.argsort(km)[:40]
    T, s = R.WINDOWS[svc]
    t0 = time.perf_counter()
    mins = R.travel_minutes(F, la, lo, c.lat.values[near], c.lon.values[near], max_min=max(2.5 * T, 300))
    dt = time.perf_counter() - t0
    fb = R.fallback_minutes(km[near])
    df = pd.DataFrame(dict(name=c.name.values[near], km=km[near].round(1), map_min=mins.round(0), fallback_min=fb.round(0), reach=R.reach(mins, svc).round(2), exp_km_r=np.exp(-km[near] / {"dialysis": 50, "emoc": 50, "trauma": 50, "cath_lab": 100}[svc]).round(2)))
    print(f"\n{svc} from {name}: window {T} min, {dt*1000:.0f} ms for the crop query; nearest-by-km rank vs by-minutes rank of top-5 by minutes:")
    df["km_rank"] = np.arange(1, len(df) + 1)
    print(df.sort_values("map_min").head(5).to_string(index=False))
