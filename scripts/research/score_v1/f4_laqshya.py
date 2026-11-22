"""Lead final: LaQshya headline on the strict link set (linkage lens), excluding the 3 links the hand check
called wrong or unsure. Read-only."""
import pandas as pd, numpy as np, re, math
from scipy.stats import beta
D="/Users/hrudaynara/Projects/CMU Datathon/data"
L=pd.read_csv("/private/tmp/claude-501/-Users-hrudaynara-Projects-CMU-Datathon/dda738e7-6b95-4922-b02b-c70ed6c6d96e/scratchpad/score_research/linkage/laq_strict_links.csv")
bad=L.our_name.isin(["CH Centre Kozhikode","District Hospital, Mandya"])|L.laq_name.str.contains("Women Child & Superspeciality",regex=False)
print("links",len(L),"excluded",int(bad.sum()))
L=L[~bad]
laq=pd.read_parquet(f"{D}/silver/ext_laqshya.parquet")
ot=laq[laq.department.eq("Maternity OT")]
L=L.assign(has_ot=L.uid.isin(set(ot.facility_uid)))
otv=ot.assign(valid=~ot.expired_as_of_retrieval.astype(bool))
valid_uids=set(otv.loc[otv.valid,"facility_uid"]); cert_uids=set(otv.loc[otv.certification_status.eq("certified"),"facility_uid"])
O=L[L.has_ot].copy()
O["ot_valid_now"]=O.uid.isin(valid_uids); O["ot_fully_certified"]=O.uid.isin(cert_uids)
facts=pd.read_parquet(f"{D}/silver/facts.parquet",columns=["facility_id","text"])
cs_re=re.compile(r"caesar|cesar|c-section|c section|\blscs\b|cesarian|caeserean",re.I)
cs_fac=set(facts.loc[facts.text.fillna("").str.contains(cs_re),"facility_id"])
O["cs_text"]=O.fid.isin(cs_fac)
g=pd.read_parquet(f"{D}/gold/capability_grades.parquet",columns=["facility_id","service_line","grade"])
e=g[g.service_line=="emoc"].set_index("facility_id").grade
O["emoc_grade"]=O.fid.map(e)
print("distinct facilities with a Maternity OT row:",O.fid.nunique(), " uids:",O.uid.nunique())
print("valid on Nov 21:",int(O.ot_valid_now.sum())," fully certified (ever):",int(O.ot_fully_certified.sum()))
print("caesarean in any fact:",int(O.cs_text.sum()))
print("emoc grades:",O.emoc_grade.value_counts().sort_index().to_dict())
def wilson(k,n,z=1.96):
    p=k/n; den=1+z*z/n; c=p+z*z/(2*n); h=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n)); return ((c-h)/den,(c+h)/den)
n=O.fid.nunique(); k=int(O.drop_duplicates('fid').cs_text.sum())
print(f"headline: {k} of {n} have caesarean text; Wilson 95% {wilson(k,n)[0]:.3f} to {wilson(k,n)[1]:.3f}")
print("public share:",O.our_op.value_counts().to_dict())
# link precision lower bounds for acceptance
for n_,errs in [(50,0),(50,1),(50,2),(100,2),(100,5),(41,4)]:
    k_=n_-errs
    lb=beta.ppf(0.05,k_,n_-k_+1) if k_<n_ else 0.05**(1/n_)
    print(f"one-sided 95% CP lower bound, {k_}/{n_} correct: {lb:.3f}")
# phone audit interval examples
for k_,n_ in [(14,20),(16,20),(18,20),(10,20)]:
    print("phone k/n",k_,n_,[round(x,2) for x in wilson(k_,n_)])
