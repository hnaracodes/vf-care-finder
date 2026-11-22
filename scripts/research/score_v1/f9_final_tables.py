"""Lead final: (a) write the per-state terrain multiplier table for WP5 to lift; (b) per-line share of people beyond
the window (fallback x terrain, rough 2011 district weights, sea-pixel post offices dropped) vs beyond the old
radius; (c) final ordering behaviour with per-line allowance (15 min emergency/recurring, 30 min planned).
Read-only on project data; writes only to this scratch folder."""
import sys, json, numpy as np, pandas as pd
sys.path.insert(0, "/Users/hrudaynara/Projects/CMU Datathon/src")
from vf import rank as RK, services as S
from sklearn.neighbors import BallTree
SR="/private/tmp/claude-501/-Users-hrudaynara-Projects-CMU-Datathon/dda738e7-6b95-4922-b02b-c70ed6c6d96e/scratchpad/score_research"
OUT=f"{SR}/lead_final"
p=pd.read_parquet(f"{SR}/reach_travel_time/out/po_nearest_tt_snapped.parquet")
fr=np.load(f"{SR}/reach_travel_time/out/friction_india_masked.npy", mmap_mode="r")
r=np.clip(((37.5-p.lat.to_numpy())*120).astype(int),0,fr.shape[0]-1); c=np.clip(((p.lon.to_numpy()-68)*120).astype(int),0,fr.shape[1]-1)
sea=np.asarray(fr[r,c])>=1.0
fb=lambda km: 2.30*np.power(np.maximum(km,0.05),0.842)
rows=[]
for line in ["emoc","icu","dialysis","trauma"]:
    ok=p[f"{line}_km"].notna()&p[f"{line}_min"].notna()&(p[f"{line}_km"]>1)&~sea
    rows.append(pd.DataFrame({"state":p.state_pin[ok],"ratio":p.loc[ok,f"{line}_min"]/fb(p.loc[ok,f"{line}_km"])}))
R=pd.concat(rows); nat=R.ratio.median()
tab=R.groupby("state").ratio.agg(["median","size"]).rename(columns={"size":"n_po_line_pairs"})
tab["multiplier"]=(tab["median"]/nat).clip(0.8,3.1)
ISL={"ANDAMAN AND NICOBAR ISLANDS","LAKSHADWEEP"}
tab.loc[tab.index.isin(ISL),"multiplier"]=np.nan
tab=tab[tab.index!="NA"]
tab[["multiplier","n_po_line_pairs"]].round(2).to_csv(f"{OUT}/terrain_multiplier.csv")
print("national MAP/fallback median",round(float(nat),3),"; states",len(tab)," saved terrain_multiplier.csv")
mult=tab.multiplier
idx=RK.load_index("/Users/hrudaynara/Projects/CMU Datathon/dbapp/bundle")
q=p[~sea].copy(); w=q.w.to_numpy(); m=q.state_pin.map(mult).fillna(1.0).to_numpy()
WIN={"emoc":120,"trauma":120,"icu":120,"nicu":120,"ct":120,"cath_lab":90,"neurosurgery":150,"burns":240,"dialysis":60,"mri":180,"cardiac_surgery":240,"oncology":240}
cov={}
for line,T in WIN.items():
    ld=idx.lines[line]; rec=(ld.grade>=2)&(~ld.blocked)&(~np.isnan(idx.lat))
    bt=BallTree(np.radians(np.c_[idx.lat[rec],idx.lon[rec]]),metric="haversine")
    dkm=bt.query(np.radians(np.c_[q.lat,q.lon]),k=1)[0][:,0]*6371.0088
    t=fb(dkm)*m; Rk=S.SERVICES[line].radius_km
    cov[line]=dict(window_min=T,beyond_window_pop=round(float(np.sum(w*(t>T))/np.sum(w)),3),beyond_window_po=round(float((t>T).mean()),3),
                   radius_km=Rk,beyond_radius_pop=round(float(np.sum(w*(dkm>Rk))/np.sum(w)),3))
    print(line,cov[line])
json.dump(cov,open(f"{OUT}/f9_coverage.json","w"),indent=1)
