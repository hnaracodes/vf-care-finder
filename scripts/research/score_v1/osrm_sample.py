import numpy as np, pandas as pd, json, time, sys, urllib.request
from scipy.spatial import cKDTree
S = sys.argv[1]; D = f"{S}/dl_osrm"
rng = np.random.default_rng(20261121)
po = pd.read_parquet(f"{S}/out/po_nearest_tt_snapped.parquet")
fac = pd.read_parquet(f"{S}/out/fac_grades.parquet")
svc = ["emoc","nicu","dialysis","icu","cardiac_surgery","cath_lab","mri","ct","trauma","burns","neurosurgery","oncology"]
fac = fac[fac.coord_valid & fac.lat.between(6, 37.5) & fac.lon.between(68, 98)].copy()
fac = fac[(fac[svc] >= 2).any(axis=1)].reset_index(drop=True)
def xyz(lat, lon):
    la, lo = np.radians(lat), np.radians(lon)
    return np.c_[np.cos(la)*np.cos(lo), np.cos(la)*np.sin(lo), np.sin(la)]
ftree = cKDTree(xyz(fac.lat.values, fac.lon.values)); ptree = cKDTree(xyz(po.lat.values, po.lon.values))
w = po.w.values / po.w.sum()
anchors = rng.choice(len(po), size=30, replace=False, p=w)
pairs_meta = []
for k, a in enumerate(anchors):
    near = ptree.query_ball_point(xyz([po.lat.values[a]], [po.lon.values[a]])[0], r=40/6371)
    near = [i for i in near if i != a]
    others = list(rng.choice(near, size=min(3, len(near)), replace=False)) if near else []
    src = [a] + others
    _, fi = ftree.query(xyz([po.lat.values[a]], [po.lon.values[a]]), k=25)
    fi = list(fi[0][:6]) + [fi[0][12], fi[0][24]]
    coords = [(po.lon.values[i], po.lat.values[i]) for i in src] + [(fac.lon.values[j], fac.lat.values[j]) for j in fi]
    cs = ";".join(f"{x:.6f},{y:.6f}" for x, y in coords)
    url = (f"https://router.project-osrm.org/table/v1/driving/{cs}?sources=" + ";".join(str(i) for i in range(len(src))) +
           "&destinations=" + ";".join(str(len(src) + j) for j in range(len(fi))) + "&annotations=duration,distance")
    req = urllib.request.Request(url, headers={"User-Agent": "research-script/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r: body = r.read()
    except Exception as e:
        print("fail", k, e); time.sleep(2); continue
    open(f"{D}/table_{k:02d}.json", "wb").write(body)
    pairs_meta.append(dict(k=k, src_po_idx=[int(i) for i in src], fac_idx=[int(j) for j in fi]))
    time.sleep(1.5)
json.dump(pairs_meta, open(f"{S}/out/osrm_pairs_meta.json", "w"))
fac[["facility_id","lat","lon"]].to_parquet(f"{S}/out/osrm_fac.parquet")
print("requests ok:", len(pairs_meta))
