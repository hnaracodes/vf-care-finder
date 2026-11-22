"""Lead final: (1) sea-pixel post offices, (2) per-state terrain multiplier for the straight-line fallback,
(3) coverage headline for emoc under the window, fallback vs fallback+terrain vs MAP, unweighted and
population-weighted, on the bundle index (22:36). Read-only."""
import sys, json, numpy as np, pandas as pd
sys.path.insert(0, "/Users/hrudaynara/Projects/CMU Datathon/src")
from vf import rank as RK
from sklearn.neighbors import BallTree
SR="/private/tmp/claude-501/-Users-hrudaynara-Projects-CMU-Datathon/dda738e7-6b95-4922-b02b-c70ed6c6d96e/scratchpad/score_research"
p=pd.read_parquet(f"{SR}/reach_travel_time/out/po_nearest_tt_snapped.parquet")
fr=np.load(f"{SR}/reach_travel_time/out/friction_india_masked.npy", mmap_mode="r")
r=np.clip(((37.5-p.lat.to_numpy())*120).astype(int),0,fr.shape[0]-1); c=np.clip(((p.lon.to_numpy()-68)*120).astype(int),0,fr.shape[1]-1)
sea=np.asarray(fr[r,c])>=1.0
print("post offices on masked sea pixels:",int(sea.sum()),"of",len(p), " pop weight share:",round(float(p.w[sea].sum()/p.w.sum()),4))
fb=lambda km: 2.30*np.power(np.maximum(km,0.05),0.842)
# terrain multiplier: per state median of MAP min / fallback min over the nearest facility for 4 lines, even po_id fit, odd evaluate
rows=[]
for line in ["emoc","icu","dialysis","trauma"]:
    ok=p[f"{line}_km"].notna()&p[f"{line}_min"].notna()&(p[f"{line}_km"]>1)&~sea
    rows.append(pd.DataFrame({"state":p.state_pin[ok],"po":p.po_id[ok],"ratio":p.loc[ok,f"{line}_min"]/fb(p.loc[ok,f"{line}_km"])}))
R=pd.concat(rows)
fit=R[R.po%2==0].groupby("state").ratio.median()
nat=R[R.po%2==0].ratio.median()
mult=(fit/nat).clip(lower=0.8)
print("national median MAP/fallback ratio:",round(float(nat),3))
print("multiplier (state median / national), top:", mult.sort_values(ascending=False).head(10).round(2).to_dict())
print("multiplier bottom:", mult.sort_values().head(5).round(2).to_dict())
for line,T in [("emoc",120),("trauma",120),("dialysis",60),("cath_lab",90)]:
    ok=p[f"{line}_km"].notna()&p[f"{line}_min"].notna()&(p[f"{line}_km"]>0)&~sea&(p.po_id%2==1)
    km=p.loc[ok,f"{line}_km"]; mm=p.loc[ok,f"{line}_min"]; st=p.loc[ok,"state_pin"]
    f0=fb(km); f1=f0*st.map(mult).fillna(1.0)*nat
    f0n=f0*nat
    for name,f in [("fallback",f0),("fallback x national",f0n),("fallback x state",f1)]:
        fi=(f<=T)&~(mm<=T); fo=(f>T)&(mm<=T)
        hill=st.isin(["ARUNACHAL PRADESH","MEGHALAYA","MANIPUR","MIZORAM","NAGALAND","JAMMU AND KASHMIR","UTTARAKHAND","HIMACHAL PRADESH","SIKKIM","TRIPURA","LADAKH","ASSAM"])
        print(f"{line:8s} {name:20s} false-inside {fi.mean():.1%} (hill/NE {fi[hill].mean():.1%}); false-outside {fo.mean():.1%}")
# coverage headline with the app's own gate (bundle): emoc recommendable, nearest straight-line km for every post office
idx=RK.load_index("/Users/hrudaynara/Projects/CMU Datathon/dbapp/bundle")
ld=idx.lines["emoc"]; rec=(ld.grade>=2)&(~ld.blocked)&(~np.isnan(idx.lat))
bt=BallTree(np.radians(np.c_[idx.lat[rec],idx.lon[rec]]),metric="haversine")
q=p[~sea]
dkm=bt.query(np.radians(np.c_[q.lat,q.lon]),k=1)[0][:,0]*6371.0088
t0=fb(dkm); t1=t0*q.state_pin.map(mult).fillna(1.0).to_numpy()*nat
w=q.w.to_numpy()
for name,t in [("fallback",t0),("fallback x state terrain",t1),("MAP (reach lens, nearest grade2+ no flags)",q.emoc_min.to_numpy())]:
    beyond=t>120
    print(f"emoc beyond 120 min [{name}]: post offices {beyond.mean():.1%}; population-weighted {np.nansum(w*beyond)/np.nansum(w):.1%}")
print("emoc beyond 50 km straight line: post offices",f"{(dkm>50).mean():.1%}", "pop-weighted", f"{np.nansum(w*(dkm>50))/np.nansum(w):.1%}")
