"""Lead final: leave-one-anchor-out check of the fallback t = a*km^b against OSRM free-flow minutes;
band coverage of exp(+-z*sd). Then nearest-claim descriptive for emoc under the window. Read-only."""
import sys, numpy as np, pandas as pd, math
SR="/private/tmp/claude-501/-Users-hrudaynara-Projects-CMU-Datathon/dda738e7-6b95-4922-b02b-c70ed6c6d96e/scratchpad/score_research"
P=pd.read_csv(f"{SR}/reach_travel_time/out/osrm_pairs.csv")
P=P[(P.km>0)&(P.osrm_min>0)]
x=np.log(P.km.to_numpy()); y=np.log(P.osrm_min.to_numpy()); a=P.anchor.to_numpy()
b1,b0=np.polyfit(x,y,1); res_in=y-(b0+b1*x)
print(f"in-sample fit: log(min) = {b0:.3f} + {b1:.3f} log(km); resid SD {res_in.std(ddof=2):.3f}; n={len(P)}, anchors={len(set(a))}")
res=np.empty_like(y)
for k in np.unique(a):
    m=a==k; c1,c0=np.polyfit(x[~m],y[~m],1); res[m]=y[m]-(c0+c1*x[m])
sd=res.std(); print(f"leave-one-anchor-out residual SD {sd:.3f}; mean bias {res.mean():+.3f}")
for z,lab in [(1.0,'68%'),(1.96,'95%')]:
    w=z*0.26; print(f"band exp(+-{w:.2f}) covers OSRM {np.mean(np.abs(res)<=w):.1%} (target {lab})")
w=0.52; print(f"band exp(+-0.52) covers {np.mean(np.abs(res)<=0.52):.1%} LOAO")
# wrong side of 120 / 90 / 60 windows, LOAO
pred=np.exp(y-res)
for T in (60,90,120):
    ws=((pred<=T)!=(P.osrm_min.to_numpy()<=T)); print(f"T={T}: LOAO fallback on wrong side of window vs OSRM: {ws.mean():.1%}")
# plains vs hill
hill=P.state.isin(["ARUNACHAL PRADESH","MEGHALAYA","MANIPUR","MIZORAM","NAGALAND","JAMMU AND KASHMIR","UTTARAKHAND","HIMACHAL PRADESH","SIKKIM","TRIPURA","LADAKH","ASSAM"]).to_numpy()
print("pairs in hill/NE states:",int(hill.sum()),"anchors:",len(set(a[hill])), " LOAO mean resid hill", round(float(res[hill].mean()),3) if hill.any() else None)
