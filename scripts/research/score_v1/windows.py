import numpy as np, pandas as pd, sys
from scipy.stats import norm
S = sys.argv[1]
po = pd.read_parquet(f"{S}/out/po_nearest_tt_snapped.parquet")
RAD = dict(emoc=50, trauma=50, dialysis=50, ct=75, icu=75, nicu=75, mri=100, cath_lab=100, neurosurgery=150, burns=200, cardiac_surgery=250, oncology=250)
WIN = dict(emoc=120, trauma=120, dialysis=60, ct=120, icu=120, nicu=120, mri=180, cath_lab=90, neurosurgery=150, burns=240, cardiac_surgery=240, oncology=240)
SIG = {k: (0.5 if k in ("dialysis", "mri", "cardiac_surgery", "oncology") else 0.3) for k in WIN}
w = po.w.values; W = w.sum()
rows = []
for s in RAD:
    km, tt = po[s+"_km"].values, po[s+"_min"].values
    reach = norm.cdf((np.log(WIN[s]) - np.log(np.maximum(tt, 1))) / SIG[s])
    rows.append(dict(service=s, radius_km=RAD[s], window_min=WIN[s],
        abstain_by_radius=(w*(km > RAD[s])).sum()/W, abstain_by_window=(w*(tt > WIN[s])).sum()/W,
        radius_says_no_window_says_yes=(w*((km > RAD[s]) & (tt <= WIN[s]))).sum()/W,
        radius_says_yes_window_says_no=(w*((km <= RAD[s]) & (tt > WIN[s]))).sum()/W,
        mean_soft_reach=(w*reach).sum()/W,
        pop_M_beyond_window=(w*(tt > WIN[s])).sum()/1e6))
T = pd.DataFrame(rows)
pd.set_option("display.width", 250)
print("Population-weighted (district pop 2011 / POs in district), nearest grade 2+ facility, MAP free-flow minutes")
print(T.round(3).to_string(index=False))
# OSRM pairs: window misclassification by estimator, T in {60, 90, 120}
R = pd.read_csv(f"{S}/out/osrm_pairs.csv")
R["sl_model"] = np.exp(0.833) * R.km ** 0.842
R["sl_flat"] = R.km * 1.24
for Tm in (60, 90, 120, 240):
    y = R.osrm_min <= Tm
    for est in ("sl_model", "sl_flat", "map_min"):
        print(f"T={Tm:3d} {est:8s}: wrong side of window vs OSRM {np.mean((R[est] <= Tm) != y):.3f}")
# straight-line equivalents of windows under the OSRM fit, with 68% band
for Tm in (60, 90, 120, 150, 180, 240):
    km = np.exp((np.log(Tm) - 0.833) / 0.842)
    print(f"{Tm} min ~ {km:.0f} km straight-line (free-flow, median)")
