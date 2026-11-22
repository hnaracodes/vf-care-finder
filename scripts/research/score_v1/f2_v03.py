"""Lead final: v0.3 rule-level recount on the CURRENT tag build (fact_tags sha 6fa461a5...), no tag redefinition.
Read-only."""
import pandas as pd, numpy as np, re
D="/Users/hrudaynara/Projects/CMU Datathon/data"
ft=pd.read_parquet(f"{D}/gold/fact_tags.parquet",columns=["fact_id","facility_id","tags"])
facts=pd.read_parquet(f"{D}/silver/facts.parquet",columns=["fact_id","text"])
g=pd.read_parquet(f"{D}/gold/capability_grades.parquet",columns=["facility_id","service_line","grade"])
fac=pd.read_parquet(f"{D}/silver/facilities.parquet",columns=["facility_id","operator_type","coord_check","specialties","facility_type"])
ft=ft[ft.tags.map(len)>0].merge(facts,on="fact_id",how="left")
ft["tags"]=ft.tags.map(set)
ft["low"]=ft.text.fillna("").str.lower()
def fac_with(pred):
    return set(ft.loc[ft.apply(pred,axis=1),"facility_id"])
has=lambda t: set(ft.loc[ft.tags.map(lambda s: t in s),"facility_id"])
T={t:has(t) for t in ["nicu","neonatal_resp","ventilator","csection","blood","anesthesia","ot","cath_lab","ccu","icu","h24","emergency","dialysis","dialysis_unit","trauma"]}
# NICU
nicu_named_re=re.compile(r"\bnicu\b|neonatal intensive|\bnnicu\b")
nicu_facts=ft[ft.tags.map(lambda s:"nicu" in s)]
nicu_named=set(nicu_facts.loc[nicu_facts.low.str.contains(nicu_named_re),"facility_id"])
nicu_any=T["nicu"]
sncu_only=nicu_any-nicu_named
pub=set(fac.facility_id[fac.operator_type.eq("public")])
print("NICU supported (any nicu tag):",len(nicu_any)," named nicu/neonatal intensive:",len(nicu_named)," SNCU-only:",len(sncu_only)," public SNCU-only:",len(sncu_only&pub), " public nicu g2+:", len(nicu_any&pub))
# neonatal ventilator in same fact as nicu or neonatal words
neo_re=re.compile(r"neonat|newborn|\bnicu\b|\bsncu\b|preterm|prematur|infant")
vent_neo=set(ft.loc[ft.tags.map(lambda s:"ventilator" in s) & ft.low.str.contains(neo_re),"facility_id"])
lvl3=set(ft.loc[ft.low.str.contains(r"level\s*(iii|3)\b.*(nicu|neonat)|(nicu|neonat).*level\s*(iii|3)\b"),"facility_id"])
cur_corr=nicu_any & (T["neonatal_resp"]|T["ventilator"])
v03_corr=nicu_named & (T["neonatal_resp"]|vent_neo|lvl3)
print("NICU corroborated current:",len(cur_corr)," via generic ventilator only:",len(cur_corr-(T['neonatal_resp']|vent_neo|lvl3)), " v0.3 corroborated:",len(v03_corr), " v0.3 supported (named):",len(nicu_named))
# EmOC
cs=T["csection"]
cur_e=cs&T["blood"]&(T["anesthesia"]|T["ot"])
v03_e=cs&T["blood"]
fru=v03_e&T["anesthesia"]&T["h24"]
print("EmOC supported:",len(cs)," corroborated current:",len(cur_e)," via OT only:",len(cs&T['blood']&T['ot']-T['anesthesia'])," v0.3 (csection+blood):",len(v03_e)," FRU-level (+anaesthesia+24h):",len(fru), " +anaesthesia only:",len(v03_e&T['anesthesia']))
# Cath lab
cl=T["cath_lab"]
cur_c=cl&(T["ccu"]|T["icu"])&(T["h24"]|T["emergency"])
icu_vent=T["icu"]&T["ventilator"]
v03_c=cl&(T["ccu"]|icu_vent)
print("Cath lab supported:",len(cl)," corroborated current:",len(cur_c)," v0.3:",len(v03_c))
# Dialysis
du_facts=ft[ft.tags.map(lambda s:"dialysis_unit" in s)]
unit_re=re.compile(r"dialysis (unit|machine|centre|center)|h(a)?emodialysis|dialysis (ward|room|facility|department)")
du_named=set(du_facts.loc[du_facts.low.str.contains(unit_re),"facility_id"])
dia=T["dialysis"]
cur_d=dia&T["dialysis_unit"]
v03_d=dia&du_named
neph_only=cur_d-v03_d
print("Dialysis supported:",len(dia)," corroborated current:",len(cur_d)," v0.3 (unit named, not nephrologist only):",len(v03_d)," demoted (nephrologist-only):",len(neph_only))
# sample of excluded facts for hand check
ex=du_facts[du_facts.facility_id.isin(neph_only)&~du_facts.low.str.contains(unit_re)].text.head(8).tolist()
print("sample nephrologist-only dialysis_unit facts:",ex)
ex2=nicu_facts[nicu_facts.facility_id.isin(sncu_only)].text.head(6).tolist()
print("sample SNCU-only nicu facts:",ex2)
# trauma emergency tag fires on 'trauma'
tr=T["trauma"]
print("trauma facilities with emergency tag:",len(tr&T["emergency"]),"of",len(tr))
