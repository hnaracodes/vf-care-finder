"""Lead final: counts on the CURRENT build (capability_grades sha 07c52631...). Read-only."""
import pandas as pd, numpy as np, json, re, hashlib
D="/Users/hrudaynara/Projects/CMU Datathon/data"
def sha(p): return hashlib.sha256(open(p,'rb').read()).hexdigest()[:12]
print("grades sha", sha(f"{D}/gold/capability_grades.parquet"), "fact_tags sha", sha(f"{D}/gold/fact_tags.parquet"))
g=pd.read_parquet(f"{D}/gold/capability_grades.parquet")
fac=pd.read_parquet(f"{D}/silver/facilities.parquet")
print("recipe", g.recipe_version.unique(), "method", g.method.unique())
ct=g.pivot_table(index="service_line",columns="grade",values="facility_id",aggfunc="count").fillna(0).astype(int)
print(ct)
# regex v0.2 snapshot for drift
old=pd.read_parquet(f"{D}/models/wp4_student/regex_v0.2/capability_grades.parquet",columns=["facility_id","service_line","grade"])
m=g[["facility_id","service_line","grade"]].merge(old,on=["facility_id","service_line"],suffixes=("","_rx"))
dr=m.groupby("service_line").apply(lambda x: pd.Series(dict(changed=int((x.grade!=x.grade_rx).sum()),crossed_gate=int(((x.grade>=2)!=(x.grade_rx>=2)).sum()),
   g2_rx=int((x.grade_rx==2).sum()),g2=int((x.grade==2).sum()),g3_rx=int((x.grade_rx==3).sum()),g3=int((x.grade==3).sum()))))
print("drift vs regex v0.2 snapshot:\n",dr)
# obstetric specialty labels
import ast
def lst(x):
    if x is None: return []
    if isinstance(x,(list,tuple,np.ndarray)): return list(x)
    try: return json.loads(x)
    except Exception:
        try: return ast.literal_eval(x)
        except Exception: return []
sp=fac.specialties.map(lst)
OB={'gynecologyAndObstetrics','maternalFetalMedicineOrPerinatology','obstetricsAndMaternityCare'}
has_ob=sp.map(lambda s: bool(OB & set(s)))
print("facilities with an obstetric specialty label:", int(has_ob.sum()))
e=g[g.service_line=="emoc"].set_index("facility_id")
print("emoc grade>=2 (caesarean named in a fact):", int((e.grade>=2).sum()), " grade1+:",int((e.grade>=1).sum()))
ob_ids=set(fac.facility_id[has_ob])
print("of obstetric-label facilities, share with caesarean named:", round(float((e.loc[e.index.isin(ob_ids),'grade']>=2).mean()),4), int((e.loc[e.index.isin(ob_ids),'grade']>=2).sum()))
# public / hospital shares by line at grade 2 and 3 (for labels needed)
fac["is_hosp"]=fac.facility_type.eq("hospital")
usable=fac.coord_check.ne("excluded_unresolved")
H=fac[fac.is_hosp & usable].facility_id
print("usable hospitals", len(H))
gh=g[g.facility_id.isin(set(H))]
rows=[]
for line,x in gh.groupby("service_line"):
    n=len(H); g2=int((x.grade==2).sum()); g3=int((x.grade==3).sum()); g1=int((x.grade==1).sum())
    rows.append(dict(line=line,g1=g1,g2=g2,g3=g3,hosp_for_30_g1=int(np.ceil(30*n/max(g1,1))),hosp_for_30_g2=int(np.ceil(30*n/max(g2,1))),hosp_for_30_g3=int(np.ceil(30*n/max(g3,1)))))
print(pd.DataFrame(rows).to_string(index=False))
# public share of grade 2+ by line
pub=set(fac.facility_id[fac.operator_type.eq("public")])
for line,x in g[g.grade>=2].groupby("service_line"):
    print(line, "g2+",len(x),"public",int(x.facility_id.isin(pub).sum()), round(x.facility_id.isin(pub).mean()*100,1))
