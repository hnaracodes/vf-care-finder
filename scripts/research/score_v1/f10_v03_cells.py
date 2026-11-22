"""Lead final: per-line hospital cells under the v0.3 rule-level changes (current tag build). Read-only."""
import re, numpy as np, pandas as pd
D="/Users/hrudaynara/Projects/CMU Datathon/data"
ft=pd.read_parquet(f"{D}/gold/fact_tags.parquet",columns=["fact_id","facility_id","tags"])
ft=ft[ft.tags.map(len)>0].merge(pd.read_parquet(f"{D}/silver/facts.parquet",columns=["fact_id","text"]),on="fact_id")
ft["tags"]=ft.tags.map(set); ft["low"]=ft.text.fillna("").str.lower()
has=lambda t: set(ft.loc[ft.tags.map(lambda s: t in s),"facility_id"])
T={t:has(t) for t in ["nicu","neonatal_resp","ventilator","csection","blood","cath_lab","ccu","icu","dialysis","dialysis_unit"]}
nf=ft[ft.tags.map(lambda s:"nicu" in s)]
nicu_named=set(nf.loc[nf.low.str.contains(r"\bnicu\b|neonatal intensive|\bnnicu\b"),"facility_id"])
vent_neo=set(ft.loc[ft.tags.map(lambda s:"ventilator" in s)&ft.low.str.contains(r"neonat|newborn|\bnicu\b|\bsncu\b|preterm|prematur|infant"),"facility_id"])
lvl3=set(ft.loc[ft.low.str.contains(r"level\s*(?:iii|3)\b.*(?:nicu|neonat)|(?:nicu|neonat).*level\s*(?:iii|3)\b"),"facility_id"])
du=ft[ft.tags.map(lambda s:"dialysis_unit" in s)]
du_named=set(du.loc[du.low.str.contains(r"dialysis (?:unit|machine|centre|center|ward|room|facility|department)|h(?:a)?emodialysis"),"facility_id"])
v3={"nicu":(nicu_named, nicu_named&(T["neonatal_resp"]|vent_neo|lvl3)),
    "emoc":(T["csection"], T["csection"]&T["blood"]),
    "cath_lab":(T["cath_lab"], T["cath_lab"]&(T["ccu"]|(T["icu"]&T["ventilator"]))),
    "dialysis":(T["dialysis"], T["dialysis"]&du_named)}
fac=pd.read_parquet(f"{D}/silver/facilities.parquet",columns=["facility_id","facility_type","coord_check"])
H=set(fac.facility_id[fac.facility_type.eq("hospital")&fac.coord_check.ne("excluded_unresolved")]); n=len(H)
for line,(sup,cor) in v3.items():
    g2=len((sup-cor)&H); g3=len(cor&H)
    print(f"{line:9s} v0.3 hospitals g2 {g2:5d} (need {int(np.ceil(30*n/max(g2,1)))}) g3 {g3:4d} (need {int(np.ceil(30*n/max(g3,1)))})  [all records g2+ {len(sup)}, g3 {len(cor)}]")
