import pandas as pd, numpy as np, sys
R = "/Users/hrudaynara/Projects/CMU Datathon/data"
out = sys.argv[1]
fac = pd.read_parquet(f"{R}/silver/facilities.parquet", columns=["facility_id","lat","lon","coord_valid","coord_check","operator_type","name"])
g = pd.read_parquet(f"{R}/gold/capability_grades.parquet", columns=["facility_id","service_line","grade","evidence_strength"])
w = g.pivot_table(index="facility_id", columns="service_line", values="grade", aggfunc="max").reset_index()
fac = fac.merge(w, on="facility_id", how="left")
fac.to_parquet(f"{out}/fac_grades.parquet")
po = pd.read_parquet(f"{R}/silver/post_offices.parquet", columns=["po_id","lat","lon","coord_valid","district_key","state_pin","office_type"])
po = po[po.coord_valid & po.lat.between(6,37.5) & po.lon.between(68,98)]
po.to_parquet(f"{out}/po.parquet")
pop = pd.read_parquet(f"{R}/silver/ext_district_population.parquet")
pop.to_parquet(f"{out}/pop.parquet")
print(fac.shape, po.shape, pop.shape)
print(fac.coord_check.value_counts())
for s in ["emoc","nicu","dialysis","icu","cardiac_surgery","cath_lab","mri","ct","trauma","burns","neurosurgery","oncology"]:
    v = fac[fac.coord_valid]
    print(s, "g2+ with coords:", int((v[s]>=2).sum()), "g3:", int((v[s]>=3).sum()))
