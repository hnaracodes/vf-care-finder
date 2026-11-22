"""Lead final: compare ordering rules on the app's own bundle index (dbapp/bundle, 22:36 build).
Rules: v0 radius score (replica), v1 S = W x reach (first synthesis), time-first with evidence allowance A,
t/W index. Fallback minutes t = 2.30 km^0.842. 1,500 random geocoded post offices (seed 7, same as skeptic).
Read-only."""
import sys, json, math
import numpy as np, pandas as pd
from math import erf, sqrt
sys.path.insert(0, "/Users/hrudaynara/Projects/CMU Datathon/src")
from vf import rank as RK, services as S
idx = RK.load_index("/Users/hrudaynara/Projects/CMU Datathon/dbapp/bundle")
D="/Users/hrudaynara/Projects/CMU Datathon/data"
po=pd.read_parquet(f"{D}/silver/post_offices.parquet",columns=["po_id","lat","lon","coord_valid"])
po=po[po.coord_valid & po.lat.between(6,37.5) & po.lon.between(68,97.5)]
orig=po.sample(1500,random_state=7)[["lat","lon"]].to_numpy()
def hav(a,b,lat2,lon2):
    R=6371.0088
    p1=np.radians(a);p2=np.radians(lat2);dl=np.radians(lon2-b);dp=p2-p1
    h=np.sin(dp/2)**2+np.cos(p1)*np.cos(p2)*np.sin(dl/2)**2
    return 2*R*np.arcsin(np.sqrt(h))
Phi=np.vectorize(lambda x: 0.5*(1+erf(x/sqrt(2))))
def tmin(km): return 2.30*np.power(np.maximum(km,0.05),0.842)
WIN={"emoc":(120,0.3),"trauma":(120,0.3),"icu":(120,0.3),"nicu":(120,0.3),"ct":(120,0.3),"cath_lab":(90,0.3),
     "neurosurgery":(150,0.3),"burns":(240,0.3),"dialysis":(60,0.5),"mri":(180,0.5),"cardiac_surgery":(240,0.5),"oncology":(240,0.5)}
ADJ={"thin":-0.2,"moderate":0.0,"strong":0.1,"none":0.0}
lat=idx.lat; lon=idx.lon
out={}
for line,(T,sig) in WIN.items():
    ld=idx.lines[line]; R=S.SERVICES[line].radius_km
    rec=(ld.grade>=2)&(~ld.blocked)&(~np.isnan(lat))
    ridx=np.flatnonzero(rec)
    g=ld.grade[ridx].astype(int); st=np.array([ADJ.get(s,0.0) for s in ld.strength[ridx]])
    W=np.clip(np.where(g==3,1.0,0.7)+st,0,1)
    nminor=np.array([len(ld.minor_flags.get(int(p),[])) for p in ridx])
    thin=np.array([s=="thin" for s in ld.strength[ridx]])
    res={k:[] for k in ["v1","A0","A15","A30","tw","v0"]}
    promoted={"A15":0,"A30":0}; answered=0; answered_r=0; both=0; g3top={"v1":0,"A15":0,"A0":0}
    for (a,b) in orig:
        d=hav(a,b,lat[ridx],lon[ridx]); t=tmin(d)
        m=t<=T
        if m.any():
            answered+=1
            ii=np.flatnonzero(m); tt=t[ii]; near=ii[np.argmin(tt)]; tnear=t[near]
            # v1: S=W*reach, ties (<0.02) by minor flags then minutes (bubble)
            reach=Phi((math.log(T)-np.log(tt))/sig); Sx=W[ii]*reach
            order=list(np.argsort(-Sx,kind="stable"))
            key=lambda k:(nminor[ii[k]],tt[k])
            for _ in range(len(order)):
                sw=False
                for j in range(len(order)-1):
                    x,y=order[j],order[j+1]
                    if abs(Sx[x]-Sx[y])<0.02 and key(y)<key(x): order[j],order[j+1]=y,x; sw=True
                if not sw: break
            top=ii[order[0]]; res["v1"].append(t[top]-tnear); g3top["v1"]+=int(g[top]==3)
            for A in (0,15,30):
                k=tt-A*(g[ii]==3)
                top=ii[np.lexsort((tt,k))[0]]
                res[f"A{A}"].append(t[top]-tnear)
                if A>0 and top!=near: promoted[f"A{A}"]+=1
                if A in (0,15): g3top[f"A{A}"]+=int(g[top]==3)
            # t/W index (expected-minutes challenger with provisional W)
            top=ii[np.argmin(tt/W[ii])]; res["tw"].append(t[top]-tnear)
        # v0 replica on radius
        m0=d<=R
        if m0.any():
            answered_r+=1
    q=lambda x,p: round(float(np.quantile(x,p)),1) if len(x) else None
    o=dict(T=T, n_rec=int(rec.sum()), answered_window=answered, answered_radius=answered_r,
           abstain_window=round(1-answered/len(orig),3), abstain_radius=round(1-answered_r/len(orig),3))
    for k in ["v1","A0","A15","A30","tw"]:
        x=np.array(res[k])
        o[k]=dict(top_not_nearest=round(float((x>0.01).mean()),3), over15=round(float((x>15).mean()),3), over30=round(float((x>30).mean()),3),
                  over60=round(float((x>60).mean()),3), p90_extra=q(x,0.9), max_extra=round(float(x.max()),1) if len(x) else None)
    o["A15_promoted_share"]=round(promoted["A15"]/max(answered,1),3); o["A30_promoted_share"]=round(promoted["A30"]/max(answered,1),3)
    o["grade3_top_share"]={k:round(v/max(answered,1),3) for k,v in g3top.items()}
    out[line]=o
    print(line, json.dumps(o))
json.dump(out,open("f3_order.json","w"),indent=1)
