"""Lead final: how often is the nearest facility that claims the line (grade 1+) one whose own record has
no fact naming the service (grade < 2)? Window-based, fallback minutes, bundle index. Read-only."""
import sys, numpy as np, pandas as pd
sys.path.insert(0, "/Users/hrudaynara/Projects/CMU Datathon/src")
from vf import rank as RK
from sklearn.neighbors import BallTree
idx=RK.load_index("/Users/hrudaynara/Projects/CMU Datathon/dbapp/bundle")
D="/Users/hrudaynara/Projects/CMU Datathon/data"
po=pd.read_parquet(f"{D}/silver/post_offices.parquet",columns=["po_id","lat","lon","coord_valid"])
po=po[po.coord_valid & po.lat.between(6,37.5) & po.lon.between(68,97.5)]
orig=po.sample(1500,random_state=7)[["lat","lon"]].to_numpy()
fb=lambda km: 2.30*np.power(np.maximum(km,0.05),0.842)
for line,T in [("emoc",120),("dialysis",60),("icu",120),("nicu",120),("cath_lab",90)]:
    ld=idx.lines[line]; ok=~np.isnan(idx.lat)
    claim=ok&(ld.grade>=1); rec=ok&(ld.grade>=2)&~ld.blocked
    btc=BallTree(np.radians(np.c_[idx.lat[claim],idx.lon[claim]]),metric="haversine"); cidx=np.flatnonzero(claim)
    btr=BallTree(np.radians(np.c_[idx.lat[rec],idx.lon[rec]]),metric="haversine")
    dc,ic=btc.query(np.radians(orig),k=1); dr,_=btr.query(np.radians(orig),k=1)
    tc=fb(dc[:,0]*6371.0088); tr=fb(dr[:,0]*6371.0088)
    inside=tc<=T
    gnear=ld.grade[cidx[ic[:,0]]]
    no_text=(gnear<2)&inside
    extra=(tr-tc)[no_text]
    print(f"{line:8s} T={T}: nearest claim inside window for {inside.mean():.1%} of post offices; of those, its own record has no fact naming the service: {no_text.sum()/inside.sum():.1%}; median extra minutes to nearest recommendable {np.median(extra):.0f} (p75 {np.quantile(extra,0.75):.0f}); no recommendable inside window at all: {((tr>T)&inside).sum()/inside.sum():.1%}")
