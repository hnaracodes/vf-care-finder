"""Lead final: fold groups without transitive merging. Each record gets exactly one group:
its chain website domain (shared by >=2 records in >=2 cities, hosting/aggregator domains excluded), else its
district (multi-state source) or its PIN (single-state source). Read-only."""
import pandas as pd, numpy as np, re
from urllib.parse import urlparse
D="/Users/hrudaynara/Projects/CMU Datathon/data"
fac=pd.read_parquet(f"{D}/silver/facilities.parquet",columns=["facility_id","official_website","pin","city_raw","facility_type","coord_check"])
pc=pd.read_parquet(f"{D}/silver/pin_crosswalk.parquet")
print(list(pc.columns)[:12])
def dom(u):
    if not isinstance(u,str) or not u.strip() or u=="null": return None
    u=u.strip().strip('[]"\' ')
    u=u if "://" in u else "http://"+u
    try: h=urlparse(u).netloc.lower()
    except ValueError: return None
    return h[4:] if h.startswith("www.") else h
fac["dom"]=fac.official_website.map(dom)
HOST={"business.site","g.page","placeweb.site","getmy.clinic","eka.care","practo.com","blogspot.in","blogspot.com","esic.nic.in","sites.google.com","wixsite.com","facebook.com","justdial.com","lybrate.com","apollo247.com","redcliffelabs.com","maxlab.co.in","linktr.ee","instagram.com","google.com","nic.in","gov.in"}
city=fac.groupby("dom").city_raw.nunique(); cnt=fac.groupby("dom").size()
chain={d for d in cnt.index if cnt[d]>=2 and city.get(d,0)>=2 and d not in HOST and not d.endswith(".nic.in") and not d.endswith(".gov.in")}
print("chain domains:",len(chain),"records:",int(fac.dom.isin(chain).sum()))
# district from pin crosswalk (rank-1 row per pin)
pc1=pc[pc["rank"]==1].drop_duplicates("pin").set_index("pin")
pin2d=None
fac["district"]=fac.pin.map(pc1["state_pin"]).astype(str)+"|"+fac.pin.map(pc1["district_pin"]).astype(str)
fac["state"]=fac.district.str.split("|").str[0]
fac["grp_multi"]=np.where(fac.dom.isin(chain),"chain:"+fac.dom.astype(str),"dist:"+fac.district.astype(str))
fac["grp_single"]=np.where(fac.dom.isin(chain),"chain:"+fac.dom.astype(str),"pin:"+fac.pin.astype(str))
h=fac[fac.facility_type.eq("hospital")]
s=h.grp_multi.value_counts()
print("hospitals, multi-state rule: groups",len(s),"largest",s.index[0],int(s.iloc[0]),f"{s.iloc[0]/len(h):.1%}")
for st in ["Maharashtra","Karnataka","Telangana","Gujarat","Tamil Nadu","Uttar Pradesh"]:
    x=h[h.state.str.lower()==st.lower()]
    if len(x)==0: continue
    a=x.grp_multi.value_counts(); b=x.grp_single.value_counts()
    print(f"{st:14s} hospitals {len(x):4d} | district-or-chain largest {a.iloc[0]/len(x):.0%} ({a.index[0]}) | PIN-or-chain largest {b.iloc[0]/len(x):.1%} ({b.index[0]}), groups {len(b)}")
